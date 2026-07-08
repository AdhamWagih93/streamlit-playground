"""Jira Data Center client scoped to ONE project (JIRA_PROJECT_KEY).

Live mode uses basic auth (JIRA_USER / JIRA_PASSWORD). Demo mode keeps a
mutable in-memory board so the whole flow works without a Jira."""

import datetime as dt

import requests

from ..config import settings


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def is_live() -> bool:
    return bool(settings.jira_base_url and settings.jira_user
                and settings.jira_password and not settings.demo_mode)


# ---------------------------------------------------------------- demo store
DEMO_OBJECTIVES = ["Platform Reliability", "Developer Experience",
                   "Security Hardening", "Cost Optimization"]


def _demo_issue(num, summary, status, priority, assignee, due_days, itype, desc="",
                components=None):
    return {
        "key": f"{settings.jira_project_key}-{num}",
        "summary": summary,
        "status": status,
        "priority": priority,
        "assignee": assignee,
        "type": itype,
        "due": (_now() + dt.timedelta(days=due_days)).date().isoformat() if due_days is not None else None,
        "created": (_now() - dt.timedelta(days=(num % 18) + 3)).isoformat(),
        "updated": (_now() - dt.timedelta(hours=num % 30)).isoformat(),
        "description": desc,
        "components": components or [],
        "url": f"#demo/{settings.jira_project_key}-{num}",
        "comments": [],
    }


_DEMO_ISSUES: list[dict] = [
    _demo_issue(101, "Payments pipeline: flaky integration stage blocks releases", "Reopened",
                "Highest", "alice", 0, "Bug", "3 of the last 5 runs failed on testcontainers startup.", components=["Platform Reliability"]),
    _demo_issue(102, "Add SLO dashboard for checkout service", "Open", "High", "bob", 2, "Story", components=["Platform Reliability"]),
    _demo_issue(103, "Security sweep: rotate registry pull secrets", "Open", "High", None, 1, "Task", components=["Security Hardening"]),
    _demo_issue(104, "Upgrade ingress-nginx to 1.11 in staging", "In Progress", "Medium", "carol", 4, "Task"),
    _demo_issue(105, "Self-service: microservice scaffold template", "In Progress", "High", "alice", 6, "Story", components=["Developer Experience"]),
    _demo_issue(106, "Self-service: document usage & on-call escalation", "In Progress", "Low", "dave", 9, "Task"),
    _demo_issue(107, "Terraform module: standard RDS with backups", "Resolved", "Medium", "bob", 3, "Story", components=["Cost Optimization"]),
    _demo_issue(108, "Fix cert-manager renewal alerts firing twice", "Resolved", "Medium", "carol", None, "Bug", components=["Platform Reliability"]),
    _demo_issue(109, "Migrate legacy cron jobs to Argo Workflows", "Closed", "High", "dave", None, "Story", components=["Developer Experience"]),
    _demo_issue(110, "Security sweep: enable image signing in releases", "Closed", "Highest", "alice", None, "Story", components=["Security Hardening"]),
    _demo_issue(111, "Spike: cost report per team namespace", "Open", "Low", None, 12, "Spike", components=["Cost Optimization"]),
    _demo_issue(112, "Security sweep: harden Jenkins agents (no root)", "Open", "Medium",
                "dave", 5, "Task", components=["Security Hardening"]),
]


def _source() -> str:
    return "live" if is_live() else ("demo" if settings.demo_mode else "not configured")


def _require_demo() -> None:
    if not settings.demo_mode:
        raise ValueError("Jira integration is not configured "
                         "(set JIRA_BASE_URL, JIRA_USER, JIRA_PASSWORD)")


def _demo_find(key: str) -> dict:
    _require_demo()
    for issue in _DEMO_ISSUES:
        if issue["key"] == key:
            return issue
    raise KeyError(key)


# ---------------------------------------------------------------- live client
def _session() -> requests.Session:
    s = requests.Session()
    s.auth = (settings.jira_user, settings.jira_password)
    s.headers.update({"Accept": "application/json"})
    return s


def _normalize(raw: dict) -> dict:
    f = raw.get("fields", {})
    return {
        "key": raw["key"],
        "summary": f.get("summary", ""),
        "status": (f.get("status") or {}).get("name", ""),
        "priority": (f.get("priority") or {}).get("name", "Medium"),
        "assignee": (f.get("assignee") or {}).get("name"),
        "type": (f.get("issuetype") or {}).get("name", "Task"),
        "due": f.get("duedate"),
        "created": f.get("created", ""),
        "updated": f.get("updated", ""),
        "description": f.get("description") or "",
        "components": [c.get("name") for c in (f.get("components") or [])],
        "url": f"{settings.jira_base_url}/browse/{raw['key']}",
        "comments": [],
    }


