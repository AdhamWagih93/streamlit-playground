"""Configurations hub — architecture model from the Control project's per-team
config repositories.

Every dev/qc/ops team owns one repo in the ADO project "Control", laid out as
  <project>/<env>_<app>/config.yml
(the folder splits on the FIRST underscore: env = prefix, app = remainder).
Each config.yml is walked for every remote connection it carries — URLs of any
scheme, JDBC/ADO.NET connection strings, host/port sibling keys, redis, s3/nfs/
smb shares, ldap, brokers — and each connection is resolved:

  * an in-cluster host (<svc>.<namespace>.svc.cluster.local, or a bare
    <image>-service / <image>-<namespace> name) → an APPLICATION when the
    service name matches one; the namespace tells us which PROJECT it belongs
    to (inventory host_vars namespaces first, then the project / project-env /
    env naming conventions) → internal (same project) or cross-project edge
  * an unresolved *.svc.cluster.local host → a "cluster service"
  * an IPv4 host → an IP node (ports listed)
  * anything else → an external endpoint, classified by kind

Credentials embedded in URLs / connection strings are DROPPED at parse time
and reported as an anomaly — they never reach the model or the UI.
"""

import re
import time
from pathlib import Path

from ..config import settings
from .inventory import _load_yaml
from .repos import _dir_for, configured

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 300
# ADO projects that hold per-team config repos, in PRECEDENCE order:
# a config found under Control wins over the same one under App_Configs
CONTROL_PROJECTS = ("control", "app_configs")
CONTROL_PROJECT = CONTROL_PROJECTS[0]   # kept for older callers
_SKIP_SUFFIX = "_bkp"
CLUSTER_SUFFIX = ".svc.cluster.local"

_SCHEME_RE = re.compile(
    r'\b(jdbc:[a-z0-9]+|https?|wss?|grpc|ftp|sftp|smtps?|imaps?|s3|nfs|smb|cifs|'
    r'postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|oracle|sqlserver|mssql|'
    r'cassandra|clickhouse|elasticsearch|amqps?|kafka|mqtt|nats|stomp|tcp|udp|ldaps?)://'
    r'(?:([^@/\s:"\']+)(?::([^@/\s"\']*))?@)?'      # optional user[:pass]@
    r'([a-zA-Z0-9._\-]+)(?::(\d{1,5}))?', re.I)
_HOST_KEY_RE = re.compile(r'(?:^|[_.-])(host|hostname|server|servername|addr|address|fqdn|node|broker|endpoint)s?$', re.I)
_PORT_KEY_RE = re.compile(r'(?:^|[_.-])port$', re.I)
_URL_KEY_RE = re.compile(r'(?:^|[_.-])(url|uri|endpoint|dsn|connection_?string|conn_?str|datasource|data_?source|jdbc|bootstrap_?servers)$', re.I)
_HOSTPORT_RE = re.compile(r'\b([a-zA-Z][a-zA-Z0-9._\-]*\.[a-zA-Z0-9._\-]+|[a-zA-Z][a-zA-Z0-9_\-]{2,}):(\d{2,5})\b')
_KV_HOST_RE = re.compile(r'(?:host|server|data\s*source|datasource|addr)\s*=\s*([a-zA-Z0-9._\-]+)', re.I)
_KV_PORT_RE = re.compile(r'port\s*=\s*(\d{1,5})', re.I)
_KV_SECRET_RE = re.compile(r'(?:password|pwd|secret|token)\s*=\s*[^;\s]+', re.I)
_SECRET_KEY_RE = re.compile(r'pass(word)?|pwd|secret|token|api_?key|credential', re.I)
_NS_KEY_RE = re.compile(r'namespace|^ns$|ocp_project|openshift_project|k8s_project|k8s_ns', re.I)
_IPV4_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')
# NFS/SMB style shares: host:/export/path or //host/share
_SHARE_RE = re.compile(r'^(?://)?([a-zA-Z0-9][a-zA-Z0-9._\-]*)(?::/|/)[^\s]*$')
_LOCAL = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::", ""}

KINDS = {  # scheme → (kind, label)
    "http": ("http", "HTTP"), "https": ("http", "HTTPS"), "ws": ("http", "WS"), "wss": ("http", "WSS"),
    "grpc": ("http", "gRPC"), "postgres": ("db", "PostgreSQL"), "postgresql": ("db", "PostgreSQL"),
    "mysql": ("db", "MySQL"), "mariadb": ("db", "MariaDB"), "mongodb": ("db", "MongoDB"),
    "mongodb+srv": ("db", "MongoDB"), "oracle": ("db", "Oracle"), "sqlserver": ("db", "SQL Server"),
    "mssql": ("db", "SQL Server"), "cassandra": ("db", "Cassandra"), "clickhouse": ("db", "ClickHouse"),
    "elasticsearch": ("db", "Elasticsearch"), "redis": ("cache", "Redis"), "rediss": ("cache", "Redis TLS"),
    "amqp": ("queue", "AMQP"), "amqps": ("queue", "AMQP TLS"), "kafka": ("queue", "Kafka"),
    "mqtt": ("queue", "MQTT"), "nats": ("queue", "NATS"), "stomp": ("queue", "STOMP"),
    "ldap": ("ldap", "LDAP"), "ldaps": ("ldap", "LDAPS"), "s3": ("storage", "S3"), "nfs": ("storage", "NFS"),
    "smb": ("storage", "SMB"), "cifs": ("storage", "SMB"), "ftp": ("file", "FTP"), "sftp": ("file", "SFTP"),
    "smtp": ("mail", "SMTP"), "smtps": ("mail", "SMTPS"), "imap": ("mail", "IMAP"), "imaps": ("mail", "IMAPS"),
    "tcp": ("socket", "TCP"), "udp": ("socket", "UDP"),
}


