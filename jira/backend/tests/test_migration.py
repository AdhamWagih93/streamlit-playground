"""Pure-unit tests for the migration package (no DB, no network).

Covers:
- ``app.migration.config.MigrationConfig`` — env reading, validation, the
  ``is_server`` property and the DB-connection constructor (which decrypts an
  encrypted token via ``app.core.crypto``).
- ``app.migration.mapper`` — additional ADF shapes, full status-category branch
  coverage, user-mapping edge cases and priority ranking.

Nothing here touches PostgreSQL: ``from_connection`` only needs ``SECRET_KEY``
(used to derive the Fernet key) which the test environment always provides.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.crypto import encrypt
from app.migration import mapper
from app.migration.config import MigrationConfig


# ===========================================================================
# MigrationConfig.from_env
# ===========================================================================
# All JIRA_* env vars the config reads; cleared before each test so a host
# environment never leaks into assertions.
_JIRA_ENV = [
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_AUTH_MODE",
    "JIRA_PROJECT_KEYS",
    "JIRA_JQL",
    "JIRA_VERIFY_SSL",
]


@pytest.fixture
def clean_env(monkeypatch):
    for key in _JIRA_ENV:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_from_env_reads_all_fields(clean_env):
    clean_env.setenv("JIRA_BASE_URL", "https://jira.example.com/")  # trailing slash trimmed
    clean_env.setenv("JIRA_EMAIL", "  bot@example.com  ")           # whitespace trimmed
    clean_env.setenv("JIRA_API_TOKEN", "tok-123")
    clean_env.setenv("JIRA_AUTH_MODE", "SERVER")                    # lower-cased
    clean_env.setenv("JIRA_PROJECT_KEYS", "ABC, DEF ,GHI")          # split + trimmed
    clean_env.setenv("JIRA_JQL", "status = Done")
    clean_env.setenv("JIRA_VERIFY_SSL", "false")

    cfg = MigrationConfig.from_env()
    assert cfg.base_url == "https://jira.example.com"
    assert cfg.email == "bot@example.com"
    assert cfg.api_token == "tok-123"
    assert cfg.auth_mode == "server"
    assert cfg.project_keys == ["ABC", "DEF", "GHI"]
    assert cfg.jql == "status = Done"
    assert cfg.verify_ssl is False


def test_from_env_defaults_when_unset(clean_env):
    cfg = MigrationConfig.from_env()
    assert cfg.base_url == ""
    assert cfg.email == ""
    assert cfg.api_token == ""
    assert cfg.auth_mode == "cloud"        # default
    assert cfg.project_keys == []
    assert cfg.jql == ""
    assert cfg.verify_ssl is True          # default when unset


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True), ("y", True),
     ("0", False), ("no", False), ("off", False), ("anything", False)],
)
def test_from_env_verify_ssl_truthiness(clean_env, raw, expected):
    clean_env.setenv("JIRA_VERIFY_SSL", raw)
    assert MigrationConfig.from_env().verify_ssl is expected


# ===========================================================================
# MigrationConfig.is_server property
# ===========================================================================
def test_is_server_property():
    assert MigrationConfig(auth_mode="server").is_server is True
    assert MigrationConfig(auth_mode="cloud").is_server is False
    assert MigrationConfig().is_server is False  # default is cloud


# ===========================================================================
# MigrationConfig.validate
# ===========================================================================
def test_validate_ok_for_complete_cloud_config():
    cfg = MigrationConfig(
        base_url="https://x.atlassian.net", email="a@b.com", api_token="t", auth_mode="cloud"
    )
    cfg.validate()  # must not raise


def test_validate_ok_for_complete_server_config_without_email():
    cfg = MigrationConfig(base_url="https://jira", api_token="pat", auth_mode="server")
    cfg.validate()  # server (PAT) auth needs no email


def test_validate_raises_on_missing_base_url():
    cfg = MigrationConfig(base_url="", email="a@b.com", api_token="t", auth_mode="cloud")
    with pytest.raises(ValueError) as exc:
        cfg.validate()
    assert "JIRA_BASE_URL" in str(exc.value)


def test_validate_raises_on_missing_token():
    cfg = MigrationConfig(base_url="https://x", email="a@b.com", api_token="", auth_mode="cloud")
    with pytest.raises(ValueError) as exc:
        cfg.validate()
    assert "JIRA_API_TOKEN" in str(exc.value)


def test_validate_raises_on_missing_email_for_cloud():
    cfg = MigrationConfig(base_url="https://x", email="", api_token="t", auth_mode="cloud")
    with pytest.raises(ValueError) as exc:
        cfg.validate()
    assert "JIRA_EMAIL" in str(exc.value)


def test_validate_aggregates_multiple_problems():
    cfg = MigrationConfig(base_url="", email="", api_token="", auth_mode="cloud")
    with pytest.raises(ValueError) as exc:
        cfg.validate()
    msg = str(exc.value)
    assert "JIRA_BASE_URL" in msg and "JIRA_API_TOKEN" in msg and "JIRA_EMAIL" in msg


# ===========================================================================
# MigrationConfig.from_connection (decrypts the stored token)
# ===========================================================================
def test_from_connection_decrypts_token_and_maps_fields():
    conn = SimpleNamespace(
        base_url="https://jira.example.com/",
        email="  admin@example.com  ",
        auth_mode="SERVER",
        verify_ssl=False,
        api_token_enc=encrypt("super-secret-pat"),
    )
    cfg = MigrationConfig.from_connection(conn)
    assert cfg.base_url == "https://jira.example.com"
    assert cfg.email == "admin@example.com"
    assert cfg.api_token == "super-secret-pat"   # round-tripped through Fernet
    assert cfg.auth_mode == "server"
    assert cfg.is_server is True
    assert cfg.verify_ssl is False


def test_from_connection_handles_blank_token_and_defaults():
    conn = SimpleNamespace(
        base_url="https://x",
        email=None,
        auth_mode="",          # falls back to "cloud"
        verify_ssl=True,
        api_token_enc=None,    # decrypt(None) -> None -> ""
    )
    cfg = MigrationConfig.from_connection(conn)
    assert cfg.auth_mode == "cloud"
    assert cfg.email == ""
    assert cfg.api_token == ""
    assert cfg.verify_ssl is True


# ===========================================================================
# mapper.map_status_category — every branch
# ===========================================================================
@pytest.mark.parametrize(
    "key,expected",
    [
        ("new", "todo"),
        ("undefined", "todo"),
        ("indeterminate", "in_progress"),
        ("done", "done"),
        ("DONE", "done"),          # case-insensitive
        ("Indeterminate", "in_progress"),
        ("totally-bogus", "todo"), # unknown -> todo
        (None, "todo"),
        ("", "todo"),
    ],
)
def test_map_status_category_branches(key, expected):
    assert mapper.map_status_category(key) == expected


# ===========================================================================
# mapper.map_priority_rank
# ===========================================================================
@pytest.mark.parametrize(
    "name,rank",
    [
        ("Highest", 1), ("Blocker", 1), ("Critical", 1),
        ("High", 2), ("Major", 2),
        ("Medium", 3), ("Normal", 3),
        ("Low", 4), ("Minor", 4),
        ("Lowest", 5), ("Trivial", 5),
        ("  hIgH  ", 2),     # trimmed + folded
        (None, 3),           # default
        ("Spicy", 3),        # unknown -> default
        ("", 3),
    ],
)
def test_map_priority_rank(name, rank):
    assert mapper.map_priority_rank(name) == rank


# ===========================================================================
# mapper.map_user — edge cases
# ===========================================================================
def test_map_user_none_and_unidentifiable():
    assert mapper.map_user(None) is None
    assert mapper.map_user({}) is None
    # Has a display name but nothing that identifies the account -> None.
    assert mapper.map_user({"displayName": "Anon", "emailAddress": "a@b.com"}) is None


def test_map_user_missing_display_name_falls_back_to_name():
    out = mapper.map_user({"name": "jdoe"})
    assert out["display_name"] == "jdoe"        # no displayName -> uses name
    assert out["username"] == "jdoe"
    assert out["email"] == "jdoe@imported.local"  # synthesized


def test_map_user_missing_display_name_falls_back_to_external_id():
    out = mapper.map_user({"accountId": "acc-77", "emailAddress": "x@y.com"})
    assert out["external_id"] == "acc-77"
    assert out["display_name"] == "acc-77"      # no displayName/name -> external id
    assert out["username"] == "x"               # derived from email local-part


def test_map_user_missing_email_is_synthesized_from_external_id():
    out = mapper.map_user({"accountId": "Weird Id!", "displayName": "W"})
    assert out["email"].endswith("@imported.local")
    # The slug strips non-alphanumerics and lower-cases.
    assert out["email"] == "weird-id@imported.local"


def test_map_user_prefers_largest_avatar():
    out = mapper.map_user(
        {"name": "p", "avatarUrls": {"24x24": "small", "48x48": "big"}}
    )
    assert out["avatar_url"] == "big"


def test_map_user_avatar_falls_back_to_any_when_no_48():
    out = mapper.map_user({"name": "p", "avatarUrls": {"16x16": "tiny"}})
    assert out["avatar_url"] == "tiny"


def test_map_user_no_avatars_is_none():
    out = mapper.map_user({"name": "p"})
    assert out["avatar_url"] is None


# ===========================================================================
# mapper.adf_to_text — additional shapes
# ===========================================================================
def test_adf_nested_lists_indent():
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "outer"}]},
                            {
                                "type": "bulletList",
                                "content": [
                                    {
                                        "type": "listItem",
                                        "content": [
                                            {"type": "paragraph",
                                             "content": [{"type": "text", "text": "inner"}]}
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }
    text = mapper.adf_to_text(adf)
    assert "outer" in text
    assert "inner" in text


def test_adf_heading_levels():
    adf = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 3},
             "content": [{"type": "text", "text": "Deep"}]},
        ],
    }
    assert "### Deep" in mapper.adf_to_text(adf)


def test_adf_blockquote_prefixes_lines():
    adf = {
        "type": "doc",
        "content": [
            {"type": "blockquote",
             "content": [{"type": "paragraph", "content": [{"type": "text", "text": "quoted line"}]}]},
        ],
    }
    text = mapper.adf_to_text(adf)
    assert "> quoted line" in text


def test_adf_hard_break_inserts_newline():
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "line one"},
                {"type": "hardBreak"},
                {"type": "text", "text": "line two"},
            ]},
        ],
    }
    text = mapper.adf_to_text(adf)
    assert "line one\nline two" in text


def test_adf_rule_and_mention_and_inline_card():
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [
                {"type": "mention", "attrs": {"text": "@jane"}},
                {"type": "text", "text": " see "},
                {"type": "inlineCard", "attrs": {"url": "https://link.example/x"}},
            ]},
            {"type": "rule"},
        ],
    }
    text = mapper.adf_to_text(adf)
    assert "@jane" in text
    assert "https://link.example/x" in text
    assert "---" in text


def test_adf_unknown_node_descends_into_content():
    # An unrecognised wrapper node should still surface its inner text.
    adf = {
        "type": "doc",
        "content": [
            {"type": "panel", "attrs": {"panelType": "info"}, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "inside panel"}]}
            ]},
        ],
    }
    assert "inside panel" in mapper.adf_to_text(adf)


def test_adf_unknown_leaf_without_content_yields_nothing():
    # Unknown node with no content contributes no text and does not raise.
    adf = {"type": "doc", "content": [{"type": "emoji", "attrs": {"shortName": ":smile:"}}]}
    assert mapper.adf_to_text(adf) == ""


def test_adf_plain_and_non_dict_inputs():
    assert mapper.adf_to_text("plain") == "plain"
    assert mapper.adf_to_text(None) == ""
    assert mapper.adf_to_text(42) == "42"
