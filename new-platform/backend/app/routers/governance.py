from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.rbac import User
from ..auth.sessions import admin_user
from ..providers import impl

router = APIRouter(prefix="/governance", tags=["governance"])


# ------------------------------------------------------------------ sync checks
@router.get("/sync/inventory")
def sync_inventory(user: User = Depends(admin_user)):
    return impl("governance").sync_inventory(user)


@router.post("/sync/inventory/run")
def sync_inventory_run(user: User = Depends(admin_user)):
    return impl("governance").sync_inventory_run(user)


@router.get("/sync/postgres")
def sync_postgres(user: User = Depends(admin_user)):
    return impl("governance").sync_postgres(user)


@router.post("/sync/postgres/run")
def sync_postgres_run(user: User = Depends(admin_user)):
    return impl("governance").sync_postgres_run(user)


@router.get("/sync/ldap")
def sync_ldap(user: User = Depends(admin_user)):
    return impl("governance").sync_ldap(user)


@router.post("/sync/ldap/run")
def sync_ldap_run(user: User = Depends(admin_user)):
    return impl("governance").sync_ldap_run(user)


# ------------------------------------------------------------------ ADO coverage
@router.get("/ado-coverage")
def ado_coverage(user: User = Depends(admin_user)):
    return impl("governance").ado_coverage(user)


# ------------------------------------------------------------------ history → PG
@router.get("/history")
def history(user: User = Depends(admin_user)):
    return impl("governance").history(user)


@router.get("/history/tick")
def history_tick(user: User = Depends(admin_user)):
    return impl("governance").history_tick(user)


@router.post("/history/{index_key}/{action}")
def history_action(index_key: str, action: str, user: User = Depends(admin_user)):
    out = impl("governance").history_action(user, index_key, action)
    if out is None:
        raise HTTPException(status_code=404,
                            detail=f"Unknown job '{index_key}' or action '{action}'")
    return out


# ------------------------------------------------------------------ tool access
@router.get("/tool-access")
def tool_access(user: User = Depends(admin_user)):
    return impl("governance").tool_access(user)


# ------------------------------------------------------------------ glossary
@router.get("/glossary")
def glossary(user: User = Depends(admin_user)):
    return impl("meta").glossary(user)
