"""Access Management: who can do what, across the three source systems.

  ADO     — per-project teams/members + per-repository ACLs (Git security
            namespace, allow/deny bitmasks decoded to permission names)
  Jira    — permission schemes ("templates"): every permission grant and
            which projects each scheme is assigned to
  Jenkins — Matrix-based RBAC: <permission> entries from job/folder
            config.xml (project-based matrix authorization)

Built to NOT overload the sources: everything is cached server-side
(15 min TTL, explicit refresh), ADO project detail loads only on expand,
Jenkins config.xml fetches go through one shared cache, and ADO identity
descriptors are resolved in batches."""

import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

from ..config import settings

TTL = 900
HTTP_TIMEOUT = 20
HTTP_CONNECT = 5
POOL = 12          # concurrent fetches against a source (bounded, not a flood)
_CACHE: dict = {}

# ADO Git repositories security namespace + its permission bits
ADO_GIT_NAMESPACE = "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87"
ADO_GIT_BITS = [
    (1, "Administer"), (2, "Read"), (4, "Contribute"), (8, "Force push"),
    (16, "Create branch"), (32, "Create tag"), (64, "Manage notes"),
    (128, "Bypass policies (PR)"), (256, "Create repository"),
    (512, "Delete repository"), (1024, "Rename repository"),
    (2048, "Edit policies"), (4096, "Remove others' locks"),
    (8192, "Manage permissions"), (16384, "Contribute to PRs"),
    (32768, "Bypass policies (push)"),
]


def _cached(key: str, force: bool, builder):
    hit = _CACHE.get(key)
    if hit and not force and time.time() - hit["at"] < TTL:
        return {**hit["payload"], "cached": True, "cached_at": hit["at"]}
    payload = builder()
    _CACHE[key] = {"at": time.time(), "payload": payload}
    return {**payload, "cached": False, "cached_at": time.time()}


def _decode_bits(mask: int) -> list[str]:
    return [name for bit, name in ADO_GIT_BITS if mask & bit]


# ================================================================= ADO
def _ado_get(path: str, params: dict | None = None):
    r = requests.get(f"{settings.ado_url.rstrip('/')}{path}",
                     params={"api-version": "6.0", **(params or {})},
                     auth=(settings.ado_user, settings.ado_rest_password),
                     timeout=(HTTP_CONNECT, HTTP_TIMEOUT))
    r.raise_for_status()
    return r.json()


