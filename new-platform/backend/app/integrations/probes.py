"""Connection probes for the Settings page "Test" button. Short timeouts, and error
text is sanitized so credentials never surface in the response."""
from __future__ import annotations

import re

import httpx

_TIMEOUT = 6.0


def _clean(err: Exception, config: dict) -> str:
    text = f"{type(err).__name__}: {err}"
    for v in config.values():
        if isinstance(v, str) and len(v) >= 4:
            text = text.replace(v, "•••")
    return re.sub(r"://[^@/\s]+@", "://•••@", text)[:400]


def probe(key: str, config: dict) -> tuple[bool, str]:
    try:
        return _PROBES[key](config)
    except KeyError:
        return False, f"No probe for '{key}'"
    except Exception as exc:
        return False, _clean(exc, config)


def _http_get(url: str, auth=None, headers=None, verify=False) -> httpx.Response:
    with httpx.Client(timeout=_TIMEOUT, verify=verify) as client:
        return client.get(url, auth=auth, headers=headers)


def _probe_elasticsearch(cfg: dict) -> tuple[bool, str]:
    host = (cfg.get("hosts") or "").split(",")[0].strip()
    headers = {"Authorization": f"ApiKey {cfg['api_key']}"} if cfg.get("api_key") else None
    auth = (cfg.get("username"), cfg.get("password")) if cfg.get("username") else None
    r = _http_get(host, auth=auth, headers=headers, verify=bool(cfg.get("verify_certs")))
    if r.status_code == 200 and "cluster_name" in r.text:
        name = r.json().get("cluster_name", "?")
        ver = r.json().get("version", {}).get("number", "?")
        return True, f"cluster '{name}' · v{ver}"
    return False, f"HTTP {r.status_code}"


def _probe_jenkins(cfg: dict) -> tuple[bool, str]:
    r = _http_get(f"{cfg['host'].rstrip('/')}/api/json",
                  auth=(cfg.get("username", ""), cfg.get("api_token", "")))
    if r.status_code == 200:
        ver = r.headers.get("X-Jenkins", "?")
        return True, f"Jenkins {ver} · {len(r.json().get('jobs', []))} top-level jobs"
    return False, f"HTTP {r.status_code}"


def _probe_ado(cfg: dict) -> tuple[bool, str]:
    token = cfg.get("token") or cfg.get("password") or ""
    r = _http_get(f"{cfg['host'].rstrip('/')}/_apis/projects?api-version=6.0",
                  auth=("", token))
    if r.status_code == 200:
        return True, f"{r.json().get('count', '?')} projects visible"
    return False, f"HTTP {r.status_code}"


def _probe_s3(cfg: dict) -> tuple[bool, str]:
    import boto3
    from botocore.config import Config as BotoConfig

    client = boto3.client(
        "s3", endpoint_url=f"http://{cfg['host']}:{cfg.get('port', 9000)}",
        aws_access_key_id=cfg.get("access_key"), aws_secret_access_key=cfg.get("secret_key"),
        verify=False, config=BotoConfig(connect_timeout=5, read_timeout=5,
                                        retries={"max_attempts": 1}),
    )
    bucket = cfg.get("bucket") or "PrismaCloud-Logs"
    client.head_bucket(Bucket=bucket)
    return True, f"bucket '{bucket}' reachable"


def _probe_ldap(cfg: dict) -> tuple[bool, str]:
    import ldap3

    server = ldap3.Server(cfg["url"], get_info=ldap3.NONE, connect_timeout=5)
    conn = ldap3.Connection(server, user=cfg.get("bind_dn"),
                            password=cfg.get("bind_password"), auto_bind=True)
    conn.unbind()
    return True, "bind successful"


def _probe_ollama(cfg: dict) -> tuple[bool, str]:
    r = _http_get(f"{cfg['url'].rstrip('/')}/api/tags")
    if r.status_code == 200:
        models = [m.get("name", "") for m in r.json().get("models", [])]
        want = cfg.get("model", "")
        if want and not any(m.startswith(want.split(":")[0]) for m in models):
            return True, f"runtime up, but model '{want}' not in {len(models)} local models"
        return True, f"{len(models)} models available"
    return False, f"HTTP {r.status_code}"


def _probe_vault(cfg: dict) -> tuple[bool, str]:
    r = _http_get(f"{cfg['addr'].rstrip('/')}/v1/sys/health",
                  headers={"X-Vault-Token": cfg.get("token", "")})
    if r.status_code in (200, 429):   # 429 = standby node, still healthy
        body = r.json()
        return (not body.get("sealed", True)), \
            ("unsealed · " if not body.get("sealed") else "SEALED · ") + \
            f"v{body.get('version', '?')}"
    return False, f"HTTP {r.status_code}"


_PROBES = {
    "elasticsearch": _probe_elasticsearch,
    "jenkins": _probe_jenkins,
    "ado": _probe_ado,
    "s3": _probe_s3,
    "ldap_directory": _probe_ldap,
    "ollama": _probe_ollama,
    "vault": _probe_vault,
}
