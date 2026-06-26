"""Pure unit tests for stateless service helpers (no database/network).

Covers:
- ``app.migration.mapper`` — Jira REST JSON -> Trackly kwargs transforms.
- ``app.services.mail.build_message`` — MIME message construction.
- ``app.services.auth_providers.entra.authorize_url`` — OIDC auth-code URL.
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.migration import mapper
from app.services.auth_providers.entra import authorize_url
from app.services.mail import build_message


# ===========================================================================
# migration.mapper — status category
# ===========================================================================
@pytest.mark.parametrize(
    "jira_key,expected",
    [
        ("new", "todo"),
        ("New", "todo"),
        ("undefined", "todo"),
        ("indeterminate", "in_progress"),
        ("done", "done"),
        ("DONE", "done"),
        (None, "todo"),
        ("", "todo"),
        ("something-unknown", "todo"),
    ],
)
def test_map_status_category(jira_key, expected):
    assert mapper.map_status_category(jira_key) == expected


# ===========================================================================
# migration.mapper — priority rank
# ===========================================================================
@pytest.mark.parametrize(
    "name,rank",
    [
        ("Highest", 1),
        ("Blocker", 1),
        ("Critical", 1),
        ("High", 2),
        ("Major", 2),
        ("Medium", 3),
        ("Normal", 3),
        ("Low", 4),
        ("Lowest", 5),
        ("Trivial", 5),
        ("  High  ", 2),  # trimmed + case-folded
        (None, 3),         # default
        ("Wildcard", 3),   # unknown -> default
    ],
)
def test_map_priority_rank(name, rank):
    assert mapper.map_priority_rank(name) == rank


# ===========================================================================
# migration.mapper — user mapping
# ===========================================================================
def test_map_user_none_and_empty():
    assert mapper.map_user(None) is None
    assert mapper.map_user({}) is None
    # No accountId/key/name -> cannot identify -> None.
    assert mapper.map_user({"displayName": "No Id"}) is None


def test_map_user_cloud_picks_display_name_and_email_username():
    out = mapper.map_user(
        {
            "accountId": "abc-123",
            "displayName": "Jane Doe",
            "emailAddress": "jane@corp.com",
            "avatarUrls": {"48x48": "https://cdn/avatar48", "24x24": "https://cdn/avatar24"},
        }
    )
    assert out["external_id"] == "abc-123"
    assert out["display_name"] == "Jane Doe"
    assert out["email"] == "jane@corp.com"
    # No login name -> username derived from the email local-part.
    assert out["username"] == "jane"
    assert out["avatar_url"] == "https://cdn/avatar48"


def test_map_user_server_synthesizes_email_when_missing():
    out = mapper.map_user({"name": "jsmith", "key": "jsmith", "displayName": "John Smith"})
    assert out["external_id"] == "jsmith"
    assert out["username"] == "jsmith"        # Server/DC login name
    assert out["display_name"] == "John Smith"
    assert out["email"] == "jsmith@imported.local"   # synthesized
    assert out["avatar_url"] is None


def test_map_user_minimal_falls_back_to_external_id_for_display():
    out = mapper.map_user({"name": "bob"})
    assert out["external_id"] == "bob"
    assert out["username"] == "bob"
    assert out["display_name"] == "bob"        # falls back to name
    assert out["email"] == "bob@imported.local"


# ===========================================================================
# migration.mapper — ADF flattening
# ===========================================================================
def test_adf_plain_string_passthrough():
    assert mapper.adf_to_text("just a plain string") == "just a plain string"


def test_adf_none_and_non_dict():
    assert mapper.adf_to_text(None) == ""
    assert mapper.adf_to_text(12345) == "12345"


def test_adf_paragraphs_bullets_and_code_block():
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]},
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "item one"}]}
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "item two"}]}
                        ],
                    },
                ],
            },
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print('hi')"}],
            },
        ],
    }
    text = mapper.adf_to_text(adf)
    assert "Hello world" in text
    assert "- item one" in text
    assert "- item two" in text
    assert "```python" in text
    assert "print('hi')" in text
    # No runaway blank lines.
    assert "\n\n\n" not in text


def test_adf_heading_and_ordered_list():
    adf = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Title"}]},
            {
                "type": "orderedList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "first"}]}
                        ],
                    }
                ],
            },
        ],
    }
    text = mapper.adf_to_text(adf)
    assert "## Title" in text
    assert "1. first" in text


# ===========================================================================
# mail.build_message
# ===========================================================================
def test_build_message_has_headers_and_both_parts():
    msg = build_message(
        from_name="Trackly Bot",
        from_address="bot@trackly.io",
        to=["a@example.com", "b@example.com"],
        subject="Issue assigned",
        body_text="plain body",
        body_html="<p>html body</p>",
    )
    assert msg["Subject"] == "Issue assigned"
    assert "bot@trackly.io" in msg["From"]
    assert "Trackly Bot" in msg["From"]
    assert msg["To"] == "a@example.com, b@example.com"

    parts = msg.get_payload()
    content_types = {p.get_content_type() for p in parts}
    assert content_types == {"text/plain", "text/html"}
    plain = next(p for p in parts if p.get_content_type() == "text/plain")
    html = next(p for p in parts if p.get_content_type() == "text/html")
    assert "plain body" in plain.get_payload(decode=True).decode()
    assert "html body" in html.get_payload(decode=True).decode()


def test_build_message_text_only_has_single_part():
    msg = build_message(
        from_name="",
        from_address="bot@trackly.io",
        to=["solo@example.com"],
        subject="Plain only",
        body_text="just text",
    )
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain"]
    # Empty from_name falls back to the literal "Trackly".
    assert "Trackly" in msg["From"]


# ===========================================================================
# entra.authorize_url
# ===========================================================================
def _provider(**overrides):
    base = dict(
        entra_tenant_id="tenant-123",
        entra_client_id="client-abc",
        entra_redirect_uri="https://app.example.com/auth/callback",
        entra_scopes="User.Read",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_authorize_url_contains_core_params():
    url = authorize_url(_provider(), "state-xyz")
    parsed = urlparse(url)
    # Tenant is embedded in the path.
    assert "tenant-123" in parsed.path
    assert parsed.path.endswith("/oauth2/v2.0/authorize")

    q = parse_qs(parsed.query)
    assert q["client_id"] == ["client-abc"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://app.example.com/auth/callback"]
    assert q["state"] == ["state-xyz"]


def test_authorize_url_forces_oidc_scopes():
    # Custom scopes that omit the required OIDC ones get them appended.
    url = authorize_url(_provider(entra_scopes="User.Read"), "s")
    q = parse_qs(urlparse(url).query)
    scopes = q["scope"][0].split()
    for required in ("openid", "profile", "email"):
        assert required in scopes
    assert "User.Read" in scopes


def test_authorize_url_defaults_tenant_to_common():
    url = authorize_url(_provider(entra_tenant_id=None), "s")
    assert "/common/oauth2/v2.0/authorize" in url
