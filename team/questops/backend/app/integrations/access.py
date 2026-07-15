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


def _short_http(exc: Exception) -> str:
    """Compact 'HTTP 404 at /path' from a requests exception, for the UI."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        url = str(getattr(resp, "url", "")).split("?")[0]
        tail = "/" + url.split("/_apis/", 1)[1] if "/_apis/" in url else url[-60:]
        return f"HTTP {resp.status_code} at …{tail}"
    return str(exc)[:100]


# ================================================================= ADO
from . import ado as _ado


def _is_service_account(identity: str, descriptor: str = "") -> bool:
    """Access granted to QO_ADO_USER is ignored — it's the tool's own account,
    not a real grant worth reviewing."""
    su = (settings.ado_user or "").strip().lower()
    if not su:
        return False
    hay = f"{identity} {descriptor}".lower()
    # match the bare account and common domain / UPN forms
    return (su == identity.strip().lower()
            or su in re.split(r"[\\/@ ]", hay))


def ado_projects(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return {"source": "demo", "projects": [
                {"id": "p1", "coll": "DefaultCollection", "name": "Platform",
                 "description": "Product delivery",
                 "url": "https://ado.demo/DefaultCollection/Platform"},
                {"id": "p2", "coll": "DefaultCollection", "name": "Control",
                 "description": "Team config repos",
                 "url": "https://ado.demo/DefaultCollection/Control"},
                {"id": "p3", "coll": "Research", "name": "Sandbox",
                 "description": "Experiments",
                 "url": "https://ado.demo/Research/Sandbox"},
            ], "collections": ["DefaultCollection", "Research"]}
        if not settings.ado_url:
            return {"source": "not configured", "projects": [], "collections": []}
        colls = _ado.collections(force)

        def coll_projects(coll):
            try:
                data = _ado.coll_get(coll, "/_apis/projects", {"$top": 500})
                return [{"id": p["id"], "coll": coll, "name": p["name"],
                         "description": (p.get("description") or "")[:160],
                         "url": _ado.project_url(coll, p["name"])}
                        for p in data.get("value", [])]
            except requests.RequestException:
                return []

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            projects = [p for group in pool.map(coll_projects, colls) for p in group]
        projects.sort(key=lambda p: (p["coll"].lower(), p["name"].lower()))
        return {"source": "live", "projects": projects, "collections": colls}
    return _cached("ado:projects", force, build)


def _resolve_identities(descriptors: list[str]) -> dict[str, str]:
    """descriptor -> display name, batched to spare the identity service."""
    out: dict[str, str] = {}
    for i in range(0, len(descriptors), 50):
        batch = descriptors[i:i + 50]
        try:
            data = _ado.get("/_apis/identities", {"descriptors": ",".join(batch)})
            for ident in data.get("value", []):
                out[ident.get("descriptor", "")] = (
                    ident.get("providerDisplayName")
                    or ident.get("customDisplayName") or "")
        except requests.RequestException:
            continue
    return out


def _demo_project_access(project_id: str) -> dict:
    su = settings.ado_user or "svc-questops"
    teams = [{"name": "Platform Team",
              "members": ["Alice Nasr", "Bob Farid", "Carol Adel", "Dave Samir"]},
             {"name": "Platform Admins", "members": ["Alice Nasr"]}]
    # a grant to the service account — MUST be filtered out of the output
    raw_repos = [
        {"name": "Engine", "acls": [
            {"identity": "[Platform]\\Platform Team",
             "allow": ["Read", "Contribute", "Create branch", "Create tag"], "deny": []},
            {"identity": "[Platform]\\Platform Admins",
             "allow": ["Administer", "Read", "Contribute", "Force push",
                       "Edit policies", "Manage permissions"], "deny": []},
            {"identity": su, "allow": ["Read", "Contribute"], "deny": []},
            {"identity": "[Platform]\\Contractors",
             "allow": ["Read"], "deny": ["Contribute", "Force push"]},
        ]},
        {"name": "UI", "acls": [
            {"identity": "[Platform]\\Platform Team",
             "allow": ["Read", "Contribute"], "deny": []},
        ]},
    ]
    coll = "DefaultCollection"
    if project_id == "p2":
        teams = [{"name": "Control Owners", "members": ["Alice Nasr", "Bob Farid"]}]
        raw_repos = [{"name": "team-configs", "acls": [
            {"identity": "[Control]\\Control Owners",
             "allow": ["Administer", "Read", "Contribute"], "deny": []},
            {"identity": "[Control]\\Everyone", "allow": ["Read"], "deny": []},
        ]}]
    elif project_id == "p3":
        coll = "Research"
        teams = [{"name": "Sandbox Team", "members": ["Carol Adel"]}]
        raw_repos = [{"name": "prototypes", "acls": [
            {"identity": "[Research]\\Sandbox Team",
             "allow": ["Read", "Contribute"], "deny": []}]}]
    repos = []
    for r in raw_repos:
        # demo has no real ADO_USER; filter against the injected demo account
        acls = [a for a in r["acls"] if a["identity"].strip().lower() != su.strip().lower()]
        repos.append({"name": r["name"], "acls": acls,
                      "url": f"https://ado.demo/{coll}/{project_id}/_git/{r['name']}"})
    return {"source": "demo", "teams": teams, "repos": repos}


def ado_project_access(collection: str, project_id: str,
                       force: bool = False) -> dict:
    """Teams+members and per-repo ACLs for ONE project — fetched on expand.
    Access granted to QO_ADO_USER is filtered out."""
    def build():
        if settings.demo_mode:
            return _demo_project_access(project_id)
        if not settings.ado_url:
            return {"source": "not configured", "teams": [], "repos": []}
        errors = []  # per-call failures surface inline instead of blanking

        # teams (project-scoped) — non-fatal
        teams = []
        try:
            tdata = _ado.coll_get(collection, f"/_apis/projects/{project_id}/teams",
                                  {"$top": 100})
            for t in tdata.get("value", []):
                members = []
                try:
                    data = _ado.coll_get(
                        collection,
                        f"/_apis/projects/{project_id}/teams/{t['id']}/members",
                        {"$top": 200})
                    members = [(m.get("identity") or m).get("displayName", "")
                               for m in data.get("value", [])]
                except requests.RequestException:
                    pass
                teams.append({"name": t.get("name", ""),
                              "members": sorted(m for m in filter(None, members)
                                                if not _is_service_account(m))})
        except requests.RequestException as exc:
            errors.append(f"teams: {_short_http(exc)}")

        # repos — PROJECT-scoped (was collection-scoped, mixing other projects)
        try:
            repo_list = _ado.coll_get(
                collection, f"/{project_id}/_apis/git/repositories").get("value", [])[:60]
        except requests.RequestException as exc:
            errors.append(f"repositories: {_short_http(exc)}")
            repo_list = []
        descriptors: set[str] = set()
        raw_acls: dict[str, dict] = {}

        def fetch_acl(rp):
            try:
                acl = _ado.coll_get(
                    collection, f"/_apis/accesscontrollists/{ADO_GIT_NAMESPACE}",
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
        repos = []
        for rp in repo_list:
            acls = []
            for desc, ace in (raw_acls.get(rp["name"]) or {}).items():
                ident = names.get(desc) or desc[:60]
                if _is_service_account(ident, desc):  # ignore QO_ADO_USER
                    continue
                allow = _decode_bits(ace.get("allow", 0))
                deny = _decode_bits(ace.get("deny", 0))
                if allow or deny:
                    acls.append({"identity": ident, "allow": allow, "deny": deny})
            acls.sort(key=lambda a: a["identity"].lower())
            repos.append({"name": rp["name"], "acls": acls,
                          "url": _ado.repo_url(collection, project_id, rp["name"])})
        repos.sort(key=lambda r: r["name"].lower())
        return {"source": "live", "teams": teams, "repos": repos,
                "repo_cap_note": len(repo_list) >= 60, "errors": errors}
    return _cached(f"ado:project:{collection}:{project_id}", force, build)


# ================================================================= Jira
JIRA_PROJECT_CAP = 5000  # safety bound; noted if exceeded


def _jira_holder(holder: dict, role_names: dict) -> tuple[str, str, str]:
    """(label, type, parameter). Jira DC user holders carry the internal
    'JIRAUSER…' key — surfaced so it can be flagged."""
    htype = holder.get("type", "?")
    param = str(holder.get("parameter") or holder.get("value") or "")
    if htype == "projectRole":
        return f"role {role_names.get(param, param)}", htype, param
    if htype == "group":
        return f"group {param}", htype, param
    if htype in ("user", "applicationRole"):
        return f"user {param}" if htype == "user" else f"{htype} {param}", htype, param
    return htype + (f" {param}" if param else ""), htype, param


def jira_permission_schemes(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            base = "https://jira.demo"
            def slink(sid): return f"{base}/secure/admin/EditPermissionScheme!default.jspa?schemeId={sid}"
            def plink(k): return f"{base}/browse/{k}"
            schemes = [
                {"id": 1, "name": "Default Software Scheme",
                 "description": "Standard delivery-team permissions",
                 "url": slink(1),
                 "projects": [{"key": "DEVOPS", "url": plink("DEVOPS")},
                              {"key": "PLAT", "url": plink("PLAT")}],
                 "holders": [
                     {"holder": "group devops-team", "type": "group",
                      "permissions": ["Browse Projects", "Create Issues",
                                      "Edit Issues", "Transition Issues"]},
                     {"holder": "role Administrators", "type": "projectRole",
                      "permissions": ["Administer Projects", "Delete Issues"]},
                     {"holder": "user JIRAUSER10500", "type": "user", "flag": True,
                      "permissions": ["Administer Projects", "Delete Issues"]},
                 ]},
                {"id": 2, "name": "Restricted Scheme",
                 "description": "Read-mostly scheme for sensitive projects",
                 "url": slink(2),
                 "projects": [{"key": "SEC", "url": plink("SEC")}],
                 "holders": [
                     {"holder": "group security-team", "type": "group",
                      "permissions": ["Browse Projects", "Administer Projects"]},
                 ]},
            ]
            flagged = [{"scheme": s["name"], "holder": h["holder"]}
                       for s in schemes for h in s["holders"] if h.get("flag")]
            return {"source": "demo", "schemes": schemes,
                    "jirauser_grants": flagged, "project_count": 3,
                    "all_projects": [{"key": "DEVOPS", "name": "Platform"},
                                     {"key": "PLAT", "name": "Control"},
                                     {"key": "SEC", "name": "Security"}]}
        if not (settings.jira_base_url and settings.jira_user):
            return {"source": "not configured", "schemes": [], "jirauser_grants": []}
        auth = (settings.jira_user, settings.jira_password)
        base = settings.jira_base_url.rstrip("/")

        def jget(path, params=None):
            r = requests.get(f"{base}{path}", params=params, auth=auth,
                             timeout=(HTTP_CONNECT, HTTP_TIMEOUT))
            r.raise_for_status()
            return r.json()

        role_names = {}
        try:
            for role in jget("/rest/api/2/role"):
                role_names[str(role.get("id"))] = role.get("name", "")
        except requests.RequestException:
            pass

        data = jget("/rest/api/2/permissionscheme", {"expand": "permissions"})
        schemes = []
        jirauser_grants = []
        for s in data.get("permissionSchemes", []):
            by_holder: dict[tuple, list[str]] = {}
            holder_meta: dict[tuple, dict] = {}
            for perm in s.get("permissions", []):
                label, htype, param = _jira_holder(perm.get("holder") or {}, role_names)
                pname = (perm.get("permission") or "").replace("_", " ").title()
                by_holder.setdefault((label, htype), []).append(pname)
                holder_meta[(label, htype)] = {"param": param}
            holders = []
            for k, v in sorted(by_holder.items()):
                is_ju = k[1] == "user" and holder_meta[k]["param"].upper().startswith("JIRAUSER")
                holders.append({"holder": k[0], "type": k[1],
                                "permissions": sorted(set(v)), "flag": is_ju})
                if is_ju:
                    jirauser_grants.append({"scheme": s.get("name", ""),
                                            "holder": k[0]})
            schemes.append({
                "id": s.get("id"), "name": s.get("name", ""),
                "description": (s.get("description") or "")[:200],
                "url": f"{base}/secure/admin/EditPermissionScheme!default.jspa?schemeId={s.get('id')}",
                "projects": [], "holders": holders})

        # scheme -> projects: paginate ALL projects (the 'unassigned' bug came
        # from an 80-project cap) and resolve each project's scheme in parallel
        by_id = {s["id"]: s for s in schemes}
        projects, truncated = _jira_all_projects(jget)

        def proj_scheme(p):
            try:
                return p, jget(f"/rest/api/2/project/{p['key']}/permissionscheme").get("id")
            except requests.RequestException:
                return p, None

        with ThreadPoolExecutor(max_workers=POOL) as pool:
            for p, sid in pool.map(proj_scheme, projects):
                if sid in by_id:
                    by_id[sid]["projects"].append(
                        {"key": p["key"], "url": f"{base}/browse/{p['key']}"})
        for s in schemes:
            s["projects"].sort(key=lambda x: x["key"])
        return {"source": "live", "schemes": schemes,
                "jirauser_grants": jirauser_grants,
                "project_count": len(projects), "projects_truncated": truncated,
                "all_projects": projects}
    return _cached("jira:schemes", force, build)


def _jira_all_projects(jget) -> tuple[list[dict], bool]:
    """Every project (key + name), paginated. Jira DC has thousands; the old
    single-call /project cap of 80 is why assigned schemes showed 'unassigned'.
    Prefers the paginated /project/search, falls back to the legacy full list."""
    out: list[dict] = []
    try:
        start = 0
        while len(out) < JIRA_PROJECT_CAP:
            page = jget("/rest/api/2/project/search",
                        {"startAt": start, "maxResults": 50})
            values = page.get("values", [])
            out.extend({"key": p["key"], "name": p.get("name", "")}
                       for p in values if p.get("key"))
            if page.get("isLast") or not values:
                return out, False
            start += len(values)
        return out, True
    except requests.RequestException:
        pass
    # legacy Jira: /project returns them all in one shot
    try:
        allp = jget("/rest/api/2/project")
        out = [{"key": p["key"], "name": p.get("name", "")}
               for p in allp if p.get("key")]
        return out[:JIRA_PROJECT_CAP], len(out) > JIRA_PROJECT_CAP
    except requests.RequestException:
        return [], False


# ================================================================= Jenkins
_PERM_RE = re.compile(r"<permission>\s*([^<]+?)\s*</permission>")


def _parse_matrix_entries(xml_text: str) -> list[dict]:
    """Matrix-auth <permission> entries across plugin versions. Forms:
    'hudson.model.Item.Read:sid' (legacy), 'USER:hudson.model.Item.Read:sid'
    and 'GROUP:...:sid' (matrix-auth 2.x/3.x), and short ids like
    'Overall/Read'. The type prefix and ambiguous-sid ':' are handled."""
    grants: dict[tuple, set] = {}
    for raw in _PERM_RE.findall(xml_text):
        parts = raw.split(":")
        sid_type = "unknown"
        if parts and parts[0].upper() in ("USER", "GROUP", "EITHER") and len(parts) >= 3:
            sid_type = "group" if parts[0].upper() == "GROUP" else (
                "user" if parts[0].upper() == "USER" else "either")
            perm, sid = parts[1], ":".join(parts[2:])
        elif len(parts) >= 2:
            perm, sid = parts[0], ":".join(parts[1:])
        else:
            continue
        # 'hudson.model.Item.Read' -> 'Item/Read'; 'Overall/Read' kept as-is
        short = "/".join(perm.split(".")[-2:]) if "." in perm else perm
        grants.setdefault((sid, sid_type), set()).add(short)
    return [{"sid": sid, "type": sid_type, "permissions": sorted(perms)}
            for (sid, sid_type), perms in sorted(grants.items())]


def jenkins_matrix(force: bool = False) -> dict:
    def build():
        if settings.demo_mode:
            return {"source": "demo", "items": [
                {"path": "★ GLOBAL (instance-wide)", "entries": [
                    {"sid": "authenticated", "type": "group",
                     "permissions": ["Overall/Read"]},
                    {"sid": "devops-admins", "type": "group",
                     "permissions": ["Overall/Administer"]},
                ]},
                {"path": "(folder) payments-service", "entries": [
                    {"sid": "devops-team", "type": "group",
                     "permissions": ["Item/Build", "Item/Cancel", "Item/Read", "Item/Workspace"]},
                    {"sid": "alice", "type": "user",
                     "permissions": ["Item/Build", "Item/Configure", "Item/Delete", "Item/Read"]},
                ]},
                {"path": "platform-terraform/apply", "entries": [
                    {"sid": "platform-admins", "type": "group",
                     "permissions": ["Item/Build", "Item/Configure", "Item/Read"]},
                ]},
            ], "scanned": 3, "note": "", "global_found": True}
        from . import jenkins as jk
        if not jk.is_live():
            return {"source": "not configured", "items": [], "scanned": 0, "note": ""}
        auth = (settings.jenkins_user, settings.jenkins_token) if settings.jenkins_user else None
        items = []
        note_parts = []

        # GLOBAL strategy lives in the Jenkins ROOT config.xml — the most
        # common place grants are defined, and why per-job scans found nothing
        global_found = False
        try:
            rg = requests.get(f"{settings.jenkins_url}/config.xml", auth=auth,
                              timeout=(HTTP_CONNECT, 15))
            if rg.status_code in (401, 403):
                note_parts.append("global grants need Overall/Administer (root config.xml was "
                                  f"{rg.status_code}) — showing item-level only")
            elif rg.ok:
                gentries = _parse_matrix_entries(rg.text)
                if gentries:
                    items.append({"path": "★ GLOBAL (instance-wide)", "entries": gentries})
                    global_found = True
        except requests.RequestException:
            pass

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
            item_results = [x for x in pool.map(fetch_one, capped) if x]
        item_results.sort(key=lambda x: x["path"].lower())
        items.extend(item_results)  # keep GLOBAL first
        if len(paths) > len(capped):
            note_parts.append(f"scanned the first {len(capped)} of {len(paths)} items")
        if not items:
            note_parts.append("no matrix entries found — if you use PROJECT-based matrix "
                              "auth, grants are per-job/folder; if GLOBAL matrix, the account "
                              "needs Overall/Administer to read the root config")
        return {"source": "live", "items": items, "scanned": len(capped),
                "note": " · ".join(note_parts), "global_found": global_found}
    return _cached("jenkins:matrix", force, build)


# ================================================================= Summary
def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", (s or "").strip().lower())


def access_summary(force: bool = False) -> dict:
    """Per-track counts + ADO/Jira same-name detection. Cheap-exact counts
    (one call per collection); named-users is a bounded best-effort."""
    def build():
        # ---- ADO: collections, projects, repos, teams, named users ----
        ado_projects_data = ado_projects(force)
        ado_names = sorted({p["name"] for p in ado_projects_data.get("projects", [])})
        ado = {"source": ado_projects_data["source"],
               "collections": len(ado_projects_data.get("collections", [])),
               "projects": len(ado_names), "repos": 0, "teams": 0,
               "named_users": 0, "approx_users": False}

        if settings.demo_mode:
            ado.update(repos=10, teams=4, named_users=4)
        elif ado_projects_data["source"] == "live":
            colls = ado_projects_data.get("collections", [])

            def coll_counts(c):
                repos = teams = 0
                users: set[str] = set()
                try:
                    repos = len(_ado.coll_get(c, "/_apis/git/repositories").get("value", []))
                except requests.RequestException:
                    pass
                try:
                    td = _ado.coll_get(c, "/_apis/teams",
                                       {"$top": 1000, "api-version": "6.0-preview.3"})
                    teams = len(td.get("value", []))
                except requests.RequestException:
                    pass
                return repos, teams, users

            with ThreadPoolExecutor(max_workers=POOL) as pool:
                for repos, teams, users in pool.map(coll_counts, colls):
                    ado["repos"] += repos
                    ado["teams"] += teams
            # distinct named users across the instance (best-effort, bounded)
            ado["named_users"], ado["approx_users"] = _ado_named_users(colls)

        # ---- Jira: schemes, projects ----
        jira_data = jira_permission_schemes(force)
        jira_projects = jira_data.get("all_projects", [])
        jira = {"source": jira_data["source"],
                "schemes": len(jira_data.get("schemes", [])),
                "projects": len(jira_projects),
                "jirauser_grants": len(jira_data.get("jirauser_grants", []))}

        # ---- Jenkins ----
        jk_data = jenkins_matrix(force)
        jenkins = {"source": jk_data["source"],
                   "scopes": len(jk_data.get("items", [])),
                   "global": bool(jk_data.get("global_found"))}

        # ---- ADO vs Jira same-name detection ----
        ado_norm = {_norm(n): n for n in ado_names}
        jira_norm: dict[str, dict] = {}
        for p in jira_projects:
            for label in (p.get("name"), p.get("key")):
                if label:
                    jira_norm.setdefault(_norm(label), p)
        both, ado_only = [], []
        for k, name in sorted(ado_norm.items()):
            match = jira_norm.get(k)
            (both if match else ado_only).append(
                {"ado": name, "jira": (match or {}).get("key")} if match else name)
        matched_norms = {_norm(b["ado"]) for b in both} | {
            _norm((jira_norm.get(_norm(b["ado"])) or {}).get("key", "")) for b in both}
        jira_only = sorted({p.get("name") or p["key"] for p in jira_projects
                            if _norm(p.get("name", "")) not in ado_norm
                            and _norm(p.get("key", "")) not in ado_norm})
        overlap = {"both": both, "both_count": len(both),
                   "ado_only_count": len(ado_only), "ado_only": ado_only[:200],
                   "jira_only_count": len(jira_only), "jira_only": jira_only[:200],
                   "comparable": ado["source"] == jira["source"] != "not configured"}
        return {"ado": ado, "jira": jira, "jenkins": jenkins, "overlap": overlap}
    return _cached("access:summary", force, build)


def _ado_named_users(colls: list[str], team_cap: int = 150) -> tuple[int, bool]:
    """Distinct member display names across the instance's teams — bounded so
    a huge instance doesn't fan out into thousands of member calls."""
    teams: list[tuple[str, str]] = []  # (collection, team_id)
    for c in colls:
        try:
            for t in _ado.coll_get(c, "/_apis/teams",
                                   {"$top": 1000, "api-version": "6.0-preview.3"}
                                   ).get("value", []):
                teams.append((c, t.get("id", ""), t.get("projectId", "")))
        except requests.RequestException:
            continue
    capped = teams[:team_cap]
    users: set[str] = set()

    def members(entry):
        c, tid, pid = entry
        try:
            data = _ado.coll_get(c, f"/_apis/projects/{pid}/teams/{tid}/members",
                                 {"$top": 500})
            return [(m.get("identity") or m).get("displayName", "")
                    for m in data.get("value", [])]
        except requests.RequestException:
            return []

    with ThreadPoolExecutor(max_workers=POOL) as pool:
        for names in pool.map(members, capped):
            for n in names:
                if n and not _is_service_account(n):
                    users.add(n)
    return len(users), len(teams) > team_cap