def ado_projects(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return {"source": "demo", "projects": [
                {"id": "p1", "name": "Platform", "description": "Product delivery"},
                {"id": "p2", "name": "Control", "description": "Team config repos"},
            ]}
        if not settings.ado_url:
            return {"source": "not configured", "projects": []}
        data = _ado_get("/_apis/projects", {"$top": 200})
        return {"source": "live", "projects": sorted(
            ({"id": p["id"], "name": p["name"],
              "description": (p.get("description") or "")[:160]}
             for p in data.get("value", [])), key=lambda p: p["name"].lower())}
    return _cached("ado:projects", force, build)


def _resolve_identities(descriptors: list[str]) -> dict[str, str]:
    """descriptor -> display name, batched to spare the identity service."""
    out: dict[str, str] = {}
    for i in range(0, len(descriptors), 50):
        batch = descriptors[i:i + 50]
        try:
            data = _ado_get("/_apis/identities",
                            {"descriptors": ",".join(batch)})
            for ident in data.get("value", []):
                out[ident.get("descriptor", "")] = (
                    ident.get("providerDisplayName")
                    or ident.get("customDisplayName") or "")
        except requests.RequestException:
            continue
    return out


def _demo_project_access(project_id: str) -> dict:
    teams = [{"name": "Platform Team",
              "members": ["Alice Nasr", "Bob Farid", "Carol Adel", "Dave Samir"]},
             {"name": "Platform Admins", "members": ["Alice Nasr"]}]
    repos = [
        {"name": "Engine", "acls": [
            {"identity": "[Platform]\\Platform Team",
             "allow": ["Read", "Contribute", "Create branch", "Create tag"], "deny": []},
            {"identity": "[Platform]\\Platform Admins",
             "allow": ["Administer", "Read", "Contribute", "Force push",
                       "Edit policies", "Manage permissions"], "deny": []},
            {"identity": "[Platform]\\Contractors",
             "allow": ["Read"], "deny": ["Contribute", "Force push"]},
        ]},
        {"name": "UI", "acls": [
            {"identity": "[Platform]\\Platform Team",
             "allow": ["Read", "Contribute"], "deny": []},
        ]},
    ]
    if project_id == "p2":
        teams = [{"name": "Control Owners", "members": ["Alice Nasr", "Bob Farid"]}]
        repos = [{"name": "team-configs", "acls": [
            {"identity": "[Control]\\Control Owners",
             "allow": ["Administer", "Read", "Contribute"], "deny": []},
            {"identity": "[Control]\\Everyone", "allow": ["Read"], "deny": []},
        ]}]
    return {"source": "demo", "teams": teams, "repos": repos}


def ado_project_access(project_id: str, force: bool = False) -> dict:
    """Teams+members and per-repo ACLs for ONE project — fetched on expand."""
    def build():
        if settings.demo_mode:
            return _demo_project_access(project_id)
        if not settings.ado_url:
            return {"source": "not configured", "teams": [], "repos": []}
        teams = []
        for t in _ado_get(f"/_apis/projects/{project_id}/teams",
                          {"$top": 20}).get("value", []):
            members = []
            try:
                data = _ado_get(f"/_apis/projects/{project_id}/teams/{t['id']}/members",
                                {"$top": 50})
                members = [(m.get("identity") or m).get("displayName", "")
                           for m in data.get("value", [])]
            except requests.RequestException:
                pass
            teams.append({"name": t.get("name", ""), "members": sorted(filter(None, members))})

        repos = []
        repo_list = _ado_get(f"/{project_id}/_apis/git/repositories").get("value", [])[:40]
        descriptors: set[str] = set()
        raw_acls: dict[str, dict] = {}

        def fetch_acl(rp):
            try:
                acl = _ado_get(f"/_apis/accesscontrollists/{ADO_GIT_NAMESPACE}",
                               {"token": f"repoV2/{project_id}/{rp['id']}"})
                aces = {}
                for entry in acl.get("value", []):
                    aces.update(entry.get("acesDictionary", {}))
                return rp["name"], aces
            except requests.RequestException:
                return rp["name"], {}

        with ThreadPoolExecutor(max_workers=POOL) as pool:  # parallel ACL reads
            for name, aces in pool.map(fetch_acl, repo_list):
                raw_acls[name] = aces
                descriptors.update(aces.keys())
        names = _resolve_identities(sorted(descriptors))
        for rp in repo_list[:40]:
            acls = []
            for desc, ace in (raw_acls.get(rp["name"]) or {}).items():
                allow, deny = _decode_bits(ace.get("allow", 0)), _decode_bits(ace.get("deny", 0))
                if allow or deny:
                    acls.append({"identity": names.get(desc) or desc[:60],
                                 "allow": allow, "deny": deny})
            acls.sort(key=lambda a: a["identity"].lower())
            repos.append({"name": rp["name"], "acls": acls})
        repos.sort(key=lambda r: r["name"].lower())
        return {"source": "live", "teams": teams, "repos": repos,
                "repo_cap_note": len(repo_list) > 40}
    return _cached(f"ado:project:{project_id}", force, build)


# ================================================================= Jira
def jira_permission_schemes(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return {"source": "demo", "schemes": [
                {"id": 1, "name": "Default Software Scheme",
                 "description": "Standard delivery-team permissions",
                 "projects": ["DEVOPS", "PLAT"],
                 "holders": [
                     {"holder": "group devops-team", "type": "group",
                      "permissions": ["Browse Projects", "Create Issues",
                                      "Edit Issues", "Add Comments",
                                      "Transition Issues", "Resolve Issues"]},
                     {"holder": "role Administrators", "type": "projectRole",
                      "permissions": ["Administer Projects", "Delete Issues",
                                      "Manage Sprints", "Edit All Comments"]},
                     {"holder": "role Developers", "type": "projectRole",
                      "permissions": ["Assignable User", "Close Issues",
                                      "Schedule Issues", "Link Issues"]},
                 ]},
                {"id": 2, "name": "Restricted Scheme",
                 "description": "Read-mostly scheme for sensitive projects",
                 "projects": ["SEC"],
                 "holders": [
                     {"holder": "group security-team", "type": "group",
                      "permissions": ["Browse Projects", "Create Issues",
                                      "Edit Issues", "Administer Projects"]},
                     {"holder": "group devops-team", "type": "group",
                      "permissions": ["Browse Projects"]},
                 ]},
            ]}
        if not (settings.jira_base_url and settings.jira_user):
            return {"source": "not configured", "schemes": []}
        auth = (settings.jira_user, settings.jira_password)
        base = settings.jira_base_url

        def jget(path, params=None):
            r = requests.get(f"{base}{path}", params=params, auth=auth,
                             timeout=(HTTP_CONNECT, HTTP_TIMEOUT))
            r.raise_for_status()
            return r.json()

        # global project-role id -> name (holder parameters reference ids)
        role_names = {}
        try:
            for role in jget("/rest/api/2/role"):
                role_names[str(role.get("id"))] = role.get("name", "")
        except requests.RequestException:
            pass

        data = jget("/rest/api/2/permissionscheme", {"expand": "permissions"})
        schemes = []
        for s in data.get("permissionSchemes", []):
            by_holder: dict[tuple, list[str]] = {}
            for perm in s.get("permissions", []):
                holder = perm.get("holder") or {}
                htype = holder.get("type", "?")
                param = str(holder.get("parameter") or "")
                if htype == "projectRole":
                    label = f"role {role_names.get(param, param)}"
                elif htype == "group":
                    label = f"group {param}"
                elif htype == "user":
                    label = f"user {param}"
                else:
                    label = htype + (f" {param}" if param else "")
                pname = (perm.get("permission") or "").replace("_", " ").title()
                by_holder.setdefault((label, htype), []).append(pname)
            schemes.append({
                "id": s.get("id"), "name": s.get("name", ""),
                "description": (s.get("description") or "")[:200],
                "projects": [],
                "holders": [{"holder": k[0], "type": k[1],
                             "permissions": sorted(set(v))}
                            for k, v in sorted(by_holder.items())]})

        # scheme -> projects (bounded + parallel; the expensive part)
        try:
            projects = jget("/rest/api/2/project")[:80]
            by_id = {s["id"]: s for s in schemes}

            def proj_scheme(p):
                try:
                    return p["key"], jget(
                        f"/rest/api/2/project/{p['key']}/permissionscheme").get("id")
                except requests.RequestException:
                    return p["key"], None

            with ThreadPoolExecutor(max_workers=POOL) as pool:
                for key, sid in pool.map(proj_scheme, projects):
                    if sid in by_id:
                        by_id[sid]["projects"].append(key)
        except requests.RequestException:
            pass
        return {"source": "live", "schemes": schemes}
    return _cached("jira:schemes", force, build)


# ================================================================= Jenkins
_PERM_RE = re.compile(r"<permission>([^<]+)</permission>")


def _parse_matrix_entries(xml_text: str) -> list[dict]:
    """Matrix-auth <permission> entries. Forms seen in the wild:
    'hudson.model.Item.Read:sid', 'USER:Item.Read:sid', 'GROUP:...:sid'."""
    grants: dict[tuple, set] = {}
    for raw in _PERM_RE.findall(xml_text):
        parts = raw.split(":")
        sid_type = "unknown"
        if parts[0] in ("USER", "GROUP") and len(parts) >= 3:
            sid_type = parts[0].lower()
            perm, sid = parts[1], ":".join(parts[2:])
        elif len(parts) >= 2:
            perm, sid = parts[0], ":".join(parts[1:])
        else:
            continue
        # 'hudson.model.Item.Read' -> 'Item/Read'
        bits = perm.split(".")
        short = "/".join(bits[-2:]) if len(bits) >= 2 else perm
        grants.setdefault((sid, sid_type), set()).add(short)
    return [{"sid": sid, "type": sid_type, "permissions": sorted(perms)}
            for (sid, sid_type), perms in sorted(grants.items())]


def jenkins_matrix(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return {"source": "demo", "items": [
                {"path": "(folder) payments-service", "entries": [
                    {"sid": "devops-team", "type": "group",
                     "permissions": ["Item/Build", "Item/Cancel", "Item/Read", "Item/Workspace"]},
                    {"sid": "alice", "type": "user",
                     "permissions": ["Item/Build", "Item/Configure", "Item/Delete", "Item/Read"]},
                ]},
                {"path": "platform-terraform/apply", "entries": [
                    {"sid": "platform-admins", "type": "group",
                     "permissions": ["Item/Build", "Item/Configure", "Item/Read"]},
                    {"sid": "authenticated", "type": "group",
                     "permissions": ["Item/Read"]},
                ]},
            ], "scanned": 2, "note": ""}
        from . import jenkins as jk
        if not jk.is_live():
            return {"source": "not configured", "items": [], "scanned": 0, "note": ""}
        auth = (settings.jenkins_user, settings.jenkins_token) if settings.jenkins_user else None
        names = jk.all_job_names()
        # jobs + every ancestor folder, deduped, bounded
        paths: list[str] = []
        seen = set()
        for name in names:
            segs = name.split("/")
            for i in range(1, len(segs) + 1):
                p = "/".join(segs[:i])
                if p not in seen:
                    seen.add(p)
                    paths.append(p)
        capped = paths[:300]
        name_set = set(names)

        def fetch_one(p: str):
            url = settings.jenkins_url + "".join(
                f"/job/{requests.utils.quote(seg, safe='')}" for seg in p.split("/"))
            try:
                r = requests.get(f"{url}/config.xml", auth=auth,
                                 timeout=(HTTP_CONNECT, 15))
                if not r.ok:
                    return None
                entries = _parse_matrix_entries(r.text)
            except requests.RequestException:
                return None
            if not entries:
                return None
            is_folder = p not in name_set
            return {"path": ("(folder) " if is_folder else "") + p,
                    "entries": entries}

        # parallel, bounded — 300 sequential config.xml fetches was minutes
        with ThreadPoolExecutor(max_workers=POOL) as pool:
            items = [x for x in pool.map(fetch_one, capped) if x]
        items.sort(key=lambda x: x["path"].lower())
        note = (f"scanned the first {len(capped)} of {len(paths)} items"
                if len(paths) > len(capped) else "")
        return {"source": "live", "items": items, "scanned": len(capped),
                "note": note}
    return _cached("jenkins:matrix", force, build)
