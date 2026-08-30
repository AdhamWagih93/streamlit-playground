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

from .inventory import _load_yaml
from .repos import _dir_for, configured

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 300
CONTROL_PROJECT = "control"
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
    return [r for r in configured() if (r.get("project") or "").lower() == CONTROL_PROJECT]


def parse_repo(repo: dict) -> list[dict]:
    """[{team, project, env, app, path, ok, error, connections, notes, keys}]"""
    root = _dir_for(repo)
    out = []
    if not root.exists():
        return out
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
                     "path": str(cf.relative_to(root)) if cf.is_file() else f"{pdir.name}/{ea.name}/config.yml",
                     "ok": cf.is_file(), "error": "" if cf.is_file() else "config.yml missing",
                     "connections": [], "notes": [], "keys": 0}
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
                    if r.get("namespace") and ns_info and ns_info["project"] != r["project"]:
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


def analyze(refresh: bool = False) -> dict:
    if not refresh and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    repos = control_repos()
    entries = []
    for r in repos:
        entries += parse_repo(r)
    ns = namespace_map()
    model = build_model(entries, ns)
    payload = {"repos": [{"team": r["name"], "cloned": _dir_for(r).exists(),
                          "configs": sum(1 for e in entries if e["team"] == r["name"])} for r in repos],
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
