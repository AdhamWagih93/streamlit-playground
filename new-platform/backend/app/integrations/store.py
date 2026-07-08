"""Encrypted integration-settings store.

Configs are Fernet-encrypted (AES128-CBC + HMAC, via `cryptography`) BEFORE they
reach Postgres, so neither pg_dump nor a DB-level breach exposes credentials.
The encryption key comes from SETTINGS_ENCRYPTION_KEY in the environment — the one
secret that must live outside the database it protects. In local dev the key is
derived from the (default) session secret so everything works with zero config,
and the API flags that state so the UI can warn.

If Postgres is unreachable the store degrades to process-memory (persistent=False,
surfaced in the API and UI) — demo mode stays fully usable without a database.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import get_settings
from .registry import INTEGRATIONS, secret_field_names

_lock = threading.Lock()
_mem: dict[str, dict] = {}          # fallback store: key -> row dict
_pg_failed_reason: Optional[str] = None

_TABLE = "platform_integrations"
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    key               TEXT PRIMARY KEY,
    config_encrypted  TEXT NOT NULL,
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at        TIMESTAMPTZ NOT NULL,
    updated_by        TEXT NOT NULL DEFAULT '',
    last_test_status  TEXT NOT NULL DEFAULT 'never',
    last_test_detail  TEXT NOT NULL DEFAULT '',
    last_test_at      TIMESTAMPTZ
)
"""


# ---------------------------------------------------------------- crypto
def _fernet():
    from cryptography.fernet import Fernet

    s = get_settings()
    raw = s.settings_encryption_key.strip()
    if raw:
        return Fernet(raw.encode()), False
    # dev fallback: deterministic key derived from the session secret
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(f"meridian-settings::{s.session_secret}".encode()).digest()
    )
    return Fernet(derived), True


def encryption_key_is_derived() -> bool:
    return not get_settings().settings_encryption_key.strip()


def _encrypt(config: dict) -> str:
    f, _ = _fernet()
    return f.encrypt(json.dumps(config).encode()).decode()


def _decrypt(token: str) -> dict:
    f, _ = _fernet()
    return json.loads(f.decrypt(token.encode()))


# ---------------------------------------------------------------- postgres
def _dsn() -> str:
    return get_settings().database_url


def _pg():
    """Return a psycopg connection or None (fallback mode). Never raises."""
    global _pg_failed_reason
    dsn = _dsn()
    if not dsn:
        _pg_failed_reason = "DATABASE_URL not configured"
        return None
    try:
        import psycopg
        conn = psycopg.connect(dsn, connect_timeout=5, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(_DDL)
        _pg_failed_reason = None
        return conn
    except Exception as exc:
        _pg_failed_reason = f"{type(exc).__name__}: {exc}"
        return None


def storage_status() -> dict:
    conn = _pg()
    if conn is not None:
        conn.close()
        return {"persistent": True, "detail": "Postgres reachable",
                "derived_key": encryption_key_is_derived()}
    return {"persistent": False, "detail": _pg_failed_reason or "unavailable",
            "derived_key": encryption_key_is_derived()}


# ---------------------------------------------------------------- CRUD
def _row_from_db(r) -> dict:
    return dict(key=r[0], config_encrypted=r[1], enabled=r[2],
                updated_at=r[3].isoformat() if r[3] else None, updated_by=r[4],
                last_test_status=r[5], last_test_detail=r[6],
                last_test_at=r[7].isoformat() if r[7] else None)


def load_all() -> dict[str, dict]:
    conn = _pg()
    if conn is None:
        with _lock:
            return {k: dict(v) for k, v in _mem.items()}
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT key, config_encrypted, enabled, updated_at, updated_by,"
                        f" last_test_status, last_test_detail, last_test_at FROM {_TABLE}")
            return {r[0]: _row_from_db(r) for r in cur.fetchall()}
    finally:
        conn.close()


def get_config(key: str) -> Optional[dict]:
    """Decrypted config for live clients. None when unset or disabled."""
    row = load_all().get(key)
    if not row or not row.get("enabled", True):
        return None
    try:
        return _decrypt(row["config_encrypted"])
    except Exception:
        return None   # key rotated / corrupt — treat as unconfigured

def save(key: str, incoming: dict, updated_by: str) -> dict:
    """Merge-and-save: blank secret fields keep their stored value."""
    if key not in INTEGRATIONS:
        raise KeyError(key)
    secrets = secret_field_names(key)
    current = {}
    row = load_all().get(key)
    if row:
        try:
            current = _decrypt(row["config_encrypted"])
        except Exception:
            current = {}
    merged = dict(current)
    for f in INTEGRATIONS[key]["fields"]:
        name = f["name"]
        if name not in incoming:
            continue
        val = incoming[name]
        if name in secrets and (val is None or val == ""):
            continue                     # blank secret = keep existing
        merged[name] = val
    now = datetime.now(timezone.utc)
    enc = _encrypt(merged)
    new_row = dict(key=key, config_encrypted=enc, enabled=row["enabled"] if row else True,
                   updated_at=now.isoformat(), updated_by=updated_by,
                   last_test_status="never", last_test_detail="", last_test_at=None)
    conn = _pg()
    if conn is None:
        with _lock:
            _mem[key] = new_row
        return new_row
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {_TABLE} (key, config_encrypted, enabled, updated_at, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                      config_encrypted = EXCLUDED.config_encrypted,
                      updated_at = EXCLUDED.updated_at,
                      updated_by = EXCLUDED.updated_by,
                      last_test_status = 'never', last_test_detail = '', last_test_at = NULL""",
                (key, enc, new_row["enabled"], now, updated_by),
            )
        return new_row
    finally:
        conn.close()


def set_enabled(key: str, enabled: bool, updated_by: str) -> None:
    conn = _pg()
    if conn is None:
        with _lock:
            if key in _mem:
                _mem[key]["enabled"] = enabled
        return
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {_TABLE} SET enabled=%s, updated_at=%s, updated_by=%s WHERE key=%s",
                        (enabled, datetime.now(timezone.utc), updated_by, key))
    finally:
        conn.close()


def delete(key: str) -> None:
    conn = _pg()
    if conn is None:
        with _lock:
            _mem.pop(key, None)
        return
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_TABLE} WHERE key=%s", (key,))
    finally:
        conn.close()


def record_test(key: str, ok: bool, detail: str) -> None:
    now = datetime.now(timezone.utc)
    status = "ok" if ok else "failed"
    conn = _pg()
    if conn is None:
        with _lock:
            if key in _mem:
                _mem[key].update(last_test_status=status, last_test_detail=detail[:500],
                                 last_test_at=now.isoformat())
        return
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {_TABLE} SET last_test_status=%s, last_test_detail=%s,"
                        f" last_test_at=%s WHERE key=%s", (status, detail[:500], now, key))
    finally:
        conn.close()
