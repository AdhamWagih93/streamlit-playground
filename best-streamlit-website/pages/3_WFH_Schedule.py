from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Dict, List, Sequence, Set

import pandas as pd
import streamlit as st

# --- Optional infrastructure deps -------------------------------------------
# Postgres driver: psycopg v3 preferred, psycopg2 as a fallback (both are
# common in this org). Either works for our simple reads/writes.
try:
    import psycopg as _psycopg  # type: ignore  # v3
    _POSTGRES_AVAILABLE = True
except ImportError:  # pragma: no cover
    try:
        import psycopg2 as _psycopg  # type: ignore  # v2
        _POSTGRES_AVAILABLE = True
    except ImportError:
        _psycopg = None  # type: ignore
        _POSTGRES_AVAILABLE = False

# Platform vault SDK — present where the app is deployed; absent on local / CI
# boxes, in which case the page degrades to an in-memory, read-only schedule.
try:
    from utils.vault import VaultClient as _VaultClient  # type: ignore
    _VAULT_AVAILABLE = True
except Exception:  # pragma: no cover
    _VaultClient = None  # type: ignore
    _VAULT_AVAILABLE = False


TEAM_MEMBERS: List[str] = [
    "Adham",
    "Karam",
    "Hesham",
    "Salma",
    "Zanaty",
]
YEAR = 2026

# New joiner who works fully from the office (every office day) while onboarding,
# then joins the normal 50/50 rotation the day after this date (inclusive cutover).
# Sundays remain work-from-home for everyone, including the new joiner.
NEW_JOINER = "Zanaty"
NEW_JOINER_FULL_OFFICE_UNTIL = date(2026, 8, 14)
WORKDAYS = {6, 0, 1, 2, 3}  # Sunday (6) through Thursday (3) in datetime.weekday()
# Sunday is a work-from-home day for everyone; the office rotation only runs
# on the four office days, Monday (0) through Thursday (3).
OFFICE_WEEKDAYS = {0, 1, 2, 3}  # Mon-Thu
DAILY_OFFICE_MIN, DAILY_OFFICE_MAX = 2, 3  # people in the office on each office day
WEEKLY_WFO = 2                  # office days per member per week (50% of the 4 office days)
WFO_PER_MEMBER_FORTNIGHT = 4    # exactly half of the 8 office days in a 2-week window
# --- Postgres-backed storage (replaces the old per-week / holiday JSON files).
# Credentials resolve from the platform vault, mirroring cicd_dashboard.py.
# The page persists ONLY the rolling two-week window (current + next week)
# in the schedule table; the holiday registers are kept in full. Table names
# are env-overridable but default to dedicated, namespaced WFH tables.
POSTGRES_VAULT_PATH = os.environ.get("WFH_POSTGRES_VAULT_PATH", "postgres").strip()
POSTGRES_CONNECT_TIMEOUT = 10  # seconds
POSTGRES_DATA_TTL = 60         # seconds (vault cache)

WFH_SCHEDULE_TABLE = os.environ.get("WFH_SCHEDULE_TABLE", "wfh_schedule_days").strip()
WFH_PERSONAL_HOLIDAYS_TABLE = os.environ.get(
    "WFH_PERSONAL_HOLIDAYS_TABLE", "wfh_personal_holidays").strip()
WFH_PUBLIC_HOLIDAYS_TABLE = os.environ.get(
    "WFH_PUBLIC_HOLIDAYS_TABLE", "wfh_public_holidays").strip()

# --- Actuals: real attendance derived from the shared `session_states` table.
# A member counts as physically in the office on a given day when at least one
# of their sessions that day came from an on-site IP (client_ip starts with
# the office prefix below). This table is written by the wider platform, not
# by this page, so we only ever READ it.
SESSION_STATES_TABLE = os.environ.get("WFH_SESSION_STATES_TABLE", "session_states").strip()
OFFICE_IP_PREFIX = os.environ.get("WFH_OFFICE_IP_PREFIX", "10.26").strip()

# Maps a team member (as used throughout this page) to their `username` value
# in the session_states table.
MEMBER_TO_SESSION_USER: Dict[str, str] = {
    "Adham": "Adham_Wagih",
    "Karam": "Karam_Mohamed",
    "Hesham": "Hesham_Mostafa",
    "Salma": "Salma_Adel",
    "Zanaty": "Ahmed_Zanaty",
}
SESSION_USER_TO_MEMBER: Dict[str, str] = {v: k for k, v in MEMBER_TO_SESSION_USER.items()}

# Role metadata used for role-based rules
ROLE_BY_MEMBER: Dict[str, str] = {
    "Adham": "mgmt-support",
    "Karam": "mgmt-support",
    "Hesham": "mgmt-support",
    "Salma": "engineering",
    "Zanaty": "engineering",
}

# Personal, soft attendance preferences (validated as warnings only).
# Weekday indices follow datetime.weekday(): Monday=0, ..., Sunday=6.
PREFERS_WFO_DAYS: Dict[str, Set[int]] = {
    # Prefer being in-office on specific weekdays.
    "Hesham": {1, 3},       # Tuesday (1), Thursday (3)
}

DISLIKES_WFO_DAYS: Dict[str, Set[int]] = {
    # Dislike being in-office on specific weekdays.
    "Hesham": {0, 2},       # Monday (0), Wednesday (2)
    "Salma": {3},           # Thursday (3)
}

# Suggested public holidays for Egypt in YEAR (2026) as
# {"YYYY-MM-DD": "Name"}. These seed the public-holiday register
# when no custom file exists.
DEFAULT_PUBLIC_HOLIDAYS: Dict[str, str] = {
    "2026-01-07": "Coptic Christmas Day",
    "2026-01-08": "Coptic Christmas Day (Bridge)",
    "2026-01-29": "Day off for Revolution Day",
    "2026-03-21": "Eid al-Fitr (tentative)",
    "2026-03-22": "Eid al-Fitr Holiday (tentative)",
    "2026-03-23": "Eid al-Fitr Holiday (tentative)",
    "2026-04-13": "Spring Festival",
    "2026-04-25": "Sinai Liberation Day",
    "2026-05-01": "Labour Day",
    "2026-05-26": "Arafat Day (tentative)",
    "2026-05-27": "Eid al-Adha (tentative)",
    "2026-05-28": "Eid al-Adha Holiday (tentative)",
    "2026-05-29": "Eid al-Adha Holiday (tentative)",
    "2026-06-17": "Islamic New Year (Muharram)",
    "2026-07-02": "Day off for June 30 Revolution",
    "2026-07-23": "Revolution Day (July 23)",
    "2026-08-26": "Prophet's Birthday (tentative)",
    "2026-10-08": "Day off for Armed Forces Day",
}


@dataclass
class DayAssignment:
    dt: date
    members_wfo: Set[str]


def is_workday(d: date) -> bool:
    return d.year == YEAR and d.weekday() in WORKDAYS


def is_office_day(d: date) -> bool:
    """An office day is a workday on which the office rotation runs (Mon-Thu).

    Sundays are workdays but are always WFH, so they are never office days.
    """
    return is_workday(d) and d.weekday() in OFFICE_WEEKDAYS


def in_full_office_period(d: date) -> bool:
    """True on/before the new joiner's full-office cutover date.

    During this period the new joiner is in the office on every office day
    (and is exempt from the 50/50 weekly/fortnightly caps and the
    no-3-consecutive-days rule). After it, they join the normal rotation.
    """
    return d <= NEW_JOINER_FULL_OFFICE_UNTIL


def generate_two_week_pattern(rotating=None, forced_office=None) -> List[Set[str]]:
    """Generate a 10-workday (Sun-Thu x2) office pattern.

    ``rotating``      members who follow the 50/50 rule (default: the whole
                      team). Each is WFO on exactly ``WEEKLY_WFO`` (2) of the
                      four office days per week, ``WFO_PER_MEMBER_FORTNIGHT``
                      (4) per fortnight.
    ``forced_office`` members who are in the office on *every* office day and
                      are exempt from the 50/50 caps and the no-3-consecutive
                      rule (used for a new joiner during full-office onboarding).

    Hard rules (Sunday is always WFH for everyone):
    - Each office day (Mon-Thu) has 2-3 people total (forced + chosen).
    - At least one mgmt-support member is in the office every office day.
    - No *rotating* member is WFO 3 days in a row.

    Pairwise "meet in the office at least once" is a soft preference
    (a validation warning), not a hard generator constraint.
    """

    rotating = list(rotating) if rotating is not None else list(TEAM_MEMBERS)
    forced_office = list(forced_office) if forced_office is not None else []

    idx = {name: i for i, name in enumerate(TEAM_MEMBERS)}
    n_team = len(TEAM_MEMBERS)
    r_idx = [idx[m] for m in rotating]          # indices of rotating members
    forced_idx = {idx[m] for m in forced_office}
    mgmt_indices = {idx[m] for m in TEAM_MEMBERS if ROLE_BY_MEMBER.get(m) == "mgmt-support"}

    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"] * 2

    # The "chosen" rotating set plus the always-in forced set must total 2-3.
    min_choose = max(0, DAILY_OFFICE_MIN - len(forced_office))
    max_choose = max(0, DAILY_OFFICE_MAX - len(forced_office))

    all_subsets: List[Sequence[int]] = []
    for rsz in range(min_choose, max_choose + 1):
        all_subsets.extend(list(combinations(r_idx, rsz)))
    random.shuffle(all_subsets)

    # Candidate chosen-sets per day. Sunday: nobody in the office.
    options_per_day: List[List[Sequence[int]]] = []
    for day in range(10):
        wd = weekdays[day]
        if wd == "Sun":
            options_per_day.append([tuple()])
            continue
        day_opts: List[Sequence[int]] = []
        for subset in all_subsets:
            s = set(subset)
            # mgmt-support present via a forced or chosen member
            if not (mgmt_indices & (forced_idx | s)):
                continue
            day_opts.append(subset)
        options_per_day.append(day_opts)

    best_schedule: List[Set[int]] | None = None
    found = False

    def backtrack(
        day: int,
        schedule: List[Set[int]],
        counts: List[int],
        week_counts: List[List[int]],
        streaks: List[int],
    ) -> None:
        nonlocal best_schedule, found
        if found:
            return

        if day == 10:
            if any(counts[p] != WFO_PER_MEMBER_FORTNIGHT for p in r_idx):
                return
            best_schedule = [set(s) for s in schedule]
            found = True
            return

        wd = weekdays[day]
        week_idx = 0 if day < 5 else 1
        remaining_office_days = sum(1 for k in range(day, 10) if weekdays[k] != "Sun")

        candidates = list(options_per_day[day])
        random.shuffle(candidates)

        for subset in candidates:
            s = set(subset)

            new_counts = counts[:]
            new_week = [wk[:] for wk in week_counts]
            feasible = True
            for p in s:
                new_counts[p] += 1
                new_week[week_idx][p] += 1
                if new_counts[p] > WFO_PER_MEMBER_FORTNIGHT or new_week[week_idx][p] > WEEKLY_WFO:
                    feasible = False
                    break
            if not feasible:
                continue

            after_office_days = remaining_office_days - 1
            for p in r_idx:
                if new_counts[p] + after_office_days < WFO_PER_MEMBER_FORTNIGHT:
                    feasible = False
                    break
            if not feasible:
                continue

            # No 3 consecutive WFO for rotating members (forced are exempt)
            new_streaks = streaks[:]
            for p in r_idx:
                if p in s:
                    new_streaks[p] = streaks[p] + 1
                    if new_streaks[p] >= 3:
                        feasible = False
                        break
                else:
                    new_streaks[p] = 0
            if not feasible:
                continue

            schedule.append(s)
            backtrack(day + 1, schedule, new_counts, new_week, new_streaks)
            schedule.pop()
            if found:
                return

    backtrack(0, [], [0] * n_team, [[0] * n_team, [0] * n_team], [0] * n_team)

    if not found or best_schedule is None:
        raise RuntimeError("Unable to find a valid 2-week WFH/WFO pattern with given rules.")

    # Build name sets: Sunday empty; office days = forced ∪ chosen rotating.
    name_pattern: List[Set[str]] = []
    for day, s in enumerate(best_schedule):
        if weekdays[day] == "Sun":
            name_pattern.append(set())
        else:
            name_pattern.append(set(forced_office) | {TEAM_MEMBERS[i] for i in s})
    return name_pattern