def _kind(scheme: str) -> tuple[str, str]:
    s = (scheme or "").lower()
    if s.startswith("jdbc:"):
        s = s[5:]
    return KINDS.get(s, ("other", (scheme or "?").upper()))


def _kind_from_key(path: str, host: str) -> str:
    """Kind for a scheme-less endpoint from its FULL key path + host name hints."""
    k, h = (path or "").lower(), (host or "").lower()
    if "redis" in k or "redis" in h or "cache" in k or "memcache" in h:
        return "cache"
    if any(x in k for x in ("s3", "bucket", "minio", "nfs", "share", "mount", "blob")) or any(x in h for x in ("amazonaws", "minio", "nfs", "storage")):
        return "storage"
    if "ldap" in k or "ldap" in h or "ad." in h:
        return "ldap"
    if any(x in k for x in ("kafka", "broker", "bootstrap", "queue", "amqp", "rabbit", "mq", "nats")) or any(x in h for x in ("kafka", "rabbit", "broker", "mq")):
        return "queue"
    if any(x in k for x in ("db", "database", "sql", "oracle", "postgres", "mongo", "dsn")) or any(x in h for x in ("db", "sql", "oracle", "postgres", "mongo")):
        return "db"
    if any(x in k for x in ("smtp", "mail")) or "smtp" in h or "mail" in h:
        return "mail"
    return "socket"


def image_name(app: str) -> str:
    return re.sub(r"[._]", "-", str(app or "").strip().lower())


def service_names(app: str, project: str = "", env: str = "") -> list[str]:
    img = image_name(app)
    if not img:
        return []
    out = [f"{img}-service", img]
    proj = re.sub(r"[._]", "-", str(project or "").strip().lower())
    e = re.sub(r"[._]", "-", str(env or "").strip().lower())
    for ns in ((f"{proj}-{e}" if proj and e else ""), proj, e):
        if ns and f"{img}-{ns}" not in out:
            out.append(f"{img}-{ns}")
    return out


def _host_matches(host: str, cand: str) -> bool:
    return host == cand or host.startswith(cand + ".") or host.startswith(cand + "/")


def strip_comments(text: str) -> str:
    return "\n".join(l for l in (text or "").splitlines() if not l.lstrip().startswith("#"))


