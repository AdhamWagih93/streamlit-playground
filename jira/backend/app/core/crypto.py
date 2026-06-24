"""Symmetric encryption for secrets stored at rest.

Credentials configured through the admin UI (SMTP password, Jira API tokens,
LDAP bind password, Entra client secret) are encrypted before they touch the
database. The Fernet key is derived from SECRET_KEY so no extra key management
is required for a basic deployment — but rotating SECRET_KEY invalidates all
stored secrets, which is the intended, safe behaviour.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string, returning a prefixed token. None/empty pass through."""
    if plaintext is None or plaintext == "":
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value: str | None) -> str | None:
    """Decrypt a token produced by :func:`encrypt`.

    Values without the marker prefix are returned unchanged so existing
    plaintext (e.g. migrated config) keeps working.
    """
    if not value or not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(_PREFIX)
