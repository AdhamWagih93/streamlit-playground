"""UI-managed, resumable, per-project Jira synchronisation engine.

This builds on the one-shot ``app.migration.importer`` but adds:

* **Resumability** — the sync watermark (``ProjectSyncLink.updated_watermark``)
  and a coarse page cursor (``cursor_start_at``) are persisted and the DB
  session is committed every ``COMMIT_EVERY`` issues, so an interrupted or
  paused run picks up where it left off instead of restarting.
* **Idempotency** — every row is upserted by ``external_id`` (Jira id), Jira
  issue keys/numbers are preserved verbatim, and re-running only updates rows
  in place. The same upsert patterns the importer uses are reused here.
* **Cooperative pause** — between batches the engine re-reads ``link.status``
  from the database; if the UI flipped it to ``paused`` the run stops
  gracefully (no error) and can later be resumed.
* **Permission import** — optionally translates the Jira project's permission
  scheme into a Trackly :class:`PermissionScheme` with faithful grant holders.

The engine is deliberately defensive: metadata / permission failures are logged
and skipped rather than aborting an otherwise good issue sync, and a fatal error
records ``link.status='error'`` + ``link.last_error`` without crashing the
background worker.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.core.database import SessionLocal
from app.migration.jira_client import JiraClient
from app.migration.mapper import (
    adf_to_text,
    map_issue_fields,
    map_priority_rank,
    map_status_category,
    map_user,
    parse_jira_datetime,
)
from app.models import (
    Comment,
    Group,
    Issue,
    IssueType,
    PermissionGrant,
    PermissionScheme,
    Priority,
    Project,
    ProjectRole,
    ProjectSyncLink,
    Status,
    SyncRun,
    User,
    Worklog,
)
from app.models.identity import JiraConnection
from app.services import permission_keys as P
from app.services.issues import resolve_labels
from app.utils.ranking import initial_rank, rank_between

log = logging.getLogger("trackly.services.jira_sync")

# Commit (and persist the resume cursor) after this many issues.
COMMIT_EVERY = 25

# Jira's JQL date literal format for the ``updated >=`` watermark clause.
JQL_DATE_FMT = "%Y/%m/%d %H:%M"

DEFAULT_ISSUE_FIELDS = [
    "summary", "description", "issuetype", "status", "priority",
    "assignee", "reporter", "parent", "labels", "duedate",
    "timeoriginalestimate", "timeestimate", "resolution", "resolutiondate",
    "created", "updated",
    "customfield_10016", "customfield_10026", "customfield_10002",
    "customfield_10014", "customfield_10008",
]


# --------------------------------------------------------------------------- #
# Client / connection helpers
# --------------------------------------------------------------------------- #
def build_client(connection: JiraConnection) -> JiraClient:
    """Construct a :class:`JiraClient` from a stored connection row.

    Server/DC connections (``auth_mode == 'server'``) authenticate with a
    Bearer PAT (no email); Cloud connections use HTTP Basic (email + token).
    """
    token = decrypt(connection.api_token_enc) or ""
    if (connection.auth_mode or "").strip().lower() == "server":
        return JiraClient(
            base_url=connection.base_url,
            email="",
            api_token=token,
            verify=connection.verify_ssl,
            server_token=True,
        )
    return JiraClient(
        base_url=connection.base_url,
        email=connection.email or "",
        api_token=token,
        verify=connection.verify_ssl,
        server_token=False,
    )


def get_default_connection(db: Session) -> JiraConnection | None:
    """Return the preferred enabled Jira connection (default first)."""
    conns = list(
        db.scalars(
            select(JiraConnection)
            .where(JiraConnection.enabled.is_(True))
            .order_by(JiraConnection.is_default.desc(), JiraConnection.id.asc())
        )
    )
    return conns[0] if conns else None


def discover(db: Session, project: Project, connection: JiraConnection) -> dict:
    """Probe Jira for the project that should be linked to *project*.

    Returns a dict matching :class:`DiscoverResult` fields. Never raises; on any
    error returns ``found=False`` with a human-readable message.
    """
    key = (project.key or "").strip().upper()
    client: JiraClient | None = None
    try:
        client = build_client(connection)
        jp = client.get_project(key)
        result: dict[str, Any] = {
            "found": True,
            "jira_project_key": jp.get("key") or key,
            "name": jp.get("name"),
            "jira_project_id": str(jp.get("id")) if jp.get("id") else None,
            "issue_count": None,
            "message": None,
        }
        # Best-effort lightweight count; omit silently if it isn't cheap.
        try:
            result["issue_count"] = _count_issues(client, jp.get("key") or key)
        except Exception:  # noqa: BLE001 - count is optional
            pass
        return result
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        msg = f"Jira returned {code} for project {key}"
        if code == 404:
            msg = f"No Jira project found with key {key}"
        elif code in (401, 403):
            msg = f"Not authorized to read Jira project {key}"
        return {"found": False, "jira_project_key": key, "message": msg}
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "jira_project_key": key, "message": str(exc)}
    finally:
        if client is not None:
            client.close()


def _count_issues(client: JiraClient, key: str) -> int | None:
    """Cheap total via the classic search ``total`` (maxResults=0)."""
    jql = f'project = "{key}"'
    try:
        page = client._post("/rest/api/3/search", json={"jql": jql, "maxResults": 0})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            page = client._post("/rest/api/2/search", json={"jql": jql, "maxResults": 0})
        else:
            raise
    return page.get("total")


# --------------------------------------------------------------------------- #
# The resumable sync engine
# --------------------------------------------------------------------------- #
class JiraSyncEngine:
    """Run a resumable, idempotent sync for a single :class:`ProjectSyncLink`."""

    def __init__(self, db: Session, link: ProjectSyncLink) -> None:
        self.db = db
        self.link = link
        self.project: Project = db.get(Project, link.project_id)
        self.connection: JiraConnection = link.connection or db.get(
            JiraConnection, link.connection_id
        )
        self.client: JiraClient | None = None

        # In-memory resolution maps (mirrors importer).
        self.user_map: dict[str, int] = {}
        self.status_map: dict[str, int] = {}
        self.status_name_map: dict[str, int] = {}
        self.type_map: dict[str, int] = {}
        self.type_name_map: dict[str, int] = {}
        self.priority_map: dict[str, int] = {}
        self.priority_name_map: dict[str, int] = {}
        self.story_point_field_ids: list[str] = []
        self.epic_link_field_ids: list[str] = []

        # Counters for the SyncRun audit record.
        self._created = 0
        self._updated = 0
        self._errors = 0

    # -- public entrypoint -------------------------------------------------
    def run(self, trigger: str = "manual") -> SyncRun:
        now = datetime.now(timezone.utc)
        run = SyncRun(
            link_id=self.link.id,
            started_at=now,
            status="running",
            trigger=trigger,
            actor_id=None,
        )
        self.db.add(run)
        self.link.status = "running"
        self.link.last_error = None
        self.db.commit()

        try:
            self.client = build_client(self.connection)

            # 1) Reference data (global rows; project_id None).
            self._discover_fields()
            self._ensure_issue_types()
            self._ensure_statuses()
            self._ensure_priorities()

            # 2) Optional permission-scheme import.
            if self.link.sync_permissions:
                try:
                    self.import_permission_scheme(self.project, self.client)
                except Exception as exc:  # noqa: BLE001 - never fail the sync
                    log.warning("Permission-scheme import skipped for %s: %s",
                                self.project.key, exc)
                self.db.commit()

            # 3) Resumable issue pull.
            paused = self._sync_issues(run)

            if paused:
                self.link.status = "paused"
                run.status = "paused"
                run.finished_at = datetime.now(timezone.utc)
                self._finalize_run(run)
                self.db.commit()
                log.info("Sync paused for %s after %s issues", self.project.key,
                         self.link.processed_issues)
                return run

            # 4) Completion: reset cursor, record watermark + counters.
            self.link.status = "completed"
            self.link.cursor_start_at = 0
            self.link.last_synced_at = datetime.now(timezone.utc)
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            self._finalize_run(run)
            self.db.commit()
            log.info("Sync completed for %s (%s issues this run)",
                     self.project.key, run.processed)
            return run

        except Exception as exc:  # noqa: BLE001 - keep the worker alive
            self.db.rollback()
            log.exception("Sync failed for project %s", self.project.key)
            try:
                self.link.status = "error"
                self.link.last_error = str(exc)[:2000]
                run.status = "error"
                run.errors = (run.errors or 0) + 1
                run.message = str(exc)[:2000]
                run.finished_at = datetime.now(timezone.utc)
                self.db.add(self.link)
                self.db.add(run)
                self.db.commit()
            except Exception:  # noqa: BLE001
                log.exception("Could not persist error state for %s", self.project.key)
            return run
        finally:
            if self.client is not None:
                self.client.close()
                self.client = None

    def _finalize_run(self, run: SyncRun) -> None:
        run.processed = self._created + self._updated
        run.created = self._created
        run.updated = self._updated
        run.errors = self._errors

    # -- field metadata ----------------------------------------------------
    def _discover_fields(self) -> None:
        try:
            fields = self.client.get_fields()
        except Exception as exc:  # noqa: BLE001
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

    def _lookups(self) -> dict:
        return {
            "story_points_field_ids": self.story_point_field_ids or None,
            "epic_link_field_ids": self.epic_link_field_ids or None,
        }

    # -- reference rows (global) -------------------------------------------
    def _ensure_issue_types(self) -> None:
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
                itype = IssueType(name=name, is_subtask=bool(jt.get("subtask")), project_id=None)
                self.db.add(itype)
                self.db.flush()
                existing_id = itype.id
                self.type_name_map[name.lower()] = existing_id
            jid = jt.get("id")
            if jid:
                self.type_map[str(jid)] = existing_id
        self.db.commit()

    def _ensure_statuses(self) -> None:
        for st in self.db.scalars(select(Status).where(Status.project_id.is_(None))):
            self.status_name_map[st.name.lower()] = st.id
        existing_orders = self.db.scalars(
            select(Status.order).where(Status.project_id.is_(None))
        ).all()
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
                status_row = Status(name=name, category=category, order=next_order, project_id=None)
                next_order += 1
                self.db.add(status_row)
                self.db.flush()
                existing_id = status_row.id
                self.status_name_map[name.lower()] = existing_id
            jid = js.get("id")
            if jid:
                self.status_map[str(jid)] = existing_id
        self.db.commit()

    def _ensure_priorities(self) -> None:
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
        self.db.commit()

    # -- resolution helpers ------------------------------------------------
    def _resolve_type_id(self, fields: dict) -> int:
        tid = self.type_map.get(fields.get("type_ref") or "")
        if tid:
            return tid
        name = (fields.get("type_name") or "").strip().lower()
        tid = self.type_name_map.get(name)
        if tid:
            return tid
        return self.type_name_map.get("task") or next(iter(self.type_name_map.values()))

    def _resolve_status_id(self, fields: dict) -> int:
        sid = self.status_map.get(fields.get("status_ref") or "")
        if sid:
            return sid
        name = (fields.get("status_name") or "").strip().lower()
        sid = self.status_name_map.get(name)
        if sid:
            return sid
        return next(iter(self.status_name_map.values()))

    def _resolve_priority_id(self, fields: dict) -> int | None:
        pid = self.priority_map.get(fields.get("priority_ref") or "")
        if pid:
            return pid
        name = (fields.get("priority_name") or "").strip().lower()
        return self.priority_name_map.get(name)

    def _upsert_user(self, mapped: dict | None) -> int | None:
        if not mapped:
            return None
        ext = mapped["external_id"]
        if ext in self.user_map:
            return self.user_map[ext]
        user = self.db.scalars(select(User).where(User.external_id == ext)).first()
        if user is None:
            user = self.db.scalars(select(User).where(User.email == mapped["email"])).first()
        if user is None:
            import secrets as _secrets
            from app.core.security import hash_password
            user = User(
                external_id=ext,
                username=self._unique_username(mapped["username"]),
                email=mapped["email"],
                display_name=mapped["display_name"],
                avatar_url=mapped.get("avatar_url"),
                password_hash=hash_password(_secrets.token_urlsafe(24)),
                is_active=True,
            )
            self.db.add(user)
        else:
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

    # -- the resumable issue loop ------------------------------------------
    def _sync_issues(self, run: SyncRun) -> bool:
        """Pull + upsert issues. Returns True if paused mid-run, else False."""
        project = self.project
        key = self.link.jira_project_key or project.key

        watermark_clause = ""
        if self.link.updated_watermark is not None:
            wm = self.link.updated_watermark
            if wm.tzinfo is None:
                wm = wm.replace(tzinfo=timezone.utc)
            watermark_clause = f'AND updated >= "{wm.strftime(JQL_DATE_FMT)}"'
        jql = f'project = "{key}" {watermark_clause} ORDER BY updated ASC'.replace("  ", " ").strip()

        fields = list(DEFAULT_ISSUE_FIELDS)
        for fid in self.story_point_field_ids + self.epic_link_field_ids:
            if fid not in fields:
                fields.append(fid)

        # Deferred parent/epic links: resolve after all issues exist.
        link_pass: list[tuple[int, str | None, str | None]] = []
        counter = project.issue_counter
        processed = 0
        max_updated: datetime | None = self.link.updated_watermark

        for jira_issue in self.client.iter_issues(jql, fields=fields, expand="renderedFields"):
            self._upsert_issue(jira_issue, project, link_pass, counter_box := [counter])
            counter = counter_box[0]

            mapped_updated = parse_jira_datetime(
                (jira_issue.get("fields") or {}).get("updated")
            )
            if mapped_updated and (max_updated is None or mapped_updated > max_updated):
                max_updated = mapped_updated

            processed += 1

            if processed % COMMIT_EVERY == 0:
                # Persist progress so an interruption resumes from here.
                project.issue_counter = max(project.issue_counter, counter)
                self.link.cursor_start_at = processed
                self.link.processed_issues = (self.link.processed_issues or 0)
                self.link.updated_watermark = max_updated
                run.processed = self._created + self._updated
                run.created = self._created
                run.updated = self._updated
                self.db.commit()
                log.info("  ... %s issues processed for %s", processed, key)

                # Cooperative pause: re-read status fresh from the DB.
                self.db.refresh(self.link, attribute_names=["status"])
                if self.link.status == "paused":
                    self.link.processed_issues = (self.link.processed_issues or 0) + 0
                    self._resolve_links(link_pass)
                    self.db.commit()
                    return True

        # Final flush of this run's tail.
        project.issue_counter = max(project.issue_counter, counter)
        self.link.processed_issues = processed
        self.link.total_issues = max(self.link.total_issues or 0, processed)
        self.link.updated_watermark = max_updated or datetime.now(timezone.utc)
        self.db.commit()

        # Second pass: resolve parent/epic links now that all keys exist.
        self._resolve_links(link_pass)
        self.db.commit()
        return False

    def _upsert_issue(
        self,
        jira_issue: dict,
        project: Project,
        link_pass: list[tuple[int, str | None, str | None]],
        counter_box: list[int],
    ) -> None:
        jkey = jira_issue.get("key")
        mapped = map_issue_fields(jira_issue, self._lookups())
        ext = str(jira_issue.get("id")) if jira_issue.get("id") else jkey

        number = self._number_from_key(jkey)
        if number is not None:
            counter_box[0] = max(counter_box[0], number)

        issue = self.db.scalars(select(Issue).where(Issue.external_id == ext)).first()
        if issue is None and jkey:
            issue = self.db.scalars(select(Issue).where(Issue.key == jkey)).first()

        assignee_id = self._upsert_user(mapped.get("assignee"))
        reporter_id = self._upsert_user(mapped.get("reporter"))
        type_id = self._resolve_type_id(mapped)
        status_id = self._resolve_status_id(mapped)
        priority_id = self._resolve_priority_id(mapped)

        if issue is None:
            last_rank = self.db.scalars(
                select(Issue.rank).where(Issue.project_id == project.id)
                .order_by(Issue.rank.desc()).limit(1)
            ).first()
            new_rank = rank_between(last_rank, None) if last_rank else initial_rank()
            issue = Issue(
                key=jkey,
                number=number if number is not None else (counter_box[0] or 1),
                project_id=project.id,
                external_id=ext,
                rank=new_rank,
                type_id=type_id,
                status_id=status_id,
                summary=mapped["summary"],
            )
            self.db.add(issue)
            self._created += 1
        else:
            issue.external_id = ext
            if jkey:
                issue.key = jkey
            if number is not None:
                issue.number = number
            issue.type_id = type_id
            issue.status_id = status_id
            self._updated += 1

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

        parent_key = mapped.get("parent_key")
        epic_key = mapped.get("epic_key")
        if parent_key and mapped.get("parent_is_epic"):
            epic_key = epic_key or parent_key
            parent_key = None
        if parent_key or epic_key:
            link_pass.append((issue.id, parent_key, epic_key))

        self._import_comments(issue, jkey)
        self._import_worklogs(issue, jkey)

    def _resolve_links(self, link_pass: list[tuple[int, str | None, str | None]]) -> None:
        for issue_id, parent_key, epic_key in link_pass:
            issue = self.db.get(Issue, issue_id)
            if issue is None:
                continue
            if parent_key:
                pid = self._lookup_issue_id_by_key(parent_key)
                if pid and pid != issue.id:
                    issue.parent_id = pid
            if epic_key:
                eid = self._lookup_issue_id_by_key(epic_key)
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
        try:
            comments = list(self.client.iter_comments(jkey))
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not fetch comments for %s: %s", jkey, exc)
            return
        for jc in comments:
            ext = str(jc.get("id")) if jc.get("id") else None
            if not ext:
                continue
            comment = self.db.scalars(select(Comment).where(Comment.external_id == ext)).first()
            author_id = self._upsert_user(map_user(jc.get("author")))
            body = adf_to_text(jc.get("body")) or "(empty comment)"
            created = parse_jira_datetime(jc.get("created"))
            updated = parse_jira_datetime(jc.get("updated"))
            if comment is None:
                comment = Comment(issue_id=issue.id, external_id=ext, author_id=author_id, body=body)
                self.db.add(comment)
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
        try:
            worklogs = list(self.client.iter_worklogs(jkey))
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not fetch worklogs for %s: %s", jkey, exc)
            return
        for wl in worklogs:
            seconds = wl.get("timeSpentSeconds")
            if not seconds:
                continue
            started = parse_jira_datetime(wl.get("started")) or parse_jira_datetime(wl.get("created"))
            if started is None:
                continue
            author_id = self._upsert_user(map_user(wl.get("author")))
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

    # -- permission-scheme import -----------------------------------------
    def import_permission_scheme(self, project: Project, client: JiraClient) -> None:
        """Translate the Jira project's permission scheme into a Trackly one.

        Defensive: any missing endpoint / authz failure is logged and the whole
        import is skipped without aborting the issue sync. Unmappable holders or
        permission keys are skipped individually.
        """
        key = (self.link.jira_project_key or project.key).upper()
        scheme_json = self._fetch_permission_scheme(client, key)
        if not scheme_json:
            log.info("No readable permission scheme for %s; skipping import", key)
            return

        permissions = (scheme_json.get("permissions") or [])
        ext_id = str(scheme_json.get("id")) if scheme_json.get("id") else None
        scheme_name = f"{project.key} (imported from Jira)"

        # Find/replace an existing imported scheme (by external_id or name).
        scheme: PermissionScheme | None = None
        if ext_id:
            scheme = self.db.scalars(
                select(PermissionScheme).where(PermissionScheme.external_id == ext_id)
            ).first()
        if scheme is None:
            scheme = self.db.scalars(
                select(PermissionScheme).where(PermissionScheme.name == scheme_name)
            ).first()
        if scheme is None:
            scheme = PermissionScheme(
                name=scheme_name,
                description=f"Imported from Jira project {key}",
                is_default=False,
                external_id=ext_id,
            )
            self.db.add(scheme)
            self.db.flush()
        else:
            scheme.external_id = ext_id or scheme.external_id
            # Replace existing grants wholesale for an idempotent re-import.
            for g in list(scheme.grants):
                self.db.delete(g)
            self.db.flush()

        seen: set[tuple[str, str, str | None]] = set()
        for entry in permissions:
            # entry: {"permission": {"key": "..."} or "permission": "...",
            #         "holder": {"type": "...", "parameter": "..."}}
            perm_key = self._jira_permission_key(entry)
            if perm_key not in P.PROJECT_PERMISSIONS:
                continue
            holder = entry.get("holder") or {}
            grant = self._map_holder(holder)
            if grant is None:
                continue
            holder_type, holder_value = grant
            dedupe = (perm_key, holder_type, holder_value)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            self.db.add(PermissionGrant(
                scheme_id=scheme.id,
                permission=perm_key,
                holder_type=holder_type,
                holder_value=holder_value,
            ))

        self.db.flush()
        project.permission_scheme_id = scheme.id
        self.db.flush()
        log.info("Imported permission scheme for %s (%s grants)", key, len(seen))

    @staticmethod
    def _jira_permission_key(entry: dict) -> str:
        perm = entry.get("permission")
        if isinstance(perm, dict):
            return (perm.get("key") or "").strip()
        return (perm or "").strip()

    def _map_holder(self, holder: dict) -> tuple[str, str | None] | None:
        """Map a Jira permission holder onto (holder_type, holder_value)."""
        jtype = (holder.get("type") or "").strip()
        mapping = P.JIRA_HOLDER_MAP.get(jtype)
        if mapping is None:
            return None
        holder_type, special_value = mapping

        if holder_type == P.HOLDER_SPECIAL:
            return (holder_type, special_value)

        parameter = (
            holder.get("parameter")
            or holder.get("value")
            or (holder.get("group") or {}).get("name")
            or (holder.get("user") or {}).get("accountId")
            or (holder.get("projectRole") or {}).get("name")
        )
        if not parameter:
            return None
        parameter = str(parameter)

        if holder_type == P.HOLDER_GROUP:
            self._ensure_group(parameter)
            return (P.HOLDER_GROUP, parameter)

        if holder_type == P.HOLDER_USER:
            # Store the Jira external id; the permission engine matches it
            # against User.external_id as well as the local id.
            return (P.HOLDER_USER, parameter)

        if holder_type == P.HOLDER_PROJECT_ROLE:
            role_name = self._role_name_from_holder(holder, parameter)
            self._ensure_role(role_name)
            return (P.HOLDER_PROJECT_ROLE, role_name)

        return None

    def _role_name_from_holder(self, holder: dict, fallback: str) -> str:
        pr = holder.get("projectRole") or {}
        return str(pr.get("name") or fallback)

    def _ensure_group(self, name: str) -> None:
        existing = self.db.scalars(select(Group).where(Group.name == name)).first()
        if existing is None:
            self.db.add(Group(name=name, description="Imported from Jira permission scheme"))
            self.db.flush()

    def _ensure_role(self, name: str) -> None:
        existing = self.db.scalars(select(ProjectRole).where(ProjectRole.name == name)).first()
        if existing is None:
            self.db.add(ProjectRole(name=name, description="Imported from Jira permission scheme"))
            self.db.flush()

    def _fetch_permission_scheme(self, client: JiraClient, key: str) -> dict | None:
        """GET the project's permission scheme (expanded with permissions).

        Reuses the :class:`JiraClient` session/auth via its private ``_get``
        helper so we don't re-implement auth. Returns the scheme dict (with a
        ``permissions`` list) or ``None`` if it can't be read.
        """
        try:
            head = client._get(
                f"/rest/api/3/project/{key}/permissionscheme",
                params={"expand": "permissions"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                try:
                    head = client._get(f"/rest/api/2/project/{key}/permissionscheme")
                except Exception:  # noqa: BLE001
                    return None
            elif exc.response.status_code in (401, 403):
                log.info("No permission to read scheme for %s (%s)", key,
                         exc.response.status_code)
                return None
            else:
                return None
        except Exception as exc:  # noqa: BLE001
            log.debug("Permission scheme fetch failed for %s: %s", key, exc)
            return None

        # If the project endpoint already inlined permissions, use it.
        if head.get("permissions"):
            return head

        scheme_id = head.get("id")
        if not scheme_id:
            return head
        try:
            full = client._get(
                f"/rest/api/3/permissionscheme/{scheme_id}",
                params={"expand": "permissions"},
            )
            return full
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not expand scheme %s for %s: %s", scheme_id, key, exc)
            return head


# --------------------------------------------------------------------------- #
# Background runner
# --------------------------------------------------------------------------- #
def start_sync_background(project_id: int, trigger: str = "manual", actor_id: int | None = None) -> None:
    """Run a sync in its own DB session (for BackgroundTasks / threads).

    Guards against a double-run: if the link is already ``running`` it refuses.
    The route flips the link to ``running`` before scheduling this, so we only
    bail when something else genuinely owns the run.
    """
    db = SessionLocal()
    try:
        link = db.scalars(
            select(ProjectSyncLink).where(ProjectSyncLink.project_id == project_id)
        ).first()
        if link is None:
            log.warning("No sync link for project %s; nothing to run", project_id)
            return

        engine = JiraSyncEngine(db, link)
        run = engine.run(trigger=trigger)
        if actor_id is not None and run is not None:
            try:
                run.actor_id = actor_id
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
    except Exception:  # noqa: BLE001 - never crash the worker
        log.exception("Background sync crashed for project %s", project_id)
        db.rollback()
    finally:
        db.close()
