"""Provider-agnostic orchestration for external directory logins.

Turns the normalized ``info`` dict produced by an auth provider into a real
Trackly :class:`User` (JIT provisioning + group sync), and issues JWT tokens.
"""
from __future__ import annotations

import secrets

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token, hash_password
from app.models import Group, IdentityProvider, User


def find_enabled(db: Session, provider_type: str) -> list[IdentityProvider]:
    """Return enabled providers of *provider_type*, ordered by ``order``."""
    return list(
        db.scalars(
            select(IdentityProvider)
            .where(
                IdentityProvider.enabled.is_(True),
                IdentityProvider.provider_type == provider_type,
            )
            .order_by(IdentityProvider.order.asc(), IdentityProvider.id.asc())
        )
    )


def _sync_groups(db: Session, user: User, provider: IdentityProvider, group_names: list[str]) -> None:
    """Make the user's directory-synced groups match *group_names* exactly.

    Groups are ensured to exist (tagged ``directory_source = provider_type``).
    Manually-assigned groups (and groups synced from a different source) are
    left intact; only this source's synced membership is reconciled.
    """
    source = provider.provider_type
    desired = {n.strip() for n in (group_names or []) if n and n.strip()}

    # Ensure each desired group exists with the right directory source.
    name_to_group: dict[str, Group] = {}
    if desired:
        existing = db.scalars(select(Group).where(Group.name.in_(desired))).all()
        name_to_group = {g.name: g for g in existing}
        for name in desired:
            grp = name_to_group.get(name)
            if grp is None:
                grp = Group(name=name, directory_source=source)
                db.add(grp)
                db.flush()
                name_to_group[name] = grp
            elif grp.directory_source is None:
                # Adopt a previously-manual group of the same name into this source.
                grp.directory_source = source

    # Reconcile membership: drop this-source groups no longer desired, add new.
    current = list(user.groups)
    keep: list[Group] = []
    for grp in current:
        if grp.directory_source == source and grp.name not in desired:
            continue  # remove stale synced membership
        keep.append(grp)
    existing_names = {g.name for g in keep}
    for name in desired:
        if name not in existing_names:
            keep.append(name_to_group[name])
    user.groups = keep


def provision_user(db: Session, provider: IdentityProvider, info: dict) -> User:
    """Find-or-create a user from directory *info* and sync attributes/groups.

    *info* keys: ``username``, ``email``, ``display_name``, optional
    ``dn``/``external_id`` (external directory id), ``groups``.
    """
    external_id = info.get("external_id") or info.get("dn")
    email = (info.get("email") or "").strip() or None
    username = (info.get("username") or "").strip() or None
    display_name = info.get("display_name") or username or email

    user: User | None = None
    if external_id:
        user = db.scalars(
            select(User).where(User.external_directory_id == external_id)
        ).first()
    if user is None and (email or username):
        clauses = []
        if email:
            clauses.append(User.email == email)
        if username:
            clauses.append(User.username == username)
        user = db.scalars(select(User).where(or_(*clauses))).first()

    if user is None:
        if not provider.auto_provision_users:
            raise PermissionError("Auto-provisioning is disabled for this provider")
        user = User(
            username=username or email or external_id or "user",
            email=email or f"{username or external_id}@localhost",
            display_name=display_name or username or "User",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            is_active=True,
            auth_source=provider.provider_type,
            external_directory_id=external_id,
        )
        db.add(user)
        db.flush()
    else:
        # Keep the directory id and attributes fresh.
        if external_id and not user.external_directory_id:
            user.external_directory_id = external_id
        if display_name:
            user.display_name = display_name
        if email:
            user.email = email

    if provider.sync_groups:
        _sync_groups(db, user, provider, info.get("groups") or [])

    db.commit()
    db.refresh(user)
    return user


def issue_tokens(user: User, access_minutes: int | None = None, refresh_minutes: int | None = None) -> dict:
    """Return a ``Token``-shaped dict for *user* with optional custom lifetimes."""
    return {
        "access_token": create_access_token(user.id, minutes=access_minutes),
        "refresh_token": create_refresh_token(user.id, minutes=refresh_minutes),
        "token_type": "bearer",
    }
