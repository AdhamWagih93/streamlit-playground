"""External authentication/authorization providers (LDAP + Microsoft Entra ID).

Submodules:
- ``ldap``: bind-and-search authentication against an LDAP/AD server.
- ``entra``: OIDC authorization-code flow against Microsoft Entra ID.
- ``directory``: provider-agnostic orchestration (JIT user provisioning, group
  sync, token issuance, enabled-provider lookup).

Third-party clients (``ldap3``, ``httpx``) are imported lazily inside the
functions that need them so importing this package never hard-requires them.
"""
