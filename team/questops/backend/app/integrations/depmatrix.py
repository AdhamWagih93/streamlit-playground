"""Logic dependency matrix over an Engine-style repository.

Layout it understands:
  pipelines/**            groovy Jenkins pipelines — the ROOTS of the graph
  playbooks/<group>/*.yml ansible playbooks, with the roles they use NEXT to
  playbooks/<group>/roles/<role>/**   them; a role is a UNIT (its tasks/vars
                                      files inherit the role's used/unused)
  scripts/**              bash (mostly) + python scripts, calling each other
  podman_run_script.sh / podman_run_playbook.sh — standard callers whose
  ARGUMENT is the real dependency (the callers themselves count as used).

Reference extraction is static and layered:
  - full repo paths        scripts/common/setup_env.sh
  - filename tokens        build_java.sh (resolved via a basename index;
                           generic names like main.yml are excluded)
  - caller arguments       podman_run_playbook.sh deploy/deploy_app.yml
  - ansible semantics      roles: blocks, include_role/import_role,
                           import_playbook, role meta dependencies
Reachability starts at the pipelines: everything not reached is UNUSED."""

import re
from pathlib import Path

from .repos import RepoError, _repo_by_slot, _workspace

MAX_FILE = 200 * 1024
MAX_FILES = 6000
CALLERS = {"podman_run_script.sh", "podman_run_playbook.sh"}
# too generic for basename-token resolution — path/ansible layers still catch them
GENERIC_BASENAMES = {"main.yml", "main.yaml", "site.yml", "tasks.yml", "vars.yml",
                     "defaults.yml", "run.sh", "test.sh", "build.sh", "setup.py",
                     "__init__.py", "requirements.yml"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}

_PATH_RE = re.compile(r"(?:pipelines|playbooks|scripts)/[\w\-./]+")
_TOKEN_RE = re.compile(r"[\w\-.]*\w\.(?:sh|py|yml|yaml|groovy)\b")
_CALLER_RE = re.compile(
    r"podman_run_(script|playbook)\.sh['\"]?\s+['\"]?(\$?[\w\-./{}]+)")
_IMPORT_PLAYBOOK_RE = re.compile(r"import_playbook:\s*['\"]?([\w\-./]+)")
_INCLUDE_ROLE_RE = re.compile(
    r"(?:include_role|import_role)\b[^\n]*\n(?:[^\n]*\n)?\s*name:\s*['\"]?([\w\-.]+)"
    r"|(?:include_role|import_role):\s*['\"]?([\w\-.]+)")


def _read(p: Path) -> str:
    try:
        if p.stat().st_size > MAX_FILE:
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _roles_block_names(text: str, key: str) -> list[str]:
    """Names under a `roles:` / `dependencies:` yaml list (line scanner —
    no yaml dependency needed for this shape)."""
    out, active, indent = [], False, 0
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(rf"^{key}:\s*$", stripped):
            active, indent = True, len(line) - len(line.lstrip())
            continue
        if active:
            cur = len(line) - len(line.lstrip())
            if stripped and cur <= indent and not stripped.startswith("-"):
                active = False
                continue
            m = re.match(r"^-\s*(?:role:\s*)?['\"]?([\w\-.]+)", stripped)
            if m:
                out.append(m.group(1))
    return out


