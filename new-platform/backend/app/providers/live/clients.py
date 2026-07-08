"""Shared clients for live mode.

Credentials come from the in-app Settings store (encrypted in the platform's
Postgres — see app/integrations/store.py). The only env-configured integration is
Postgres itself (DATABASE_URL). Vault remains available as an *optional broker*:
if the vault integration is configured, `vault_secrets(path)` still works for
setups that keep credentials there.

Every consumer treats a missing integration as a clear 503 with a hint pointing
at the Settings page — never a silent demo fallback.
"""
from __future__ import annotations

import re
import threading
from typing import Any

from fastapi import HTTPException

from ...config import get_settings
from ...integrations import store

_lock = threading.Lock()


class IntegrationUnavailable(HTTPException):
    def __init__(self, name: str, detail: str):
        super().__init__(
            status_code=503,
            detail=f"{name} unavailable: {detail} — configure it in Settings → Integrations.",
        )


def integration_config(key: str, display: str) -> dict[str, Any]:
    cfg = store.get_config(key)
    if not cfg:
        raise IntegrationUnavailable(display, "not configured (or disabled)")
    return cfg


# ---------------------------------------------------------------- vault (optional broker)
def vault_secrets(path: str) -> dict[str, Any]:
    cfg = integration_config("vault", "Secrets vault")
    try:
        import hvac
        client = hvac.Client(url=cfg["addr"], token=cfg.get("token", ""), verify=False)
        secret = client.secrets.kv.v2.read_secret_version(path=path)
        return secret["data"]["data"]
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - live only
        raise IntegrationUnavailable("Secrets vault", f"read '{path}' failed: {exc}")


# ---------------------------------------------------------------- elasticsearch
_es = None
_es_cfg_sig: str = ""


def es():
    """Elasticsearch client built from the settings store; rebuilt when config changes."""
    global _es, _es_cfg_sig
    cfg = integration_config("elasticsearch", "Search & analytics store")
    sig = repr(sorted(cfg.items()))
    with _lock:
        if _es is None or sig != _es_cfg_sig:
            try:
                from elasticsearch import Elasticsearch
                kwargs: dict[str, Any] = dict(
                    hosts=[h.strip() for h in str(cfg.get("hosts", "")).split(",") if h.strip()],
                    verify_certs=bool(cfg.get("verify_certs", False)),
                    request_timeout=60,
                )
                if cfg.get("api_key"):
                    kwargs["api_key"] = cfg["api_key"]
                elif cfg.get("username"):
                    kwargs["basic_auth"] = (cfg["username"], cfg.get("password", ""))
                _es = Elasticsearch(**kwargs)
                _es_cfg_sig = sig
            except Exception as exc:  # pragma: no cover
                raise IntegrationUnavailable("Search & analytics store", str(exc))
    return _es


IDX = {
    "inventory": "ef-devops-inventory",
    "versions": "ef-cicd-versions-lookup",
    "commits": "ef-git-commits",
    "jira": "ef-bs-jira-issues",
    "approval": "ef-cicd-approval",
    "requests": "ef-devops-requests",
    "builds": "ef-cicd-builds",
    "deployments": "ef-cicd-deployments",
    "releases": "ef-cicd-releases",
    "prismacloud": "ef-cicd-prismacloud",
    "invicti": "ef-cicd-invicti",
    "zap": "ef-cicd-zap",
    "trufflehog": "ef-cicd-trufflehog",
    "devops_projects": "ef-devops-projects",
    "tools_access": "ef-devops-tools-access",
}


# ---------------------------------------------------------------- postgres (platform DB)
def pg_conn(readonly: bool = True):
    """The platform database — env-configured (DATABASE_URL), not a Settings entry."""
    dsn = get_settings().database_url
    if not dsn:
        raise IntegrationUnavailable("Platform database", "DATABASE_URL not set in the environment")
    try:
        import psycopg
        conn = psycopg.connect(dsn, connect_timeout=10)
        if readonly:
            conn.execute("SET default_transaction_read_only = on")
        return conn
    except Exception as exc:  # pragma: no cover
        raise IntegrationUnavailable("Platform database", str(exc))


SAFE_IDENT = re.compile(r"^[A-Za-z0-9_.]+$")


def safe_ident(name: str) -> str:
    if not SAFE_IDENT.match(name or ""):
        raise HTTPException(status_code=400, detail=f"Unsafe identifier: {name!r}")
    return name


# ---------------------------------------------------------------- jenkins
def jenkins_creds() -> dict[str, str]:
    cfg = integration_config("jenkins", "Pipeline orchestrator")
    return {"host": cfg.get("host", ""),
            "public_name": cfg.get("public_name", "") or cfg.get("host", ""),
            "username": cfg.get("username", ""), "api_token": cfg.get("api_token", "")}


# ---------------------------------------------------------------- s3 (scan reports)
def s3_client():
    cfg = integration_config("s3", "Scan report store")
    try:
        import boto3
    except ImportError:
        raise IntegrationUnavailable("Scan report store", "boto3 not installed (requirements-live.txt)")
    return boto3.client(
        "s3",
        endpoint_url=f"http://{cfg.get('host')}:{cfg.get('port', 9000)}",
        aws_access_key_id=cfg.get("access_key"), aws_secret_access_key=cfg.get("secret_key"),
        verify=False,
    )


def s3_report_location() -> tuple[str, str]:
    """(bucket, key_pattern) from the s3 integration config, with platform defaults."""
    s = get_settings()
    cfg = integration_config("s3", "Scan report store")
    return (cfg.get("bucket") or s.prisma_s3_bucket,
            cfg.get("key_pattern") or s.prisma_s3_key_pattern)


# ---------------------------------------------------------------- ollama (model runtime)
def ollama_config() -> dict[str, str]:
    cfg = integration_config("ollama", "On-prem model runtime")
    s = get_settings()
    return {"url": cfg.get("url") or s.docchat_ollama_url,
            "model": cfg.get("model") or s.docchat_model}


# ---------------------------------------------------------------- ado (source control)
def ado_creds() -> dict[str, str]:
    cfg = integration_config("ado", "Source control & inventory host")
    return {"host": cfg.get("host", ""), "username": cfg.get("username", ""),
            "password": cfg.get("password", ""),
            "token": cfg.get("token", "") or cfg.get("password", ""),
            "collection": cfg.get("collection", "DevOps"),
            "project": cfg.get("project", "Control")}