def _live_search(jql: str, page_size: int = 100, limit: int = 5000) -> list[dict]:
    """Paginates through ALL matching issues (Jira caps a page at ~100/1000).
    `limit` is a runaway safety net, not a feature."""
    out: list[dict] = []
    s = _session()
    while True:
        r = s.post(f"{settings.jira_base_url}/rest/api/2/search",
                   json={"jql": jql, "startAt": len(out), "maxResults": page_size,
                         "fields": ["summary", "status", "priority", "assignee",
                                    "issuetype", "duedate", "created", "updated",
                                    "description", "components"]},
                   timeout=30)
        r.raise_for_status()
        data = r.json()
        page = data.get("issues", [])
        out.extend(_normalize(i) for i in page)
        total = data.get("total", len(out))
        if not page or len(out) >= min(total, limit):
            return out


# ---------------------------------------------------------------- public API
def _column_for(issue: dict) -> str | None:
    """Reopened (etc.) issues are shown in the first column."""
    st = issue["status"].lower()
    if st in settings.reopened_statuses:
        return settings.board_statuses[0]
    for s in settings.board_statuses:
        if s.lower() == st:
            return s
    return None


def _not_done_jql() -> str:
    quoted = ", ".join(f'"{s}"' for s in settings._csv(settings.jira_done_statuses))
    return f"status not in ({quoted})"


def _is_open(issue: dict) -> bool:
    return issue["status"].lower() not in settings.done_statuses


def _board_jql() -> str:
    """All open issues + issues closed within the recent window only."""
    days = settings.jira_closed_window_days
    recently_closed = " OR ".join(
        f'status CHANGED TO "{s}" AFTER -{days}d'
        for s in settings._csv(settings.jira_done_statuses))
    return (f'project = "{settings.jira_project_key}" '
            f'AND ({_not_done_jql()} OR {recently_closed}) '
            f'ORDER BY priority DESC, updated DESC')


def list_objectives() -> list[str]:
    """Team objectives = the Jira project's components (archived ones excluded)."""
    if is_live():
        r = _session().get(
            f"{settings.jira_base_url}/rest/api/2/project/{settings.jira_project_key}/components",
            timeout=20)
        r.raise_for_status()
        return sorted(c["name"] for c in r.json() if not c.get("archived"))
    return list(DEMO_OBJECTIVES) if settings.demo_mode else []


def objectives_coverage() -> dict:
    """Open + recently-closed tickets per objective, and open tickets
    that violate the 'every open ticket has an objective' rule."""
    if is_live():
        issues = _live_search(_board_jql())
    else:
        issues = [dict(i) for i in _DEMO_ISSUES] if settings.demo_mode else []
    per = {o: {"open": 0, "closed_recent": 0} for o in list_objectives()}
    missing = []
    for i in issues:
        comps = i.get("components") or []
        if not comps:
            if _is_open(i):
                missing.append({"key": i["key"], "summary": i["summary"],
                                "assignee": i["assignee"], "url": i["url"]})
            continue
        bucket = "open" if _is_open(i) else "closed_recent"
        for c in comps:
            if c in per:  # archived components never surface as objective rows
                per[c][bucket] += 1
    return {"objectives": [{"name": k, **v} for k, v in per.items()],
            "missing": missing}


def set_components(key: str, names: list[str]) -> dict:
    if is_live():
        _session().put(f"{settings.jira_base_url}/rest/api/2/issue/{key}",
                       json={"fields": {"components": [{"name": n} for n in names]}},
                       timeout=20).raise_for_status()
        return _live_search(f'key = "{key}"')[0]
    issue = _demo_find(key)
    issue["components"] = names
    issue["updated"] = _now().isoformat()
    return dict(issue)


def get_assignee(key: str) -> str | None:
    if is_live():
        found = _live_search(f'key = "{key}"')
        return found[0]["assignee"] if found else None
    return _demo_find(key)["assignee"]


_GROUP_MIN_PREFIX = 8  # chars a shared summary prefix needs (after trimming) to form a group


