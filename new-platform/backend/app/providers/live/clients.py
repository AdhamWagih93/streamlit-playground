"""Shared clients for live mode. Credentials resolve via Vault (matching the original
platform's paths) with env fallbacks. Every consumer treats a missing integration as
a clear 503, never a silent demo fallback.

All of these are lazy singletons — nothing connects until a live slice is called.
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

from ...config import get_settings

_lock = threading.Lock()


class IntegrationUnavailable(HTTPException):
    def __init__(self, name: str, detail: str):
        super().__init__(status_code=503, detail=f"{name} unavailable: {detail}")


# ---------------------------------------------------------------- vault
@lru_cache(maxsize=32)
def vault_secrets(path: str) -> dict[str, Any]:
    s = get_settings()
    token = s.vault_token
    if s.vault_token_file and os.path.isfile(s.vault_token_file):
        token = open(s.vault_token_file).read().strip()
    if not s.vault_addr or not token:
        raise IntegrationUnavailable("Vault", "VAULT_ADDR / VAULT_TOKEN(_FILE) not configured")
    try:
        import hvac
        client = hvac.Client(url=s.vault_addr, token=token, verify=False)
        secret = client.secrets.kv.v2.read_secret_version(path=path)
        return secret["data"]["data"]
    except Exception as exc:  # pragma: no cover - live only
        raise IntegrationUnavailable("Vault", f"read '{path}' failed: {exc}")


# ---------------------------------------------------------------- elasticsearch
_es = None


def es():
    global _es
    with _lock:
        if _es is None:
            s = get_settings()
            if not s.es_hosts:
                raise IntegrationUnavailable("Elasticsearch", "ES_HOSTS not configured")
            try:
                from elasticsearch import Elasticsearch
                kwargs: dict[str, Any] = dict(
                    hosts=[h.strip() for h in s.es_hosts.split(",")],
                    verify_certs=s.es_verify_certs,
                    request_timeout=60,
                )
                if s.es_api_key:
                    kwargs["api_key"] = s.es_api_key
                _es = Elasticsearch(**kwargs)
            except Exception as exc:  # pragma: no cover
                raise IntegrationUnavailable("Elasticsearch", str(exc))
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


# ---------------------------------------------------------------- postgres
def pg_conn(readonly: bool = True):
    try:
        import psycopg
    except ImportError:
        raise IntegrationUnavailable("Postgres", "psycopg not installed (requirements-live.txt)")
    creds = vault_secrets(get_settings().postgres_vault_path)
    try:
        conn = psycopg.connect(
            host=creds.get("host", ""), port=int(creds.get("port", 5432)),
            dbname=creds.get("database", ""), user=creds.get("username", ""),
            password=creds.get("password", ""), connect_timeout=10,
        )
        if readonly:
            conn.execute("SET default_transaction_read_only = on")
        return conn
    except Exception as exc:  # pragma: no cover
        raise IntegrationUnavailable("Postgres", str(exc))


SAFE_IDENT = __import__("re").compile(r"^[A-Za-z0-9_.]+$")


def safe_ident(name: str) -> str:
    if not SAFE_IDENT.match(name or ""):
        raise HTTPException(status_code=400, detail=f"Unsafe identifier: {name!r}")
    return name


# ---------------------------------------------------------------- jenkins
def jenkins_creds() -> dict[str, str]:
    s = get_settings()
    try:
        c = vault_secrets(s.jenkins_vault_path)
        return {"host": c.get("host", ""), "public_name": c.get("public_name", ""),
                "username": c.get("username", ""), "api_token": c.get("api_token", "")}
    except HTTPException:
        host = os.environ.get("JENKINS_HOSTNAME", "")
        if not host:
            raise
        return {"host": host, "public_name": host,
                "username": os.environ.get("JENKINS_USER", ""),
                "api_token": os.environ.get("JENKINS_TOKEN", "")}


# ---------------------------------------------------------------- s3 (scan reports)
def s3_client():
    try:
        import boto3
    except ImportError:
        raise IntegrationUnavailable("S3", "boto3 not installed (requirements-live.txt)")
    s = get_settings()
    c = vault_secrets(s.prisma_s3_vault_path)
    return boto3.client(
        "s3",
        endpoint_url=f"http://{c.get('host')}:{c.get('port')}",
        aws_access_key_id=c.get("access_key"), aws_secret_access_key=c.get("secret_key"),
        verify=False,
    )
