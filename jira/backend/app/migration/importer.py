"""Orchestration: pull from Jira and upsert into Trackly's Postgres DB.

Everything is idempotent: rows are matched by ``external_id`` (Jira ids) or by
natural name, so re-running updates in place rather than duplicating. Jira
issue keys are preserved verbatim, enabling a phased cutover.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.bootstrap import run_bootstrap
from app.core.security import hash_password
from app.models import (
    Board,
    Comment,
    Issue,
    IssueType,
    Priority,
    Project,
    Status,
    User,
    Worklog,
)
from app.services.issues import resolve_labels
from app.utils.ranking import initial_rank, rank_between

from app.migration.jira_client import JiraClient
from app.migration.mapper import (
    map_issue_fields,
    map_priority_rank,
    map_status_category,
    map_user,
    parse_jira_datetime,
)

log = logging.getLogger("trackly.migration.importer")

DEFAULT_ISSUE_FIELDS = [
    "summary", "description", "issuetype", "status", "priority",
    "assignee", "reporter", "parent", "labels", "duedate",
    "timeoriginalestimate", "timeestimate", "resolution", "resolutiondate",
    "created", "updated",
    # Common story-point / epic-link customfields (extra ids added at runtime).
    "customfield_10016", "customfield_10026", "customfield_10002",
    "customfield_10014", "customfield_10008",
]


@dataclass
class ImportOptions:
    commit_every: int = 50
    import_comments: bool = True
    import_worklogs: bool = True


@dataclass
class ImportStats:
    users: int = 0
    projects: int = 0
    issues: int = 0
    comments: int = 0
    worklogs: int = 0

    def as_rows(self) -> list[tuple[str, int]]:
        return [
            ("Users", self.users),
            ("Projects", self.projects),
            ("Issues", self.issues),
            ("Comments", self.comments),
            ("Worklogs", self.worklogs),
        ]


class Importer:
    def __init__(self, db: Session, client: JiraClient, options: ImportOptions | None = None) -> None:
        self.db = db
        self.client = client
        self.options = options or ImportOptions()
        self.stats = ImportStats()

        # In-memory resolution maps (populated as we go).
        self.user_map: dict[str, int] = {}          # Jira external_id -> User.id
        self.status_map: dict[str, int] = {}        # Jira status id   -> Status.id
        self.status_name_map: dict[str, int] = {}   # status name      -> Status.id
        self.type_map: dict[str, int] = {}          # Jira type id     -> IssueType.id
        self.type_name_map: dict[str, int] = {}
        self.priority_map: dict[str, int] = {}      # Jira priority id -> Priority.id
        self.priority_name_map: dict[str, int] = {}
        self.issue_key_map: dict[str, int] = {}     # Jira issue key   -> Issue.id
        self.story_point_field_ids: list[str] = []
        self.epic_link_field_ids: list[str] = []

    # -- helpers -----------------------------------------------------------
    def _commit(self) -> None:
        self.db.commit()

    # -- field metadata ----------------------------------------------------
    def discover_fields(self) -> None:
        """Identify Story Points / Epic Link customfield ids by display name."""
        try:
            fields = self.client.get_fields()
        except Exception as exc:  # noqa: BLE001 - metadata is best-effort
            log.warning("Could not fetch field metadata: %s", exc)
            return
        for f in fields:
            name = (f.get("name") or "").strip().lower()
            fid = f.get("id")
            if not fid:
                continue
            if name in ("story points", "story point estimate"):
                self.story_point_field_ids.append(fid)
            elif name == "epic link":
                self.epic_link_field_ids.append(fid)
        if self.story_point_field_ids:
            log.info("Story-point fields: %s", self.story_point_field_ids)

    def _lookups(self) -> dict:
        return {
            "story_points_field_ids": self.story_point_field_ids or None,
            "epic_link_field_ids": self.epic_link_field_ids or None,
        }

    # -- users -------------------------------------------------------------
    def _upsert_user(self, mapped: dict | None) -> int | None:
        if not mapped:
            return None
        ext = mapped["external_id"]
        if ext in self.user_map:
            return self.user_map[ext]

        user = self.db.scalars(select(User).where(User.external_id == ext)).first()
        if user is None:
            # Fall back to matching by email to avoid clashing with seeded users.
            user = self.db.scalars(select(User).where(User.email == mapped["email"])).first()

        if user is None:
            user = User(
                external_id=ext,
                username=self._unique_username(mapped["username"]),
                email=mapped["email"],
                display_name=mapped["display_name"],
                avatar_url=mapped.get("avatar_url"),
                password_hash=hash_password(secrets.token_urlsafe(24)),
                is_active=True,
            )
            self.db.add(user)
            self.stats.users += 1
        else:
            # Update mutable attributes; never overwrite the existing password.
            user.external_id = ext
            user.display_name = mapped["display_name"]
            if mapped.get("avatar_url"):
                user.avatar_url = mapped["avatar_url"]
        self.db.flush()
        self.user_map[ext] = user.id
        return user.id

    def _unique_username(self, base: str) -> str:
        base = (base or "user").strip()[:100] or "user"
        candidate = base
        suffix = 1
        while self.db.scalars(select(User.id).where(User.username == candidate)).first():
            tail = f"-{suffix}"
            candidate = f"{base[:100 - len(tail)]}{tail}"
            suffix += 1
        return candidate

    def sync_users(self) -> None:
        log.info("Syncing users...")
        for jira_user in self.client.iter_users():
            self._upsert_user(map_user(jira_user))
        self._commit()
        log.info("Users synced (new this run: %s)", self.stats.users)

    # -- statuses ----------------------------------------------------------
    def ensure_statuses(self) -> None:
        log.info("Ensuring statuses...")
        # Preload existing global statuses by name.
        for st in self.db.scalars(select(Status).where(Status.project_id.is_(None))):
            self.status_name_map[st.name.lower()] = st.id

        existing_orders = self.db.scalars(select(Status.order).where(Status.project_id.is_(None))).all()
        next_order = (max(existing_orders) + 1) if existing_orders else 0

        try:
            jira_statuses = self.client.get_statuses()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch statuses: %s", exc)
            jira_statuses = []

        for js in jira_statuses:
            name = (js.get("name") or "").strip()
            if not name:
                continue
            category = map_status_category((js.get("statusCategory") or {}).get("key"))
            existing_id = self.status_name_map.get(name.lower())
            if existing_id is None:
                status = Status(name=name, category=category, order=next_order, project_id=None)
                next_order += 1
                self.db.add(status)
                self.db.flush()
                existing_id = status.id
                self.status_name_map[name.lower()] = existing_id
            jid = js.get("id")
            if jid:
                self.status_map[str(jid)] = existing_id
        self._commit()

    def _resolve_status_id(self, fields: dict) -> int:
        sid = self.status_map.get(fields.get("status_ref") or "")
        if sid:
            return sid
        name = (fields.get("status_name") or "").strip().lower()
        sid = self.status_name_map.get(name)
        if sid:
            return sid
        # Fall back to the first todo status.
        return next(iter(self.status_name_map.values()))

    # -- issue types -------------------------------------------------------
    def ensure_issue_types(self) -> None:
        log.info("Ensuring issue types...")
        for it in self.db.scalars(select(IssueType).where(IssueType.project_id.is_(None))):
            self.type_name_map[it.name.lower()] = it.id

        try:
            jira_types = self.client.get_issue_types()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch issue types: %s", exc)
            jira_types = []

        for jt in jira_types:
            name = (jt.get("name") or "").strip()
            if not name:
                continue
            existing_id = self.type_name_map.get(name.lower())
            if existing_id is None:
                itype = IssueType(
                    name=name,
                    is_subtask=bool(jt.get("subtask")),
                    project_id=None,
                )
                self.db.add(itype)
                self.db.flush()
                existing_id = itype.id
                self.type_name_map[name.lower()] = existing_id
            jid = jt.get("id")
            if jid:
                self.type_map[str(jid)] = existing_id
        self._commit()

    def _resolve_type_id(self, fields: dict) -> int:
        tid = self.type_map.get(fields.get("type_ref") or "")
        if tid:
            return tid
        name = (fields.get("type_name") or "").strip().lower()
        tid = self.type_name_map.get(name)
        if tid:
            return tid
        return self.type_name_map.get("task") or next(iter(self.type_name_map.values()))

    # -- priorities --------------------------------------------------------
    def ensure_priorities(self) -> None:
        log.info("Ensuring priorities...")
        for p in self.db.scalars(select(Priority)):
            self.priority_name_map[p.name.lower()] = p.id

        try:
            jira_priorities = self.client.get_priorities()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch priorities: %s", exc)
            jira_priorities = []

        for jp in jira_priorities:
            name = (jp.get("name") or "").strip()
            if not name:
                continue
            existing_id = self.priority_name_map.get(name.lower())
            if existing_id is None:
                prio = Priority(name=name, rank=map_priority_rank(name))
                self.db.add(prio)
                self.db.flush()
                existing_id = prio.id
                self.priority_name_map[name.lower()] = existing_id
            jid = jp.get("id")
            if jid:
                self.priority_map[str(jid)] = existing_id
        self._commit()

    def _resolve_priority_id(self, fields: dict) -> int | None:
        pid = self.priority_map.get(fields.get("priority_ref") or "")
        if pid:
            return pid
        name = (fields.get("priority_name") or "").strip().lower()
        return self.priority_name_map.get(name)

    # -- projects ----------------------------------------------------------
    def import_project(self, jira_project: dict) -> Project:
        ext = str(jira_project.get("id")) if jira_project.get("id") else None
        key = (jira_project.get("key") or "").strip()
        name = (jira_project.get("name") or key or "Imported").strip()
        description = jira_project.get("description")
        if isinstance(description, dict):
            from app.migration.mapper import adf_to_text
            description = adf_to_text(description)

        project = None
        if ext:
            project = self.db.scalars(select(Project).where(Project.external_id == ext)).first()
        if project is None and key:
            project = self.db.scalars(select(Project).where(Project.key == key)).first()

        lead_id = self._upsert_user(map_user(jira_project.get("lead")))

        if project is None:
            project = Project(
                key=key[:20] or "PROJ",
                name=name[:255],
                description=description,
                external_id=ext,
                lead_id=lead_id,
                issue_counter=0,
            )
            self.db.add(project)
            self.stats.projects += 1
        else:
            project.name = name[:255]
            project.description = description
            if ext:
                project.external_id = ext
            if lead_id:
                project.lead_id = lead_id
        self.db.flush()

        # Ensure a default scrum board exists.
        has_board = self.db.scalars(
            select(Board.id).where(Board.project_id == project.id)
        ).first()
        if not has_board:
            self.db.add(Board(project_id=project.id, name=f"{project.key} board", board_type="scrum"))
        self.db.flush()
        self._commit()
        log.info("Imported project %s (%s)", project.key, project.name)
        return project

    # -- issues ------------------------------------------------------------
    def import_issues(self, project: Project, jql: str) -> None:
        log.info("Importing issues for %s (jql: %s)", project.key, jql)
        last_rank = self.db.scalars(
            select(Issue.rank).where(Issue.project_id == project.id)
            .order_by(Issue.rank.desc()).limit(1)
        ).first()

        # (issue_id, parent_key, epic_key) deferred until all issues exist.
        link_pass: list[tuple[int, str | None, str | None]] = []
        counter = project.issue_counter
        processed = 0

        fields = list(DEFAULT_ISSUE_FIELDS)
        for fid in self.story_point_field_ids + self.epic_link_field_ids:
            if fid not in fields:
                fields.append(fid)

        for jira_issue in self.client.iter_issues(jql, fields=fields, expand="renderedFields"):
            jkey = jira_issue.get("key")
            mapped = map_issue_fields(jira_issue, self._lookups())
            ext = str(jira_issue.get("id")) if jira_issue.get("id") else jkey

            number = self._number_from_key(jkey)
            if number is not None:
                counter = max(counter, number)

            issue = self.db.scalars(select(Issue).where(Issue.external_id == ext)).first()
            if issue is None and jkey:
                issue = self.db.scalars(select(Issue).where(Issue.key == jkey)).first()

            assignee_id = self._upsert_user(mapped.get("assignee"))
            reporter_id = self._upsert_user(mapped.get("reporter"))
            type_id = self._resolve_type_id(mapped)
            status_id = self._resolve_status_id(mapped)
            priority_id = self._resolve_priority_id(mapped)

            if issue is None:
                last_rank = rank_between(last_rank, None) if last_rank else initial_rank()
                issue = Issue(
                    key=jkey,
                    number=number if number is not None else (counter or 1),
                    project_id=project.id,
                    external_id=ext,
                    rank=last_rank,
                    type_id=type_id,
                    status_id=status_id,
                    summary=mapped["summary"],
                )
                self.db.add(issue)
                self.stats.issues += 1
            else:
                issue.external_id = ext
                if jkey:
                    issue.key = jkey
                if number is not None:
                    issue.number = number
                issue.type_id = type_id
                issue.status_id = status_id

            # Common scalar fields (set on both create + update paths).
            issue.summary = mapped["summary"]
            issue.description = mapped["description"]
            issue.priority_id = priority_id
            issue.assignee_id = assignee_id
            issue.reporter_id = reporter_id
            issue.story_points = mapped["story_points"]
            issue.due_date = mapped["due_date"]
            issue.original_estimate_seconds = mapped["original_estimate_seconds"]
            issue.remaining_estimate_seconds = mapped["remaining_estimate_seconds"]
            issue.resolution = mapped["resolution"]
            issue.resolved_at = mapped["resolved_at"]
            if mapped.get("created_at"):
                issue.created_at = mapped["created_at"]
            if mapped.get("updated_at"):
                issue.updated_at = mapped["updated_at"]
            issue.labels = resolve_labels(self.db, mapped.get("labels") or [])

            self.db.flush()
            if jkey:
                self.issue_key_map[jkey] = issue.id

            parent_key = mapped.get("parent_key")
            epic_key = mapped.get("epic_key")
            # If the parent is an epic, treat it as the epic link instead.
            if parent_key and mapped.get("parent_is_epic"):
                epic_key = epic_key or parent_key
                parent_key = None
            if parent_key or epic_key:
                link_pass.append((issue.id, parent_key, epic_key))

            if self.options.import_comments:
                self._import_comments(issue, jkey)
            if self.options.import_worklogs:
                self._import_worklogs(issue, jkey)

            processed += 1
            if processed % self.options.commit_every == 0:
                self._commit()
                log.info("  ... %s issues processed for %s", processed, project.key)

        # Persist the highest seen number so future native creates don't collide.
        project.issue_counter = max(project.issue_counter, counter)
        self.db.flush()
        self._commit()

        # Second pass: resolve parent / epic links now that all keys are known.
        self._resolve_links(link_pass)
        self._commit()
        log.info("Finished %s: %s issues processed", project.key, processed)

    def _resolve_links(self, link_pass: list[tuple[int, str | None, str | None]]) -> None:
        for issue_id, parent_key, epic_key in link_pass:
            issue = self.db.get(Issue, issue_id)
            if issue is None:
                continue
            if parent_key:
                pid = self.issue_key_map.get(parent_key) or self._lookup_issue_id_by_key(parent_key)
                if pid and pid != issue.id:
                    issue.parent_id = pid
            if epic_key:
                eid = self.issue_key_map.get(epic_key) or self._lookup_issue_id_by_key(epic_key)
                if eid and eid != issue.id:
                    issue.epic_id = eid
        self.db.flush()

    def _lookup_issue_id_by_key(self, key: str) -> int | None:
        return self.db.scalars(select(Issue.id).where(Issue.key == key)).first()

    @staticmethod
    def _number_from_key(key: str | None) -> int | None:
        if not key or "-" not in key:
            return None
        tail = key.rsplit("-", 1)[-1]
        return int(tail) if tail.isdigit() else None

    # -- comments / worklogs ----------------------------------------------
    def _import_comments(self, issue: Issue, jkey: str | None) -> None:
        if not jkey:
            return
        from app.migration.mapper import adf_to_text
        for jc in self.client.iter_comments(jkey):
            ext = str(jc.get("id")) if jc.get("id") else None
            if not ext:
                continue
            comment = self.db.scalars(
                select(Comment).where(Comment.external_id == ext)
            ).first()
            author_id = self._upsert_user(map_user(jc.get("author")))
            body = adf_to_text(jc.get("body")) or "(empty comment)"
            created = parse_jira_datetime(jc.get("created"))
            updated = parse_jira_datetime(jc.get("updated"))
            if comment is None:
                comment = Comment(
                    issue_id=issue.id, external_id=ext,
                    author_id=author_id, body=body,
                )
                self.db.add(comment)
                self.stats.comments += 1
            else:
                comment.body = body
                comment.author_id = author_id
            self.db.flush()
            if created:
                comment.created_at = created
            if updated:
                comment.updated_at = updated

    def _import_worklogs(self, issue: Issue, jkey: str | None) -> None:
        if not jkey:
            return
        from app.migration.mapper import adf_to_text
        for wl in self.client.iter_worklogs(jkey):
            seconds = wl.get("timeSpentSeconds")
            if not seconds:
                continue
            started = parse_jira_datetime(wl.get("started")) or parse_jira_datetime(wl.get("created"))
            if started is None:
                continue
            ext = str(wl.get("id")) if wl.get("id") else None
            author_id = self._upsert_user(map_user(wl.get("author")))
            existing = None
            if ext:
                # Match an existing worklog for this issue by started+author+secs.
                existing = self.db.scalars(
                    select(Worklog).where(
                        Worklog.issue_id == issue.id,
                        Worklog.started_at == started,
                        Worklog.time_spent_seconds == int(seconds),
                    )
                ).first()
            if existing is None:
                self.db.add(Worklog(
                    issue_id=issue.id,
                    author_id=author_id,
                    time_spent_seconds=int(seconds),
                    comment=adf_to_text(wl.get("comment")) or None,
                    started_at=started,
                ))
                self.stats.worklogs += 1

    # -- top-level pipeline ------------------------------------------------
    def run(self, project_keys: list[str] | None = None, jql_extra: str = "") -> ImportStats:
        log.info("Bootstrapping Trackly schema + defaults...")
        run_bootstrap()

        self.discover_fields()
        self.sync_users()
        self.ensure_statuses()
        self.ensure_issue_types()
        self.ensure_priorities()

        projects = self._select_projects(project_keys)
        for jira_project in projects:
            project = self.import_project(jira_project)
            jql = self._build_jql(project.key, jql_extra)
            self.import_issues(project, jql)

        self._print_summary()
        return self.stats

    def _select_projects(self, project_keys: list[str] | None) -> list[dict]:
        if project_keys:
            out = []
            for key in project_keys:
                try:
                    out.append(self.client.get_project(key))
                except Exception as exc:  # noqa: BLE001
                    log.error("Could not fetch project %s: %s", key, exc)
            return out
        return list(self.client.iter_projects())

    @staticmethod
    def _build_jql(project_key: str, jql_extra: str) -> str:
        base = f'project = "{project_key}"'
        if jql_extra.strip():
            base = f"{base} AND ({jql_extra.strip()})"
        return f"{base} ORDER BY created ASC"

    def _print_summary(self) -> None:
        print("\n=== Migration summary ===")
        for label, value in self.stats.as_rows():
            print(f"  {label:<10} {value}")
        print("=========================\n")
