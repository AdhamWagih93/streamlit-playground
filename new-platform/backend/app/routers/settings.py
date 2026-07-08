from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth.rbac import User
from ..auth.sessions import admin_user, current_user
from ..config import get_settings
from ..integrations import store
from ..integrations.probes import probe
from ..integrations.registry import FEATURES, INTEGRATIONS, secret_field_names

router = APIRouter(prefix="/settings", tags=["settings"])


def _masked_config(key: str, row: dict | None) -> dict:
    """Field values for the form: secrets come back only as {set: true} markers."""
    out: dict = {}
    if not row:
        return out
    cfg = store.get_config(key) if row.get("enabled", True) else None
    if cfg is None:
        # disabled or undecryptable — still show which fields exist
        try:
            cfg = store._decrypt(row["config_encrypted"])  # masked below, never returned raw
        except Exception:
            return {}
    secrets = secret_field_names(key)
    for f in INTEGRATIONS[key]["fields"]:
        name = f["name"]
        if name not in cfg:
            continue
        out[name] = {"set": bool(cfg[name])} if name in secrets else cfg[name]
    return out


def _integration_status(key: str, rows: dict) -> dict:
    meta = INTEGRATIONS[key]
    row = rows.get(key)
    required = [f["name"] for f in meta["fields"] if f.get("required")]
    cfg = store.get_config(key) or {}
    configured = bool(row) and all(cfg.get(r) not in (None, "") for r in required)
    used_by = [f["label"] for f in FEATURES if key in f["requires"]]
    optional_for = [f["label"] for f in FEATURES if key in f["optional"]]
    return dict(
        key=key, role=meta["role"], tool=meta["tool"], glyph=meta["glyph"],
        fields=meta["fields"],
        config=_masked_config(key, row),
        configured=configured,
        enabled=row.get("enabled", True) if row else True,
        updated_at=row.get("updated_at") if row else None,
        updated_by=row.get("updated_by") if row else None,
        last_test_status=row.get("last_test_status", "never") if row else "never",
        last_test_detail=row.get("last_test_detail", "") if row else "",
        last_test_at=row.get("last_test_at") if row else None,
        used_by=used_by, optional_for=optional_for,
    )


@router.get("/integrations")
def list_integrations(user: User = Depends(admin_user)):
    s = get_settings()
    rows = store.load_all()
    return {
        "storage": store.storage_status(),
        "data_mode": s.data_mode,
        "postgres": {
            "role": "Platform database", "tool": "PostgreSQL", "glyph": "▣",
            "configured": bool(s.database_url),
            "source": "environment (.env / deployment)",
            "detail": store.storage_status()["detail"],
        },
        "integrations": [_integration_status(k, rows) for k in INTEGRATIONS],
    }


class SaveBody(BaseModel):
    config: dict


@router.put("/integrations/{key}")
def save_integration(key: str, body: SaveBody, user: User = Depends(admin_user)):
    if key not in INTEGRATIONS:
        raise HTTPException(status_code=404, detail=f"Unknown integration '{key}'")
    store.save(key, body.config, updated_by=user.username)
    rows = store.load_all()
    return _integration_status(key, rows)


class EnableBody(BaseModel):
    enabled: bool


@router.post("/integrations/{key}/enabled")
def toggle_integration(key: str, body: EnableBody, user: User = Depends(admin_user)):
    if key not in INTEGRATIONS:
        raise HTTPException(status_code=404, detail=f"Unknown integration '{key}'")
    store.set_enabled(key, body.enabled, updated_by=user.username)
    return {"ok": True, "enabled": body.enabled}


@router.delete("/integrations/{key}")
def delete_integration(key: str, user: User = Depends(admin_user)):
    if key not in INTEGRATIONS:
        raise HTTPException(status_code=404, detail=f"Unknown integration '{key}'")
    store.delete(key)
    return {"ok": True}


@router.post("/integrations/{key}/test")
def test_integration(key: str, user: User = Depends(admin_user)):
    if key not in INTEGRATIONS:
        raise HTTPException(status_code=404, detail=f"Unknown integration '{key}'")
    cfg = store.get_config(key)
    if not cfg:
        raise HTTPException(status_code=400, detail="Integration is not configured (or disabled)")
    ok, detail = probe(key, cfg)
    store.record_test(key, ok, detail)
    return {"ok": ok, "detail": detail}


@router.get("/requirements")
def requirements(user: User = Depends(current_user)):
    """Per-feature integration requirements + live availability.
    Available to every signed-in user — it drives the per-page strips."""
    s = get_settings()
    rows = store.load_all()

    def available(key: str) -> bool:
        row = rows.get(key)
        if not row or not row.get("enabled", True):
            return False
        cfg = store.get_config(key) or {}
        required = [f["name"] for f in INTEGRATIONS[key]["fields"] if f.get("required")]
        return all(cfg.get(r) not in (None, "") for r in required)

    integ_state = {k: available(k) for k in INTEGRATIONS}
    features = []
    for f in FEATURES:
        missing = [k for k in f["requires"] if not integ_state[k]]
        missing_optional = [k for k in f["optional"] if not integ_state[k]]
        features.append(dict(
            key=f["key"], label=f["label"], route=f["route"],
            requires=f["requires"], optional=f["optional"],
            missing=missing, missing_optional=missing_optional,
            available=(s.data_mode == "demo") or not missing,
        ))
    names = {k: {"role": v["role"], "tool": v["tool"], "glyph": v["glyph"]}
             for k, v in INTEGRATIONS.items()}
    return {"data_mode": s.data_mode, "integrations": names,
            "state": integ_state, "features": features}