def extract_connections(cfg) -> tuple[list[dict], list[str]]:
    """(connections, anomalies). Each connection: kind, label, host, port,
    scheme, via (dotted key path). Embedded credentials are dropped."""
    conns: list[dict] = []
    notes: list[str] = []
    host_c: dict = {}
    port_c: dict = {}

    def add(kind, label, host, port, scheme, via):
        h = (host or "").strip().strip("/").lower()
        if h in _LOCAL or "{" in h or "$" in h:
            return
        conns.append({"kind": kind, "label": label, "host": h,
                      "port": int(port) if str(port or "").isdigit() else None,
                      "scheme": scheme, "via": via})

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif node is not None and not isinstance(node, bool):
            sval = str(node)
            key = path.rsplit(".", 1)[-1].split("[", 1)[0]
            parent = path.rsplit(".", 1)[0] if "." in path else ""
            has_scheme = False
            for m in _SCHEME_RE.finditer(sval):
                has_scheme = True
                if m.group(2) or m.group(3):
                    notes.append(f"credentials embedded in {path}")
                kind, label = _kind(m.group(1))
                add(kind, label, m.group(4), m.group(5), m.group(1).lower(), path)
            mh = _KV_HOST_RE.search(sval)
            if mh and not has_scheme:
                if _KV_SECRET_RE.search(sval):
                    notes.append(f"credentials embedded in {path}")
                mp = _KV_PORT_RE.search(sval)
                add("db", "conn-string", mh.group(1), mp.group(1) if mp else None, "connstr", path)
            if not has_scheme and not mh:
                for m in _HOSTPORT_RE.finditer(sval):
                    add(_kind_from_key(path, m.group(1)), "host:port", m.group(1), m.group(2), "host:port", path)
                ms = _SHARE_RE.match(sval.strip())
                if ms and re.search(r"share|mount|export|nfs|smb|cifs|path", key, re.I) and "." in ms.group(1):
                    add("storage", "share", ms.group(1), None, "share", path)
            bare = sval.strip()
            if _HOST_KEY_RE.search(key) and "://" not in bare and re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._\-]*$', bare):
                host_c[parent] = (bare, key)
            if _PORT_KEY_RE.search(key) and bare.isdigit():
                port_c[parent] = bare
            if _URL_KEY_RE.search(key) and "://" not in bare and bare and not mh:
                m = _HOSTPORT_RE.search(bare)
                if m:
                    add(_kind_from_key(path, m.group(1)), "endpoint", m.group(1), m.group(2), "endpoint", path)
                elif re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._\-]+$', bare) and "." in bare:
                    add(_kind_from_key(path, bare), "endpoint", bare, None, "endpoint", path)
            if _SECRET_KEY_RE.search(key) and bare and not bare.startswith(("{", "$")):
                notes.append(f"plaintext secret under {path}")
    walk(cfg, "")
    for p, (h, key) in host_c.items():
        add(_kind_from_key(f"{p}.{key}", h), "host+port", h, port_c.get(p), "host+port", p or "(root)")
    seen, out = set(), []
    for c in conns:
        k = (c["kind"], c["host"], c["port"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out, sorted(set(notes))


# ---------------------------------------------------------------- namespaces
def namespace_map() -> dict:
    """namespace → {project, env} from inventory host_vars (any key that looks
    like a namespace), plus the naming conventions project / project-env / env."""
    from . import inventory
    ns: dict = {}
    try:
        inv = inventory.parse()
    except Exception:  # noqa: BLE001
        return ns
    for p in inv.get("projects") or []:
        pname = p["name"]
        for host, hv in ((p.get("config") or {}).get("host_vars") or {}).items():
            env = str(host).split("_", 1)[0].lower() if "_" in str(host) else ""
            for k, v in (hv or {}).items():
                if _NS_KEY_RE.search(str(k)) and isinstance(v, str) and v.strip():
                    ns.setdefault(v.strip().lower(), {"project": pname, "env": env, "source": f"host_vars/{host}.{k}"})
        proj = re.sub(r"[._]", "-", pname.lower())
        ns.setdefault(proj, {"project": pname, "env": "", "source": "convention"})
        for env in p.get("envs") or []:
            ns.setdefault(f"{proj}-{env.lower()}", {"project": pname, "env": env.lower(), "source": "convention"})
    return ns


# ---------------------------------------------------------------- repos + parse
def control_repos() -> list[dict]:
    """Team config repos from every config ADO project — Control first, then
    App_Configs. Each repo carries source_project + priority (0 = Control)."""
    out = []
    for prio, proj in enumerate(CONTROL_PROJECTS):
        for r in configured():
            if (r.get("project") or "").lower() == proj:
                out.append({**r, "source_project": r.get("project"), "priority": prio})
    return out


def _file_dates(root) -> dict:
    """{relative path: ISO date of its last commit} from ONE `git log` walk of
    the clone (2000 files → one subprocess, not 2000)."""
    import subprocess
    try:
        p = subprocess.run(["git", "log", "--format=%x01%cI", "--name-only", "--no-renames"],
                           cwd=str(root), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    dates: dict = {}
    cur = ""
    for line in p.stdout.splitlines():
        if line.startswith("\x01"):
            cur = line[1:20]
        elif line.strip() and line.strip() not in dates:
            dates[line.strip()] = cur
    return dates


def parse_repo(repo: dict) -> list[dict]:
    """[{team, project, env, app, path, ok, error, connections, notes, keys, changed}]"""
    root = _dir_for(repo)
    out = []
    if not root.exists():
        return out
    dates = _file_dates(root)
    for pdir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                       and not p.name.lower().endswith(_SKIP_SUFFIX)):
        for ea in sorted(p for p in pdir.iterdir() if p.is_dir() and not p.name.lower().endswith(_SKIP_SUFFIX)):
            if "_" not in ea.name:
                continue
            env, app = ea.name.split("_", 1)
            cf = ea / "config.yml"
            if not cf.is_file():
                cf = ea / "config.yaml"
            entry = {"team": repo["name"], "project": pdir.name, "env": env.lower(), "app": app,
                     "source_project": repo.get("source_project") or repo.get("project") or "",
                     "priority": repo.get("priority", 0),
                     "path": str(cf.relative_to(root)) if cf.is_file() else f"{pdir.name}/{ea.name}/config.yml",
                     "ok": cf.is_file(), "error": "" if cf.is_file() else "config.yml missing",
                     "connections": [], "notes": [], "keys": 0,
                     "changed": dates.get(str(cf.relative_to(root))) if cf.is_file() else ""}
            if cf.is_file():
                try:
                    cfg = _load_yaml(strip_comments(cf.read_text(encoding="utf-8", errors="replace")))
                    entry["keys"] = len(cfg) if isinstance(cfg, dict) else 0
                    entry["connections"], entry["notes"] = extract_connections(cfg)
                except Exception as exc:  # noqa: BLE001
                    entry.update(ok=False, error=f"unparseable: {str(exc)[:80]}")
            out.append(entry)
    return out


# ---------------------------------------------------------------- model
def build_model(entries: list[dict], ns_map: dict) -> dict:
    """Per-environment topology: nodes, edges, anomalies."""
    envs: dict = {}
    for e in entries:
        envs.setdefault(e["env"], []).append(e)
    # service index over EVERY app (all envs) — longest prefix first
    idx: list = []
    for e in entries:
        for svc in service_names(e["app"], e["project"], e["env"]):
            idx.append((svc, e["project"], e["app"], e["env"]))
    idx.sort(key=lambda x: -len(x[0]))

    def resolve(host: str):
        h = host.lower()
        ns = ""
        if h.endswith(CLUSTER_SUFFIX):
            parts = h[:-len(CLUSTER_SUFFIX)].split(".")
            svc = parts[0]
            ns = parts[1] if len(parts) > 1 else ""
        else:
            svc = h.split(".", 1)[0]
        for cand, proj, app, env in idx:
            if _host_matches(svc, cand):
                return {"project": proj, "app": app, "env": env, "namespace": ns}
        return {"namespace": ns} if h.endswith(CLUSTER_SUFFIX) else None

    model: dict = {"envs": {}, "projects": sorted({e["project"] for e in entries})}
    all_ext_labels: dict = {}
    for env, items in sorted(envs.items()):
        nodes: dict = {}
        edges: list = []
        anomalies: list = []
        for e in items:
            aid = f"app:{e['project']}/{e['app']}"
            nodes[aid] = {"id": aid, "type": "app", "project": e["project"], "app": e["app"], "team": e["team"],
                          "ok": e["ok"], "error": e["error"], "path": e["path"], "keys": e["keys"],
                          "notes": e["notes"], "out": 0, "in": 0}
            for n in e["notes"]:
                anomalies.append({"env": env, "app": e["app"], "project": e["project"], "kind": "secret", "detail": n})
            if not e["ok"]:
                anomalies.append({"env": env, "app": e["app"], "project": e["project"], "kind": "config", "detail": e["error"]})
        for e in items:
            src = f"app:{e['project']}/{e['app']}"
            for c in e["connections"]:
                r = resolve(c["host"])
                if r and r.get("app"):
                    tid = f"app:{r['project']}/{r['app']}"
                    if tid not in nodes:
                        nodes[tid] = {"id": tid, "type": "app", "project": r["project"], "app": r["app"],
                                      "team": "", "ok": True, "error": "", "path": "", "keys": 0, "notes": [],
                                      "out": 0, "in": 0, "other_env": r["env"] if r["env"] != env else ""}
                    scope = "internal" if r["project"] == e["project"] else "cross-project"
                    ns_info = ns_map.get(r.get("namespace") or "")
                    if r.get("namespace") and ns_info and ns_info.get("project") and ns_info["project"] != r["project"]:
                        anomalies.append({"env": env, "app": e["app"], "project": e["project"], "kind": "namespace",
                                          "detail": f"{c['host']} resolves to {r['app']} but namespace {r['namespace']} belongs to {ns_info['project']}"})
                    edges.append({"from": src, "to": tid, "kind": c["kind"], "label": c["label"], "scope": scope,
                                  "host": c["host"], "port": c["port"], "via": c["via"]})
                elif r is not None:   # cluster service, unresolved app
                    ns = r.get("namespace") or ""
                    owner = (ns_map.get(ns) or {}).get("project", "")
                    tid = f"svc:{c['host']}"
                    nodes.setdefault(tid, {"id": tid, "type": "cluster", "label": c["host"][:-len(CLUSTER_SUFFIX)],
                                           "namespace": ns, "owner": owner, "kind": c["kind"], "out": 0, "in": 0})
                    edges.append({"from": src, "to": tid, "kind": c["kind"], "label": c["label"],
                                  "scope": "cluster" if not owner or owner == e["project"] else "cross-project",
                                  "host": c["host"], "port": c["port"], "via": c["via"]})
                elif _IPV4_RE.match(c["host"]):
                    tid = f"ip:{c['host']}"
                    n = nodes.setdefault(tid, {"id": tid, "type": "ip", "label": c["host"], "ports": [], "kind": c["kind"], "out": 0, "in": 0})
                    if c["port"] and c["port"] not in n["ports"]:
                        n["ports"].append(c["port"])
                    edges.append({"from": src, "to": tid, "kind": c["kind"], "label": c["label"], "scope": "external",
                                  "host": c["host"], "port": c["port"], "via": c["via"]})
                else:
                    tid = f"ext:{c['host']}"
                    nodes.setdefault(tid, {"id": tid, "type": "external", "label": c["host"], "kind": c["kind"],
                                           "ports": [], "out": 0, "in": 0})
                    if c["port"] and c["port"] not in nodes[tid]["ports"]:
                        nodes[tid]["ports"].append(c["port"])
                    edges.append({"from": src, "to": tid, "kind": c["kind"], "label": c["label"], "scope": "external",
                                  "host": c["host"], "port": c["port"], "via": c["via"]})
                    all_ext_labels.setdefault(f"{c['host']}:{c['port'] or ''}", set()).add(env)
        for ed in edges:
            nodes[ed["from"]]["out"] += 1
            nodes[ed["to"]]["in"] += 1
        kinds: dict = {}
        for ed in edges:
            kinds[ed["kind"]] = kinds.get(ed["kind"], 0) + 1
        model["envs"][env] = {"nodes": list(nodes.values()), "edges": edges, "anomalies": anomalies,
                              "summary": {"apps": sum(1 for n in nodes.values() if n["type"] == "app"),
                                          "external": sum(1 for n in nodes.values() if n["type"] == "external"),
                                          "cluster": sum(1 for n in nodes.values() if n["type"] == "cluster"),
                                          "ips": sum(1 for n in nodes.values() if n["type"] == "ip"),
                                          "edges": len(edges), "kinds": kinds,
                                          "internal": sum(1 for x in edges if x["scope"] == "internal"),
                                          "cross": sum(1 for x in edges if x["scope"] == "cross-project"),
                                          "issues": len(anomalies)}}
    # same external endpoint in several envs = un-differentiated config (dev → prod DB…)
    shared = {k: sorted(v) for k, v in all_ext_labels.items() if len(v) > 1}
    for env, m in model["envs"].items():
        for k, evs in shared.items():
            if env in evs:
                m["anomalies"].append({"env": env, "kind": "shared", "detail": f"{k} is also used in {', '.join(e for e in evs if e != env)}"})
        m["summary"]["issues"] = len(m["anomalies"])
    model["shared_endpoints"] = shared
    return model


_FRESH_AT: dict = {}          # slot -> monotonic time of the last git refresh
_FRESH_TTL = 120              # seconds between refreshes of the same repo
_FRESH_STATUS: dict = {}      # slot -> "" (ok) | error text


def _freshen(repo_list: list[dict]) -> dict:
    """Bring every repo the analysis reads to the LATEST git state before
    parsing: pull the server clone (ff-only, authed), or clone it when it is
    defined but was never cloned. Throttled per repo so a burst of analyses
    doesn't hammer the git server; a failed refresh keeps the existing clone
    and is reported, never fatal. Returns {repo name: error-or-empty}."""
    from . import repos as repos_mod
    out: dict = {}
    for r in repo_list:
        slot = r.get("slot")
        name = r.get("name") or str(slot)
        if slot is None:
            continue
        now = time.monotonic()
        if now - _FRESH_AT.get(slot, 0) < _FRESH_TTL:
            out[name] = _FRESH_STATUS.get(slot, "")
            continue
        _FRESH_AT[slot] = now
        try:
            if _dir_for(r).exists():
                repos_mod.pull(slot)
            else:
                repos_mod.clone(slot)
            _FRESH_STATUS[slot] = ""
        except Exception as exc:  # noqa: BLE001 — stale beats broken
            _FRESH_STATUS[slot] = str(exc)[:150]
        out[name] = _FRESH_STATUS[slot]
    return out


def _used_repos() -> list[dict]:
    """Everything the analysis reads: the config team repos plus the
    inventories repo (namespace map / expected apps come from it)."""
    from .repos import configured
    used = control_repos()
    inv = next((r for r in configured() if (r.get("name") or "").lower() == "inventories"), None)
    if inv is not None:
        used.append(inv)
    return used


def analyze(refresh: bool = False) -> dict:
    if not refresh and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    fresh = _freshen(_used_repos())   # latest git state before any parsing
    repos = control_repos()
    entries = []
    for r in repos:
        entries += parse_repo(r)
    ns = namespace_map()
    model = build_model(entries, ns)
    payload = {"entries": entries,
               "repos": [{"team": r["name"], "cloned": _dir_for(r).exists(),
                          "refresh_error": fresh.get(r["name"]) or "",
                          "configs": sum(1 for e in entries if e["team"] == r["name"])} for r in repos],
               "refresh_errors": {k: v for k, v in fresh.items() if v},
               "configs": len(entries), "namespaces": ns, "model": model,
               "teams": sorted({e["team"] for e in entries}),
               "envs": sorted(model["envs"], key=lambda e: ({"dev": 1, "qc": 2, "uat": 3, "prd": 4}.get(e, 9), e))}
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)


def redact(text: str) -> str:
    """Config text safe to display: URL credentials and secret-looking keys masked."""
    text = _SCHEME_RE.sub(lambda m: m.group(0).replace(m.group(0).split("://", 1)[1].split("@", 1)[0] + "@", "***@") if "@" in m.group(0) else m.group(0), text)
    text = _KV_SECRET_RE.sub(lambda m: m.group(0).split("=", 1)[0] + "=***", text)
    return re.sub(r'(?im)^(\s*[^:\n#]*(?:pass(?:word)?|pwd|secret|token|api_?key|credential)[^:\n]*:\s*)(.+)$', r"\1***", text)


# ---------------------------------------------------------------- deployments cross-reference
_DEP_CACHE: dict = {"at": 0.0, "payload": None}
_ENV_ORDER = {"dev": 1, "qc": 2, "uat": 3, "prd": 4}


def _norm(v) -> str:
    return re.sub(r"[\s._\-]+", "", str(v or "").lower())


def deployments_latest(refresh: bool = False) -> tuple[dict, str]:
    """{norm(project): {env: {norm(app): {when, status, version, app}}}} — the
    latest REAL deployment of every app per environment, from ONE size-0
    aggregation on ef-cicd-deployments. A config is only effective once its
    app has been deployed to that environment."""
    if not refresh and _DEP_CACHE["payload"] is not None and time.time() - _DEP_CACHE["at"] < _TTL:
        return _DEP_CACHE["payload"], ""
    out: dict = {}
    err = ""
    if settings.demo_mode:
        from . import inventory
        for i, p in enumerate(inventory.parse().get("projects") or []):
            for env in p.get("envs") or []:
                for j, app in enumerate(p.get("apps") or []):
                    if env == "prd" and j == len(p["apps"]) - 1:
                        continue                      # last app never reached prd
                    import datetime as _dt
                    when = _dt.datetime.utcnow() + _dt.timedelta(hours=2) - _dt.timedelta(days=i * 3 + j * 2)
                    out.setdefault(_norm(p["name"]), {}).setdefault(env, {})[_norm(app)] = {
                        "app": app, "when": when.strftime("%Y-%m-%dT%H:%M"),
                        "status": "FAILED" if (env == "uat" and j == 1) else "SUCCESS",
                        "version": f"1.{20 - j}.{i}"}
    else:
        try:
            from .project_report import _es
            resp = _es("ef-cicd-deployments", {"size": 0, "query": {"bool": {"must_not": [{"term": {"testflag": True}}]}},
                "aggs": {"p": {"terms": {"field": "project", "size": 2000}, "aggs": {
                    "e": {"terms": {"field": "environment", "size": 12}, "aggs": {
                        "a": {"terms": {"field": "application", "size": 1000}, "aggs": {
                            "last": {"top_hits": {"size": 1, "_source": ["startdate", "status", "codeversion"],
                                                  "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}]}}}}}}}}}})
            for pb in ((resp.get("aggregations") or {}).get("p") or {}).get("buckets", []):
                for eb in (pb.get("e") or {}).get("buckets", []):
                    env = str(eb.get("key") or "").lower()
                    for ab in (eb.get("a") or {}).get("buckets", []):
                        hit = (((ab.get("last") or {}).get("hits") or {}).get("hits") or [{}])[0]
                        src = hit.get("_source") or {}
                        out.setdefault(_norm(pb.get("key")), {}).setdefault(env, {})[_norm(ab.get("key"))] = {
                            "app": ab.get("key"), "when": (src.get("startdate") or "")[:16],
                            "status": src.get("status") or "", "version": src.get("codeversion") or ""}
        except Exception as exc:  # noqa: BLE001 — the page degrades to "deployments unknown"
            err = str(exc)[:120]
    _DEP_CACHE.update(at=time.time(), payload=out)
    return out, err


# ---------------------------------------------------------------- inventory-driven overview
_OV_CACHE: dict = {"at": 0.0, "payload": None}


def _config_state(entry, dep) -> str:
    """One word for a (project, env, app) cell:
       effective   config present + app deployed there (config in force)
       stale       config changed AFTER the last deployment — not yet effective
       dormant     config present but the app was never deployed to that env
       missing     app deployed there but no config in the team repo
       unparseable config file present but broken
       absent      neither config nor deployment"""
    if entry is not None and not entry["ok"] and not entry["error"].startswith("config.yml missing"):
        return "unparseable"
    has_cfg = entry is not None and entry["ok"]
    if has_cfg and dep:
        if entry.get("changed") and dep.get("when") and entry["changed"][:16] > dep["when"][:16]:
            return "stale"
        return "effective"
    if has_cfg:
        return "dormant"
    if dep:
        return "missing"
    return "absent"


def _index_entries(entries: list[dict]) -> tuple[dict, dict]:
    """Index configs by (project, env, app). Returns (best, dupes):
      best   {key: entry} — the entry the states are computed from (a parsed
             one wins over a broken one; then the most recently changed)
      dupes  {key: [entry, …]} — every key defined in MORE THAN ONE place
             (two team repos, or twice in one repo via case/spacing variants);
             that is drift: nobody can tell which copy is authoritative."""
    all_by_key: dict = {}
    for e in entries:
        all_by_key.setdefault((_norm(e["project"]), e["env"], _norm(e["app"])), []).append(e)
    best: dict = {}
    for k, v in all_by_key.items():
        pool = [e for e in v if e["ok"]] or v            # a parsed config beats a broken one
        top = min(e.get("priority", 0) for e in pool)     # Control beats App_Configs
        pool = [e for e in pool if e.get("priority", 0) == top]
        best[k] = max(pool, key=lambda e: e.get("changed") or "")   # then the freshest
    dupes = {k: v for k, v in all_by_key.items() if len(v) > 1}
    return best, dupes


_BUILD_LOCK = __import__("threading").Lock()
_BUILDING: dict = {"active": False, "started": 0.0, "error": ""}


def _kick_build(refresh: bool) -> None:
    """Start ONE background overview build; concurrent kicks join the running one."""
    import threading
    with _BUILD_LOCK:
        if _BUILDING["active"]:
            return
        _BUILDING.update(active=True, started=time.time(), error="")

    def run():
        try:
            _build_overview(refresh)
            _BUILDING["error"] = ""
        except Exception as exc:  # noqa: BLE001
            _BUILDING["error"] = str(exc)[:200]
        finally:
            _BUILDING["active"] = False
    threading.Thread(target=run, name="configs-overview-build", daemon=True).start()


def overview(refresh: bool = False) -> dict:
    """NON-BLOCKING: the initial fetch is always light.
      cache fresh   → serve it
      cache stale   → serve it AS-IS (stale: true) and rebuild in the background
      cache empty   → return {building: true} instantly; the heavy parse runs
                      in a background thread and the UI polls until it lands"""
    if _OV_CACHE["payload"] and not refresh and time.time() - _OV_CACHE["at"] < _TTL:
        return {**_OV_CACHE["payload"], "cached": True}
    if _OV_CACHE["payload"]:
        _kick_build(refresh)
        return {**_OV_CACHE["payload"], "cached": True, "stale": True, "building": _BUILDING["active"]}
    _kick_build(refresh)
    return {"building": True, "projects": [], "unknown_projects": [], "unknown_configs": 0,
            "repos": [], "configs": 0, "envs": [], "refresh_errors": {},
            "deployments": {"available": False, "error": "", "projects": 0},
            "totals": {}, "build_error": _BUILDING["error"]}


def _build_overview(refresh: bool = False) -> dict:
    """The landing payload: ONE small row per INVENTORY project — expected
    configs (apps × envs) vs. what the Control team repos hold, deployment
    cross-reference, connection scopes and issues. Extra configs (repo
    folders naming apps / envs / projects the inventory does not know) are
    counted so drift is visible before any project is opened. No topology
    is shipped here — that is per project (project_detail)."""
    from . import inventory
    a = analyze(refresh)
    entries = a.get("entries") or []
    deps, dep_err = deployments_latest(refresh)
    inv = inventory.parse()
    by_key, dupes = _index_entries(entries)
    edges_by_proj: dict = {}
    issues_by_proj: dict = {}
    for env, m in (a.get("model") or {}).get("envs", {}).items():
        byid = {n["id"]: n for n in m["nodes"]}
        for ed in m["edges"]:
            n = byid.get(ed["from"]) or {}
            # an edge may start or end at a non-app node (cluster service,
            # external, IP) — those carry owner / no project key at all
            proj = n.get("project") or n.get("owner")
            if proj:
                d = edges_by_proj.setdefault(_norm(proj), {"internal": 0, "cross-project": 0, "cluster": 0, "external": 0, "targets": set()})
                d[ed["scope"]] = d.get(ed["scope"], 0) + 1
                if ed["scope"] == "cross-project":
                    t = byid.get(ed["to"]) or {}
                    tp = t.get("project") or t.get("owner")
                    if tp:
                        d["targets"].add(tp)
        for an in m.get("anomalies") or []:
            if an.get("kind") in ("secret", "shared") and an.get("project"):
                issues_by_proj[_norm(an["project"])] = issues_by_proj.get(_norm(an["project"]), 0) + 1
    known: set = set()
    projects = []
    for p in inv.get("projects") or []:
        pk = _norm(p["name"])
        known.add(pk)
        apps = p.get("apps") or []
        envs = sorted(p.get("envs") or [], key=lambda e: (_ENV_ORDER.get(e, 9), e))
        pv = (p.get("config") or {}).get("project_vars") or {}
        states: dict = {}
        per_env: dict = {}
        last_change = ""
        teams_seen: set = set()
        for env in envs:
            pe = per_env[env] = {"env": env, "expected": len(apps), "present": 0, "effective": 0, "stale": 0,
                                 "dormant": 0, "missing": 0, "unparseable": 0, "absent": 0}
            for app in apps:
                e = by_key.get((pk, env, _norm(app)))
                dep = ((deps.get(pk) or {}).get(env) or {}).get(_norm(app))
                st = _config_state(e, dep)
                states[st] = states.get(st, 0) + 1
                pe[st] = pe.get(st, 0) + 1
                if e is not None and e["ok"]:
                    pe["present"] += 1
                    teams_seen.add(e["team"])
                    last_change = max(last_change, e.get("changed") or "")
        my_dupes = [{"where": f"{env}_{v[0]['app']}", "env": env2, "app": v[0]["app"],
                     "places": [f"{e['team']}:{e['path']}" for e in v]}
                    for (pk2, env2, _ak), v in dupes.items() if pk2 == pk for env in [env2]]
        extra = [e for e in entries if _norm(e["project"]) == pk
                 and (e["env"] not in envs or _norm(e["app"]) not in {_norm(x) for x in apps})]
        expected = len(apps) * len(envs)
        present = sum(pe["present"] for pe in per_env.values())
        eg = edges_by_proj.get(pk) or {}
        projects.append({
            "name": p["name"], "company": pv.get("company") or "", "deploy_platform": pv.get("deploy_platform") or "",
            "teams": {"dev": p.get("dev_team"), "qc": p.get("qc_team"), "prd": p.get("prd_team")},
            "apps": len(apps), "envs": envs, "expected": expected, "present": present,
            "coverage": round(100 * present / expected) if expected else None,
            "states": states, "per_env": [per_env[e] for e in envs],
            "extra": len(extra), "extra_items": [f"{e['env']}_{e['app']}" for e in extra[:12]],
            "duplicates": len(my_dupes),
            "duplicate_items": [f"{d['where']} ({' vs '.join(d['places'])})" for d in my_dupes[:8]],
            "issues": issues_by_proj.get(pk, 0),
            "edges": {k: eg.get(k, 0) for k in ("internal", "cross-project", "cluster", "external")},
            "cross_targets": sorted(eg.get("targets") or []),
            "config_teams": sorted(teams_seen), "last_change": last_change,
        })
    unknown = sorted({e["project"] for e in entries if _norm(e["project"]) not in known})
    tot = lambda k: sum(x[k] for x in projects)  # noqa: E731
    payload = {"projects": projects, "unknown_projects": unknown,
               "refresh_errors": a.get("refresh_errors") or {},
               "unknown_configs": sum(1 for e in entries if _norm(e["project"]) not in known),
               "repos": a.get("repos"), "configs": len(entries), "envs": a.get("envs"),
               "deployments": {"available": not dep_err, "error": dep_err,
                               "projects": len(deps)},
               "totals": {"projects": len(projects), "apps": tot("apps"), "expected": tot("expected"),
                          "present": tot("present"), "extra": tot("extra"), "issues": tot("issues"),
                          "duplicates": len(dupes),
                          "states": {k: sum(p["states"].get(k, 0) for p in projects)
                                     for k in ("effective", "stale", "dormant", "missing", "unparseable", "absent")},
                          "cross": sum(p["edges"]["cross-project"] for p in projects)},
               "inventory_source": inv.get("source")}
    _OV_CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def project_detail(name: str, refresh: bool = False) -> dict:
    """Everything for ONE project. On a completely cold cache this does not
    block: it returns {building: true} and kicks the shared background build
    (the UI polls). Once anything is cached, detail is served synchronously.
    """
    if _CACHE["payload"] is None and not settings.demo_mode:
        _kick_build(refresh)
        return {"building": True, "name": name}
    return _project_detail(name, refresh)


def _project_detail(name: str, refresh: bool = False) -> dict:
    """Everything for ONE project: per-env topology restricted to the project
    and whatever it touches (so cross-project edges keep their far end), the
    app × env matrix with config state + last deployment, anomalies, extras."""
    from . import inventory
    a = analyze(refresh)
    deps, dep_err = deployments_latest(refresh)
    pk = _norm(name)
    inv_p = next((p for p in inventory.parse().get("projects") or [] if _norm(p["name"]) == pk), None)
    apps = list((inv_p or {}).get("apps") or [])
    inv_envs = list((inv_p or {}).get("envs") or [])
    entries = [e for e in (a.get("entries") or []) if _norm(e["project"]) == pk]
    envs_all = sorted({*inv_envs, *(e["env"] for e in entries)}, key=lambda e: (_ENV_ORDER.get(e, 9), e))
    model_envs: dict = {}
    anomalies: list = []
    for env, m in (a.get("model") or {}).get("envs", {}).items():
        byid = {n["id"]: n for n in m["nodes"]}
        mine = {n["id"] for n in m["nodes"] if n.get("type") == "app" and _norm(n.get("project")) == pk}
        if not mine:
            continue
        edges = [ed for ed in m["edges"] if ed["from"] in mine or ed["to"] in mine]
        keep = mine | {ed["from"] for ed in edges} | {ed["to"] for ed in edges}
        nodes = [byid[i] for i in keep if i in byid]
        # far-end projects are drawn collapsed by the UI; give them their full app count
        model_envs[env] = {"nodes": nodes, "edges": edges,
                           "anomalies": [x for x in m.get("anomalies") or [] if _norm(x.get("project")) == pk],
                           "summary": {"apps": len(mine), "edges": len(edges),
                                       "internal": sum(1 for e in edges if e["scope"] == "internal"),
                                       "cross": sum(1 for e in edges if e["scope"] == "cross-project")}}
        anomalies += model_envs[env]["anomalies"]
    best_all, dupes_all = _index_entries(entries)
    by_key = {(env, ak): e for (_pk, env, ak), e in best_all.items()}
    dup_by_key = {(env, ak): [f"{x['team']}:{x['path']}" for x in v] for (_pk, env, ak), v in dupes_all.items()}
    app_names = list(dict.fromkeys([*apps, *(e["app"] for e in entries if _norm(e["app"]) not in {_norm(x) for x in apps})]))
    matrix = []
    for app in app_names:
        row = {"app": app, "in_inventory": _norm(app) in {_norm(x) for x in apps}, "cells": []}
        for env in envs_all:
            e = by_key.get((env, _norm(app)))
            dep = ((deps.get(pk) or {}).get(env) or {}).get(_norm(app))
            row["cells"].append({"env": env, "state": _config_state(e, dep), "in_inventory_env": env in inv_envs,
                                 "duplicates": dup_by_key.get((env, _norm(app))) or [],
                                 "path": e["path"] if e else "", "team": e["team"] if e else "",
                                 "changed": (e or {}).get("changed") or "", "keys": (e or {}).get("keys") or 0,
                                 "connections": len((e or {}).get("connections") or []), "notes": (e or {}).get("notes") or [],
                                 "error": (e or {}).get("error") or "",
                                 "deploy": dep})
        matrix.append(row)
    return {"name": (inv_p or {}).get("name") or name, "in_inventory": inv_p is not None,
            "inventory": {"apps": apps, "envs": inv_envs, "teams": {"dev": (inv_p or {}).get("dev_team"), "qc": (inv_p or {}).get("qc_team"), "prd": (inv_p or {}).get("prd_team")}},
            "envs": envs_all, "model": {"envs": model_envs, "projects": sorted({n.get("project") or n.get("owner") or "" for m in model_envs.values() for n in m["nodes"] if n.get("project") or n.get("owner")})},
            "matrix": matrix, "anomalies": anomalies,
            "config_teams": sorted({e["team"] for e in entries}),
            "deployments": {"available": not dep_err, "error": dep_err}, "namespaces": {k: v for k, v in (a.get("namespaces") or {}).items() if _norm(v.get("project")) == pk}}


def warm_up() -> None:
    """Background pre-build of the cached analyses so the first visitor of the
    page is not the one paying for 2000 YAML parses + a git walk."""
    import threading

    def run():
        try:
            overview()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, name="configs-warm-up", daemon=True).start()
