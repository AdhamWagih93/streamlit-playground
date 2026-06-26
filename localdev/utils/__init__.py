# Local-dev shim of the platform `utils` package.
# Replaces the VPN-only platform SDK (Elasticsearch / Vault / S3 / LDAP / mail)
# with self-contained fakes so the dashboard renders and is testable on a
# laptop with zero access to the real services. See localdev/README.md.
