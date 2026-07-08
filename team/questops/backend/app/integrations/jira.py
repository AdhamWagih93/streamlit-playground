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
def _demo_issue(num, summary, status, priority, assignee, due_days, itype, desc=""):
    return {
        "key": f"{settings.jira_project_key}-{num}",
        "summary": summary,
        "status": status,
        "priority": priority,
        "assignee": assignee,
        "type": itype,
        "due": (_now() + dt.timedelta(days=due_days)).date().isoformat() if due_days is not None else None,
        "updated": (_now() - dt.timedelta(hours=num % 30)).isoformat(),
        "description": desc,
        "url": f"#demo/{settings.jira_project_key}-{num}",
        "comments": [],
    }


_DEMO_ISSUES: list[dict] = [
    _demo_issue(101, "Payments pipeline: flaky integration stage blocks releases", "Reopened",
                "Highest", "alice", 0, "Bug", "3 of the last 5 runs failed on testcontainers startup."),
    _demo_issue(102, "Add SLO dashboard for checkout service", "Open", "High", "bob", 2, "Story"),
    _demo_issue(103, "Rotate registry pull secrets across all namespaces", "Open", "High", None, 1, "Task"),
    _demo_issue(104, "Upgrade ingress-nginx to 1.11 in staging", "In Progress", "Medium", "carol", 4, "Task"),
    _demo_issue(105, "Self-service: template for new microservice scaffold", "In Progress", "High", "alice", 6, "Story"),
    _demo_issue(106, "Document on-call escalation for platform tools", "In Progress", "Low", "dave", 9, "Task"),
    _demo_issue(107, "Terraform module: standard RDS with backups", "Resolved", "Medium", "bob", 3, "Story"),
    _demo_issue(108, "Fix cert-manager renewal alerts firing twice", "Resolved", "Medium", "carol", None, "Bug"),
    _demo_issue(109, "Migrate legacy cron jobs to Argo Workflows", "Closed", "High", "dave", None, "Story"),
    _demo_issue(110, "Enable image signing in the release pipeline", "Closed", "Highest", "alice", None, "Story"),
    _demo_issue(111, "Spike: cost report per team namespace", "Open", "Low", None, 12, "Spike"),
    _demo_issue(112, "Harden Jenkins agents (drop root, pin images)", "Open", "Medium", "dave", 5, "Task"),
]


def _demo_find(key: str) -> dict:
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
        "updated": f.get("updated", ""),
        "description": f.get("description") or "",
        "url": f"{settings.jira_base_url}/browse/{raw['key']}",
        "comments": [],
    }


def _live_search(jql: str, max_results: int = 100) -> list[dict]:
    r = _session().post(f"{settings.jira_base_url}/rest/api/2/search",
                        json={"jql": jql, "maxResults": max_results,
                              "fields": ["summary", "status", "priority", "assignee",
                                         "issuetype", "duedate", "updated", "description"]},
                        timeout=20)
    r.raise_for_status()
    return [_normalize(i) for i in r.json().get("issues", [])]


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


def board() -> dict:
    if is_live():
        issues = _live_search(_board_jql())
    else:
        issues = [dict(i) for i in _DEMO_ISSUES]
    columns = [{"name": s, "issues": [i for i in issues if _column_for(i) == s]}
               for s in settings.board_statuses]
    return {"project": settings.jira_project_key, "columns": columns,
            "source": "live" if is_live() else "demo"}


def my_open_issues(username: str) -> list[dict]:
    if is_live():
        return _live_search(
            f'project = "{settings.jira_project_key}" AND assignee = "{username}" '
            f'AND {_not_done_jql()} ORDER BY priority DESC, duedate ASC')
    return [i for i in _DEMO_ISSUES if i["assignee"] == username and _is_open(i)]


def unassigned_issues() -> list[dict]:
    if is_live():
        return _live_search(
            f'project = "{settings.jira_project_key}" AND assignee IS EMPTY '
            f'AND {_not_done_jql()} ORDER BY priority DESC')
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


def assign(key: str, username: str) -> dict:
    if is_live():
        _session().put(f"{settings.jira_base_url}/rest/api/2/issue/{key}/assignee",
                       json={"name": username}, timeout=20).raise_for_status()
        return _live_search(f'key = "{key}"')[0]
    issue = _demo_find(key)
    issue["assignee"] = username
    issue["updated"] = _now().isoformat()
    return dict(issue)
