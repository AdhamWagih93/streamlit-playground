"""Gitea REST client + name mapping for the ADO -> Gitea migration.

One self-hosted Gitea instance receives one ADO collection. The ADO hierarchy
(collection > project > repo) maps onto Gitea (instance > org > repo):
  ADO collection -> a configured Gitea instance (url + token)
  ADO project    -> a Gitea ORG
  ADO repo       -> a repo in that org (source pulled via Gitea's migrate API)
  ADO team/group -> a Gitea org TEAM (permission from its ADO privilege tier)
  repo-level ACL -> a repo COLLABORATOR
  PR reviewers   -> a branch protection requiring approvals from the PR team

Read methods power the dry-run/current-state view; write methods run only on an
explicit, approver-gated execute. Demo mode serves a realistic half-migrated
instance so the whole flow is viewable offline."""

import re

import requests

from ..config import settings

HTTP_TIMEOUT = (5, 25)


# ------------------------------------------------------------- name mapping
def _slug(s: str) -> str:
    """A Gitea-safe name: alnum, dash, underscore, dot; collapse the rest."""
    s = re.split(r"[\\/]", (s or "").strip())[-1]          # last path segment
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-._")
    return s or "unnamed"


def org_name(project: str, collection: str, strategy: str = "project") -> str:
    if strategy == "collection_project":
        return _slug(f"{collection}-{project}")
    return _slug(project)


def repo_name(name: str) -> str:
    return _slug(name)


def team_name(name: str) -> str:
    return _slug(name)[:255]


def gitea_user(identity: str) -> str:
    """Best-effort Gitea username from an ADO identity. 'DOMAIN\\jdoe' or
    'jdoe@corp' -> 'jdoe'; a display name -> a lowercase slug (flagged for
    verification in the plan, since display-name -> username isn't guaranteed)."""
    ident = re.split(r"[\\/]", (identity or "").strip())[-1]
    ident = ident.split("@")[0]
    if " " in ident:  # a display name like "Alice Nasr"
        return re.sub(r"[^a-z0-9]+", "", ident.lower())
    return re.sub(r"[^A-Za-z0-9._-]+", "", ident).lower()


def is_display_name(identity: str) -> bool:
    seg = re.split(r"[\\/]", (identity or "").strip())[-1]
    return " " in seg  # a space => a human display name, mapping is uncertain


# ADO privilege tier -> Gitea permission
_TIER_PERM = {"admin": "admin", "write": "write", "read": "read", "other": "read"}


def gitea_permission(tier: str) -> str:
    return _TIER_PERM.get(tier, "read")


# ------------------------------------------------------------- client
class GiteaError(Exception):
    pass