def _prefix_groups(issues: list[dict]) -> dict[str, str]:
    """Cluster issues by the longest common starting string of their summaries.
    Greedy pass over the sorted summaries; a cluster needs >= 2 members and a
    word-boundary-trimmed prefix of at least _GROUP_MIN_PREFIX chars."""
    def common(a: str, b: str) -> str:
        n = 0
        for x, y in zip(a, b):
            if x.lower() != y.lower():
                break
            n += 1
        return a[:n]

    def trim(prefix: str) -> str:
        if " " in prefix and not prefix.endswith(" "):
            prefix = prefix[:prefix.rfind(" ")]  # never cut mid-word
        prefix = prefix.strip(" |-:/·—–,.(")
        return prefix if len(prefix) >= _GROUP_MIN_PREFIX else ""

    groups: dict[str, str] = {}
    cluster: list[dict] = []
    prefix = ""

    def flush() -> None:
        label = trim(prefix)
        if len(cluster) >= 2 and label:
            for i in cluster:
                groups[i["key"]] = label

    for issue in sorted(issues, key=lambda i: i["summary"].lower()):
        if cluster:
            shared = common(prefix, issue["summary"])
            if trim(shared):
                cluster.append(issue)
                prefix = shared
                continue
            flush()
        cluster, prefix = [issue], issue["summary"]
    flush()
    return groups


def board() -> dict:
    if is_live():
        issues = _live_search(_board_jql())
    else:
        issues = [dict(i) for i in _DEMO_ISSUES] if settings.demo_mode else []
    groups = _prefix_groups(issues)
    for i in issues:
        i["needs_objective"] = _is_open(i) and not i.get("components")
        i["group"] = groups.get(i["key"])
    def _label(s: str) -> str:
        # done columns are windowed to the recent past, name them accordingly
        return f"Recently {s.lower()}" if s.lower() in settings.done_statuses else s

    columns = [{"name": s, "label": _label(s),
                "issues": [i for i in issues if _column_for(i) == s]}
               for s in settings.board_statuses]
    return {"project": settings.jira_project_key, "columns": columns,
            "source": _source()}


def my_open_issues(username: str) -> list[dict]:
    if is_live():
        return _live_search(
            f'project = "{settings.jira_project_key}" AND assignee = "{username}" '
            f'AND {_not_done_jql()} ORDER BY priority DESC, duedate ASC')
    if not settings.demo_mode:
        return []
    return [i for i in _DEMO_ISSUES if i["assignee"] == username and _is_open(i)]


def unassigned_issues() -> list[dict]:
    if is_live():
        return _live_search(
            f'project = "{settings.jira_project_key}" AND assignee IS EMPTY '
            f'AND {_not_done_jql()} ORDER BY priority DESC')
    if not settings.demo_mode:
        return []
    return [i for i in _DEMO_ISSUES if not i["assignee"] and _is_open(i)]


def transition_issue(key: str, to_status: str) -> dict:
    if is_live():
        s = _session()
        r = s.get(f"{settings.jira_base_url}/rest/api/2/issue/{key}/transitions", timeout=20)
        r.raise_for_status()
        match = next((t for t in r.json().get("transitions", [])
                      if t["to"]["name"].lower() == to_status.lower()
                      or t["name"].lower() == to_status.lower()), None)
        if not match:
            raise ValueError(f"no transition to '{to_status}' from current status of {key}")
        s.post(f"{settings.jira_base_url}/rest/api/2/issue/{key}/transitions",
               json={"transition": {"id": match["id"]}}, timeout=20).raise_for_status()
        return _live_search(f'key = "{key}"')[0]
    issue = _demo_find(key)
    issue["status"] = to_status
    issue["updated"] = _now().isoformat()
    return dict(issue)


def add_comment(key: str, body: str, username: str) -> None:
    if is_live():
        _session().post(f"{settings.jira_base_url}/rest/api/2/issue/{key}/comment",
                        json={"body": body}, timeout=20).raise_for_status()
        return
    _demo_find(key)["comments"].append(
        {"author": username, "body": body, "at": _now().isoformat()})


def assign(key: str, username: str | None) -> dict:
    """username=None unassigns (used to restore an originally-unassigned ticket)."""
    if is_live():
        _session().put(f"{settings.jira_base_url}/rest/api/2/issue/{key}/assignee",
                       json={"name": username}, timeout=20).raise_for_status()
        return _live_search(f'key = "{key}"')[0]
    issue = _demo_find(key)
    issue["assignee"] = username
    issue["updated"] = _now().isoformat()
    return dict(issue)
