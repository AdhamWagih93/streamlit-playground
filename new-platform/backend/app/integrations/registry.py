"""Integration registry — the single source of truth for:
  * which external integrations exist (described by role, tool named second)
  * their config fields (secret fields are never echoed back)
  * how to probe each one (connection test)
  * which platform features depend on which integrations

Postgres is deliberately NOT here: it is the platform's own database, configured via
.env / deployment environment, and is the store this registry's configs live in.
"""
from __future__ import annotations

FIELD = dict  # {name, label, type: text|password|url|number|bool, required: bool, default}

INTEGRATIONS: dict[str, dict] = {
    "elasticsearch": {
        "role": "Search & analytics store",
        "tool": "Elasticsearch",
        "glyph": "◌",
        "fields": [
            FIELD(name="hosts", label="Hosts (comma-separated)", type="url", required=True),
            FIELD(name="api_key", label="API key", type="password", required=False),
            FIELD(name="username", label="Username (basic auth)", type="text", required=False),
            FIELD(name="password", label="Password", type="password", required=False),
            FIELD(name="verify_certs", label="Verify TLS certificates", type="bool", required=False,
                  default=False),
        ],
    },
    "jenkins": {
        "role": "Pipeline orchestrator",
        "tool": "Jenkins",
        "glyph": "▶",
        "fields": [
            FIELD(name="host", label="API base URL", type="url", required=True),
            FIELD(name="public_name", label="Public URL (links)", type="url", required=False),
            FIELD(name="username", label="Username", type="text", required=True),
            FIELD(name="api_token", label="API token", type="password", required=True),
        ],
    },
    "ado": {
        "role": "Source control & inventory host",
        "tool": "Azure DevOps Server",
        "glyph": "❖",
        "fields": [
            FIELD(name="host", label="Server URL", type="url", required=True),
            FIELD(name="username", label="Username (git)", type="text", required=False),
            FIELD(name="password", label="Password / PAT (git)", type="password", required=True),
            FIELD(name="token", label="REST API token (coverage walk)", type="password",
                  required=False),
            FIELD(name="collection", label="Config collection", type="text", required=False,
                  default="DevOps"),
            FIELD(name="project", label="Config project", type="text", required=False,
                  default="Control"),
        ],
    },
    "s3": {
        "role": "Scan report store",
        "tool": "S3 / MinIO",
        "glyph": "▤",
        "fields": [
            FIELD(name="host", label="Endpoint host", type="text", required=True),
            FIELD(name="port", label="Port", type="number", required=True, default=9000),
            FIELD(name="access_key", label="Access key", type="password", required=True),
            FIELD(name="secret_key", label="Secret key", type="password", required=True),
            FIELD(name="bucket", label="Reports bucket", type="text", required=False,
                  default="PrismaCloud-Logs"),
            FIELD(name="key_pattern", label="Object key pattern", type="text", required=False,
                  default="{project}/{application}_{version}-PrismaCloudLog.txt"),
        ],
    },
    "ldap_directory": {
        "role": "Identity directory (roster sync)",
        "tool": "LDAP / Active Directory",
        "glyph": "◎",
        "fields": [
            FIELD(name="url", label="Directory URL (ldaps://…)", type="url", required=True),
            FIELD(name="bind_dn", label="Service bind DN", type="text", required=True),
            FIELD(name="bind_password", label="Service bind password", type="password",
                  required=True),
            FIELD(name="user_search_base", label="User search base", type="text", required=True),
        ],
    },
    "ollama": {
        "role": "On-prem model runtime",
        "tool": "Ollama",
        "glyph": "✦",
        "fields": [
            FIELD(name="url", label="Runtime URL", type="url", required=True),
            FIELD(name="model", label="Model", type="text", required=True, default="qwen3.5:9b"),
        ],
    },
    "vault": {
        "role": "Secrets vault (optional broker)",
        "tool": "HashiCorp Vault",
        "glyph": "🔒",
        "fields": [
            FIELD(name="addr", label="Vault address", type="url", required=True),
            FIELD(name="token", label="Token", type="password", required=True),
        ],
    },
}

SECRET_TYPES = {"password"}


def secret_field_names(key: str) -> set[str]:
    return {f["name"] for f in INTEGRATIONS[key]["fields"] if f["type"] in SECRET_TYPES}


# ---- feature → integration dependencies -------------------------------------
# route: frontend path whose page hosts the feature (drives the per-page strip).
FEATURES: list[dict] = [
    dict(key="overview", label="Platform overview & live events", route="/",
         requires=["elasticsearch"], optional=[]),
    dict(key="fleet", label="Delivery fleet inventory", route="/fleet",
         requires=["elasticsearch"], optional=["ado"]),
    dict(key="events", label="Event log", route="/events",
         requires=["elasticsearch"], optional=[]),
    dict(key="actions", label="Pipeline actions & orchestrator status", route="/actions",
         requires=["jenkins", "elasticsearch"], optional=[]),
    dict(key="security", label="Security posture (scan summaries)", route="/security",
         requires=["elasticsearch"], optional=[]),
    dict(key="security_reports", label="Full scan report viewer", route="/security",
         requires=["s3"], optional=[]),
    dict(key="incidents", label="AI incident analysis", route="/incidents",
         requires=["elasticsearch", "ollama"], optional=["jenkins"]),
    dict(key="assistant", label="Knowledge assistant", route="/assistant",
         requires=["ollama"], optional=["ado"]),
    dict(key="architecture", label="Environment architecture & discovery", route="/architecture",
         requires=["ado"], optional=["elasticsearch", "ollama"]),
    dict(key="technology", label="Tech & platform analytics", route="/technology",
         requires=["elasticsearch"], optional=[]),
    dict(key="teams", label="Teams & members roster", route="/teams",
         requires=["ldap_directory"], optional=[]),
    dict(key="people", label="People insights", route="/people",
         requires=["elasticsearch"], optional=["ldap_directory"]),
    dict(key="governance", label="Governance (sync, coverage, audit, history)", route="/governance",
         requires=["elasticsearch", "ado"], optional=["ldap_directory", "vault"]),
]