class Gitea:
    """Thin Gitea API client for one instance (url + token)."""

    def __init__(self, url: str, token: str):
        self.base = (url or "").rstrip("/") + "/api/v1"
        self.token = token
        self.headers = {"Authorization": f"token {token}",
                        "Accept": "application/json"}

    def _req(self, method: str, path: str, **kw):
        r = requests.request(method, f"{self.base}{path}", headers=self.headers,
                             timeout=HTTP_TIMEOUT,
                             verify=settings.gitea_verify_ssl, **kw)
        return r

    def _get(self, path: str, params: dict | None = None):
        r = self._req("GET", path, params=params)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return None

    def _paged(self, path: str, params: dict | None = None, cap: int = 2000) -> list:
        out, page = [], 1
        params = dict(params or {})
        while len(out) < cap:
            params.update({"limit": 50, "page": page})
            batch = self._get(path, params)
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < 50:
                break
            page += 1
        return out

    # ---- read ----
    def version(self) -> str:
        return (self._get("/version") or {}).get("version", "")

    def whoami(self) -> dict:
        return self._get("/user") or {}

    def orgs(self) -> list[str]:
        # orgs the token's user administers/belongs to
        return [o.get("username", "") for o in self._paged("/user/orgs") if o.get("username")]

    def org_exists(self, org: str) -> bool:
        r = self._req("GET", f"/orgs/{org}")
        return r.status_code == 200

    def org_repos(self, org: str) -> list[str]:
        return [r.get("name", "") for r in self._paged(f"/orgs/{org}/repos") if r.get("name")]

    def org_teams(self, org: str) -> list[dict]:
        teams = self._paged(f"/orgs/{org}/teams")
        out = []
        for t in teams:
            out.append({"id": t.get("id"), "name": t.get("name", ""),
                        "permission": t.get("permission", "")})
        return out

    def team_members(self, team_id) -> list[str]:
        return [m.get("login", "") for m in self._paged(f"/teams/{team_id}/members")
                if m.get("login")]

    def repo_collaborators(self, org: str, repo: str) -> list[str]:
        return [c.get("login", "") for c in self._paged(f"/repos/{org}/{repo}/collaborators")
                if c.get("login")]

    def branch_protections(self, org: str, repo: str) -> list[dict]:
        got = self._get(f"/repos/{org}/{repo}/branch_protections")
        return got if isinstance(got, list) else []

    # ---- write (execute only) ----
    def create_org(self, org: str, description: str = "") -> None:
        r = self._req("POST", "/orgs",
                      json={"username": org, "full_name": org, "description": description[:255]})
        if r.status_code not in (201, 200, 422):  # 422 = already exists
            raise GiteaError(f"create org {org}: HTTP {r.status_code} {r.text[:160]}")

    def migrate_repo(self, org: str, repo: str, clone_addr: str,
                     auth_user: str, auth_pass: str, description: str = "") -> None:
        r = self._req("POST", "/repos/migrate", json={
            "clone_addr": clone_addr, "repo_owner": org, "repo_name": repo,
            "service": "git", "auth_username": auth_user, "auth_password": auth_pass,
            "mirror": False, "private": True, "description": description[:255]})
        if r.status_code not in (201, 200, 409):  # 409 = repo already exists
            raise GiteaError(f"migrate {org}/{repo}: HTTP {r.status_code} {r.text[:200]}")

    def create_team(self, org: str, name: str, permission: str) -> int | None:
        # Gitea team perms: read/write/admin; grant all repo units
        units = ["repo.code", "repo.issues", "repo.pulls", "repo.releases", "repo.wiki"]
        r = self._req("POST", f"/orgs/{org}/teams", json={
            "name": name, "permission": permission, "units": units,
            "can_create_org_repo": False, "includes_all_repositories": False})
        if r.status_code in (201, 200):
            try:
                return r.json().get("id")
            except ValueError:
                return None
        if r.status_code == 422:  # exists — look it up
            for t in self.org_teams(org):
                if t["name"].lower() == name.lower():
                    return t["id"]
        raise GiteaError(f"create team {org}/{name}: HTTP {r.status_code} {r.text[:160]}")

    def add_team_member(self, team_id, username: str) -> None:
        r = self._req("PUT", f"/teams/{team_id}/members/{username}")
        if r.status_code not in (204, 200, 404):  # 404 = user not in Gitea yet
            raise GiteaError(f"add member {username}: HTTP {r.status_code} {r.text[:120]}")

    def add_team_repo(self, team_id, org: str, repo: str) -> None:
        r = self._req("PUT", f"/teams/{team_id}/repos/{org}/{repo}")
        if r.status_code not in (204, 200):
            raise GiteaError(f"team repo {org}/{repo}: HTTP {r.status_code} {r.text[:120]}")

    def add_collaborator(self, org: str, repo: str, username: str, permission: str) -> None:
        r = self._req("PUT", f"/repos/{org}/{repo}/collaborators/{username}",
                      json={"permission": permission})
        if r.status_code not in (204, 200, 404):
            raise GiteaError(f"collab {username}@{repo}: HTTP {r.status_code} {r.text[:120]}")

    def create_branch_protection(self, org: str, repo: str, branch: str,
                                 approvals: int, team: str | None) -> None:
        body = {"rule_name": branch, "branch_name": branch,
                "enable_approvals_whitelist": bool(team),
                "required_approvals": approvals,
                "approvals_whitelist_teams": [team] if team else []}
        r = self._req("POST", f"/repos/{org}/{repo}/branch_protections", json=body)
        if r.status_code not in (201, 200, 409, 422):
            raise GiteaError(f"protect {org}/{repo}: HTTP {r.status_code} {r.text[:120]}")


# ------------------------------------------------------------- demo state
def _demo_state(collection: str) -> dict:
    """A half-migrated Gitea instance so the diff shows create vs exists."""
    if collection == "DefaultCollection":
        return {"reachable": True, "version": "1.22.3", "error": None,
                "orgs": {
                    "Platform": {"repos": ["Engine"],   # UI not migrated yet
                                 "teams": {"platform-devs": {"permission": "write",
                                                             "members": ["alice", "bob"]}}},
                }}
    return {"reachable": True, "version": "1.22.3", "error": None, "orgs": {}}


# ------------------------------------------------------------- state snapshot
def instance_state(url: str, token: str, collection: str = "") -> dict:
    """Current Gitea state for the diff: reachability + orgs -> repos + teams
    (with members). Best-effort — a partial read still yields a useful diff."""
    if settings.demo_mode:
        return _demo_state(collection)
    g = Gitea(url, token)
    state = {"reachable": False, "version": "", "error": None, "orgs": {}}
    try:
        state["version"] = g.version()
        g.whoami()  # validates the token
        state["reachable"] = True
    except requests.RequestException as exc:
        state["error"] = _short(exc)
        return state
    try:
        for org in g.orgs():
            teams = {}
            for t in g.org_teams(org):
                try:
                    members = g.team_members(t["id"])
                except requests.RequestException:
                    members = []
                teams[t["name"]] = {"permission": t["permission"], "members": members}
            state["orgs"][org] = {"repos": g.org_repos(org), "teams": teams}
    except requests.RequestException as exc:
        state["error"] = _short(exc)
    return state


def _short(exc: Exception) -> str:
    s = str(exc)
    for m, h in (("NameResolution", "host not found"), ("ConnectTimeout", "connect timeout"),
                 ("SSLError", "TLS error (self-signed? set GITEA_VERIFY_SSL=false)"),
                 ("ConnectionError", "connection refused")):
        if m in s:
            return h
    return s[:140]
