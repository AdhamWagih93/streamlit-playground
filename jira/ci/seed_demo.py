#!/usr/bin/env python3
"""Seed an isolated Trackly instance with demo data for screenshots.

Stdlib-only (urllib) so it runs with a plain python3 — no dependencies. Targets
a throwaway CI stack (default http://localhost:8099); never point it at a real
instance. Populates projects, issues across every type/status, sprints (for
velocity), comments/worklogs, people & roles, components/versions, plus admin
config (mail, a Jira connection, an identity provider, a permission scheme,
groups) so every page in the UI has something to show.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

PROJECT_KEY = "DEMO"


class API:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token: str | None = None

    def _req(self, method: str, path: str, *, json_body=None, form=None, headers=None):
        url = f"{self.base}/api{path}"
        data = None
        hdrs = {"User-Agent": "TracklySeed/1.0"}
        if headers:
            hdrs.update(headers)
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body)
            except ValueError:
                return e.code, body.decode("utf-8", "replace")

    def get(self, p):
        return self._req("GET", p)

    def post(self, p, body=None):
        return self._req("POST", p, json_body=body)

    def patch(self, p, body=None):
        return self._req("PATCH", p, json_body=body)

    def put(self, p, body=None):
        return self._req("PUT", p, json_body=body)

    def login(self, email, password):
        st, data = self._req("POST", "/auth/login", form={"username": email, "password": password})
        if st != 200:
            raise SystemExit(f"login failed ({st}): {data}")
        self.token = data["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8099")
    ap.add_argument("--email", default="admin@trackly.local")
    ap.add_argument("--password", default="admin")
    args = ap.parse_args()

    api = API(args.url)
    api.login(args.email, args.password)
    print(f"seeding {args.url} …")

    # --- meta lookups ---
    _, types = api.get("/meta/issue-types")
    _, statuses = api.get("/meta/statuses")
    _, priorities = api.get("/meta/priorities")
    T = {t["name"]: t["id"] for t in types}
    S = {s["name"]: s for s in statuses}
    P = {p["name"]: p["id"] for p in priorities}

    # --- extra users ---
    users = {}
    for uname, email, display in [
        ("alice", "alice@example.com", "Alice Martin"),
        ("bob", "bob@example.com", "Bob Chen"),
        ("carol", "carol@example.com", "Carol Diaz"),
    ]:
        st, data = api.post("/auth/register",
                            {"username": uname, "email": email, "display_name": display, "password": "password123"})
        if st == 201:
            users[uname] = data["id"]
    # resolve ids for any that already existed
    _, all_users = api.get("/users")
    idby_email = {u["email"]: u["id"] for u in all_users}
    for uname, email in [("alice", "alice@example.com"), ("bob", "bob@example.com"), ("carol", "carol@example.com")]:
        users.setdefault(uname, idby_email.get(email))

    # --- a group ---
    st, grp = api.post("/groups", {"name": "engineering", "description": "Engineering team"})
    gid = grp["id"] if st in (200, 201) else None
    if gid and users.get("alice"):
        api.post(f"/groups/{gid}/members", {"user_id": users["alice"]})
        if users.get("bob"):
            api.post(f"/groups/{gid}/members", {"user_id": users["bob"]})

    # --- project ---
    st, proj = api.post("/projects", {"key": PROJECT_KEY, "name": "Demo Project",
                                      "description": "A seeded project showing off Trackly."})
    if st not in (200, 201):
        _, proj = api.get(f"/projects/{PROJECT_KEY}")
    pid = proj["id"]

    # --- people & roles ---
    _, roles = api.get("/roles")
    role = {r["name"]: r["id"] for r in roles}
    if users.get("alice"):
        api.post(f"/roles/projects/{pid}/actors", {"role_id": role["Developers"], "user_id": users["alice"]})
    if users.get("bob"):
        api.post(f"/roles/projects/{pid}/actors", {"role_id": role["Developers"], "user_id": users["bob"]})
    if users.get("carol"):
        api.post(f"/roles/projects/{pid}/actors", {"role_id": role["Viewers"], "user_id": users["carol"]})
    if gid:
        api.post(f"/roles/projects/{pid}/actors", {"role_id": role["Viewers"], "group_id": gid})

    # --- components & versions ---
    for name, desc in [("API", "Backend services"), ("Web", "Frontend SPA"), ("Infra", "CI/CD & deploy")]:
        api.post(f"/projects/{pid}/components", {"name": name, "description": desc})
    for name, released in [("v1.0", True), ("v1.1", False), ("v2.0", False)]:
        api.post(f"/projects/{pid}/versions", {"name": name, "released": released})

    # --- issues ---
    assignees = [users.get("alice"), users.get("bob"), users.get("carol"), None]
    specs = [
        ("Epic", "Onboarding revamp", "Done", "High", 0, ["onboarding"]),
        ("Story", "As a user I can reset my password", "Done", "Medium", 5, ["auth"]),
        ("Story", "Project insights dashboard", "In Progress", "High", 8, ["analytics", "ui"]),
        ("Story", "Bulk-edit issues", "To Do", "Low", 5, ["ui"]),
        ("Task", "Wire up email notifications", "In Review", "Medium", 3, ["backend"]),
        ("Task", "Add LDAP login", "To Do", "High", 8, ["auth", "backend"]),
        ("Bug", "Board drag drops to wrong column", "In Progress", "Highest", 2, ["bug", "ui"]),
        ("Bug", "Timezone off by one on due dates", "To Do", "Medium", 1, ["bug"]),
        ("Task", "Document the REST API", "Done", "Low", 2, ["docs"]),
        ("Story", "Sprint velocity chart", "Done", "Medium", 5, ["analytics"]),
    ]
    issue_keys = []
    for i, (typ, summary, status, prio, pts, labels) in enumerate(specs):
        body = {
            "project_id": pid, "type_id": T.get(typ, T["Task"]),
            "summary": summary, "description": f"Seeded demo issue: {summary}.",
            "priority_id": P.get(prio), "assignee_id": assignees[i % len(assignees)],
            "story_points": pts or None, "label_names": labels,
        }
        st, iss = api.post("/issues", body)
        if st != 201:
            continue
        key = iss["key"]
        issue_keys.append(key)
        if status != "To Do":
            api.patch(f"/issues/{key}", {"status_id": S[status]["id"]})
        if i < 4:
            api.post(f"/issues/{key}/comments", {"body": f"Looking into this — {summary.lower()}."})
        if i == 2:
            api.post(f"/issues/{key}/worklogs", {"time_spent": "3h 30m", "comment": "Initial work"})

    if len(issue_keys) >= 2:
        api.post(f"/issues/{issue_keys[0]}/links", {"link_type": "blocks", "target_key": issue_keys[1]})

    # --- sprints (for velocity) ---
    _, boards = api.get(f"/agile/boards?project_id={pid}")
    if boards:
        bid = boards[0]["id"]
        # Closed sprint with completed work.
        st, sp1 = api.post(f"/agile/boards/{bid}/sprints", {"name": "Sprint 1", "goal": "Foundations"})
        if st in (200, 201):
            api.post(f"/agile/sprints/{sp1['id']}/start", {})
            for key in issue_keys[:4]:
                api.put(f"/issues/{key}/rank", {"sprint_id": sp1["id"]})
            api.post(f"/agile/sprints/{sp1['id']}/complete", {})
        # Active sprint in progress.
        st, sp2 = api.post(f"/agile/boards/{bid}/sprints", {"name": "Sprint 2", "goal": "Insights & polish"})
        if st in (200, 201):
            api.post(f"/agile/sprints/{sp2['id']}/start", {})
            for key in issue_keys[4:7]:
                api.put(f"/issues/{key}/rank", {"sprint_id": sp2["id"]})

    # --- a saved filter for the search page ---
    api.post("/search/filters", {"name": "My open bugs", "query": "type = Bug AND statusCategory != done", "is_shared": True})

    # --- admin config so admin pages have content ---
    api.put("/admin/mail", {"enabled": False, "host": "smtp.example.com", "port": 587,
                            "from_address": "trackly@example.com", "from_name": "Trackly Demo", "use_tls": True})
    api.post("/admin/jira-connections", {"name": "Demo Jira", "base_url": "https://demo.atlassian.net",
                                         "auth_mode": "cloud", "email": "ci@example.com",
                                         "api_token": "demo-token", "is_default": True})
    api.post("/admin/identity-providers", {"name": "Corp LDAP", "provider_type": "ldap", "enabled": False,
                                           "ldap_host": "ldap.example.com", "ldap_port": 636,
                                           "ldap_bind_password": "demo-bind-pw"})
    api.post("/permission-schemes", {"name": "Demo Scheme", "description": "An extra scheme for the demo."})
    if gid:
        api.post("/admin/global-permissions", {"permission": "USER_PICKER", "holder_type": "group", "holder_value": "engineering"})

    print(f"seeded: project {PROJECT_KEY} with {len(issue_keys)} issues, sprints, people, components/versions, admin config.")
    print(f"first issue: {issue_keys[0] if issue_keys else '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