def get_anchor_sunday() -> date:
    """Return a Sunday before or on Jan 1st YEAR to anchor the 2-week cycle.

    This lets us repeat the 10-workday pattern across years consistently
    while still only caring about dates within YEAR for display.
    """

    first_of_year = date(YEAR, 1, 1)
    # Move backwards to the most recent Sunday
    offset = (first_of_year.weekday() - 6) % 7
    return first_of_year - timedelta(days=offset)


def build_base_schedule(
    intro_pattern: List[Set[str]],
    normal_pattern: List[Set[str]],
) -> Dict[str, Dict[str, str]]:
    """Build the default schedule for all workdays in YEAR.

    Office days on/before the new joiner's cutover use ``intro_pattern``
    (new joiner full-office, the rest on 50/50); office days after it use
    ``normal_pattern`` (everyone on 50/50). Both patterns are 10-slot
    Sun-Thu x2 rotations tiled by workday index.

    Returns a mapping: iso-date -> {member: "WFO" or "WFH"}.
    """

    anchor = get_anchor_sunday()
    end = date(YEAR, 12, 31)

    workday_index = 0
    d = anchor
    schedule: Dict[str, Dict[str, str]] = {}

    while d <= end:
        if d.weekday() in WORKDAYS:
            pattern = intro_pattern if in_full_office_period(d) else normal_pattern
            members_wfo = pattern[workday_index % len(pattern)]
            if d.year == YEAR:
                schedule[d.isoformat()] = {
                    member: ("WFO" if member in members_wfo else "WFH")
                    for member in TEAM_MEMBERS
                }
            workday_index += 1
        d += timedelta(days=1)

    return schedule


def iter_sundays_in_year() -> List[date]:
    d = date(YEAR, 1, 1)
    # Find first Sunday in YEAR
    while d.weekday() != 6:
        d += timedelta(days=1)
    sundays: List[date] = []
    while d.year == YEAR:
        sundays.append(d)
        d += timedelta(days=7)
    return sundays


# =============================================================================
# POSTGRES STORAGE LAYER
# =============================================================================
# Mirrors the connection pattern in cicd_dashboard.py: vault-resolved creds,
# psycopg v3/v2, idempotent `CREATE TABLE IF NOT EXISTS` schema. Three
# dedicated tables back this page:
#
#   wfh_schedule_days      — (day, member) -> status; holds ONLY the rolling
#                            two-week window (older rows are pruned on load).
#   wfh_personal_holidays  — (day, member) personal days off.
#   wfh_public_holidays    — (day) -> name public holidays.
#
# Every read/write degrades gracefully: when vault or Postgres is unavailable
# (local dev / CI), reads fall back to the rule-based base schedule and the
# default holiday list, and writes become no-ops with a surfaced warning.


@st.cache_data(ttl=POSTGRES_DATA_TTL, show_spinner=False)
def _vault_secrets_raw(path: str) -> dict:
    """Cached vault read. Returns ``{}`` when vault is unavailable. Re-raises
    on a genuine vault error so a transient failure isn't memoised as empty."""
    if not _VAULT_AVAILABLE or not path:
        return {}
    vc = _VaultClient()  # constructor (re)initialises the auth token per call
    cfg = vc.read_all_nested_secrets(path) or {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def _vault_secrets(path: str) -> dict:
    """Public resolver — returns ``{}`` on any error instead of raising."""
    if not _VAULT_AVAILABLE or not path:
        return {}
    try:
        return _vault_secrets_raw(path)
    except Exception:  # noqa: BLE001
        return {}


def _postgres_creds() -> dict:
    """Resolve ``{host, port, database, username, password}`` from vault.
    An empty ``host`` means "not configured" (the caller then degrades)."""
    cfg = _vault_secrets(POSTGRES_VAULT_PATH)
    if not cfg:
        return {}
    return {
        "host":     (cfg.get("host") or "").strip(),
        "port":     str(cfg.get("port") or "5432").strip(),
        "database": (cfg.get("database") or "").strip(),
        "username": (cfg.get("username") or "").strip(),
        "password": (cfg.get("password") or "").strip(),
    }


def _pg_safe_ident(s: str) -> bool:
    """Permissive identifier guard — alphanumerics, underscore and dot only.
    Gates the env-overridable table names before interpolating into DDL/DML."""
    return bool(s) and all(c.isalnum() or c in "_." for c in s)


def _pg_connect():
    """Open a Postgres connection from vault-resolved creds. Raises on any
    misconfiguration *without* attempting a socket connect (so local/CI boxes
    fail fast instead of blocking on a connect timeout)."""
    if not _POSTGRES_AVAILABLE:
        raise RuntimeError("psycopg not installed")
    creds = _postgres_creds()
    if not creds or not creds.get("host"):
        raise RuntimeError("postgres creds not resolved (check vault)")
    try:
        _port = int(creds["port"])
    except (ValueError, TypeError):
        _port = 5432
    return _psycopg.connect(
        host=creds["host"], port=_port, dbname=creds["database"],
        user=creds["username"], password=creds["password"],
        connect_timeout=POSTGRES_CONNECT_TIMEOUT,
    )


def _pg_ensure_schema(conn) -> None:
    """Idempotent schema creation. Cheap on warm runs; safe every cold start."""
    for _name in (WFH_SCHEDULE_TABLE, WFH_PERSONAL_HOLIDAYS_TABLE,
                  WFH_PUBLIC_HOLIDAYS_TABLE):
        if not _pg_safe_ident(_name):
            raise RuntimeError(f"unsafe table identifier: {_name!r}")
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_SCHEDULE_TABLE} (
            day        DATE NOT NULL,
            member     TEXT NOT NULL,
            status     TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (day, member)
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_PERSONAL_HOLIDAYS_TABLE} (
            day        DATE NOT NULL,
            member     TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (day, member)
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_PUBLIC_HOLIDAYS_TABLE} (
            day        DATE PRIMARY KEY,
            name       TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.close()
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def _base_day_map(base_schedule: Dict[str, Dict[str, str]], iso: str) -> Dict[str, str]:
    """The rule-based assignment for one day, normalised to WFO/WFH."""
    base_day = base_schedule.get(iso, {})
    return {m: ("WFO" if base_day.get(m) == "WFO" else "WFH") for m in TEAM_MEMBERS}


def _seed_and_read(
    dates: List[date],
    base_schedule: Dict[str, Dict[str, str]],
    prune: bool,
) -> Dict[str, Dict[str, str]]:
    """Read ``dates`` from the schedule table, seeding any missing day from the
    rule-based base schedule. When ``prune`` is set, rows for any day outside
    ``dates`` are deleted first — this is how the table is kept to the rolling
    two-week window. Falls back to the in-memory base schedule on any DB error.
    """
    out: Dict[str, Dict[str, str]] = {}
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        if prune and dates:
            cur.execute(
                f"DELETE FROM {WFH_SCHEDULE_TABLE} WHERE day <> ALL(%s)",
                ([d for d in dates],),
            )
        cur.execute(
            f"SELECT day, member, status FROM {WFH_SCHEDULE_TABLE} WHERE day = ANY(%s)",
            ([d for d in dates],),
        )
        existing: Dict[str, Dict[str, str]] = {}
        for d, m, s in cur.fetchall():
            iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
            existing.setdefault(iso, {})[m] = s

        to_insert: List[tuple] = []
        for d in dates:
            iso = d.isoformat()
            base_map = _base_day_map(base_schedule, iso)
            day_map: Dict[str, str] = {}
            for m in TEAM_MEMBERS:
                if iso in existing and m in existing[iso]:
                    day_map[m] = existing[iso][m]
                else:
                    day_map[m] = base_map[m]
                    to_insert.append((d, m, base_map[m]))
            out[iso] = day_map

        if to_insert:
            cur.executemany(
                f"INSERT INTO {WFH_SCHEDULE_TABLE} (day, member, status) "
                f"VALUES (%s, %s, %s) ON CONFLICT (day, member) DO NOTHING",
                to_insert,
            )
        conn.commit()
        cur.close()
        return out
    except Exception:  # noqa: BLE001
        # Postgres / vault unavailable — render from the rules in memory.
        return {d.isoformat(): _base_day_map(base_schedule, d.isoformat()) for d in dates}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def load_window(
    window: List[date],
    base_schedule: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, str]]:
    """Read (and prune to) the rolling two-week window. Call once per render."""
    return _seed_and_read(window, base_schedule, prune=True)


def load_week(
    week_start: date,
    base_schedule: Dict[str, Dict[str, str]],
) -> Dict[str, Dict[str, str]]:
    """Read a single week's workdays (no pruning) — used by the editor."""
    dates = [
        week_start + timedelta(days=offset)
        for offset in range(5)
        if is_workday(week_start + timedelta(days=offset))
    ]
    return _seed_and_read(dates, base_schedule, prune=False)


def save_schedule_days(days: Dict[str, Dict[str, str]]) -> None:
    """Upsert a set of ``{iso: {member: status}}`` assignments."""
    rows: List[tuple] = []
    for iso, members in days.items():
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        for m in TEAM_MEMBERS:
            rows.append((d, m, "WFO" if members.get(m) == "WFO" else "WFH"))
    if not rows:
        return
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.executemany(
            f"INSERT INTO {WFH_SCHEDULE_TABLE} (day, member, status) "
            f"VALUES (%s, %s, %s) "
            f"ON CONFLICT (day, member) DO UPDATE "
            f"SET status = EXCLUDED.status, updated_at = NOW()",
            rows,
        )
        conn.commit()
        cur.close()
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save the schedule to Postgres: {e}", icon="⚠️")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def save_week(week_start: date, days: Dict[str, Dict[str, str]]) -> None:
    """Persist a week's assignments (kept for the editor's call sites)."""
    save_schedule_days(days)


