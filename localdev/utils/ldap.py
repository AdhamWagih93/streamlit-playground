"""Fake LDAP module for local/CI testing.

The dashboard imports ``utils.ldap`` opportunistically (guarded) for live LDAP
lookups; the authoritative member data normally comes from Postgres
(``ldap_users`` / ``ldap_team_members``). With PG unconfigured locally, these
return empty and the Teams tab shows its "no synced users" state. Provide your
own implementation here if you want to simulate LDAP rosters.
"""

from __future__ import annotations


def search_users(*args, **kwargs):
    return []


def get_user(*args, **kwargs):
    return None