def analyze(slot: int, username: str | None = None) -> dict:
    repo = _repo_by_slot(slot)
    root = _workspace(repo, username)

    # ---------------------------------------------------------- inventory
    nodes: dict[str, dict] = {}
    role_files: dict[str, list[Path]] = {}
    file_count = 0
    for p in sorted(root.rglob("*")):
        if file_count >= MAX_FILES:
            break
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(root))
        parts = Path(rel).parts
        if not parts:
            continue
        file_count += 1
        top = parts[0]
        if top == "pipelines":
            # ANY file under pipelines/ is a pipeline — they carry arbitrary
            # names, not .groovy/Jenkinsfile — except docs/assets
            if (p.name.startswith(".")
                    or p.suffix.lower() in {".md", ".txt", ".png", ".jpg",
                                            ".jpeg", ".svg", ".gif"}):
                continue
            nodes[rel] = {"id": rel, "type": "pipeline", "path": rel, "out": set()}
        elif top == "playbooks" and "roles" in parts:
            ri = parts.index("roles")
            if len(parts) > ri + 1:
                role_id = "role:" + "/".join(parts[:ri + 2])
                role_files.setdefault(role_id, []).append(p)
        elif top == "playbooks" and p.suffix in (".yml", ".yaml"):
            group = parts[1] if len(parts) > 2 else ""
            nodes[rel] = {"id": rel, "type": "playbook", "path": rel,
                          "group": group, "out": set()}
        elif top == "scripts" and p.suffix in (".sh", ".py"):
            ntype = "caller" if p.name in CALLERS else "script"
            nodes[rel] = {"id": rel, "type": ntype, "path": rel, "out": set()}

    for role_id, files in role_files.items():
        role_path = role_id[len("role:"):]
        nodes[role_id] = {"id": role_id, "type": "role", "path": role_path,
                          "name": Path(role_path).name,
                          "group": Path(role_path).parts[1] if len(Path(role_path).parts) > 1 else "",
                          "files": sorted(str(f.relative_to(root)) for f in files),
                          "out": set()}

    # lookup indexes ----------------------------------------------------
    by_path = {n["path"]: nid for nid, n in nodes.items()}
    by_basename: dict[str, list[str]] = {}
    for nid, n in nodes.items():
        if n["type"] == "role":
            continue
        base = Path(n["path"]).name
        if base not in GENERIC_BASENAMES:
            by_basename.setdefault(base, []).append(nid)
    roles_by_name: dict[str, list[str]] = {}
    for nid, n in nodes.items():
        if n["type"] == "role":
            roles_by_name.setdefault(n["name"], []).append(nid)

    ambiguous: list[dict] = []
    dynamic: list[dict] = []

    def resolve_path(raw: str) -> str | None:
        raw = raw.strip("'\"`,;:)([]").rstrip("/")
        if raw in by_path:
            return by_path[raw]
        for role_id, n in nodes.items():  # a path INTO a role dir -> the role unit
            if n["type"] == "role" and raw.startswith(n["path"] + "/"):
                return role_id
        return None

    def resolve_token(src: str, token: str) -> list[str]:
        cands = [c for c in by_basename.get(token, []) if c != src]
        if len(cands) > 1:
            ambiguous.append({"src": src, "token": token, "candidates": cands})
        return cands

    def resolve_role(src: str, name: str) -> list[str]:
        cands = roles_by_name.get(name, [])
        src_group = nodes[src].get("group")
        same = [c for c in cands if nodes[c].get("group") == src_group]
        picked = same or cands
        if len(picked) > 1:
            ambiguous.append({"src": src, "token": f"role {name}", "candidates": picked})
        return picked

    def resolve_caller_arg(src: str, kind: str, arg: str) -> list[str]:
        if "$" in arg or "{" in arg:
            dynamic.append({"src": src, "arg": arg})
            return []
        prefix = "scripts" if kind == "script" else "playbooks"
        for candidate in (arg, f"{prefix}/{arg}"):
            hit = resolve_path(candidate)
            if hit:
                return [hit]
        return resolve_token(src, Path(arg).name)

    # ---------------------------------------------------------- extraction
    def node_text(n: dict) -> str:
        if n["type"] == "role":
            return "\n".join(_read(root / f) for f in n["files"])
        return _read(root / n["path"])

    for nid, n in nodes.items():
        text = node_text(n)
        if not text:
            continue
        refs: set[str] = set()
        for raw in _PATH_RE.findall(text):
            hit = resolve_path(raw)
            if hit and hit != nid:
                refs.add(hit)
        for token in set(_TOKEN_RE.findall(text)):
            refs.update(resolve_token(nid, token))
        for kind, arg in _CALLER_RE.findall(text):
            refs.update(resolve_caller_arg(nid, kind, arg))
        if n["type"] in ("playbook", "role"):
            for name in _roles_block_names(text, "roles"):
                refs.update(resolve_role(nid, name))
            for name in _roles_block_names(text, "dependencies"):
                refs.update(resolve_role(nid, name))
            for m in _INCLUDE_ROLE_RE.findall(text):
                name = m[0] or m[1]
                if name:
                    refs.update(resolve_role(nid, name))
            for raw in _IMPORT_PLAYBOOK_RE.findall(text):
                hit = resolve_path(raw) or resolve_path(f"playbooks/{raw}")
                if hit:
                    refs.add(hit)
                else:
                    refs.update(resolve_token(nid, Path(raw).name))
        refs.discard(nid)
        n["out"] = refs

    # ---------------------------------------------------------- reachability
    roots = [nid for nid, n in nodes.items() if n["type"] == "pipeline"]
    used: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop()
        if cur in used:
            continue
        used.add(cur)
        stack.extend(nodes[cur]["out"])

    in_counts: dict[str, int] = {nid: 0 for nid in nodes}
    for n in nodes.values():
        for ref in n["out"]:
            in_counts[ref] += 1

    # ------------------------------------------------ Jenkins cross-reference
    # which pipeline FILES are actually wired to jobs on the Jenkins instance
    try:
        from . import jenkins
        script_paths = jenkins.pipeline_script_paths()
    except Exception:  # noqa: BLE001 — Jenkins down: matrix still works
        script_paths = {}
    jenkins_missing = sorted(
        ({"path": sp, "jobs": sorted(jobs)}
         for sp, jobs in script_paths.items() if sp not in by_path),
        key=lambda x: x["path"])

    # ---------------------------------------------------------- payload
    out_nodes = []
    for nid, n in nodes.items():
        jobs = script_paths.get(n["path"], []) if n["type"] == "pipeline" else None
        out_nodes.append({
            "id": nid, "type": n["type"], "path": n["path"],
            "name": n.get("name") or Path(n["path"]).name,
            "group": n.get("group", ""),
            "files": n.get("files"),
            "out": sorted(n["out"]),
            "in_count": in_counts[nid],
            "used": nid in used,
            "jenkins_jobs": sorted(jobs) if jobs is not None else None,
        })
    unused = {
        t: sorted(x["path"] for x in out_nodes if x["type"] == t and not x["used"])
        for t in ("script", "playbook", "role")
    }
    stats = {}
    for t in ("pipeline", "playbook", "role", "script", "caller"):
        of_type = [x for x in out_nodes if x["type"] == t]
        stats[t] = {"total": len(of_type),
                    "used": sum(1 for x in of_type if x["used"])}
    pipelines = [x for x in out_nodes if x["type"] == "pipeline"]
    jenkins_info = {
        "available": bool(script_paths),
        "wired": sum(1 for x in pipelines if x["jenkins_jobs"]),
        "not_wired": sorted(x["path"] for x in pipelines if not x["jenkins_jobs"])
        if script_paths else [],
        "missing": jenkins_missing,
    }
    return {"repo": {"slot": repo["slot"], "name": repo["name"]},
            "nodes": out_nodes, "roots": sorted(roots),
            "unused": unused, "stats": stats, "jenkins": jenkins_info,
            "ambiguous": ambiguous[:50], "dynamic": dynamic[:50],
            "files_scanned": file_count,
            "truncated": file_count >= MAX_FILES}