def load_holidays() -> Dict[str, List[str]]:
    """Personal holidays as ``{iso_date: [member, ...]}``. Empty on DB error."""
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT day, member FROM {WFH_PERSONAL_HOLIDAYS_TABLE}")
        out: Dict[str, List[str]] = {}
        for d, m in cur.fetchall():
            iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
            if m in TEAM_MEMBERS:
                out.setdefault(iso, []).append(m)
        cur.close()
        return {iso: sorted(set(v)) for iso, v in out.items()}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def save_holidays(holidays: Dict[str, List[str]]) -> None:
    """Replace the personal-holiday table with ``holidays`` (full snapshot)."""
    rows: List[tuple] = []
    for iso, members in holidays.items():
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        for m in members:
            if m in TEAM_MEMBERS:
                rows.append((d, m))
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {WFH_PERSONAL_HOLIDAYS_TABLE}")
        if rows:
            cur.executemany(
                f"INSERT INTO {WFH_PERSONAL_HOLIDAYS_TABLE} (day, member) "
                f"VALUES (%s, %s) ON CONFLICT (day, member) DO NOTHING",
                rows,
            )
        conn.commit()
        cur.close()
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save personal holidays to Postgres: {e}", icon="⚠️")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def load_public_holidays() -> Dict[str, str]:
    """Public holidays for YEAR as ``{iso_date: name}``. Seeds the built-in
    Egypt list into the table on first use; falls back to it on any DB error."""
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT day, name FROM {WFH_PUBLIC_HOLIDAYS_TABLE}")
        rows = cur.fetchall()
        if not rows:
            # First run — seed the suggested defaults.
            seed = [(date.fromisoformat(iso), name)
                    for iso, name in DEFAULT_PUBLIC_HOLIDAYS.items()]
            cur.executemany(
                f"INSERT INTO {WFH_PUBLIC_HOLIDAYS_TABLE} (day, name) "
                f"VALUES (%s, %s) ON CONFLICT (day) DO NOTHING",
                seed,
            )
            conn.commit()
            cur.close()
            return dict(DEFAULT_PUBLIC_HOLIDAYS)
        cur.close()
        out: Dict[str, str] = {}
        for d, name in rows:
            dd = d if hasattr(d, "year") else date.fromisoformat(str(d))
            if dd.year == YEAR:
                out[dd.isoformat()] = str(name)
        return out or dict(DEFAULT_PUBLIC_HOLIDAYS)
    except Exception:  # noqa: BLE001
        return dict(DEFAULT_PUBLIC_HOLIDAYS)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def save_public_holidays(public_holidays: Dict[str, str]) -> None:
    """Replace the public-holiday table with ``public_holidays``."""
    rows: List[tuple] = []
    for iso, name in public_holidays.items():
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        rows.append((d, str(name)))
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {WFH_PUBLIC_HOLIDAYS_TABLE}")
        if rows:
            cur.executemany(
                f"INSERT INTO {WFH_PUBLIC_HOLIDAYS_TABLE} (day, name) "
                f"VALUES (%s, %s) "
                f"ON CONFLICT (day) DO UPDATE SET name = EXCLUDED.name, updated_at = NOW()",
                rows,
            )
        conn.commit()
        cur.close()
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save public holidays to Postgres: {e}", icon="⚠️")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def load_actuals(window: List[date]) -> Dict[str, Dict[str, dict]]:
    """Derive real per-day attendance from the shared ``session_states`` table.

    Returns ``{iso_date: {member: {"status": "WFO"|"WFH", "sessions": int,
    "office_sessions": int}}}`` — a member only appears on days they had at
    least one session. Detection rule:

    * ``WFO`` (on-site) when *any* of the member's sessions that day came from
      an office IP (``client_ip`` starts with ``OFFICE_IP_PREFIX``).
    * ``WFH`` otherwise (they were active but never from an office IP).
    * A member absent from a day's map simply had no sessions (no signal).

    Impersonated sessions (``original_user`` set to someone else) are excluded
    so an admin "view as user" doesn't get attributed as that member's
    presence. Read-only; ``{}`` on any DB error."""
    out: Dict[str, Dict[str, dict]] = {}
    if not window or not _pg_safe_ident(SESSION_STATES_TABLE):
        return out
    start = min(window)
    end = max(window) + timedelta(days=1)
    usernames = list(MEMBER_TO_SESSION_USER.values())
    conn = None
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute(
            f"SELECT s.username, (s.timestamp)::date AS day, "
            f"COUNT(*) FILTER (WHERE s.client_ip LIKE %s) AS office_sessions, "
            f"COUNT(*) AS sessions "
            f"FROM {SESSION_STATES_TABLE} AS s "
            f"WHERE s.username = ANY(%s) "
            f"AND s.timestamp >= %s AND s.timestamp < %s "
            f"AND (s.original_user IS NULL OR s.original_user = s.username) "
            f"GROUP BY s.username, (s.timestamp)::date",
            (OFFICE_IP_PREFIX + "%", usernames, start, end),
        )
        rows = cur.fetchall()
        cur.close()
        for username, day, office_sessions, sessions in rows:
            member = SESSION_USER_TO_MEMBER.get(username)
            if not member:
                continue
            iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
            office_n = int(office_sessions or 0)
            out.setdefault(iso, {})[member] = {
                "status": "WFO" if office_n > 0 else "WFH",
                "sessions": int(sessions or 0),
                "office_sessions": office_n,
            }
        return out
    except Exception:  # noqa: BLE001
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def get_week_start_for_date(d: date) -> date:
    """Return the Sunday that defines the "current" working week.

    Weeks are Sunday-based, but Saturdays are treated as belonging to the
    upcoming week so that opening the app on a Saturday shows the next
    week's schedule.
    """

    # weekday(): Monday=0, ..., Sunday=6
    ref = d
    if d.weekday() == 5:  # Saturday
        ref = d + timedelta(days=1)

    offset = (ref.weekday() - 6) % 7
    return ref - timedelta(days=offset)


