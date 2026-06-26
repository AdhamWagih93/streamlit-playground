"""Pure unit tests (no database required)."""
from __future__ import annotations

from app.core.crypto import decrypt, encrypt, is_encrypted
from app.services import permission_keys as P
from app.utils.ranking import initial_rank, rank_between
from app.utils.timetracking import format_duration, parse_duration


def test_rank_between_orders_strictly():
    a = initial_rank()
    after = rank_between(a, None)
    before = rank_between(None, a)
    middle = rank_between(a, after)
    assert before < a < after
    assert a < middle < after


def test_rank_between_many_inserts_stay_ordered():
    # Repeatedly insert between the first two items; order must hold.
    lo, hi = rank_between(None, None), rank_between(rank_between(None, None), None)
    lo, hi = sorted([lo, hi])
    prev = lo
    for _ in range(20):
        mid = rank_between(prev, hi)
        assert prev < mid < hi
        prev = mid


def test_parse_duration():
    assert parse_duration("2h 30m") == 2 * 3600 + 30 * 60
    assert parse_duration("1d") == 8 * 3600
    assert parse_duration("1w") == 5 * 8 * 3600
    assert parse_duration("90") == 90  # bare digits == seconds
    assert parse_duration("") is None
    assert parse_duration(None) is None


def test_format_duration_roundtrip():
    assert format_duration(2 * 3600 + 30 * 60) == "2h 30m"
    assert format_duration(0) == "0m"
    assert parse_duration(format_duration(9000)) == 9000


def test_crypto_roundtrip():
    token = encrypt("super-secret")
    assert token.startswith("enc:v1:")
    assert is_encrypted(token)
    assert decrypt(token) == "super-secret"
    # Plain (unprefixed) values pass through unchanged.
    assert decrypt("plaintext") == "plaintext"
    # Empty / None are not encrypted.
    assert encrypt("") == ""
    assert encrypt(None) is None


def test_permission_keys_are_consistent():
    assert "BROWSE_PROJECTS" in P.PROJECT_PERMISSIONS
    assert "CREATE_ISSUES" in P.PROJECT_PERMISSIONS
    assert P.ADMINISTER in P.GLOBAL_PERMISSIONS
    assert P.ALL_PERMISSIONS == {**P.GLOBAL_PERMISSIONS, **P.PROJECT_PERMISSIONS}
    # Jira holder mapping translates onto known holder types.
    holder_type, _ = P.JIRA_HOLDER_MAP["projectRole"]
    assert holder_type == P.HOLDER_PROJECT_ROLE