def render_week_table(
    title: str,
    days: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]] | None = None,
    public_holidays: Dict[str, str] | None = None,
    highlight_today: bool = False,
) -> None:
    if not days:
        st.write("No working days in this week.")
        return

    st.markdown(f"<h3 class='section-title'>{title}</h3>", unsafe_allow_html=True)

    # Sort by date
    sorted_dates = sorted(days.keys())
    today = date.today()

    # --- Compute "skippable" office days for this week ---
    # A member-day is skippable (minimal impact) when:
    # - It is a WFO day for that member.
    # - That member has **at least 3** WFO days in this week.
    # - The day already has **at least 3** people in the office.
    #
    # If such a day is skipped:
    # - The member still has ≥ 2 WFO days that week (core weekly rule).
    # - The day still has ≥ 2 people in the office (core daily rule).

    # Total WFO days per member within this week
    member_week_wfo: Dict[str, int] = {m: 0 for m in TEAM_MEMBERS}
    # Total WFO people per day within this week
    daily_wfo_count: Dict[str, int] = {}

    for iso, assignments in days.items():
        day_total = 0
        for member in TEAM_MEMBERS:
            if assignments.get(member) == "WFO":
                day_total += 1
                member_week_wfo[member] += 1
        daily_wfo_count[iso] = day_total

    skippable_cells: Set[tuple[str, str]] = set()
    for iso, assignments in days.items():
        day_total = daily_wfo_count.get(iso, 0)
        if day_total < 3:
            continue  # skipping anyone would break the daily ≥2 rule
        for member in TEAM_MEMBERS:
            if assignments.get(member) == "WFO" and member_week_wfo.get(member, 0) >= 3:
                skippable_cells.add((iso, member))

    header_cells = ["<th class='header-cell'>Day</th>"]
    for member in TEAM_MEMBERS:
        header_cells.append(f"<th class='header-cell'>{member}</th>")

    body_rows: List[str] = []
    for iso in sorted_dates:
        d = date.fromisoformat(iso)
        friendly = d.strftime("%a %d %b")
        is_today = highlight_today and (d == today)
        day_label = friendly
        day_extra_cls = ""
        row_extra_cls = ""
        if is_today:
            day_label = f"{friendly} <span class='today-badge'>Today</span>"
            day_extra_cls = " today-day-cell"
            row_extra_cls = "week-today-row"

        public_name = public_holidays.get(iso) if public_holidays else None
        if public_name:
            # Subtle "Day off for <holiday>" note in the Day column; the member
            # cells still show the normal rotation so it's clear who should be in.
            tag_text = (
                public_name
                if public_name.lower().startswith("day off")
                else f"Day off for {public_name}"
            )
            day_label = (
                f"{day_label} "
                f"<span class='public-holiday-tag'>{tag_text}</span>"
            )

        row_cells = [f"<td class='day-cell{day_extra_cls}'>{day_label}</td>"]
        assignments = days[iso]
        for member in TEAM_MEMBERS:
            is_member_holiday = holidays is not None and member in holidays.get(iso, [])
            status = assignments.get(member, "WFH")
            if is_member_holiday:
                # Personal holiday: this member is off regardless of rotation.
                css_class = "holiday-cell"
                label = "Holiday"
            elif status == "WFO":
                # Show who should be at the office, even on a public holiday.
                is_skippable = (iso, member) in skippable_cells
                css_class = "wfo-cell wfo-skippable-cell" if is_skippable else "wfo-cell"
                label = "Office"
            else:
                css_class = "wfh-cell"
                label = "Home"
            row_cells.append(f"<td class='{css_class}'>{label}</td>")
        body_rows.append(f"<tr class='{row_extra_cls}'>" + "".join(row_cells) + "</tr>")

    table_html = f"""
    <div class='week-table-wrapper'>
      <table class='schedule-table'>
        <thead>
          <tr>{''.join(header_cells)}</tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption("Amber-bordered office cells mark low-impact (3rd+ WFO with 3+ in office) days.")


def validate_schedule(
    year_sched: Dict[str, Dict[str, str]]
) -> tuple[List[tuple[date | None, str]], List[tuple[date | None, str]]]:
    """Return (errors, warnings) for the current year schedule.

    Errors correspond to hard attendance rules; warnings correspond to
    softer preferences.
    """

    errors: List[tuple[date | None, str]] = []
    warnings: List[tuple[date | None, str]] = []

    if not year_sched:
        return errors, warnings

    # All workdays in order
    all_dates = sorted(
        d for d in (date.fromisoformat(k) for k in year_sched.keys()) if is_workday(d)
    )
    if not all_dates:
        return errors, warnings

    # --- Daily hard rule: office days have 2-3 people; Sunday is WFH ---
    for d in all_dates:
        assign = year_sched.get(d.isoformat(), {})
        wfo = [m for m, v in assign.items() if v == "WFO"]

        # Sunday (and any non-office workday) must be fully work-from-home.
        if d.weekday() not in OFFICE_WEEKDAYS:
            if wfo:
                errors.append(
                    (d, f"{d:%a %d %b %Y}: {len(wfo)} in office but {d:%A} is always WFH.")
                )
            continue

        if len(wfo) < DAILY_OFFICE_MIN or len(wfo) > DAILY_OFFICE_MAX:
            errors.append(
                (
                    d,
                    f"{d:%a %d %b %Y}: {len(wfo)} people in office "
                    f"(expected {DAILY_OFFICE_MIN}–{DAILY_OFFICE_MAX}).",
                )
            )

        # Role-based hard rule: at least one mgmt-support member must be
        # present in the office on every office day.
        mgmt_present = [
            m
            for m in wfo
            if ROLE_BY_MEMBER.get(m) == "mgmt-support"
        ]
        if not mgmt_present:
            errors.append(
                (
                    d,
                    f"{d:%a %d %b %Y}: no mgmt-support member in office (at least one required).",
                )
            )

        # New joiner is in the office every office day during onboarding.
        if in_full_office_period(d) and NEW_JOINER in TEAM_MEMBERS and NEW_JOINER not in wfo:
            errors.append(
                (
                    d,
                    f"{d:%a %d %b %Y}: {NEW_JOINER} should be in the office every day "
                    f"until {NEW_JOINER_FULL_OFFICE_UNTIL:%d %b %Y} (onboarding).",
                )
            )

    # --- Global 3+ consecutive WFO days (soft warning) ---
    for member in TEAM_MEMBERS:
        streak = 0
        for d in all_dates:
            status = year_sched.get(d.isoformat(), {}).get(member, "WFH")
            if status == "WFO":
                streak += 1
                onboarding = member == NEW_JOINER and in_full_office_period(d)
                if streak >= 3 and not onboarding:
                    warnings.append(
                        (
                            d,
                            f"{member}: 3+ consecutive WFO days ending on {d:%a %d %b %Y}.",
                        )
                    )
            else:
                streak = 0

    sundays = iter_sundays_in_year()

    # --- Weekly and 2-week window checks ---
    for ws in sundays:
        week1_days = [
            d for d in all_dates if ws <= d <= ws + timedelta(days=4)
        ]
        week2_start = ws + timedelta(days=7)
        week2_days = [
            d for d in all_dates if week2_start <= d <= week2_start + timedelta(days=4)
        ]
        window_days = [
            d for d in all_dates if ws <= d < ws + timedelta(days=14)
        ]
        if len(window_days) < 10:
            continue

        # Per-member weekly 50% rule: exactly WEEKLY_WFO office days across
        # the four Mon-Thu office days (Sunday is always WFH) – hard
        for week_idx, wdays in enumerate([week1_days, week2_days], start=1):
            if not wdays:
                continue
            week_is_intro = any(
                is_office_day(d) and in_full_office_period(d) for d in wdays
            )
            for member in TEAM_MEMBERS:
                # The new joiner is full-office while onboarding (not on 50/50);
                # the daily full-office check enforces their attendance instead.
                if member == NEW_JOINER and week_is_intro:
                    continue
                wfo_count = sum(
                    1
                    for d in wdays
                    if year_sched.get(d.isoformat(), {}).get(member) == "WFO"
                )
                if wfo_count != WEEKLY_WFO:
                    errors.append(
                        (
                            ws,
                            f"Week {week_idx} starting {ws:%Y-%m-%d}: {member} has "
                            f"{wfo_count} office days (expected {WEEKLY_WFO}).",
                        )
                    )

        # Per-member 2-week total exactly WFO_PER_MEMBER_FORTNIGHT – hard
        window_has_intro = any(
            is_office_day(d) and in_full_office_period(d) for d in window_days
        )
        for member in TEAM_MEMBERS:
            # New joiner is exempt from the fortnight cap while any part of the
            # window falls in the full-office onboarding period.
            if member == NEW_JOINER and window_has_intro:
                continue
            total_wfo = sum(
                1
                for d in window_days
                if year_sched.get(d.isoformat(), {}).get(member) == "WFO"
            )
            if total_wfo != WFO_PER_MEMBER_FORTNIGHT:
                errors.append(
                    (
                        ws,
                        f"2-week window starting {ws:%Y-%m-%d}: {member} has "
                        f"{total_wfo} office days (expected exactly {WFO_PER_MEMBER_FORTNIGHT}).",
                    )
                )

        # Preference: each pair meets at least once in 2 weeks – warning
        member_pairs = list(combinations(TEAM_MEMBERS, 2))
        for a, b in member_pairs:
            met = False
            for d in window_days:
                assign = year_sched.get(d.isoformat(), {})
                if assign.get(a) == "WFO" and assign.get(b) == "WFO":
                    met = True
                    break
            if not met:
                warnings.append(
                    (
                        ws,
                        f"2-week window starting {ws:%Y-%m-%d}: {a} and {b} "
                        "never share an office day.",
                    )
                )

    # Preference: personal day-of-week constraints (soft warnings)
    # - PREFERS_WFO_DAYS: warn when a member is not WFO on a preferred weekday.
    # - DISLIKES_WFO_DAYS: warn when a member is WFO on a disliked weekday.
    for d in all_dates:
        iso = d.isoformat()
        weekday_idx = d.weekday()
        weekday_full = d.strftime("%A")

        assignments = year_sched.get(iso, {})

        # "Prefers WFO" warnings
        for member, preferred_days in PREFERS_WFO_DAYS.items():
            if member not in TEAM_MEMBERS:
                continue
            if weekday_idx in preferred_days:
                status = assignments.get(member, "WFH")
                if status != "WFO":
                    warnings.append(
                        (
                            d,
                            f"{d:%a %d %b %Y}: {member} is {status} but prefers WFO on {weekday_full}.",
                        )
                    )

        # "Dislikes WFO" warnings
        for member, disliked_days in DISLIKES_WFO_DAYS.items():
            if member not in TEAM_MEMBERS:
                continue
            if weekday_idx in disliked_days:
                status = assignments.get(member, "WFH")
                if status == "WFO":
                    warnings.append(
                        (
                            d,
                            f"{d:%a %d %b %Y}: {member} is WFO but dislikes office on {weekday_full}.",
                        )
                    )

    # Deduplicate messages (by text) while preserving the first associated date
    seen_err: set[str] = set()
    dedup_errors: List[tuple[date | None, str]] = []
    for dt, msg in errors:
        if msg not in seen_err:
            seen_err.add(msg)
            dedup_errors.append((dt, msg))

    seen_warn: set[str] = set()
    dedup_warnings: List[tuple[date | None, str]] = []
    for dt, msg in warnings:
        if msg not in seen_warn:
            seen_warn.add(msg)
            dedup_warnings.append((dt, msg))

    return dedup_errors, dedup_warnings


def build_error_free_base_schedule(
    max_attempts: int = 50,
) -> tuple[List[Set[str]], Dict[str, Dict[str, str]]]:
    """Generate pattern + base schedule with no hard-rule validation errors.

    This repeatedly generates a 2-week pattern and derives a base schedule
    for the whole year, then runs ``validate_schedule`` on that schedule.
    Any schedule with hard-rule errors is discarded; warnings are allowed.
    """

    last_errors: List[tuple[date | None, str]] = []

    rotating_after = list(TEAM_MEMBERS)
    rotating_intro = [m for m in TEAM_MEMBERS if m != NEW_JOINER]

    for _ in range(max_attempts):
        # Onboarding regime: new joiner full-office, the rest on 50/50.
        intro_pattern = generate_two_week_pattern(
            rotating=rotating_intro, forced_office=[NEW_JOINER]
        )
        # Steady-state regime: everyone (incl. new joiner) on 50/50.
        normal_pattern = generate_two_week_pattern(rotating=rotating_after)
        base_schedule = build_base_schedule(intro_pattern, normal_pattern)

        # Validate the pure rule-based schedule (without manual overrides
        # from persisted week files) to ensure the generator and validator
        # agree on hard constraints.
        tmp_year_sched: Dict[str, Dict[str, str]] = {
            iso: members for iso, members in base_schedule.items()
        }
        errors, _ = validate_schedule(tmp_year_sched)
        if not errors:
            return (intro_pattern, normal_pattern), base_schedule

        last_errors = errors

    example = last_errors[0][1] if last_errors else "no details"
    raise RuntimeError(
        "Unable to generate an error-free schedule after "
        f"{max_attempts} attempts. Example error: {example}"
    )


def render_validations(year_sched: Dict[str, Dict[str, str]]) -> None:
    st.markdown("<h3 class='section-title'>Validations</h3>", unsafe_allow_html=True)

    errors, warnings = validate_schedule(year_sched)

    if not errors and not warnings:
        st.success("No rule violations or preference warnings detected.")
        return

    def sort_entries(
        entries: List[tuple[date | None, str]]
    ) -> List[tuple[date | None, str]]:
        indexed = list(enumerate(entries))

        def key(item: tuple[int, tuple[date | None, str]]) -> tuple[date, int]:
            idx, (dt, _msg) = item
            return (dt or date.max, idx)

        return [entry for _, entry in sorted(indexed, key=key)]

    sorted_errors = sort_entries(errors)
    sorted_warnings = sort_entries(warnings)

    total_errors = len(sorted_errors)
    total_warnings = len(sorted_warnings)

    st.markdown(
        f"""
        <div class='validation-summary'>
          <div class='validation-counter validation-counter-errors'>
            <div class='validation-counter-label'>Hard rule violations</div>
            <div class='validation-counter-value'>{total_errors}</div>
          </div>
          <div class='validation-counter validation-counter-warnings'>
            <div class='validation-counter-label'>Preference warnings</div>
            <div class='validation-counter-value'>{total_warnings}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(2)

    with cols[0]:
        st.markdown(
            "<h4 class='validation-subtitle'>Hard rules</h4>",
            unsafe_allow_html=True,
        )
        if sorted_errors:
            items_html: List[str] = []
            for dt, msg in sorted_errors:
                dt_str = dt.strftime("%a %d %b %Y") if dt is not None else "Undated"
                items_html.append(
                    f"<div class='validation-item validation-item-error'>"
                    f"<div class='validation-item-date'>{dt_str}</div>"
                    f"<div class='validation-item-text'>❌ {msg}</div>"
                    "</div>"
                )
            joined = "".join(items_html)
            st.markdown(
                f"<div class='validation-list'>{joined}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.success("✅ No hard rule violations detected.")

    with cols[1]:
        st.markdown(
            "<h4 class='validation-subtitle'>Preferences</h4>",
            unsafe_allow_html=True,
        )
        if sorted_warnings:
            items_html: List[str] = []
            for dt, msg in sorted_warnings:
                dt_str = dt.strftime("%a %d %b %Y") if dt is not None else "Undated"
                items_html.append(
                    f"<div class='validation-item validation-item-warning'>"
                    f"<div class='validation-item-date'>{dt_str}</div>"
                    f"<div class='validation-item-text'>⚠️ {msg}</div>"
                    "</div>"
                )
            joined = "".join(items_html)
            st.markdown(
                f"<div class='validation-list'>{joined}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("ℹ️ No preference-related warnings detected.")


def render_week_warnings(
    week_start: date,
    errors: List[tuple[date | None, str]],
    warnings: List[tuple[date | None, str]],
    label: str,
) -> None:
    """Show a compact, date-aware summary of validations touching a given week.

    This surfaces both errors and warnings in chronological order with a
    short preview and an inline expander for full context.
    """

    if not errors and not warnings:
        return

    week_end = week_start + timedelta(days=4)

    # Collect validations whose primary date falls inside this week only.
    all_entries: List[tuple[date | None, str, str]] = []
    for dt, msg in errors:
        if dt is None:
            continue
        if week_start <= dt <= week_end:
            all_entries.append((dt, msg, "error"))
    for dt, msg in warnings:
        if dt is None:
            continue
        if week_start <= dt <= week_end:
            all_entries.append((dt, msg, "warning"))

    if not all_entries:
        return

    # Sort by date, with errors before warnings when dates tie, and
    # deduplicate by (kind, message) while preserving first occurrence.
    indexed = list(enumerate(all_entries))

    def sort_key(item: tuple[int, tuple[date | None, str, str]]) -> tuple[date, int, int]:
        idx, (dt, _msg, kind) = item
        severity_rank = 0 if kind == "error" else 1
        return (dt or date.max, severity_rank, idx)

    ordered = [entry for _, entry in sorted(indexed, key=sort_key)]

    seen_pairs: set[tuple[str, str]] = set()
    filtered: List[tuple[date | None, str, str]] = []
    for dt, msg, kind in ordered:
        key = (kind, msg)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        filtered.append((dt, msg, kind))

    if not filtered:
        return

    total_count = len(filtered)
    error_count = sum(1 for _dt, _msg, kind in filtered if kind == "error")
    warning_count = total_count - error_count

    # Short preview (up to 2 items) with icons; the rest live in the expander.
    preview_items = filtered[:2]
    preview_html_lines: List[str] = []
    for _dt, msg, kind in preview_items:
        icon = "❌" if kind == "error" else "⚠️"
        preview_html_lines.append(f"{icon} {msg}")
    preview_html = "<br>".join(preview_html_lines)

    if error_count and warning_count:
        title_text = (
            f"❌ {error_count} error(s) & ⚠️ {warning_count} warning(s) "
            f"impact this {label.lower()}."
        )
    elif error_count:
        title_text = f"❌ {error_count} error(s) impact this {label.lower()}."
    else:
        title_text = f"⚠️ {warning_count} warning(s) impact this {label.lower()}."

    st.markdown(
        f"""
        <div class='week-warnings'>
          <span class='week-warnings-title'>{title_text}</span><br>
          <span class='week-warnings-body'>{preview_html}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander(f"Show all {total_count} validation item(s) for this {label.lower()}"):
        for dt, msg, kind in filtered:
            icon = "❌" if kind == "error" else "⚠️"
            if dt is not None:
                dt_str = dt.strftime("%a %d %b %Y")
                st.markdown(f"- {icon} **{dt_str}** — {msg}")
            else:
                st.markdown(f"- {icon} {msg}")


def render_today_and_next(
    year_sched: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]] | None = None,
    public_holidays: Dict[str, str] | None = None,
) -> None:
    today = date.today()

    def next_working_day(start: date) -> date | None:
        d = start + timedelta(days=1)
        # Look ahead within the same year only
        while d.year <= YEAR:
            if is_workday(d) and d.isoformat() in year_sched:
                return d
            d += timedelta(days=1)
        return None

    st.markdown("<h3 class='section-title'>Who's at the office</h3>", unsafe_allow_html=True)

    today_label = today.strftime("%A %d %B %Y")
    today_iso = today.isoformat()
    todays_holidays = set(holidays.get(today_iso, [])) if holidays else set()
    today_public_name = public_holidays.get(today_iso) if public_holidays else None

    if today_iso in year_sched and is_workday(today):
        today_assign = year_sched[today_iso]
        today_wfo = [m for m, v in today_assign.items() if v == "WFO" and m not in todays_holidays]
        today_holiday_members = [m for m in todays_holidays if m in TEAM_MEMBERS]

        # Public holidays no longer hide the rotation; we still surface who is
        # scheduled to be at the office, with a note that it's a public holiday.
        public_note = ""
        if today_public_name:
            public_note = (
                "<div class='office-public-note'>Public holiday: "
                f"{today_public_name}</div>"
            )

        if today_wfo:
            today_html = "".join(
                f"<span class='office-pill'>{m}</span>" for m in today_wfo
            )
            extra = ""
            if today_holiday_members:
                hol_html = "".join(
                    f"<span class='holiday-pill'>{m}</span>" for m in today_holiday_members
                )
                extra = (
                    "<div class='office-holiday-label'>On holiday: "
                    f"{hol_html}</div>"
                )
            today_body = public_note + today_html + extra
        else:
            today_body = public_note + "<span class='office-empty'>No one scheduled in office.</span>"
    else:
        today_body = (
            "<span class='office-empty'>No scheduled office day "
            "(weekend or outside 2026).</span>"
        )

    nxt = next_working_day(today)
    if nxt is not None and nxt.isoformat() in year_sched:
        nxt_iso = nxt.isoformat()
        nxt_assign = year_sched[nxt_iso]
        nxt_holidays = set(holidays.get(nxt_iso, [])) if holidays else set()
        nxt_wfo = [m for m, v in nxt_assign.items() if v == "WFO" and m not in nxt_holidays]
        nxt_holiday_members = [m for m in nxt_holidays if m in TEAM_MEMBERS]
        nxt_label = nxt.strftime("%A %d %B %Y")
        if nxt_wfo:
            nxt_html = "".join(
                f"<span class='office-pill'>{m}</span>" for m in nxt_wfo
            )
            extra = ""
            if nxt_holiday_members:
                hol_html = "".join(
                    f"<span class='holiday-pill'>{m}</span>" for m in nxt_holiday_members
                )
                extra = (
                    "<div class='office-holiday-label'>On holiday: "
                    f"{hol_html}</div>"
                )
            nxt_body = nxt_html + extra
        else:
            nxt_body = "<span class='office-empty'>No one scheduled in office.</span>"
    else:
        nxt_label = "Not within 2026"
        nxt_body = "<span class='office-empty'>No upcoming working day in 2026.</span>"

    section_html = f"""
    <div class='office-section'>
      <div class='office-card'>
        <div class='office-card-header'>
          <div class='office-card-title'>Today</div>
          <div class='office-card-date'>{today_label}</div>
        </div>
        <div class='office-card-body'>
          {today_body}
        </div>
      </div>
      <div class='office-card'>
        <div class='office-card-header'>
          <div class='office-card-title'>Next working day</div>
          <div class='office-card-date'>{nxt_label}</div>
        </div>
        <div class='office-card-body'>
          {nxt_body}
        </div>
      </div>
    </div>
    """
    st.markdown(section_html, unsafe_allow_html=True)


def render_edit_interface(
    base_schedule: Dict[str, Dict[str, str]],
    current_week_start: date,
    next_week_start: date | None,
) -> None:
    st.markdown("<h3 class='section-title'>Edit schedule</h3>", unsafe_allow_html=True)
    with st.expander("Adjust current or next week's WFH/WFO plan", expanded=False):
        sundays = iter_sundays_in_year()
        if not sundays:
            st.write("No Sundays/weeks found for this year.")
            return

        current_ws = current_week_start
        next_ws = next_week_start if next_week_start and next_week_start.year == YEAR else None

        # Restrict editing strictly to current and (if present) next week
        editable_sundays: List[date] = []
        if current_ws in sundays:
            editable_sundays.append(current_ws)
        if next_ws is not None and next_ws in sundays:
            editable_sundays.append(next_ws)

        if not editable_sundays:
            st.info("No editable weeks (current/next) found within 2026.")
            return

        label_map = {}
        for ws in editable_sundays:
            if ws == current_ws:
                label = f"Current week (starting {ws.isoformat()})"
            else:
                label = f"Next week (starting {ws.isoformat()})"
            label_map[label] = ws

        options = list(label_map.keys())
        default_index = 0

        selected_label = st.selectbox("Select week to edit", options, index=default_index)
        week_start = label_map[selected_label]

        week_days = load_week(week_start, base_schedule)
        if not week_days:
            st.write("No working days for this week.")
            return

        st.markdown("**Quick adjustments**", unsafe_allow_html=False)

        # --- "I won't be going" helper ---
        col_quick1, col_quick2 = st.columns(2)
        with col_quick1:
            member_quick = st.selectbox(
                "I can't make it to the office",
                TEAM_MEMBERS,
                key=f"quick_member_{week_start.isoformat()}",
            )
            # All dates this member is currently WFO in this week
            member_wfo_days = [
                (iso, date.fromisoformat(iso))
                for iso, members in sorted(week_days.items())
                if members.get(member_quick) == "WFO"
            ]
            if member_wfo_days:
                labels = [f"{d:%a %d %b}" for _iso, d in member_wfo_days]
                idx = st.selectbox(
                    "Select day",
                    list(range(len(labels))),
                    format_func=lambda i: labels[i],
                    key=f"quick_day_idx_{week_start.isoformat()}",
                )
                iso_quick, d_quick = member_wfo_days[idx]
                if st.button(
                    f"Mark as WFH ({d_quick:%a %d %b})",
                    key=f"quick_mark_wfh_{week_start.isoformat()}",
                ):
                    new_week_days = {k: v.copy() for k, v in week_days.items()}
                    day_members = new_week_days.get(iso_quick, {}).copy()
                    day_members[member_quick] = "WFH"
                    new_week_days[iso_quick] = day_members

                    # Optional soft check: warn if this drops office count below 2
                    wfo_after = [
                        m for m, v in day_members.items() if v == "WFO"
                    ]
                    if len(wfo_after) < 2:
                        st.warning(
                            "This change leaves fewer than 2 people in the "
                            "office that day; validations will flag it.",
                            icon="⚠️",
                        )

                    save_week(week_start, new_week_days)
                    st.success("Update saved.")
                    st.rerun()
            else:
                st.caption("This member has no office days in the selected week.")

        # --- Same-day swap helper ---
        with col_quick2:
            st.markdown("Swap a single office day", unsafe_allow_html=False)
            member_out = st.selectbox(
                "Person stepping out",
                TEAM_MEMBERS,
                key=f"swap_out_{week_start.isoformat()}",
            )

            # Days where member_out is WFO
            out_days = [
                (iso, date.fromisoformat(iso))
                for iso, members in sorted(week_days.items())
                if members.get(member_out) == "WFO"
            ]
            if out_days:
                labels_out = [f"{d:%a %d %b}" for _iso, d in out_days]
                idx_out = st.selectbox(
                    "Their office day",
                    list(range(len(labels_out))),
                    format_func=lambda i: labels_out[i],
                    key=f"swap_day_idx_{week_start.isoformat()}",
                )
                iso_swap, d_swap = out_days[idx_out]

                # Candidates who are WFH on that same day
                day_members_full = week_days.get(iso_swap, {})
                swap_candidates = [
                    m
                    for m in TEAM_MEMBERS
                    if m != member_out and day_members_full.get(m, "WFH") == "WFH"
                ]

                if swap_candidates:
                    member_in = st.selectbox(
                        "Swap with (will go in)",
                        swap_candidates,
                        key=f"swap_in_{week_start.isoformat()}",
                    )
                    if st.button(
                        f"Swap {member_out} with {member_in} on {d_swap:%a %d %b}",
                        key=f"swap_button_{week_start.isoformat()}",
                    ):
                        new_week_days = {k: v.copy() for k, v in week_days.items()}
                        day_members = new_week_days.get(iso_swap, {}).copy()
                        day_members[member_out] = "WFH"
                        day_members[member_in] = "WFO"
                        new_week_days[iso_swap] = day_members

                        save_week(week_start, new_week_days)
                        st.success("Swap applied.")
                        st.rerun()
                else:
                    st.caption(
                        "No one else is WFH that day to swap with.",
                    )
            else:
                st.caption("This person has no office days to swap in this week.")

        # Build DataFrame for editing
        records = []
        for iso, members in sorted(week_days.items()):
            d = date.fromisoformat(iso)
            label = d.strftime("%a %d %b")
            row = {"Day": label, "Date": iso}
            for member in TEAM_MEMBERS:
                row[member] = members.get(member, "WFH")
            records.append(row)

        df = pd.DataFrame(records)

        col_config = {"Day": st.column_config.TextColumn(disabled=True)}
        for member in TEAM_MEMBERS:
            col_config[member] = st.column_config.SelectboxColumn(
                options=["WFH", "WFO"],
                default="WFH",
            )

        edited = st.data_editor(
            df,
            column_config=col_config,
            hide_index=True,
            num_rows="fixed",
            key=f"editor_{week_start.isoformat()}",
        )

        if st.button("Save changes", type="primary", key=f"save_{week_start.isoformat()}"):
            new_week_days: Dict[str, Dict[str, str]] = {}
            for _, row in edited.iterrows():
                iso = row["Date"]
                members: Dict[str, str] = {}
                for member in TEAM_MEMBERS:
                    val = str(row.get(member, "WFH")).upper()
                    members[member] = "WFO" if val == "WFO" else "WFH"
                new_week_days[iso] = members
            save_week(week_start, new_week_days)
            st.success("Week schedule saved. Validations updated.")
            st.rerun()


def render_holidays_tab(
    holidays: Dict[str, List[str]],
    public_holidays: Dict[str, str],
) -> Dict[str, List[str]]:
    st.markdown("<h3 class='section-title'>Holidays</h3>", unsafe_allow_html=True)
    st.caption(
        "Define personal holidays and review public holidays; both overlay "
        "the two-week schedule and the office views."
    )

    tabs = st.tabs(["Public holidays", "Personal holidays"])

    # --- Public holidays tab ---
    with tabs[0]:
        st.markdown(
            "<p class='info-line'>Egypt 2026 public holidays used to "
            "decorate the two-week schedule and office views.</p>",
            unsafe_allow_html=True,
        )

        ph_records: List[Dict[str, object]] = []
        for iso, name in public_holidays.items():
            try:
                d = date.fromisoformat(iso)
            except ValueError:
                continue
            ph_records.append({"Date": d, "Name": name})

        if ph_records:
            ph_df = pd.DataFrame(ph_records).sort_values(["Date", "Name"])
            cards_html = ["<div class='holiday-card-grid'>"]
            for _, row in ph_df.iterrows():
                d: date = row["Date"]
                name: str = row["Name"]
                cards_html.append(
                    "<div class='holiday-card'>"
                    f"<div class='holiday-card-date'>{d:%a %d %b}</div>"
                    f"<div class='holiday-card-name'>{name}</div>"
                    "</div>"
                )
            cards_html.append("</div>")
            st.markdown("".join(cards_html), unsafe_allow_html=True)
        else:
            st.info("No public holidays registered yet for 2026.")

        with st.form("add_public_holiday_form"):
            st.markdown("**Add or update a public holiday**")
            default_date = date(YEAR, 1, 1)
            ph_date = st.date_input("Date", value=default_date, key="public_holiday_date")
            ph_name = st.text_input("Name", key="public_holiday_name")
            submitted_ph = st.form_submit_button("Save public holiday")

        if submitted_ph:
            if isinstance(ph_date, date) and ph_date.year == YEAR and ph_name.strip():
                iso = ph_date.isoformat()
                public_holidays[iso] = ph_name.strip()
                save_public_holidays(public_holidays)
                st.success("Public holiday saved.")
                st.rerun()
            else:
                st.warning("Please choose a 2026 date and a non-empty name.")

        if public_holidays:
            options = sorted(public_holidays.keys())
            to_remove = st.multiselect(
                "Remove public holidays",
                options,
                format_func=lambda iso: f"{iso} – {public_holidays.get(iso, '')}",
                key="public_holiday_remove",
            )
            if to_remove and st.button("Remove selected public holidays", key="btn_remove_public_holidays"):
                for iso in to_remove:
                    public_holidays.pop(iso, None)
                save_public_holidays(public_holidays)
                st.success("Selected public holidays removed.")
                st.rerun()

        with st.expander("Advanced: reset public holidays", expanded=False):
            st.caption("Reset to the suggested Egypt 2026 public holiday list.")
            if st.button("Reset to defaults", type="secondary", key="reset_public_holidays"):
                public_holidays.clear()
                public_holidays.update(DEFAULT_PUBLIC_HOLIDAYS)
                save_public_holidays(public_holidays)
                st.success("Public holidays reset to defaults.")
                st.rerun()

    # --- Personal holidays tab ---
    with tabs[1]:
        st.markdown(
            "<p class='info-line'>Personal days off for individual team "
            "members, layered on top of the base schedule.</p>",
            unsafe_allow_html=True,
        )

        with st.form("add_holiday_form"):
            member = st.selectbox("Team member", TEAM_MEMBERS, key="holiday_member")
            default_date = date(YEAR, 1, 1)
            dr = st.date_input(
                "Holiday date or range",
                value=(default_date, default_date),
                key="holiday_dates",
            )
            submitted = st.form_submit_button("Add holiday(s)")

        if submitted:
            # Normalise the date input into a list of dates
            if isinstance(dr, tuple) or isinstance(dr, list):
                if len(dr) == 2:
                    start, end = dr
                    if isinstance(start, date) and isinstance(end, date):
                        if start > end:
                            start, end = end, start
                        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
                    else:
                        dates = []
                else:
                    dates = [d for d in dr if isinstance(d, date)]
            elif isinstance(dr, date):
                dates = [dr]
            else:
                dates = []

            applied = 0
            for d in dates:
                if d.year != YEAR:
                    continue
                iso = d.isoformat()
                current = set(holidays.get(iso, []))
                current.add(member)
                holidays[iso] = sorted(current)
                applied += 1

            if applied:
                save_holidays(holidays)
                st.success(f"Added {applied} holiday day(s) for {member}.")
                st.rerun()
            else:
                st.info("No 2026 dates were selected; nothing changed.")

        # Show existing personal holidays as small cards grouped by date
        records: List[Dict[str, object]] = []
        for iso, members in holidays.items():
            try:
                d = date.fromisoformat(iso)
            except ValueError:
                continue
            for m in members:
                records.append({"Date": d, "Member": m})

        if records:
            df = pd.DataFrame(records).sort_values(["Date", "Member"])
            cards_html = ["<div class='holiday-card-grid'>"]
            for _, row in df.iterrows():
                d: date = row["Date"]
                m: str = row["Member"]
                cards_html.append(
                    "<div class='holiday-card'>"
                    f"<div class='holiday-card-date'>{d:%a %d %b}</div>"
                    f"<div class='holiday-card-name'>{m}</div>"
                    "</div>"
                )
            cards_html.append("</div>")
            st.markdown("".join(cards_html), unsafe_allow_html=True)
        else:
            st.info("No holidays defined yet for 2026.")

        with st.expander("Advanced: clear all holidays", expanded=False):
            st.caption("Remove every saved holiday entry for 2026.")
            if st.button("Clear all holidays", type="secondary", key="clear_holidays"):
                holidays.clear()
                save_holidays(holidays)
                st.success("All holidays cleared.")
                st.rerun()

    return holidays


def _classify_actual(
    member: str,
    d: date,
    window_sched: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]],
    public_holidays: Dict[str, str],
    actuals: Dict[str, Dict[str, dict]],
) -> tuple[str, str, str | None, str | None]:
    """Return ``(category, planned, actual, note)`` for one member-day.

    category ∈ {"off", "nodata", "match", "mismatch"}; planned/actual are
    "WFO"/"WFH" (actual is None when off / no data)."""
    iso = d.isoformat()
    planned = window_sched.get(iso, {}).get(member, "WFH")
    is_public = bool(public_holidays.get(iso)) if public_holidays else False
    is_personal = member in holidays.get(iso, []) if holidays else False
    if is_public or is_personal:
        return ("off", planned, None, "Public holiday" if is_public else "Day off")
    info = actuals.get(iso, {}).get(member)
    if not info:
        return ("nodata", planned, None, None)
    actual = info.get("status")
    return ("match" if actual == planned else "mismatch", planned, actual, None)


def render_actuals_tab(
    window: List[date],
    window_sched: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]],
    public_holidays: Dict[str, str],
) -> None:
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        "<h3 class='section-title'>Plan vs actual attendance</h3>",
        unsafe_allow_html=True,
    )

    user_map = " · ".join(
        f"{k}→<code>{v}</code>" for k, v in MEMBER_TO_SESSION_USER.items()
    )
    st.markdown(
        f"""
        <div class='algo-box'>
          <div class='algo-title'>How these actuals are computed</div>
          <ul class='algo-list'>
            <li><b>Source</b> — read-only from the shared <code>{SESSION_STATES_TABLE}</code>
                table. Members are matched by session <code>username</code>: {user_map}.</li>
            <li><b>On-site rule</b> — a member is counted <b>at the office</b> on a day when
                <em>any</em> of their sessions that day came from an office IP
                (<code>client_ip</code> starting <code>{OFFICE_IP_PREFIX}</code>). Active but
                never from an office IP → <b>home</b>. No sessions → <b>no data</b>.</li>
            <li><b>Impersonation</b> — sessions whose <code>original_user</code> is someone else
                (admin “view as”) are ignored.</li>
            <li><b>Off days</b> — public holidays and personal days off carry no expectation:
                shown as <b>Off</b> and excluded from every percentage.</li>
            <li><b>Adherence %</b> = matched days ÷ comparable days (workdays that are not Off
                and have session data). Future days have no data yet, so they don't count.</li>
          </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )

    actuals = load_actuals(window)
    workdays = sorted(d for d in window if is_workday(d))

    stats: Dict[str, Dict[str, int]] = {
        m: {
            "comparable": 0, "matches": 0,
            "plan_office_data": 0, "attended_office": 0,
            "plan_home_data": 0, "stayed_home": 0,
            "nodata": 0, "off": 0,
        }
        for m in TEAM_MEMBERS
    }

    for m in TEAM_MEMBERS:
        for d in workdays:
            cat, planned, actual, _note = _classify_actual(
                m, d, window_sched, holidays, public_holidays, actuals
            )
            s = stats[m]
            if cat == "off":
                s["off"] += 1
            elif cat == "nodata":
                s["nodata"] += 1
            else:
                s["comparable"] += 1
                if cat == "match":
                    s["matches"] += 1
                if planned == "WFO":
                    s["plan_office_data"] += 1
                    if actual == "WFO":
                        s["attended_office"] += 1
                else:
                    s["plan_home_data"] += 1
                    if actual == "WFH":
                        s["stayed_home"] += 1

    team_comparable = sum(s["comparable"] for s in stats.values())
    team_matches = sum(s["matches"] for s in stats.values())

    def pct(a: int, b: int) -> str:
        return f"{round(100 * a / b)}%" if b else "—"

    any_data = any(actuals.get(d.isoformat()) for d in workdays)
    if not any_data:
        st.info(
            "No session activity found for these two weeks yet (or the session "
            "table is unavailable). Actuals fill in as the team uses the platform."
        )

    # --- Team headline metrics ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Team adherence", pct(team_matches, team_comparable),
        help="Actual matched the plan across all comparable member-days.",
    )
    c2.metric(
        "Comparable days", f"{team_comparable}",
        help="Workdays with session data, excluding holidays / days off.",
    )
    c3.metric("Matches", f"{team_matches}")
    c4.metric("Mismatches", f"{team_comparable - team_matches}")

    # --- Per-member summary ---
    st.markdown(
        "<h4 class='section-title'>Per member</h4>", unsafe_allow_html=True
    )
    headers = ["Member", "Adherence", "Office attended", "Home kept", "No data", "Off"]
    header_html = "".join(f"<th class='header-cell'>{h}</th>" for h in headers)
    rows_html: List[str] = []
    for m in TEAM_MEMBERS:
        s = stats[m]
        adh = pct(s["matches"], s["comparable"])
        adh_cls = ""
        if s["comparable"]:
            r = s["matches"] / s["comparable"]
            adh_cls = "adh-good" if r >= 0.8 else ("adh-mid" if r >= 0.5 else "adh-bad")
        office = (
            f"{s['attended_office']}/{s['plan_office_data']} "
            f"({pct(s['attended_office'], s['plan_office_data'])})"
        )
        home = (
            f"{s['stayed_home']}/{s['plan_home_data']} "
            f"({pct(s['stayed_home'], s['plan_home_data'])})"
        )
        rows_html.append(
            f"<tr><td class='day-cell'>{m}</td>"
            f"<td class='{adh_cls}'>{adh}</td>"
            f"<td>{office}</td><td>{home}</td>"
            f"<td>{s['nodata']}</td><td>{s['off']}</td></tr>"
        )
    st.markdown(
        f"<div class='week-table-wrapper'><table class='schedule-table'>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Office attended = of planned-office days with data, how many were actually "
        "on-site. Home kept = of planned-home days with data, how many stayed home."
    )

    # --- Day-by-day plan vs actual grid ---
    st.markdown(
        "<h4 class='section-title'>Day by day</h4>", unsafe_allow_html=True
    )
    grid_header = "<th class='header-cell'>Day</th>" + "".join(
        f"<th class='header-cell'>{m}</th>" for m in TEAM_MEMBERS
    )
    grid_rows: List[str] = []
    for d in workdays:
        iso = d.isoformat()
        friendly = d.strftime("%a %d %b")
        tag = ""
        if public_holidays and public_holidays.get(iso):
            tag = " <span class='public-holiday-tag'>Public</span>"
        cells = [f"<td class='day-cell'>{friendly}{tag}</td>"]
        for m in TEAM_MEMBERS:
            cat, planned, actual, note = _classify_actual(
                m, d, window_sched, holidays, public_holidays, actuals
            )
            plan_lbl = "Office" if planned == "WFO" else "Home"
            if cat == "off":
                cells.append(
                    "<td class='actual-cell actual-off'>"
                    "<div class='actual-status'>Off</div>"
                    f"<div class='actual-plan'>{note}</div></td>"
                )
            elif cat == "nodata":
                cells.append(
                    "<td class='actual-cell actual-nodata'>"
                    "<div class='actual-status'>No data</div>"
                    f"<div class='actual-plan'>plan: {plan_lbl}</div></td>"
                )
            else:
                act_lbl = "Office" if actual == "WFO" else "Home"
                icon = "✓" if cat == "match" else "✗"
                cells.append(
                    f"<td class='actual-cell actual-{cat}'>"
                    f"<div class='actual-status'>{icon} {act_lbl}</div>"
                    f"<div class='actual-plan'>plan: {plan_lbl}</div></td>"
                )
        grid_rows.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(
        f"<div class='week-table-wrapper'><table class='schedule-table'>"
        f"<thead><tr>{grid_header}</tr></thead>"
        f"<tbody>{''.join(grid_rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def inject_css() -> None:
    st.markdown(
        """
        <style>
        body {
            background-color: #f5f8fc;
        }
        .main > div {
            padding-top: 0.5rem;
        }
        .section-title {
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: #0b3c5d;
            margin-bottom: 0.5rem;
        }
        .week-table-wrapper {
            background: #ffffff;
            border-radius: 8px;
            padding: 0.5rem 0.75rem 0.75rem 0.75rem;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
        }
        .schedule-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.88rem;
        }
        .schedule-table th,
        .schedule-table td {
            border-bottom: 1px solid #e3ebf5;
            padding: 0.3rem 0.5rem;
            text-align: center;
        }
        .header-cell {
            background: #e3f2fd;
            color: #0b3c5d;
            font-weight: 600;
        }
        .day-cell {
            text-align: left;
            font-weight: 500;
            color: #13466b;
            background: #f7fbff;
        }
        .today-day-cell {
            position: relative;
            font-weight: 600;
            color: #0b3c5d;
        }
        .wfo-cell {
            background: #a5d6a7;
            color: #1b5e20;
            font-weight: 500;
        }
        .wfo-cell.wfo-skippable-cell {
            box-shadow: inset 0 0 0 2px #fbbf24;
            position: relative;
        }
        .wfh-cell {
            background: #f9fafb;
            color: #374151;
            font-weight: 500;
        }
        .week-today-row td {
            box-shadow: inset 2px 0 0 #ffb74d;
        }
        .today-badge {
            display: inline-block;
            margin-left: 0.35rem;
            padding: 0.08rem 0.45rem;
            border-radius: 999px;
            background: #ffecb3;
            color: #8c6d1f;
            font-size: 0.7rem;
            font-weight: 600;
        }
        .holiday-cell {
            background: #ffe0b2;
            color: #8d4b16;
            font-weight: 500;
        }
        .year-grid-wrapper {
            margin-top: 1.5rem;
            background: #ffffff;
            border-radius: 8px;
            padding: 0.75rem;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
        }
        .year-grid-scroll {
            overflow-x: auto;
            padding-bottom: 0.25rem;
        }
        .year-grid-table {
            border-collapse: collapse;
            font-size: 0.7rem;
        }
        .year-grid-table th,
        .year-grid-table td {
            border: 1px solid #e3ebf5;
            width: 14px;
            height: 14px;
            padding: 0;
        }
        .grid-day-header {
            writing-mode: vertical-rl;
            text-orientation: mixed;
            font-size: 0.55rem;
            background: #e3f2fd;
            color: #0b3c5d;
        }
        .member-col {
            background: #e3f2fd;
            color: #0b3c5d;
            text-align: left;
            padding: 0.2rem 0.4rem;
        }
        .member-label {
            padding: 0.2rem 0.4rem;
            text-align: left;
            font-weight: 500;
            color: #13466b;
            white-space: nowrap;
        }
        .grid-wfo {
            background: #66bb6a;
        }
        .grid-wfh {
            background: #f3f4f6;
        }
        .grid-holiday {
            background: #ffcc80;
        }
        .grid-today {
            box-shadow: 0 0 0 2px #ffb74d inset;
        }
        .today-header {
            background: #ffecb3;
            color: #8c6d1f;
        }
        .info-line {
            font-size: 0.95rem;
            margin-bottom: 0.25rem;
        }
        .office-section {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 0.75rem;
            margin: 0.5rem 0 1rem 0;
        }
        .office-card {
            background: #ffffff;
            border-radius: 10px;
            padding: 0.75rem 0.9rem;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.12);
            border: 1px solid #e3ebf5;
        }
        .office-card-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 0.4rem;
        }
        .office-card-title {
            font-weight: 600;
            color: #0b3c5d;
            font-size: 0.95rem;
        }
        .office-card-date {
            font-size: 0.8rem;
            color: #64748b;
        }
        .office-card-body {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            align-items: center;
        }
        .office-pill {
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            background: #e3f2fd;
            color: #0d47a1;
            font-size: 0.82rem;
            font-weight: 500;
            border: 1px solid #bbdefb;
        }
        .office-empty {
            font-size: 0.85rem;
            color: #9ca3af;
        }
        .holiday-pill {
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            background: #fff7ed;
            color: #92400e;
            font-size: 0.8rem;
            font-weight: 500;
            border: 1px solid #fed7aa;
            margin-left: 0.25rem;
        }
        .office-holiday-label {
            margin-top: 0.25rem;
            font-size: 0.8rem;
            color: #92400e;
        }
        .office-public-note {
            margin-bottom: 0.4rem;
            font-size: 0.8rem;
            font-weight: 500;
            color: #b91c1c;
        }
        .public-holiday-tag {
            display: inline-block;
            margin-left: 0.35rem;
            padding: 0.06rem 0.45rem;
            border-radius: 999px;
            background: #f3f4f6;
            color: #9ca3af;
            font-size: 0.68rem;
            font-weight: 500;
        }
        .holiday-card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 0.5rem;
            margin: 0.5rem 0 0.75rem 0;
        }
        .holiday-card {
            background: #ffffff;
            border-radius: 8px;
            padding: 0.45rem 0.6rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
            border: 1px solid #e5e7eb;
        }
        .holiday-card-date {
            font-size: 0.8rem;
            color: #6b7280;
            margin-bottom: 0.1rem;
        }
        .holiday-card-name {
            font-size: 0.86rem;
            color: #111827;
            font-weight: 500;
        }
        .weekly-grid-container {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            margin-top: 0.75rem;
        }
        .year-grid-wrapper.week-grid {
            margin-top: 0.25rem;
            padding: 0.5rem;
        }
        .year-grid-wrapper.week-grid .section-title {
            font-size: 0.9rem;
            margin-bottom: 0.25rem;
        }
        .week-warnings {
            margin-top: 0.4rem;
            padding: 0.35rem 0.6rem;
            border-radius: 6px;
            background: #fff7ed;
            border: 1px dashed #fed7aa;
            font-size: 0.78rem;
            color: #92400e;
        }
        .week-warnings-title {
            font-weight: 600;
        }
        .week-warnings-body {
            display: inline-block;
            margin-top: 0.05rem;
        }
        .week-warnings-more {
            color: #9ca3af;
            font-style: italic;
        }
        .validation-summary {
            display: flex;
            gap: 0.75rem;
            margin: 0.5rem 0 0.75rem 0;
        }
        .validation-counter {
            flex: 1;
            padding: 0.6rem 0.8rem;
            border-radius: 8px;
            background: #f9fafb;
            border: 1px solid #e5e7eb;
        }
        .validation-counter-label {
            font-size: 0.78rem;
            color: #6b7280;
            margin-bottom: 0.15rem;
        }
        .validation-counter-value {
            font-size: 1.1rem;
            font-weight: 600;
            color: #0b3c5d;
        }
        .validation-counter-errors {
            background: #fef2f2;
            border-color: #fecaca;
        }
        .validation-counter-warnings {
            background: #fffbeb;
            border-color: #fed7aa;
        }
        .validation-subtitle {
            font-size: 0.92rem;
            margin-top: 0.25rem;
            margin-bottom: 0.3rem;
            color: #0b3c5d;
        }
        .validation-list {
            margin-top: 0.1rem;
        }
        .validation-item {
            border-radius: 6px;
            padding: 0.35rem 0.55rem;
            margin-bottom: 0.3rem;
            background: #f9fafb;
            border: 1px solid #e5e7eb;
            font-size: 0.82rem;
        }
        .validation-item-error {
            background: #fef2f2;
            border-color: #fecaca;
        }
        .validation-item-warning {
            background: #fffbeb;
            border-color: #fed7aa;
        }
        .validation-item-date {
            font-size: 0.72rem;
            color: #6b7280;
            margin-bottom: 0.05rem;
        }
        .validation-item-text {
            color: #111827;
        }
        .algo-box {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-left: 4px solid #0b3c5d;
            border-radius: 8px;
            padding: 0.6rem 0.9rem;
            margin: 0.25rem 0 0.9rem 0;
        }
        .algo-title {
            font-weight: 600;
            color: #0b3c5d;
            margin-bottom: 0.3rem;
            font-size: 0.92rem;
        }
        .algo-list {
            margin: 0;
            padding-left: 1.1rem;
            font-size: 0.82rem;
            color: #334155;
        }
        .algo-list li {
            margin-bottom: 0.2rem;
        }
        .algo-box code {
            background: #eef2f7;
            padding: 0.02rem 0.3rem;
            border-radius: 4px;
            font-size: 0.78rem;
            color: #0b3c5d;
        }
        .actual-cell {
            text-align: center;
            line-height: 1.15;
        }
        .actual-status {
            font-weight: 600;
            font-size: 0.85rem;
        }
        .actual-plan {
            font-size: 0.68rem;
            opacity: 0.75;
        }
        .actual-match {
            background: #c8e6c9;
            color: #1b5e20;
        }
        .actual-mismatch {
            background: #ffcdd2;
            color: #b71c1c;
        }
        .actual-off {
            background: #eceff1;
            color: #607d8b;
        }
        .actual-nodata {
            background: #f9fafb;
            color: #9ca3af;
        }
        .adh-good {
            background: #c8e6c9;
            color: #1b5e20;
            font-weight: 600;
        }
        .adh-mid {
            background: #fff3c4;
            color: #8a6d00;
            font-weight: 600;
        }
        .adh-bad {
            background: #ffcdd2;
            color: #b71c1c;
            font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


WEEKDAY_LABELS = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}


def render_preferences_tab() -> None:
    """Read-only view of each member's current attendance preferences and rules."""

    st.markdown("<h3 class='section-title'>Member preferences</h3>", unsafe_allow_html=True)
    st.caption(
        "Current attendance rules and preferences per member. Hard rules are always "
        "enforced; preferences are honoured when possible and otherwise only raise warnings."
    )

    dash = "<span style='color:#9ca3af'>—</span>"

    def fmt_days(days: Set[int]) -> str:
        if not days:
            return dash
        return ", ".join(WEEKDAY_LABELS[i] for i in sorted(days))

    st.markdown(
        "<div style='border:1px dashed #93c5fd; background:#eff6ff; border-radius:6px; "
        "padding:0.5rem 0.75rem; font-size:0.86rem; color:#1e3a5f; margin:0.3rem 0 0.85rem;'>"
        "<b>Team-wide:</b> Sundays are always work-from-home. The office rotation runs "
        "Monday–Thursday, with everyone in the office 2 of those 4 days (a 50/50 split)."
        "</div>",
        unsafe_allow_html=True,
    )

    header = "".join(
        f"<th class='header-cell'>{h}</th>"
        for h in ["Member", "Role", "Prefers office", "Avoids office", "Special rule"]
    )

    rows: List[str] = []
    for m in TEAM_MEMBERS:
        role = ROLE_BY_MEMBER.get(m, "—")
        prefers = fmt_days(PREFERS_WFO_DAYS.get(m, set()))
        dislikes = fmt_days(DISLIKES_WFO_DAYS.get(m, set()))

        special: List[str] = []
        if m == NEW_JOINER:
            special.append(
                "Onboarding: <b>full office</b> every office day through "
                f"{NEW_JOINER_FULL_OFFICE_UNTIL:%d %b %Y}, then joins the 50/50 rotation"
            )
        special_html = "<br>".join(special) if special else dash

        if role == "mgmt-support":
            role_badge = (
                "<span style='padding:0.1rem 0.5rem; border-radius:999px; font-size:0.76rem; "
                "font-weight:600; background:#e0e7ff; color:#3730a3;'>mgmt-support</span>"
            )
        else:
            role_badge = (
                "<span style='padding:0.1rem 0.5rem; border-radius:999px; font-size:0.76rem; "
                "font-weight:600; background:#dcfce7; color:#166534;'>engineering</span>"
            )

        rows.append(
            "<tr>"
            f"<td class='day-cell'>{m}</td>"
            f"<td>{role_badge}</td>"
            f"<td>{prefers}</td>"
            f"<td>{dislikes}</td>"
            f"<td style='text-align:left'>{special_html}</td>"
            "</tr>"
        )

    table_html = (
        "<div class='week-table-wrapper'><table class='schedule-table'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption(
        "“Prefers office” = soft wish to be in on those days · "
        "“Avoids office” = soft wish to stay home on those days. "
        "Preferences never block a schedule; they only surface as warnings in the Validations tab."
    )


def main() -> None:
    st.set_page_config(
        page_title="WFH/WFO Schedule 2026",
        layout="wide",
        page_icon="📅",
    )

    inject_css()

    st.markdown("<h1 class='section-title'>WFH vs WFO Schedule</h1>", unsafe_allow_html=True)
    st.caption(
        "Rolling two-week rotation (current + next week) respecting attendance "
        "rules and preferences, with per-week overrides, persisted in Postgres. "
        "Sundays are always work-from-home; the office rotation runs Mon–Thu "
        "with each person in the office 2 of those 4 days (a 50/50 split). "
        f"{NEW_JOINER} works fully from the office through "
        f"{NEW_JOINER_FULL_OFFICE_UNTIL:%d %b %Y} while onboarding, then joins the rotation."
    )

    # Rule-based pattern, used to seed any day not yet persisted in Postgres.
    # Keep regenerating until the base schedule has no hard-rule validation
    # errors (warnings are allowed).
    _pattern, base_schedule = build_error_free_base_schedule()
    holidays = load_holidays()
    public_holidays = load_public_holidays()

    # Current date context
    today = date.today()
    if today.year == YEAR:
        current_week_start = get_week_start_for_date(today)
    else:
        sundays = iter_sundays_in_year()
        current_week_start = sundays[0] if sundays else date(YEAR, 1, 1)

    next_week_start = current_week_start + timedelta(days=7)

    # The rolling two-week window: current week + next week (workdays only).
    # Postgres only ever holds these days — older rows are pruned on load.
    window: List[date] = []
    for ws in (current_week_start, next_week_start):
        if ws.year != YEAR:
            continue
        for offset in range(5):
            d = ws + timedelta(days=offset)
            if is_workday(d):
                window.append(d)
    window.sort()

    window_sched = load_window(window, base_schedule)

    def _slice_week(ws: date) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for offset in range(5):
            iso = (ws + timedelta(days=offset)).isoformat()
            if iso in window_sched:
                out[iso] = window_sched[iso]
        return out

    # Pre-compute validations (scoped to the two-week window) so both the tab
    # label and the week-level warnings share the same data.
    val_errors, val_warnings = validate_schedule(window_sched)
    val_tab_label = "Validations"
    if val_errors or val_warnings:
        parts: List[str] = []
        if val_errors:
            parts.append(f"{len(val_errors)} errors")
        if val_warnings:
            parts.append(f"{len(val_warnings)} warnings")
        val_tab_label = f"Validations (" + ", ".join(parts) + ")"

    col1, col2 = st.columns(2)
    with col1:
        render_week_table(
            "Current week", _slice_week(current_week_start),
            holidays, public_holidays, highlight_today=True,
        )
        render_week_warnings(current_week_start, val_errors, val_warnings, "Current week")
    with col2:
        if next_week_start.year == YEAR:
            render_week_table(
                "Next week", _slice_week(next_week_start), holidays, public_holidays,
            )
            render_week_warnings(next_week_start, val_errors, val_warnings, "Next week")
        else:
            st.markdown("<p>No next week within 2026.</p>", unsafe_allow_html=True)

    # Who's in the office today / next working day
    render_today_and_next(window_sched, holidays, public_holidays)

    tabs = st.tabs(["Editing", "Actuals", "Preferences", val_tab_label, "Holidays"])

    # --- Editing tab ---
    with tabs[0]:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown(
            "<h3 class='section-title'>Editing tools</h3>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Use quick actions for one-off changes or the table editor "
            "below for full control over a week's plan."
        )

        # Editing interface (restricted to current and next week)
        render_edit_interface(
            base_schedule,
            current_week_start,
            next_week_start if next_week_start.year == YEAR else None,
        )

        # Reset the two-week window back to the rule-based rotation.
        with st.expander("Advanced: reset the two-week schedule", expanded=False):
            st.caption(
                "Rebuild the current and next week from the rules, discarding "
                "any manual edits."
            )
            if st.button("Reset to rule-based rotation", type="secondary"):
                _, refreshed_base = build_error_free_base_schedule()
                reset_days: Dict[str, Dict[str, str]] = {}
                for d in window:
                    iso = d.isoformat()
                    reset_days[iso] = _base_day_map(refreshed_base, iso)
                save_schedule_days(reset_days)
                st.success("Two-week schedule reset from the rules.")
                st.rerun()

    with tabs[1]:
        render_actuals_tab(window, window_sched, holidays, public_holidays)

    with tabs[2]:
        render_preferences_tab()

    with tabs[3]:
        render_validations(window_sched)

    with tabs[4]:
        render_holidays_tab(holidays, public_holidays)


if __name__ == "__main__":
    main()
