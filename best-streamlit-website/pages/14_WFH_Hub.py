"""WFH Hub — the combined schedule + attendance workspace.

Merges the WFH Schedule (rotation building/editing) and Team Attendance
(policy tracking) pages into one professional workspace:

* **My Space** — personal status vs the 50% policy, fix past attendance
  (Office / Home / Day off).
* **Team & Schedule** — who's in today, an *editable* repeating two-week
  rotation (management; full-office members are fixed), and management-set
  per-day plan overrides.
* **Reports** — over any selected duration:
  - *Management report*: actual WFO/WFH per member, regardless of plan
    (CSV export, summary + day level).
  - *Team report*: plan vs actual, deviation-focused (CSV export).
  - *Hours & punctuality*: working hours edited alongside attendance
    (9:00–17:00 defaults; office days pick up detected session times),
    lateness vs a configurable threshold with a permission checkbox, and
    short-day (<8h) detection (CSV export). All times in Africa/Cairo.
  - *Detection quality*: every manual correction compared against what IP
    detection said — missed-office / ghost-office / day-off-activity buckets
    feed the detection-enhancement backlog (CSV export).
* **Method & Settings** — the measurement algorithm, IP-detection start date,
  and the company-holiday calendar (management-editable).

Data sources (all in the same vault-credentialed Postgres as the platform):
    wfh_rotation           the repeating 2-week pattern (10 Sun–Thu slots)
    wfh_plan_overrides     per-day, per-member plan deviations from rotation
    wfh_manual_attendance  self-reported actuals: WFO/WFH/OFF (fills/corrects
                           IP gaps; OFF = self-recorded day off)
    wfh_settings           key/value settings (ip_detection_start,
                           late_threshold)
    wfh_day_times          member-set office start/end + lateness
                           permission confirmations
    wfh_public_holidays    company holidays (editable in Settings)
    wfh_personal_holidays  personal leave register (read-only here)
    session_states         shared platform table → IP-based presence

Actual-attendance precedence: a member's own saved record wins (it is an
explicit, audited correction), otherwise IP detection (only on/after the
detection start date), otherwise unknown.

Colour language (validated for colour-vision deficiency on light surfaces):
    green #16a34a  in office        orange #f59e0b  deviation from PLAN
    blue  #3b82f6  home             red    #dc2626  below the 50% HR POLICY
    every coloured cell also carries a glyph (O/H/P/V/?) so colour is never
    the only channel.

Degrades gracefully without Postgres/vault: deterministic in-memory rotation,
empty actuals, saves warn instead of failing.
"""

from __future__ import annotations

import os
import random
import re
from datetime import date, datetime, time, timedelta, timezone as _tzutc
from zoneinfo import ZoneInfo
from itertools import combinations
from typing import Dict, List, Sequence, Set, Tuple

import pandas as pd
import streamlit as st

# --- Optional infra deps ------------------------------------------------------
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

try:
    from utils.vault import VaultClient as _VaultClient  # type: ignore
    _VAULT_AVAILABLE = True
except Exception:  # pragma: no cover
    _VaultClient = None  # type: ignore
    _VAULT_AVAILABLE = False


# =============================================================================
# TEAM RULES & CONFIG
# =============================================================================
TEAM_MEMBERS: List[str] = ["Adham", "Karam", "Hesham", "Salma", "Zanaty", "Marwan"]
YEAR = 2026

ROLE_BY_MEMBER: Dict[str, str] = {
    "Adham": "mgmt-support",
    "Karam": "mgmt-support",
    "Hesham": "mgmt-support",
    "Salma": "engineering",
    "Zanaty": "engineering",
    "Marwan": "mgmt-support",
}

# Members who work from the office EVERY office day, permanently. They are
# excluded from the 50/50 rotation (the rotation pattern never contains
# them); rotation_default overlays WFO on Mon–Thu, WFH on Sundays.
FULL_OFFICE_MEMBERS: Set[str] = {"Marwan"}
ROTATING_MEMBERS: List[str] = [m for m in TEAM_MEMBERS if m not in FULL_OFFICE_MEMBERS]

NEW_JOINER = "Zanaty"
NEW_JOINER_FULL_OFFICE_UNTIL = date(2026, 8, 14)

WORKDAYS = {6, 0, 1, 2, 3}       # Sun(6)–Thu(3)
OFFICE_WEEKDAYS = {0, 1, 2, 3}   # Mon–Thu (Sundays always WFH)
# Daily cap raised 3→4 when Marwan (permanent full-office) joined: 5 rotating
# members × 2 days = 10 rotating slots per week, and with one seat taken every
# day only a 2–4 band leaves the required 1–3 rotating slots per day.
DAILY_OFFICE_MIN, DAILY_OFFICE_MAX = 2, 4
WEEKLY_WFO = 2
WFO_PER_MEMBER_FORTNIGHT = 4

POLICY_MIN_RATE = 0.50
ROTATION_SEED = 20260101

SESSION_STATES_TABLE = os.environ.get("WFH_SESSION_STATES_TABLE", "session_states").strip()
OFFICE_IP_PREFIX = os.environ.get("WFH_OFFICE_IP_PREFIX", "10.26").strip()
MEMBER_TO_SESSION_USER: Dict[str, str] = {
    "Adham": "Adham_Wagih",
    "Karam": "Karam_Mohamed",
    "Hesham": "Hesham_Mostafa",
    "Salma": "Salma_Adel",
    "Zanaty": "Ahmed_zanaty",  # note the lowercase z — as it appears in session_states
    "Marwan": "Marwan_Bakeer",
}
SESSION_USER_TO_MEMBER: Dict[str, str] = {v: k for k, v in MEMBER_TO_SESSION_USER.items()}
# All session-username matching is case-insensitive: the platform is not
# consistent about capitalisation (e.g. Ahmed_zanaty vs Ahmed_Zanaty).
SESSION_USER_TO_MEMBER_CI: Dict[str, str] = {
    v.lower(): k for k, v in MEMBER_TO_SESSION_USER.items()
}

POSTGRES_VAULT_PATH = os.environ.get("WFH_POSTGRES_VAULT_PATH", "postgres").strip()
POSTGRES_CONNECT_TIMEOUT = 10
POSTGRES_DATA_TTL = 60

WFH_ROTATION_TABLE = os.environ.get("WFH_ROTATION_TABLE", "wfh_rotation").strip()
WFH_MANUAL_TABLE = os.environ.get("WFH_MANUAL_ATTENDANCE_TABLE", "wfh_manual_attendance").strip()
WFH_OVERRIDES_TABLE = os.environ.get("WFH_PLAN_OVERRIDES_TABLE", "wfh_plan_overrides").strip()
WFH_SETTINGS_TABLE = os.environ.get("WFH_SETTINGS_TABLE", "wfh_settings").strip()
WFH_DAY_TIMES_TABLE = os.environ.get("WFH_DAY_TIMES_TABLE", "wfh_day_times").strip()
WFH_PERSONAL_HOLIDAYS_TABLE = os.environ.get(
    "WFH_PERSONAL_HOLIDAYS_TABLE", "wfh_personal_holidays").strip()
WFH_PUBLIC_HOLIDAYS_TABLE = os.environ.get(
    "WFH_PUBLIC_HOLIDAYS_TABLE", "wfh_public_holidays").strip()

DETECTION_START_SETTING = "ip_detection_start"
LATE_THRESHOLD_SETTING = "late_threshold"
DEFAULT_LATE_THRESHOLD = time(10, 0)   # office starts after this = late
DEFAULT_WORK_START = time(9, 0)        # default working hours (9:00–17:00)
DEFAULT_WORK_END = time(17, 0)

# session_states.timestamp is naive UTC; the team lives in Cairo (UTC+2/+3),
# so every date bucket and clock time must convert — otherwise a 9:00 office
# start reads as 6:00 and late-evening sessions land on the wrong day.
LOCAL_TZ_NAME = os.environ.get("WFH_TIMEZONE", "Africa/Cairo").strip()
LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def local_today() -> date:
    return local_now().date()


def _utc_bounds(start: date, end: date) -> Tuple[datetime, datetime]:
    """[start, end] in local calendar days → naive-UTC half-open bounds
    matching the storage format of session_states.timestamp."""
    lo = datetime.combine(start, time(0), tzinfo=LOCAL_TZ)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=LOCAL_TZ)
    return (lo.astimezone(_tzutc.utc).replace(tzinfo=None),
            hi.astimezone(_tzutc.utc).replace(tzinfo=None))
MIN_DAY_HOURS = 8.0                    # office days shorter than this are flagged
DEFAULT_DETECTION_START = date(2026, 6, 1)

DEFAULT_PUBLIC_HOLIDAYS: Dict[str, str] = {
    "2026-01-07": "Coptic Christmas Day",
    "2026-01-29": "Day off for Revolution Day",
    "2026-04-13": "Spring Festival",
    "2026-04-25": "Sinai Liberation Day",
    "2026-05-01": "Labour Day",
    "2026-06-17": "Islamic New Year (Muharram)",
    "2026-07-23": "Revolution Day (July 23)",
    "2026-08-26": "Prophet's Birthday (tentative)",
    "2026-10-08": "Day off for Armed Forces Day",
}

# Validated palette (dataviz six-checks, light surface): identity is never
# colour-alone — every cell carries a glyph and reports have table views.
C_OFFICE = "#16a34a"
C_HOME = "#3b82f6"
C_DEV = "#f59e0b"
C_BREACH = "#dc2626"


# =============================================================================
# CALENDAR HELPERS
# =============================================================================
def is_workday(d: date) -> bool:
    return d.weekday() in WORKDAYS


def is_office_day(d: date) -> bool:
    return d.weekday() in OFFICE_WEEKDAYS


def in_full_office_period(d: date) -> bool:
    return d <= NEW_JOINER_FULL_OFFICE_UNTIL


def anchor_sunday(year: int) -> date:
    first = date(year, 1, 1)
    return first - timedelta(days=(first.weekday() - 6) % 7)


def sunday_of(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 6) % 7)


def is_mgmt(member: str | None) -> bool:
    return bool(member) and ROLE_BY_MEMBER.get(member) == "mgmt-support"


def workdays_between(start: date, end: date) -> List[date]:
    out, d = [], start
    while d <= end:
        if is_workday(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def rotation_slot(d: date) -> int | None:
    """0–9 slot in the repeating Sun–Thu×2 cycle; None on Fri/Sat."""
    delta = (d - anchor_sunday(d.year)).days
    rem = delta % 7
    if rem > 4:
        return None
    return ((delta // 7) * 5 + rem) % 10


# =============================================================================
# ROTATION GENERATOR (hard rules preserved from the original pages)
# =============================================================================
def generate_two_week_pattern(rotating=None, forced_office=None) -> List[Set[str]]:
    rotating = list(rotating) if rotating is not None else list(TEAM_MEMBERS)
    forced_office = list(forced_office) if forced_office is not None else []

    idx = {name: i for i, name in enumerate(TEAM_MEMBERS)}
    n_team = len(TEAM_MEMBERS)
    r_idx = [idx[m] for m in rotating]
    forced_idx = {idx[m] for m in forced_office}
    mgmt_indices = {idx[m] for m in TEAM_MEMBERS if ROLE_BY_MEMBER.get(m) == "mgmt-support"}
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"] * 2

    min_choose = max(0, DAILY_OFFICE_MIN - len(forced_office))
    max_choose = max(0, DAILY_OFFICE_MAX - len(forced_office))

    all_subsets: List[Sequence[int]] = []
    for rsz in range(min_choose, max_choose + 1):
        all_subsets.extend(list(combinations(r_idx, rsz)))
    random.shuffle(all_subsets)

    options_per_day: List[List[Sequence[int]]] = []
    for day in range(10):
        if weekdays[day] == "Sun":
            options_per_day.append([tuple()])
            continue
        options_per_day.append(
            [s for s in all_subsets if (mgmt_indices & (forced_idx | set(s)))]
        )

    best: List[Set[int]] | None = None
    found = False

    def backtrack(day, schedule, counts, week_counts, streaks):
        nonlocal best, found
        if found:
            return
        if day == 10:
            if any(counts[p] != WFO_PER_MEMBER_FORTNIGHT for p in r_idx):
                return
            best = [set(s) for s in schedule]
            found = True
            return
        week_idx = 0 if day < 5 else 1
        remaining = sum(1 for k in range(day, 10) if weekdays[k] != "Sun")
        candidates = list(options_per_day[day])
        random.shuffle(candidates)
        for subset in candidates:
            s = set(subset)
            nc = counts[:]
            nw = [wk[:] for wk in week_counts]
            ok = True
            for p in s:
                nc[p] += 1
                nw[week_idx][p] += 1
                if nc[p] > WFO_PER_MEMBER_FORTNIGHT or nw[week_idx][p] > WEEKLY_WFO:
                    ok = False
                    break
            if not ok:
                continue
            after = remaining - 1
            for p in r_idx:
                if nc[p] + after < WFO_PER_MEMBER_FORTNIGHT:
                    ok = False
                    break
            if not ok:
                continue
            ns = streaks[:]
            for p in r_idx:
                if p in s:
                    ns[p] = streaks[p] + 1
                    if ns[p] >= 3:
                        ok = False
                        break
                else:
                    ns[p] = 0
            if not ok:
                continue
            schedule.append(s)
            backtrack(day + 1, schedule, nc, nw, ns)
            schedule.pop()
            if found:
                return

    backtrack(0, [], [0] * n_team, [[0] * n_team, [0] * n_team], [0] * n_team)
    if not found or best is None:
        raise RuntimeError("Unable to find a valid 2-week pattern.")

    out: List[Set[str]] = []
    for day, s in enumerate(best):
        if weekdays[day] == "Sun":
            out.append(set())
        else:
            out.append(set(forced_office) | {TEAM_MEMBERS[i] for i in s})
    return out


def generate_stable_rotation(seed_bump: int = 0) -> List[Set[str]]:
    """Deterministic rotation for the ROTATING members only. Full-office
    members are not part of the stored pattern — ``rotation_default``
    overlays them as WFO on every office day."""
    state = random.getstate()
    try:
        random.seed(ROTATION_SEED + seed_bump)
        for _ in range(200):
            try:
                pat = generate_two_week_pattern(
                    rotating=list(ROTATING_MEMBERS),
                    forced_office=sorted(FULL_OFFICE_MEMBERS),
                )
                # Strip the forced members: the pattern stores rotation only.
                return [slot - FULL_OFFICE_MEMBERS for slot in pat]
            except RuntimeError:
                continue
        raise RuntimeError("could not generate rotation")
    finally:
        random.setstate(state)


def validate_rotation(pattern: List[Set[str]]) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for an edited rotation. Errors are hard rules.

    ``pattern`` holds only rotating members; full-office members are counted
    as implicitly present on every office day (daily size + mgmt coverage)."""
    errors: List[str] = []
    warnings: List[str] = []
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"] * 2
    n_full = len(FULL_OFFICE_MEMBERS)
    full_mgmt = any(ROLE_BY_MEMBER.get(m) == "mgmt-support" for m in FULL_OFFICE_MEMBERS)
    for i, members in enumerate(pattern):
        wd = weekdays[i]
        label = f"Week {1 + i // 5} {wd}"
        if wd == "Sun":
            if members:
                errors.append(f"{label}: Sundays are always home.")
            continue
        n = len(members) + n_full
        if n < DAILY_OFFICE_MIN or n > DAILY_OFFICE_MAX:
            errors.append(
                f"{label}: {n} in office incl. full-office members "
                f"(need {DAILY_OFFICE_MIN}–{DAILY_OFFICE_MAX})."
            )
        if not full_mgmt and not any(ROLE_BY_MEMBER.get(m) == "mgmt-support" for m in members):
            errors.append(f"{label}: no mgmt-support member present.")
    for m in ROTATING_MEMBERS:
        for w in range(2):
            cnt = sum(1 for i in range(w * 5, w * 5 + 5) if m in pattern[i])
            if cnt != WEEKLY_WFO:
                errors.append(f"{m}: {cnt} office day(s) in week {w + 1} (policy plan is exactly {WEEKLY_WFO}).")
        for w in range(2):
            streak = 0
            for i in range(w * 5, w * 5 + 5):
                if m in pattern[i]:
                    streak += 1
                    if streak >= 3:
                        warnings.append(f"{m}: 3+ consecutive office days in week {w + 1}.")
                        break
                else:
                    streak = 0
    return errors, warnings


# =============================================================================
# POSTGRES LAYER (same vault-credentialed connection as the platform)
# =============================================================================
@st.cache_data(ttl=POSTGRES_DATA_TTL, show_spinner=False)
def _vault_secrets_raw(path: str) -> dict:
    if not _VAULT_AVAILABLE or not path:
        return {}
    vc = _VaultClient()
    cfg = vc.read_all_nested_secrets(path) or {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def _vault_secrets(path: str) -> dict:
    if not _VAULT_AVAILABLE or not path:
        return {}
    try:
        return _vault_secrets_raw(path)
    except Exception:  # noqa: BLE001
        return {}


def _postgres_creds() -> dict:
    cfg = _vault_secrets(POSTGRES_VAULT_PATH)
    if not cfg:
        return {}
    return {
        "host": (cfg.get("host") or "").strip(),
        "port": str(cfg.get("port") or "5432").strip(),
        "database": (cfg.get("database") or "").strip(),
        "username": (cfg.get("username") or "").strip(),
        "password": (cfg.get("password") or "").strip(),
    }


def _pg_safe_ident(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "_." for c in s)


def _pg_connect():
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
    for _name in (WFH_ROTATION_TABLE, WFH_MANUAL_TABLE,
                  WFH_OVERRIDES_TABLE, WFH_SETTINGS_TABLE, WFH_DAY_TIMES_TABLE,
                  WFH_PUBLIC_HOLIDAYS_TABLE, WFH_PERSONAL_HOLIDAYS_TABLE):
        if not _pg_safe_ident(_name):
            raise RuntimeError(f"unsafe table identifier: {_name!r}")
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_ROTATION_TABLE} (
            slot         INT PRIMARY KEY,
            members_wfo  TEXT NOT NULL DEFAULT '',
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_MANUAL_TABLE} (
            day        DATE NOT NULL,
            member     TEXT NOT NULL,
            status     TEXT NOT NULL,
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (day, member)
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_OVERRIDES_TABLE} (
            day        DATE NOT NULL,
            member     TEXT NOT NULL,
            status     TEXT NOT NULL,
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (day, member)
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_SETTINGS_TABLE} (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_DAY_TIMES_TABLE} (
            day             DATE NOT NULL,
            member          TEXT NOT NULL,
            start_time      TIME,
            end_time        TIME,
            late_permission BOOLEAN,
            updated_by      TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (day, member)
        )
    """)
    # Holiday tables — shared with (and identical to) the WFH Schedule page's
    # DDL, ensured here too so the hub is self-sufficient on a fresh database.
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {WFH_PUBLIC_HOLIDAYS_TABLE} (
            day        DATE PRIMARY KEY,
            name       TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    cur.close()
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def _pg_read(fn):
    """Run ``fn(cur)`` on a fresh connection; return its result or None."""
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        out = fn(cur)
        conn.commit()
        cur.close()
        return out
    except Exception:  # noqa: BLE001
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _pg_write(fn, err_label: str) -> bool:
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        fn(cur)
        conn.commit()
        cur.close()
        return True
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save {err_label}: {e}", icon="⚠️")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---- Rotation ----------------------------------------------------------------
def load_rotation() -> List[Set[str]]:
    def _q(cur):
        cur.execute(f"SELECT slot, members_wfo FROM {WFH_ROTATION_TABLE} ORDER BY slot")
        return cur.fetchall()

    rows = _pg_read(_q)
    if rows is None:
        return generate_stable_rotation()
    by_slot = {int(s): (m or "") for s, m in rows}
    if len(by_slot) < 10:
        pattern = generate_stable_rotation()
        save_rotation(pattern, quiet=True)
        return pattern
    return [
        {m for m in by_slot.get(i, "").split(",") if m in TEAM_MEMBERS}
        for i in range(10)
    ]


def save_rotation(pattern: List[Set[str]], quiet: bool = False) -> bool:
    def _w(cur):
        cur.executemany(
            f"INSERT INTO {WFH_ROTATION_TABLE} (slot, members_wfo) VALUES (%s, %s) "
            f"ON CONFLICT (slot) DO UPDATE SET members_wfo = EXCLUDED.members_wfo, "
            f"updated_at = NOW()",
            [(i, ",".join(sorted(pattern[i]))) for i in range(10)],
        )

    if quiet:
        conn = None
        try:
            conn = _pg_connect()
            _pg_ensure_schema(conn)
            cur = conn.cursor()
            _w(cur)
            conn.commit()
            cur.close()
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return _pg_write(_w, "the rotation")


# ---- Settings ----------------------------------------------------------------
def get_detection_start() -> date:
    def _q(cur):
        cur.execute(f"SELECT value FROM {WFH_SETTINGS_TABLE} WHERE key = %s",
                    (DETECTION_START_SETTING,))
        return cur.fetchone()

    row = _pg_read(_q)
    if row and row[0]:
        try:
            return date.fromisoformat(str(row[0]))
        except ValueError:
            pass
    return DEFAULT_DETECTION_START


def set_detection_start(d: date) -> bool:
    return _pg_write(
        lambda cur: cur.execute(
            f"INSERT INTO {WFH_SETTINGS_TABLE} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            (DETECTION_START_SETTING, d.isoformat()),
        ),
        "the detection start date",
    )


def get_late_threshold() -> time:
    """Office starts after this time count as late (Settings-editable)."""
    def _q(cur):
        cur.execute(f"SELECT value FROM {WFH_SETTINGS_TABLE} WHERE key = %s",
                    (LATE_THRESHOLD_SETTING,))
        return cur.fetchone()

    row = _pg_read(_q)
    if row and row[0]:
        try:
            hh, mm = str(row[0]).split(":")[:2]
            return time(int(hh), int(mm))
        except (ValueError, TypeError):
            pass
    return DEFAULT_LATE_THRESHOLD


def set_late_threshold(t: time) -> bool:
    return _pg_write(
        lambda cur: cur.execute(
            f"INSERT INTO {WFH_SETTINGS_TABLE} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            (LATE_THRESHOLD_SETTING, t.strftime("%H:%M")),
        ),
        "the lateness threshold",
    )


# ---- Holidays ----------------------------------------------------------------
def load_public_holidays() -> Dict[str, str]:
    def _q(cur):
        cur.execute(f"SELECT day, name FROM {WFH_PUBLIC_HOLIDAYS_TABLE}")
        return cur.fetchall()

    rows = _pg_read(_q)
    if not rows:
        return dict(DEFAULT_PUBLIC_HOLIDAYS)
    out = {}
    for d, name in rows:
        dd = d if hasattr(d, "isoformat") else date.fromisoformat(str(d))
        out[dd.isoformat()] = str(name)
    return out or dict(DEFAULT_PUBLIC_HOLIDAYS)


def load_personal_holidays() -> Dict[str, List[str]]:
    def _q(cur):
        cur.execute(f"SELECT day, member FROM {WFH_PERSONAL_HOLIDAYS_TABLE}")
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, List[str]] = {}
    for d, m in rows or []:
        iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if m in TEAM_MEMBERS:
            out.setdefault(iso, []).append(m)
    return out


def load_public_holidays_db() -> Dict[str, str] | None:
    """Rows actually stored in the table — no default-list fallback.
    ``None`` means the database is unreachable; ``{}`` means simply empty."""
    def _q(cur):
        cur.execute(f"SELECT day, name FROM {WFH_PUBLIC_HOLIDAYS_TABLE}")
        return cur.fetchall()

    rows = _pg_read(_q)
    if rows is None:
        return None
    out: Dict[str, str] = {}
    for d, name in rows:
        dd = d if hasattr(d, "isoformat") else date.fromisoformat(str(d))
        out[dd.isoformat()] = str(name)
    return out


def upsert_public_holidays(entries: Dict[str, str]) -> bool:
    """Add or rename company holidays: {iso: name}."""
    rows = []
    for iso, name in entries.items():
        try:
            rows.append((date.fromisoformat(iso), str(name).strip()))
        except ValueError:
            continue
    if not rows:
        return True

    def _w(cur):
        cur.executemany(
            f"INSERT INTO {WFH_PUBLIC_HOLIDAYS_TABLE} (day, name) VALUES (%s, %s) "
            f"ON CONFLICT (day) DO UPDATE SET name = EXCLUDED.name, updated_at = NOW()",
            rows,
        )

    return _pg_write(_w, "company holidays")


def delete_public_holidays(isos: List[str]) -> bool:
    days = []
    for iso in isos:
        try:
            days.append((date.fromisoformat(iso),))
        except ValueError:
            continue
    if not days:
        return True
    return _pg_write(
        lambda cur: cur.executemany(
            f"DELETE FROM {WFH_PUBLIC_HOLIDAYS_TABLE} WHERE day = %s", days),
        "company holidays",
    )


# ---- Actuals & overrides ------------------------------------------------------
def _office_ip_regex() -> str:
    """POSIX regex matching the office prefix at the start of the IP or of any
    entry in a proxy chain ("203.0.1.2, 10.26.3.4" — X-Forwarded-For style)."""
    return "(^|[, ])" + re.escape(OFFICE_IP_PREFIX)


# Session rows in the wild are messy: usernames arrive with stray case and
# whitespace, original_user may be '' instead of NULL, and client_ip can be a
# comma-separated proxy chain. All session queries share these conditions.
_SESSION_USER_COND = "LOWER(TRIM(s.username)) = ANY(%s)"
_SESSION_IMPERSONATION_COND = (
    "(s.original_user IS NULL OR TRIM(s.original_user) = '' "
    "OR LOWER(TRIM(s.original_user)) = LOWER(TRIM(s.username)))"
)


def load_ip_actuals(start: date, end: date) -> Dict[str, Dict[str, str]]:
    if not _pg_safe_ident(SESSION_STATES_TABLE):
        return {}
    usernames = [u.lower() for u in MEMBER_TO_SESSION_USER.values()]

    utc_lo, utc_hi = _utc_bounds(start, end)

    def _q(cur):
        cur.execute(
            f"SELECT s.username, "
            f"(s.timestamp AT TIME ZONE 'UTC' AT TIME ZONE %s)::date AS day, "
            f"COUNT(*) FILTER (WHERE s.client_ip ~ %s) AS office_sessions "
            f"FROM {SESSION_STATES_TABLE} AS s "
            f"WHERE {_SESSION_USER_COND} "
            f"AND s.timestamp >= %s AND s.timestamp < %s "
            f"AND {_SESSION_IMPERSONATION_COND} "
            f"GROUP BY s.username, 2",
            (LOCAL_TZ_NAME, _office_ip_regex(), usernames, utc_lo, utc_hi),
        )
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, Dict[str, str]] = {}
    for username, day, office_sessions in rows or []:
        member = SESSION_USER_TO_MEMBER_CI.get(str(username).lower())
        if not member:
            continue
        iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
        out.setdefault(iso, {})[member] = "WFO" if int(office_sessions or 0) > 0 else "WFH"
    return out


def load_ip_times(start: date, end: date) -> Dict[str, Dict[str, Tuple[time, time]]]:
    """Detected office hours: first/last office-IP session per member-day.
    {iso: {member: (first_time, last_time)}} — only days with office sessions."""
    if not _pg_safe_ident(SESSION_STATES_TABLE):
        return {}
    usernames = [u.lower() for u in MEMBER_TO_SESSION_USER.values()]

    utc_lo, utc_hi = _utc_bounds(start, end)

    def _q(cur):
        cur.execute(
            f"SELECT s.username, "
            f"(s.timestamp AT TIME ZONE 'UTC' AT TIME ZONE %s)::date AS day, "
            f"MIN(s.timestamp AT TIME ZONE 'UTC' AT TIME ZONE %s) "
            f"FILTER (WHERE s.client_ip ~ %s) AS first_office, "
            f"MAX(s.timestamp AT TIME ZONE 'UTC' AT TIME ZONE %s) "
            f"FILTER (WHERE s.client_ip ~ %s) AS last_office "
            f"FROM {SESSION_STATES_TABLE} AS s "
            f"WHERE {_SESSION_USER_COND} "
            f"AND s.timestamp >= %s AND s.timestamp < %s "
            f"AND {_SESSION_IMPERSONATION_COND} "
            f"GROUP BY s.username, 2",
            (LOCAL_TZ_NAME, LOCAL_TZ_NAME, _office_ip_regex(),
             LOCAL_TZ_NAME, _office_ip_regex(), usernames, utc_lo, utc_hi),
        )
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, Dict[str, Tuple[time, time]]] = {}
    for username, day, first_ts, last_ts in rows or []:
        member = SESSION_USER_TO_MEMBER_CI.get(str(username).lower())
        if not member or first_ts is None or last_ts is None:
            continue
        iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
        out.setdefault(iso, {})[member] = (first_ts.time(), last_ts.time())
    return out


def load_day_times(start: date, end: date) -> Dict[str, Dict[str, dict]]:
    """Member-set office hours + lateness confirmations from wfh_day_times:
    {iso: {member: {start, end, perm}}} (perm: True/False/None)."""
    def _q(cur):
        cur.execute(
            f"SELECT day, member, start_time, end_time, late_permission "
            f"FROM {WFH_DAY_TIMES_TABLE} WHERE day >= %s AND day <= %s",
            (start, end),
        )
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, Dict[str, dict]] = {}
    for d, m, st_t, en_t, perm in rows or []:
        iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if m in TEAM_MEMBERS:
            out.setdefault(iso, {})[m] = {"start": st_t, "end": en_t, "perm": perm}
    return out


def save_day_times(member: str, entries: Dict[str, dict], by: str) -> bool:
    """entries: {iso: {start: time|None, end: time|None, perm: bool|None}}.
    A row with everything None is deleted."""
    ups, dels = [], []
    for iso, v in entries.items():
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        if v.get("start") is None and v.get("end") is None and v.get("perm") is None:
            dels.append((d, member))
        else:
            ups.append((d, member, v.get("start"), v.get("end"), v.get("perm"), by))

    def _w(cur):
        if ups:
            cur.executemany(
                f"INSERT INTO {WFH_DAY_TIMES_TABLE} "
                f"(day, member, start_time, end_time, late_permission, updated_by) "
                f"VALUES (%s, %s, %s, %s, %s, %s) "
                f"ON CONFLICT (day, member) DO UPDATE SET "
                f"start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time, "
                f"late_permission = EXCLUDED.late_permission, "
                f"updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                ups,
            )
        if dels:
            cur.executemany(
                f"DELETE FROM {WFH_DAY_TIMES_TABLE} WHERE day = %s AND member = %s", dels)

    return _pg_write(_w, "office hours")


def _load_day_member_status(table: str, start: date, end: date,
                            allow_off: bool = False) -> Dict[str, Dict[str, str]]:
    def _q(cur):
        cur.execute(
            f"SELECT day, member, status FROM {table} WHERE day >= %s AND day <= %s",
            (start, end),
        )
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, Dict[str, str]] = {}
    for d, m, sv in rows or []:
        iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if m not in TEAM_MEMBERS:
            continue
        if sv == "WFO":
            status = "WFO"
        elif allow_off and sv == "OFF":
            status = "OFF"
        else:
            status = "WFH"
        out.setdefault(iso, {})[m] = status
    return out


def load_manual(start: date, end: date) -> Dict[str, Dict[str, str]]:
    # Manual records may also be 'OFF' — a self-reported day off.
    return _load_day_member_status(WFH_MANUAL_TABLE, start, end, allow_off=True)


def load_manual_meta(start: date, end: date) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """Manual records with audit info: {iso: {member: (status, updated_by)}}.
    Used by the detection-quality report to attribute corrections."""
    def _q(cur):
        cur.execute(
            f"SELECT day, member, status, updated_by FROM {WFH_MANUAL_TABLE} "
            f"WHERE day >= %s AND day <= %s",
            (start, end),
        )
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, Dict[str, Tuple[str, str]]] = {}
    for d, m, sv, by in rows or []:
        iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
        if m not in TEAM_MEMBERS:
            continue
        status = sv if sv in ("WFO", "WFH", "OFF") else "WFH"
        out.setdefault(iso, {})[m] = (status, (by or "").strip() or "unknown")
    return out


def load_overrides(start: date, end: date) -> Dict[str, Dict[str, str]]:
    return _load_day_member_status(WFH_OVERRIDES_TABLE, start, end)


def _save_day_member_entries(table: str, member: str, entries: Dict[str, str | None],
                             by: str, label: str) -> bool:
    """entries: {iso: 'WFO'|'WFH'|'OFF'|None}; None deletes the row.
    ('OFF' is only meaningful for the manual-attendance table.)"""
    ups = [(date.fromisoformat(iso), member, v, by) for iso, v in entries.items() if v]
    dels = [(date.fromisoformat(iso), member) for iso, v in entries.items() if not v]

    def _w(cur):
        if ups:
            cur.executemany(
                f"INSERT INTO {table} (day, member, status, updated_by) "
                f"VALUES (%s, %s, %s, %s) "
                f"ON CONFLICT (day, member) DO UPDATE SET status = EXCLUDED.status, "
                f"updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                ups,
            )
        if dels:
            cur.executemany(f"DELETE FROM {table} WHERE day = %s AND member = %s", dels)

    return _pg_write(_w, label)


def save_manual_entries(member: str, entries: Dict[str, str | None], by: str) -> bool:
    return _save_day_member_entries(WFH_MANUAL_TABLE, member, entries, by, "attendance")


def save_override_entries(member: str, entries: Dict[str, str | None], by: str) -> bool:
    return _save_day_member_entries(WFH_OVERRIDES_TABLE, member, entries, by, "plan changes")


# =============================================================================
# RESOLUTION & METRICS
# =============================================================================
def resolve_member(identity: str | None) -> str | None:
    if not identity:
        return None
    ident = str(identity).strip()
    if ident.lower() in SESSION_USER_TO_MEMBER_CI:
        return SESSION_USER_TO_MEMBER_CI[ident.lower()]
    low = ident.lower()
    for m in TEAM_MEMBERS:
        if m.lower() == low:
            return m
    tokens = {t for t in re.split(r"[^a-z0-9]+", low.split("@")[0]) if t}
    for m in TEAM_MEMBERS:
        mtok = {t for t in re.split(r"[^a-z0-9]+", MEMBER_TO_SESSION_USER.get(m, "").lower()) if t}
        mtok.add(m.lower())
        if tokens & mtok:
            return m
    return None


class Resolver:
    """Answers plan/actual questions for a period, from pre-loaded data."""

    def __init__(self, rotation, ip, manual, overrides, holidays, public, cutover, today,
                 ip_times=None, day_times=None, late_threshold=DEFAULT_LATE_THRESHOLD):
        self.rotation = rotation
        self.ip = ip
        self.manual = manual
        self.overrides = overrides
        self.holidays = holidays
        self.public = public
        self.cutover = cutover
        self.today = today
        self.ip_times = ip_times or {}
        self.day_times = day_times or {}
        self.late_threshold = late_threshold

    def off_external(self, member: str, d: date) -> str | None:
        """Off for a reason outside the member's own record (holiday registers)."""
        iso = d.isoformat()
        if self.public.get(iso):
            return "Public holiday"
        if member in self.holidays.get(iso, []):
            return "Day off"
        return None

    def is_off(self, member: str, d: date) -> str | None:
        ext = self.off_external(member, d)
        if ext:
            return ext
        # Self-reported day off recorded via "Fix my attendance".
        if self.manual.get(d.isoformat(), {}).get(member) == "OFF":
            return "Day off (self-reported)"
        return None

    def rotation_default(self, member: str, d: date) -> str | None:
        """Pure rotation plan (no overrides). None on Fri/Sat."""
        if not is_workday(d):
            return None
        if member in FULL_OFFICE_MEMBERS:
            # Permanently full-office: WFO every office day, WFH Sundays.
            return "WFO" if is_office_day(d) else "WFH"
        if in_full_office_period(d) and member == NEW_JOINER and is_office_day(d):
            return "WFO"
        slot = rotation_slot(d)
        if slot is None:
            return None
        return "WFO" if member in self.rotation[slot] else "WFH"

    def planned(self, member: str, d: date) -> str | None:
        """Effective plan = per-day override, else the rotation."""
        base = self.rotation_default(member, d)
        if base is None:
            return None
        ov = self.overrides.get(d.isoformat(), {}).get(member)
        return ov or base

    def actual(self, member: str, d: date) -> Tuple[str | None, str | None]:
        """(status, source). Precedence: the member's own record (explicit,
        audited correction) → IP detection. On days before the detection start
        the IP signal is still used as a best-effort *fallback* when it happens
        to exist, tagged ``ip-early`` so views can flag the lower confidence."""
        iso = d.isoformat()
        man = self.manual.get(iso, {}).get(member)
        if man == "OFF":
            # A self-reported day off is handled by is_off(); it is not a
            # WFO/WFH signal and IP must not override it.
            return None, None
        if man:
            return man, "self"
        ipd = self.ip.get(iso, {}).get(member)
        if ipd:
            return ipd, ("ip" if d >= self.cutover else "ip-early")
        return None, None

    def cell(self, member: str, d: date) -> dict:
        off = self.is_off(member, d)
        planned = self.planned(member, d)
        overridden = bool(self.overrides.get(d.isoformat(), {}).get(member))
        if off:
            return {"cat": "off", "note": off, "planned": planned, "actual": None,
                    "source": None, "overridden": overridden}
        actual, source = self.actual(member, d)
        if actual is None:
            if d > self.today:
                return {"cat": "future", "note": None, "planned": planned,
                        "actual": None, "source": None, "overridden": overridden}
            if d.weekday() == 6:
                # Mandated-WFH Sunday with no signal: assume Home rather than
                # nagging for data — consistent with the attendance credit.
                deviation = (planned is not None) and (planned != "WFH")
                return {"cat": "home", "note": None, "planned": planned,
                        "actual": "WFH", "source": "assumed",
                        "deviation": deviation, "overridden": overridden}
            return {"cat": "unknown", "note": None, "planned": planned,
                    "actual": None, "source": None, "overridden": overridden}
        deviation = (planned is not None) and (actual != planned)
        state = {"cat": "office" if actual == "WFO" else "home", "note": None,
                 "planned": planned, "actual": actual, "source": source,
                 "deviation": deviation, "overridden": overridden}
        if state["cat"] == "office":
            oh = self.office_hours(member, d)
            if oh and (oh["start_src"] != "default" or oh["end_src"] != "default"
                       or oh["late"] or oh["short"]):
                parts = [f"{fmt_time(oh['start'])}–{fmt_time(oh['end'])}"]
                if oh["hours"] is not None:
                    parts[0] += f" ({oh['hours']:.1f}h)"
                if oh["late"]:
                    parts.append("late (permitted)" if oh["perm"] else "late (no permission)")
                if oh["short"]:
                    parts.append(f"short day (<{MIN_DAY_HOURS:.0f}h)")
                state["hours_tip"] = " · ".join(parts)
        return state

    def office_hours(self, member: str, d: date) -> dict | None:
        """Hours detail for a known Office/Home day (None otherwise).

        Every tracked day has hours: precedence is member-set start/end →
        (office days only) session-derived first/last office-IP timestamps →
        the 9:00–17:00 default. Returns::

            {kind: 'office'|'home', start, end, start_src, end_src,
             hours, late, perm, short, missing: [..]}

        ``late``  — office start after the threshold (office days only).
        ``perm``  — lateness-permission checkbox (False by default).
        ``short`` — worked < MIN_DAY_HOURS.
        ``missing`` — only 'invalid' remains (end not after start).
        """
        actual, _src = self.actual(member, d)
        if actual not in ("WFO", "WFH"):
            return None
        kind = "office" if actual == "WFO" else "home"
        iso = d.isoformat()
        manual_t = self.day_times.get(iso, {}).get(member, {})
        ip_t = self.ip_times.get(iso, {}).get(member) if kind == "office" else None
        # A single office ping yields first == last — a start signal only.
        ip_end = ip_t[1] if (ip_t and ip_t[1] != ip_t[0]) else None

        start = manual_t.get("start") or (ip_t[0] if ip_t else None) or DEFAULT_WORK_START
        start_src = ("self" if manual_t.get("start")
                     else "ip" if ip_t else "default")
        end = manual_t.get("end") or ip_end or DEFAULT_WORK_END
        end_src = ("self" if manual_t.get("end")
                   else "ip" if ip_end else "default")
        perm = bool(manual_t.get("perm"))
        missing: List[str] = []
        hours = None
        delta = (datetime.combine(d, end) - datetime.combine(d, start)).total_seconds()
        if delta <= 0:
            missing.append("invalid")
        else:
            hours = delta / 3600.0
        late = (kind == "office" and start > self.late_threshold)
        short = (hours < MIN_DAY_HOURS) if hours is not None else None
        return {"kind": kind, "start": start, "end": end,
                "start_src": start_src, "end_src": end_src, "hours": hours,
                "late": late, "perm": perm, "short": short, "missing": missing}


def _off_kind(note: str | None) -> str | None:
    """Classify an is_off() note.

    'public' — company holiday · 'leave' — registered personal holiday ·
    'self' — day off self-recorded in Fix my attendance. ('leave' and 'self'
    both count as personal absence in the policy stats.)"""
    if not note:
        return None
    if note == "Public holiday":
        return "public"
    return "self" if "self-reported" in note else "leave"


_OFF_CELL = {  # kind -> (css class, glyph)
    "public": ("c-public", "P"),   # violet — company holiday
    "leave":  ("c-vac", "V"),      # pink   — registered personal holiday
    "self":   ("c-off", "D"),      # slate  — self-recorded day off
}


def member_stats(res: Resolver, member: str, days: List[date]) -> dict:
    """Policy metrics over ``days``.

    Mon–Thu are the office days the 50% policy is judged on. Sundays are
    mandated WFH workdays: every past, non-off Sunday is automatically
    credited as GOOD attendance (numerator and denominator alike), so
    working the Sunday from home lifts the attendance rate rather than
    being ignored. A Sunday spent in the office still counts as good — it
    just also shows as a plan deviation.

    Past office days with NO data count as assumed Home — in the
    denominator only — so missing data lowers the score until the member
    corrects it (Office raises it; Day off removes the day entirely).
    Plan-adherence numbers stay strictly data-based: assumptions never
    enter the adherence base.
    """
    eligible = known = attended = planned_office = 0
    deviations = dev_skipped = dev_extra = unknown_past = 0
    public_off = personal_off = 0
    sunday_ok = sunday_known = 0
    for d in days:
        if not is_workday(d):
            continue
        off_note = res.is_off(member, d)
        if off_note:
            if _off_kind(off_note) == "public":
                public_off += 1
            else:
                personal_off += 1
            continue
        if d.weekday() == 6:  # Sunday: expected-WFH workday, auto-credited
            if d > res.today:
                continue
            sunday_ok += 1
            plan = res.planned(member, d)
            actual, _src = res.actual(member, d)
            if actual is not None:
                sunday_known += 1
                if plan is not None and actual != plan:
                    deviations += 1
                    if plan == "WFO":
                        dev_skipped += 1
                    else:
                        dev_extra += 1
            continue
        eligible += 1
        plan = res.planned(member, d)
        if plan == "WFO":
            planned_office += 1
        actual, _src = res.actual(member, d)
        if actual is None:
            if d <= res.today:
                unknown_past += 1
            continue
        known += 1
        if actual == "WFO":
            attended += 1
        if plan is not None and actual != plan:
            deviations += 1
            if plan == "WFO":
                dev_skipped += 1     # planned office, stayed home
            else:
                dev_extra += 1       # planned home, came in
    # Past no-data office days count as ASSUMED HOME: they enter the
    # denominator (not the numerator), so the attendance score drops until
    # the member corrects them to Office or records a Day off.
    rated_days = known + sunday_ok + unknown_past
    rate = ((attended + sunday_ok) / rated_days) if rated_days else None
    adh_base = known + sunday_known
    adherence = ((adh_base - deviations) / adh_base) if adh_base else None
    return {
        "member": member, "eligible": eligible, "known": known,
        "attended": attended, "home": known - attended,
        "sunday_ok": sunday_ok, "sunday_known": sunday_known,
        "planned_office": planned_office, "deviations": deviations,
        "dev_skipped": dev_skipped, "dev_extra": dev_extra,
        "unknown_past": unknown_past,
        "off_days": public_off + personal_off,
        "public_off": public_off, "personal_off": personal_off,
        "rate": rate, "adherence": adherence,
        "compliant": (rate is not None and rate >= POLICY_MIN_RATE),
        "low_sample": rated_days < 2,
    }


def member_time_stats(res: Resolver, member: str, days: List[date]) -> dict:
    """Office-hours metrics for one member: averages, lateness, short days.
    Only office days count here — home days carry (default) hours for the
    editor and CSVs but stay out of the punctuality stats."""
    office_days = timed = late = late_perm = late_noperm = 0
    short = missing = 0
    tot_hours = 0.0
    starts: List[time] = []
    for d in days:
        if not is_workday(d) or d > res.today:
            continue
        oh = res.office_hours(member, d)
        if oh is None or oh["kind"] != "office":
            continue
        office_days += 1
        if oh["missing"]:
            missing += 1
        if oh["hours"] is not None:
            timed += 1
            tot_hours += oh["hours"]
        starts.append(oh["start"])
        if oh["late"]:
            late += 1
            if oh["perm"]:
                late_perm += 1
            else:
                late_noperm += 1
        if oh["short"]:
            short += 1
    avg_hours = (tot_hours / timed) if timed else None
    avg_start = None
    if starts:
        secs = sum(t.hour * 3600 + t.minute * 60 + t.second for t in starts) // len(starts)
        avg_start = time(secs // 3600, (secs % 3600) // 60)
    return {
        "member": member, "office_days": office_days, "timed": timed,
        "avg_hours": avg_hours, "avg_start": avg_start,
        "late": late, "late_perm": late_perm, "late_noperm": late_noperm,
        "short": short, "missing": missing,
    }


def fmt_time(t: time | None) -> str:
    return t.strftime("%H:%M") if t else "—"


# =============================================================================
# SMART FILTER BAR (period + members)
# =============================================================================
def _month_window(today: date, offset: int) -> Tuple[date, date]:
    m0 = today.month - 1 + offset
    y = today.year + m0 // 12
    m = m0 % 12 + 1
    start = date(y, m, 1)
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return start, nxt - timedelta(days=1)


def _quarter_window(today: date, offset: int) -> Tuple[date, date]:
    q0 = (today.month - 1) // 3 + offset
    y = today.year + q0 // 4
    q = q0 % 4
    start = date(y, q * 3 + 1, 1)
    endm = q * 3 + 3
    nxt = date(y + (endm == 12), (endm % 12) + 1, 1)
    return start, nxt - timedelta(days=1)


# Preset registry: label -> (navigable, resolver(today, offset, cutover) -> (start, end)).
# Navigable presets step with ◀/▶; anchored ones (detection / YTD) don't.
# Adding a new duration is one entry here — the UI picks it up automatically.
PERIOD_PRESETS: Dict[str, Tuple[bool, object]] = {
    "This week": (True, lambda t, o, c: (sunday_of(t) + timedelta(days=7 * o),
                                         sunday_of(t) + timedelta(days=7 * o + 4))),
    "14 days": (True, lambda t, o, c: (t + timedelta(days=14 * o - 13),
                                       t + timedelta(days=14 * o))),
    "Month": (True, lambda t, o, c: _month_window(t, o)),
    "30 days": (True, lambda t, o, c: (t + timedelta(days=30 * o - 29),
                                       t + timedelta(days=30 * o))),
    "Quarter": (True, lambda t, o, c: _quarter_window(t, o)),
    "Since detection": (False, lambda t, o, c: (min(c, t), t)),
    "Year to date": (False, lambda t, o, c: (date(t.year, 1, 1), t)),
    "Custom": (False, None),
}
DEFAULT_PRESET = "14 days"


def resolve_period(preset: str, offset: int, today: date, custom,
                   cutover: date | None = None) -> Tuple[date, date]:
    cutover = cutover or DEFAULT_DETECTION_START
    if preset == "Custom":
        if (isinstance(custom, (tuple, list)) and len(custom) == 2
                and all(isinstance(x, date) for x in custom)):
            a, b = custom
            return (a, b) if a <= b else (b, a)
        return today - timedelta(days=13), today
    navigable, fn = PERIOD_PRESETS.get(preset, PERIOD_PRESETS[DEFAULT_PRESET])
    return fn(today, offset if navigable else 0, cutover)


def render_filter_bar(today: date, cutover: date,
                      key: str = "hub") -> Tuple[date, date, List[str]]:
    """Period pills + prev/today/next stepping + member include/exclude.
    Returns (start, end, selected_members)."""
    with st.container():
        preset = st.pills(
            "Period", list(PERIOD_PRESETS.keys()), selection_mode="single",
            default=DEFAULT_PRESET, key=f"{key}_preset",
        ) or DEFAULT_PRESET
        if st.session_state.get(f"_{key}_last_preset") != preset:
            st.session_state[f"{key}_offset"] = 0
            st.session_state[f"_{key}_last_preset"] = preset
        offset = st.session_state.get(f"{key}_offset", 0)
        navigable = PERIOD_PRESETS.get(preset, (False, None))[0]

        nav = st.columns([1, 1, 1, 3.2, 3])
        if nav[0].button("◀ Prev", use_container_width=True,
                         disabled=not navigable, key=f"{key}_prev"):
            st.session_state[f"{key}_offset"] = offset - 1
            st.rerun()
        if nav[1].button("Today", use_container_width=True,
                         disabled=not navigable or offset == 0, key=f"{key}_today"):
            st.session_state[f"{key}_offset"] = 0
            st.rerun()
        if nav[2].button("Next ▶", use_container_width=True,
                         disabled=not navigable or offset >= 0, key=f"{key}_next"):
            st.session_state[f"{key}_offset"] = offset + 1
            st.rerun()
        custom = None
        if preset == "Custom":
            with nav[3]:
                custom = st.date_input(
                    "Custom range", value=(today - timedelta(days=13), today),
                    key=f"{key}_custom", label_visibility="collapsed",
                )
        members = st.pills(
            "Members (empty = everyone)", TEAM_MEMBERS, selection_mode="multi",
            default=TEAM_MEMBERS, key=f"{key}_members",
        )
        members = [m for m in (members or []) if m in TEAM_MEMBERS] or list(TEAM_MEMBERS)

    start, end = resolve_period(preset, st.session_state.get(f"{key}_offset", 0),
                                today, custom, cutover)
    n_office = sum(1 for d in workdays_between(start, end) if is_office_day(d))
    scope = "everyone" if len(members) == len(TEAM_MEMBERS) else ", ".join(members)
    st.markdown(
        f"<div class='filter-cap'>📅 {start:%a %d %b %Y} → {end:%a %d %b %Y} · "
        f"{n_office} office days · 👥 {scope}</div>",
        unsafe_allow_html=True,
    )
    return start, end, members


# =============================================================================
# UI PRIMITIVES
# =============================================================================
def rate_pct(rate: float | None) -> str:
    return f"{round(100 * rate)}%" if rate is not None else "—"


def gauge_html(rate: float | None, low_sample: bool = False) -> str:
    if rate is None:
        return ("<div class='gauge'><div class='gauge-track'><div class='gauge-target'></div></div>"
                "<div class='gauge-cap muted'>no data yet</div></div>")
    width = max(0, min(100, round(100 * rate)))
    cls = "ok" if rate >= POLICY_MIN_RATE else "bad"
    note = " · low sample" if low_sample else ""
    return ("<div class='gauge'><div class='gauge-track'>"
            f"<div class='gauge-fill {cls}' style='width:{width}%'></div>"
            "<div class='gauge-target'></div></div>"
            f"<div class='gauge-cap {cls}'>{width}% attendance · policy ≥ 50%{note}</div></div>")


def legend_html(plan_focus: bool = False) -> str:
    chips = [
        ("chip-office", "In office"),
        ("chip-home", "Home"),
        ("chip-dev", "Deviated from plan"),
        ("chip-breach", "Below 50% policy"),
        ("chip-unknown", "? No data — fill in My Space"),
        ("chip-public", "Public holiday"),
        ("chip-vac", "Personal holiday"),
        ("chip-off", "Day off (self)"),
    ]
    if plan_focus:
        chips.insert(2, ("chip-override", "Plan override"))
    inner = "".join(f"<span class='chip {c}'>{t}</span>" for c, t in chips)
    return f"<div class='legend'>{inner}</div>"


def cell_html(state: dict, member: str, d: date) -> str:
    cat = state["cat"]
    plan_lbl = {"WFO": "Office", "WFH": "Home", None: "—"}[state.get("planned")]
    ov = " (override)" if state.get("overridden") else ""
    if cat == "off":
        # Three visually distinct absences — company holiday (violet P),
        # registered personal holiday (pink V), self-recorded day off
        # (slate D). The glyph carries the split for CVD/print too.
        cls, glyph = _OFF_CELL[_off_kind(state.get("note"))]
        return (f"<td class='hcell {cls}' title='{member} · {d:%a %d %b} · "
                f"{state['note']}'>{glyph}</td>")
    if cat in ("unknown", "future"):
        cls = "c-unknown" if cat == "unknown" else "c-future"
        glyph = "?" if cat == "unknown" else ("O" if state.get("planned") == "WFO" else "H")
        note = ("no data, counted as Home — correct it in My Space → Fix my attendance"
                if cat == "unknown" else "upcoming")
        return (f"<td class='hcell {cls}' title='{member} · {d:%a %d %b} · {note} · "
                f"plan: {plan_lbl}{ov}'>{glyph}</td>")
    base = "c-office" if cat == "office" else "c-home"
    dev = " c-dev" if state.get("deviation") else ""
    assumed = " c-assumed" if state.get("source") == "assumed" else ""
    glyph = "O" if cat == "office" else "H"
    src = {"ip": "IP", "ip-early": "IP · before detection start, low confidence",
           "self": "self-reported",
           "assumed": "assumed — WFH Sunday, no data needed"}.get(state["source"], "—")
    actual_lbl = "Office" if state["actual"] == "WFO" else "Home"
    tip = f" · {state['hours_tip']}" if state.get("hours_tip") else ""
    return (f"<td class='hcell {base}{dev}{assumed}' title='{member} · {d:%a %d %b} · "
            f"was {actual_lbl} ({src}) · plan: {plan_lbl}{ov}{tip}'>{glyph}</td>")


def render_day_grid(res: Resolver, members: List[str], days: List[date],
                    breach: Dict[str, bool] | None = None):
    breach = breach or {}
    grid_days = [d for d in days if is_workday(d)]  # Sundays included
    if not grid_days:
        st.caption("No workdays in this range.")
        return
    header = "<th class='hhdr sticky'>Member</th>" + "".join(
        f"<th class='hhdr{' hhdr-sun' if d.weekday() == 6 else ''}'>"
        f"<span>{d:%d}</span><span class='mini'>{d:%a}</span></th>"
        for d in grid_days
    )
    rows = []
    for m in members:
        cls = "hlabel breach" if breach.get(m) else "hlabel"
        badge = " <span class='breach-dot' title='Below 50% policy'></span>" if breach.get(m) else ""
        cells = "".join(cell_html(res.cell(m, d), m, d) for d in grid_days)
        rows.append(f"<tr><td class='{cls} sticky'>{m}{badge}</td>{cells}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def bars_vs_policy(stats: Dict[str, dict], value_key: str = "rate",
                   suffix: str = "attendance") -> None:
    order = sorted(stats.keys(), key=lambda m: (stats[m][value_key] is None,
                                                -(stats[m][value_key] or 0)))
    bars = []
    for m in order:
        v = stats[m][value_key]
        if v is None:
            bars.append(f"<div class='bar-row'><div class='bar-name'>{m}</div>"
                        f"<div class='bar-track'><div class='bar-target'></div></div>"
                        f"<div class='bar-val muted'>—</div></div>")
            continue
        w = max(2, min(100, round(100 * v)))
        good = v >= POLICY_MIN_RATE if value_key == "rate" else v >= 0.8
        cls = "ok" if good else "bad"
        tag = "" if good else " <span class='mini-breach'>low</span>"
        bars.append(
            f"<div class='bar-row'><div class='bar-name'>{m}</div>"
            f"<div class='bar-track' title='{m}: {w}% {suffix}'>"
            f"<div class='bar-fill {cls}' style='width:{w}%'></div>"
            f"<div class='bar-target'></div></div>"
            f"<div class='bar-val {cls}'>{w}%{tag}</div></div>"
        )
    st.markdown(f"<div class='board'>{''.join(bars)}</div>", unsafe_allow_html=True)


# =============================================================================
# SECTION: MY SPACE
# =============================================================================
def render_my_space(res: Resolver, member: str, start: date, end: date):
    days = workdays_between(start, end)
    s = member_stats(res, member, days)
    breach_flag = s["rate"] is not None and not s["compliant"]
    role = "management" if is_mgmt(member) else "engineering"
    flag_cls = "breach" if breach_flag else "ok"
    flag_txt = ("Below policy" if breach_flag
                else "On policy" if s["rate"] is not None else "No data")
    st.markdown(
        f"<div class='panel'><div class='panel-head'>"
        f"<div class='avatar'>{member[0]}</div>"
        f"<div><div class='panel-name'>{member}</div>"
        f"<div class='panel-sub'>{role} · target ≥ 50% office days</div></div>"
        f"<div class='panel-flag {flag_cls}'>{flag_txt}</div></div>"
        f"{gauge_html(s['rate'], s['low_sample'])}</div>",
        unsafe_allow_html=True,
    )

    k = st.columns(4)
    k[0].metric("Attendance score", rate_pct(s["rate"]),
                help="Office days + credited WFH Sundays, over all counted days. "
                     "No-data days count as Home until corrected, so filling "
                     "them can only help your score.")
    k[1].metric("In office", f"{s['attended']}/{s['known']}")
    k[2].metric("Plan deviations", f"{s['deviations']}",
                help="Days your actual differed from your effective plan.")
    k[3].metric("Missing days", f"{s['unknown_past']}",
                help="Past office days with no data — fill them below.")

    if s["unknown_past"]:
        st.markdown(
            f"<div class='fill-cta'>✍️ <b>{s['unknown_past']} day(s) have no data</b> "
            "(the dashed <b>?</b> cells below) — each counts as <b>Home</b> in your "
            "attendance score until you correct it to Office or a Day off in "
            "<b>Fix my attendance</b> just under the grid.</div>",
            unsafe_allow_html=True,
        )

    st.markdown(legend_html(), unsafe_allow_html=True)
    render_day_grid(res, [member], days, {member: breach_flag})

    _render_fix_attendance(res, member, days)


def _render_fix_attendance(res: Resolver, member: str, days: List[date],
                           by: str | None = None, key_prefix: str = "my",
                           title: str = "✍️ Fix my attendance"):
    """Attendance-history editor for ``member``. ``by`` is the audit identity
    stamped on saved rows (defaults to the member themselves); management
    passes its own identity when editing someone else's history."""
    by = by or member
    st.markdown(f"<div class='board-title'>{title}</div>", unsafe_allow_html=True)
    st.caption(
        "Record the real status — Office, Home, or a Day off — and the day's "
        "working hours. Hours default to 9:00–17:00 (office days pick up the "
        f"detected times when available). Starts after {fmt_time(res.late_threshold)} "
        "count as late — tick “Late permission” if a time permission covered it. "
        "Saved records win over detection and are audit-stamped."
    )
    # Public holidays / registered leave stay out of the editor, but a day the
    # member marked off *here* stays editable so it can be reverted.
    editable = [
        d for d in days
        if is_workday(d) and d <= res.today and not res.off_external(member, d)
    ]
    if not editable:
        st.info("No past office days in the selected period.")
        return
    # The editor covers the FULL selected period (past workdays only) —
    # long ranges scroll inside the fixed-height editor instead of truncating.
    st.caption(f"{len(editable)} past workday(s) in the selected period (Sundays included — they default to Home).")

    # Subtle rollup — quiet by design.
    n_late_noperm = n_short = 0
    for d in editable:
        oh = res.office_hours(member, d)
        if not oh:
            continue
        if oh["late"] and not oh["perm"]:
            n_late_noperm += 1
        if oh["short"]:
            n_short += 1
    notes = []
    if n_late_noperm:
        notes.append(f"{n_late_noperm} late day(s) without permission")
    if n_short:
        notes.append(f"{n_short} day(s) under {MIN_DAY_HOURS:.0f}h")
    if notes:
        st.markdown(f"<div class='hours-note'>· {' · '.join(notes)}</div>",
                    unsafe_allow_html=True)

    recs = []
    for d in editable:
        iso = d.isoformat()
        ipd = res.ip.get(iso, {}).get(member) if d >= res.cutover else None
        mine = res.manual.get(iso, {}).get(member)
        mt = res.day_times.get(iso, {}).get(member, {})
        ipt = res.ip_times.get(iso, {}).get(member)
        oh = res.office_hours(member, d)
        detected = {"WFO": "Office", "WFH": "Home", None: "—"}[ipd]
        if ipd == "WFO" and ipt:
            detected += f" {fmt_time(ipt[0])}–{fmt_time(ipt[1]) if ipt[1] != ipt[0] else '…'}"
        # Prefill: saved values → hours engine (detected/9–17 defaults).
        start_v = mt.get("start") or (oh["start"] if oh else DEFAULT_WORK_START)
        end_v = mt.get("end") or (oh["end"] if oh else DEFAULT_WORK_END)
        flags = []
        if oh and oh["late"]:
            flags.append("late")
        if oh and oh["short"]:
            flags.append(f"<{MIN_DAY_HOURS:.0f}h")
        if oh and "invalid" in oh["missing"]:
            flags.append("invalid")
        recs.append({
            "Date": d.strftime("%a %d %b"),
            "_iso": iso,
            "_start0": start_v, "_end0": end_v, "_perm0": bool(mt.get("perm")),
            "Plan": {"WFO": "Office", "WFH": "Home", None: "—"}[res.planned(member, d)],
            "Detected": detected,
            "My record": {"WFO": "Office", "WFH": "Home", "OFF": "Day off", None: "—"}[mine],
            "Start": start_v,
            "End": end_v,
            "Late permission": bool(mt.get("perm")),
            "Hours": ((f"{oh['hours']:.1f}h" if oh and oh["hours"] is not None else "—")
                      + ((" · " + " ".join(flags)) if flags else "")),
        })
    df = pd.DataFrame(recs)
    hidden = ["_iso", "_start0", "_end0", "_perm0"]
    edited = st.data_editor(
        df.drop(columns=hidden),
        column_config={
            "Date": st.column_config.TextColumn(disabled=True),
            "Plan": st.column_config.TextColumn(disabled=True, help="Your effective plan"),
            "Detected": st.column_config.TextColumn(
                disabled=True, help="IP-based detection (status and, for office "
                                    "days, first–last office session)."),
            "My record": st.column_config.SelectboxColumn(
                options=["—", "Office", "Home", "Day off"], default="—",
                help="Your own record — overrides detection when set. "
                     "Day off excludes the day from the 50% policy."),
            "Start": st.column_config.TimeColumn(format="HH:mm", step=300),
            "End": st.column_config.TimeColumn(format="HH:mm", step=300),
            "Late permission": st.column_config.CheckboxColumn(
                default=False,
                help="Tick when a time permission covered a start after "
                     f"{fmt_time(res.late_threshold)}."),
            "Hours": st.column_config.TextColumn(disabled=True),
        },
        hide_index=True, num_rows="fixed", use_container_width=True,
        height=min(520, 64 + 35 * len(recs)),
        key=f"fix_{key_prefix}_{member}",
    )
    save_label = "Save my attendance" if by == member else f"Save {member}'s attendance"
    if st.button(save_label, type="primary", key=f"fix_save_{key_prefix}_{member}"):
        entries: Dict[str, str | None] = {}
        time_entries: Dict[str, dict] = {}
        for i, row in edited.iterrows():
            v = str(row["My record"])
            orig = df.iloc[i]
            entries[orig["_iso"]] = {
                "Office": "WFO", "Home": "WFH", "Day off": "OFF",
            }.get(v)
            sv = row["Start"] if isinstance(row["Start"], time) else None
            ev = row["End"] if isinstance(row["End"], time) else None
            pv = bool(row["Late permission"])
            # Delta-save: only persist rows the user actually changed, so
            # detected times keep flowing for untouched days.
            if (sv != orig["_start0"] or ev != orig["_end0"] or pv != orig["_perm0"]):
                time_entries[orig["_iso"]] = {"start": sv, "end": ev, "perm": pv}
        ok = save_manual_entries(member, entries, by=by)
        if time_entries:
            ok = save_day_times(member, time_entries, by=by) and ok
        if ok:
            st.success("Attendance saved.")
            st.rerun()


# =============================================================================
# SECTION: TEAM & SCHEDULE
# =============================================================================
@st.fragment(run_every=60)
def render_today_strip(res: Resolver, members: List[str]):
    """Live who's-in strip. Re-queries session_states every 60s on its own —
    deliberately independent of the page's period filter, so today's
    detections always show even when a past range is selected. All data it
    needs is fetched inside the fragment (no outside-widget dependency)."""
    d = res.today
    while not is_workday(d) or res.public.get(d.isoformat()):
        d += timedelta(days=1)
        if (d - res.today).days > 14:
            return
    label = "Today" if d == res.today else f"Next workday · {d:%a %d %b}"

    # Fresh, single-day fetch on every fragment tick — never the page cache.
    ip_now = load_ip_actuals(d, d).get(d.isoformat(), {})
    times_now = load_ip_times(d, d).get(d.isoformat(), {})

    planned_in = [m for m in members
                  if res.planned(m, d) == "WFO" and not res.is_off(m, d)]
    detected_in = [m for m in members if ip_now.get(m) == "WFO"]
    home_active = [m for m in members
                   if ip_now.get(m) == "WFH" and not res.is_off(m, d)]
    on_leave = [m for m in members if res.is_off(m, d)]

    def _since(m: str) -> str:
        t = times_now.get(m)
        return f" · since {fmt_time(t[0])}" if t and t[0] else ""

    pl = "".join(f"<span class='pill pill-plan'>{m}</span>" for m in planned_in) or \
         "<span class='pill-empty'>nobody planned</span>"
    dt = "".join(f"<span class='pill pill-live'>{m}{_since(m)}</span>"
                 for m in detected_in) or \
         "<span class='pill-empty'>none detected yet</span>"
    hm = "".join(f"<span class='pill pill-plan'>{m}</span>" for m in home_active)
    hm_row = (f"<div class='strip-row'><span class='strip-k'>Active from home</span>{hm}</div>"
              if hm else "")
    lv = ("".join(f"<span class='pill pill-off'>{m}</span>" for m in on_leave)) if on_leave else ""
    lv_row = f"<div class='strip-row'><span class='strip-k'>On leave</span>{lv}</div>" if lv else ""
    stamp = local_now().strftime("%H:%M:%S")
    st.markdown(
        f"<div class='panel strip'><div class='strip-title'>{label}"
        f"<span class='strip-live'>● live · checked {stamp}</span></div>"
        f"<div class='strip-row'><span class='strip-k'>Planned in office</span>{pl}</div>"
        f"<div class='strip-row'><span class='strip-k'>Detected in office</span>{dt}</div>"
        f"{hm_row}{lv_row}</div>",
        unsafe_allow_html=True,
    )
    if st.button("🔄 Check now", key="strip_refresh", help="Re-query detection immediately."):
        st.rerun(scope="fragment")


def _pattern_to_df(pattern: List[Set[str]]) -> pd.DataFrame:
    """Editor dataframe — ROTATING members only; full-office members are
    fixed (always in) and don't appear as editable rows."""
    cols = [f"W{w + 1} {wd}" for w in range(2) for wd in ["Mon", "Tue", "Wed", "Thu"]]
    slots = [1, 2, 3, 4, 6, 7, 8, 9]  # office slots (skip Sundays 0 and 5)
    data = {}
    for c, slot in zip(cols, slots):
        data[c] = [m in pattern[slot] for m in ROTATING_MEMBERS]
    df = pd.DataFrame(data, index=ROTATING_MEMBERS)
    df.insert(0, "Member", ROTATING_MEMBERS)
    return df


def _df_to_pattern(df: pd.DataFrame) -> List[Set[str]]:
    cols = [f"W{w + 1} {wd}" for w in range(2) for wd in ["Mon", "Tue", "Wed", "Thu"]]
    slots = [1, 2, 3, 4, 6, 7, 8, 9]
    pattern: List[Set[str]] = [set() for _ in range(10)]
    for c, slot in zip(cols, slots):
        for i, m in enumerate(ROTATING_MEMBERS):
            if bool(df.iloc[i][c]):
                pattern[slot].add(m)
    return pattern


def render_schedule(res: Resolver, viewer: str | None, start: date, end: date,
                    members: List[str]):
    render_today_strip(res, members)

    st.markdown("<div class='board-title'>📆 Next two weeks — effective plan</div>",
                unsafe_allow_html=True)
    st.caption("Rotation + overrides + holidays for the filtered members. Sundays default to Home but can be overridden. Orange ring = override.")
    horizon = workdays_between(sunday_of(res.today), sunday_of(res.today) + timedelta(days=13))
    _render_plan_grid(res, horizon, members)

    st.markdown("<div class='board-title'>🔁 The repeating two-week rotation</div>",
                unsafe_allow_html=True)
    can_edit = is_mgmt(viewer)
    if can_edit:
        full = ", ".join(sorted(FULL_OFFICE_MEMBERS))
        st.caption(
            "Tick = in office. Save validates the team rules; you can save "
            "anyway if you accept the listed violations. Sundays are always home"
            + (f"; {full} works full-office and is counted automatically on every "
               "office day (no editable row)." if full else ".")
        )
        df = _pattern_to_df(res.rotation)
        cfg = {"Member": st.column_config.TextColumn(disabled=True)}
        for c in df.columns[1:]:
            cfg[c] = st.column_config.CheckboxColumn(help="In office?")
        edited = st.data_editor(
            df, column_config=cfg, hide_index=True, num_rows="fixed",
            use_container_width=True, key="rotation_editor",
        )
        new_pattern = _df_to_pattern(edited)
        errors, warnings = validate_rotation(new_pattern)
        if errors:
            st.markdown(
                "<div class='vbox vbox-err'><b>Rule violations</b><ul>"
                + "".join(f"<li>{e}</li>" for e in errors[:8])
                + (f"<li>… and {len(errors) - 8} more</li>" if len(errors) > 8 else "")
                + "</ul></div>",
                unsafe_allow_html=True,
            )
        if warnings:
            st.markdown(
                "<div class='vbox vbox-warn'><b>Preferences</b><ul>"
                + "".join(f"<li>{w}</li>" for w in warnings[:6]) + "</ul></div>",
                unsafe_allow_html=True,
            )
        if not errors and not warnings:
            st.markdown("<div class='vbox vbox-ok'>✓ All team rules satisfied.</div>",
                        unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1.4, 1.6, 3])
        force = c2.checkbox("Save despite violations", value=False,
                            disabled=not errors, key="rot_force")
        if c1.button("💾 Save rotation", type="primary", key="rot_save",
                     disabled=bool(errors) and not force):
            if save_rotation(new_pattern):
                st.success("Rotation saved — it now repeats for all future weeks.")
                st.rerun()
        with c3.popover("✨ Auto-generate instead"):
            st.caption("Generate a fresh rule-valid rotation and save it.")
            if st.button("Generate & save", key="rot_regen"):
                bump = st.session_state.get("rot_bump", 0) + 1
                st.session_state["rot_bump"] = bump
                try:
                    pat = generate_stable_rotation(seed_bump=bump)
                except RuntimeError:
                    st.error("Could not generate a valid rotation, try again.")
                    pat = None
                if pat and save_rotation(pat):
                    st.success("New rotation saved.")
                    st.rerun()
    else:
        _render_rotation_readonly(res.rotation)
        st.info("Management can edit the rotation here; you can adjust your own "
                "days in My Space.", icon="🔒")

    if can_edit:
        with st.expander("🛠 Management: edit a member's plan overrides"):
            _render_member_overrides_editor(res, viewer)
        with st.expander("🗂 Management: edit a member's attendance history"):
            st.caption(
                "Correct any member's past records over the selected period — "
                "same rules as self-editing; every save is stamped with your name."
            )
            target = st.selectbox("Member", TEAM_MEMBERS, key="hist_member")
            _render_fix_attendance(
                res, target, workdays_between(start, end),
                by=viewer, key_prefix="mgmt",
                title=f"✍️ {target}'s attendance history",
            )


def _render_plan_grid(res: Resolver, days: List[date], members: List[str] | None = None):
    plan_days = [d for d in days if is_workday(d)]  # Sundays included (default Home)
    header = "<th class='hhdr sticky'>Member</th>" + "".join(
        f"<th class='hhdr{' hhdr-sun' if d.weekday() == 6 else ''}'>"
        f"<span>{d:%a}</span><span class='mini'>{d:%d %b}</span></th>"
        for d in plan_days
    )
    rows = []
    for m in (members or TEAM_MEMBERS):
        cells = []
        for d in plan_days:
            off = res.is_off(m, d)
            plan = res.planned(m, d)
            ov = bool(res.overrides.get(d.isoformat(), {}).get(m))
            if off:
                cls, glyph = _OFF_CELL[_off_kind(off)]
                cells.append(f"<td class='hcell {cls}' title='{m} · {d:%a %d %b} · {off}'>{glyph}</td>")
                continue
            base = "c-office" if plan == "WFO" else "c-home"
            ring = " c-dev" if ov else ""
            glyph = "O" if plan == "WFO" else "H"
            note = " (override)" if ov else ""
            cells.append(
                f"<td class='hcell {base}{ring}' "
                f"title='{m} · {d:%a %d %b} · plan: "
                f"{'Office' if plan == 'WFO' else 'Home'}{note}'>{glyph}</td>"
            )
        rows.append(f"<tr><td class='hlabel sticky'>{m}</td>{''.join(cells)}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _render_rotation_readonly(pattern: List[Set[str]]):
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"]
    header = "<th class='hhdr sticky'>Member</th>" + "".join(
        f"<th class='hhdr'>W{w + 1} {wd}</th>" for w in range(2) for wd in weekdays
    )
    rows = []
    for m in TEAM_MEMBERS:
        cells = []
        for slot in range(10):
            in_office = (
                weekdays[slot % 5] != "Sun"
                and (m in pattern[slot] or m in FULL_OFFICE_MEMBERS)
            )
            if in_office:
                cells.append("<td class='hcell c-office'>O</td>")
            else:
                cells.append("<td class='hcell c-home'>H</td>")
        tag = " <span class='mini-full'>full office</span>" if m in FULL_OFFICE_MEMBERS else ""
        rows.append(f"<tr><td class='hlabel sticky'>{m}{tag}</td>{''.join(cells)}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _render_member_overrides_editor(res: Resolver, viewer: str):
    target = st.selectbox("Member", TEAM_MEMBERS, key="ov_member")
    upcoming = [
        d for d in workdays_between(res.today, res.today + timedelta(days=28))
        if is_workday(d) and not res.is_off(target, d)
    ]
    if not upcoming:
        st.info("No upcoming office days.")
        return
    recs = []
    for d in upcoming:
        iso = d.isoformat()
        rot = res.rotation_default(target, d)
        ov = res.overrides.get(iso, {}).get(target)
        recs.append({
            "Date": d.strftime("%a %d %b"), "_iso": iso,
            "Rotation": {"WFO": "Office", "WFH": "Home", None: "—"}[rot],
            "Plan": {"WFO": "Office", "WFH": "Home", None: "Rotation"}[ov],
        })
    df = pd.DataFrame(recs)
    edited = st.data_editor(
        df.drop(columns=["_iso"]),
        column_config={
            "Date": st.column_config.TextColumn(disabled=True),
            "Rotation": st.column_config.TextColumn(disabled=True),
            "Plan": st.column_config.SelectboxColumn(
                options=["Rotation", "Office", "Home"], default="Rotation"),
        },
        hide_index=True, num_rows="fixed", use_container_width=True,
        key=f"ov_editor_{target}",
    )
    if st.button(f"Save {target}'s plan", type="secondary", key=f"ov_save_{target}"):
        entries: Dict[str, str | None] = {}
        for i, row in edited.iterrows():
            v = str(row["Plan"])
            entries[df.iloc[i]["_iso"]] = (
                "WFO" if v == "Office" else "WFH" if v == "Home" else None
            )
        if save_override_entries(target, entries, by=viewer):
            st.success(f"{target}'s plan updated.")
            st.rerun()


# =============================================================================
# SECTION: REPORTS
# =============================================================================
def _daily_detail_rows(res: Resolver, days: List[date],
                       members: List[str] | None = None) -> List[dict]:
    rows = []
    for d in days:
        if not is_workday(d):    # Sundays included; Fri/Sat excluded
            continue
        for m in (members or TEAM_MEMBERS):
            off = res.is_off(m, d)
            plan = res.planned(m, d)
            actual, source = res.actual(m, d)
            if (actual is None and not off and d.weekday() == 6
                    and d <= res.today):
                # Mandated-WFH Sunday with no signal — assumed Home.
                actual, source = "WFH", "assumed"
            rows.append({
                "date": d.isoformat(),
                "weekday": d.strftime("%a"),
                "member": m,
                "planned": {"WFO": "Office", "WFH": "Home", None: ""}[plan],
                "actual": {"WFO": "Office", "WFH": "Home", None: ""}[actual],
                "source": source or "",
                "off": off or "",
                "off_kind": _off_kind(off) or "",
                "deviation": ("yes" if (not off and actual and plan and actual != plan) else ""),
                "start": "", "end": "", "hours": "", "late": "",
                "late_permission": "", "short_day": "",
            })
            oh = res.office_hours(m, d)
            if oh is not None:
                rows[-1].update({
                    "start": fmt_time(oh["start"]) if oh["start"] else "",
                    "end": fmt_time(oh["end"]) if oh["end"] else "",
                    "hours": (f"{oh['hours']:.2f}" if oh["hours"] is not None else ""),
                    "late": "yes" if oh["late"] else ("" if oh["late"] is None else "no"),
                    "late_permission": {True: "with permission", False: "no permission",
                                        None: ""}[oh["perm"]],
                    "short_day": "yes" if oh["short"] else "",
                })
    return rows


def _detection_analysis(res: Resolver,
                        manual_meta: Dict[str, Dict[str, Tuple[str, str]]],
                        days: List[date],
                        members: List[str] | None = None) -> dict:
    """Compare every manual correction against what IP detection said.

    Only office days on/after the detection start (and not in the future) are
    in scope. Buckets:
      * agree        — manual matches detection (confirms it)
      * missed_office— detected Home, corrected to Office → office egress IP
                       likely missing from the office prefix
      * ghost_office — detected Office, corrected to Home → a non-office
                       network (VPN/NAT) is inside the prefix, or a shared login
      * dayoff_activity — sessions detected on a self-recorded day off
      * no_signal    — correction on a day detection saw nothing (coverage gap)
    """
    conflicts: List[dict] = []
    agree = no_signal = 0
    for d in days:
        if not is_workday(d) or d < res.cutover or d > res.today:
            continue
        iso = d.isoformat()
        for m in (members or TEAM_MEMBERS):
            rec = manual_meta.get(iso, {}).get(m)
            if not rec:
                continue
            man, by = rec
            ip = res.ip.get(iso, {}).get(m)
            if ip is None:
                no_signal += 1
                continue
            det_lbl = "Office" if ip == "WFO" else "Home"
            if man == "OFF":
                conflicts.append({
                    "date": iso, "weekday": d.strftime("%a"), "member": m,
                    "detected": det_lbl, "corrected_to": "Day off",
                    "edited_by": by, "category": "Day-off activity",
                    "hint": ("Office-IP sessions on a day off — check shared "
                             "logins / stale sessions" if ip == "WFO" else
                             "Home sessions on a day off — likely worked while off"),
                })
            elif man != ip:
                missed = man == "WFO"
                conflicts.append({
                    "date": iso, "weekday": d.strftime("%a"), "member": m,
                    "detected": det_lbl,
                    "corrected_to": "Office" if man == "WFO" else "Home",
                    "edited_by": by,
                    "category": "Missed office" if missed else "Ghost office",
                    "hint": ("Was in office but no session came from the office "
                             "prefix — an office egress IP may be missing"
                             if missed else
                             "Detected office but was home — a VPN/NAT range may "
                             "wrongly match the prefix, or a shared login"),
                })
            else:
                agree += 1
    reviewed = agree + no_signal + len(conflicts)
    return {
        "conflicts": conflicts, "agree": agree, "no_signal": no_signal,
        "reviewed": reviewed,
        "missed": sum(1 for c in conflicts if c["category"] == "Missed office"),
        "ghost": sum(1 for c in conflicts if c["category"] == "Ghost office"),
        "dayoff": sum(1 for c in conflicts if c["category"] == "Day-off activity"),
    }


def _hours_detail_rows(res: Resolver, days: List[date],
                       members: List[str]) -> List[dict]:
    rows = []
    for d in days:
        if not is_workday(d) or d > res.today:
            continue
        for m in members:
            oh = res.office_hours(m, d)
            if oh is None:
                continue
            rows.append({
                "date": d.isoformat(), "weekday": d.strftime("%a"), "member": m,
                "day_kind": oh["kind"],
                "start": fmt_time(oh["start"]),
                "end": fmt_time(oh["end"]),
                "start_source": oh["start_src"],
                "end_source": oh["end_src"],
                "hours": (f"{oh['hours']:.2f}" if oh["hours"] is not None else ""),
                "late": "yes" if oh["late"] else "no",
                "late_permission": ("with permission" if oh["perm"] else "no permission")
                                   if oh["late"] else "",
                "short_day": "yes" if oh["short"] else "",
                "invalid": "yes" if "invalid" in oh["missing"] else "",
            })
    return rows


def _render_hours_report(res: Resolver, start: date, end: date,
                         members: List[str], stats: Dict[str, dict],
                         period_tag: str):
    st.markdown(
        "<div class='report-head'><div class='report-title'>Hours & punctuality</div>"
        f"<div class='report-sub'>{start:%d %b %Y} → {end:%d %b %Y} · office start/end times "
        f"(member-set over detected) · late = start after {fmt_time(res.late_threshold)} · "
        f"short = under {MIN_DAY_HOURS:.0f}h</div></div>",
        unsafe_allow_html=True,
    )
    days = workdays_between(start, end)
    tstats = {m: member_time_stats(res, m, days) for m in members}

    total_timed = sum(t["timed"] for t in tstats.values())
    tot_hours = sum((t["avg_hours"] or 0) * t["timed"] for t in tstats.values())
    team_avg = (tot_hours / total_timed) if total_timed else None
    total_late = sum(t["late"] for t in tstats.values())
    total_perm = sum(t["late_perm"] for t in tstats.values())
    total_noperm = sum(t["late_noperm"] for t in tstats.values())
    total_short = sum(t["short"] for t in tstats.values())
    total_invalid = sum(t["missing"] for t in tstats.values())

    k = st.columns(4)
    k[0].metric("Avg office day", f"{team_avg:.1f}h" if team_avg is not None else "—",
                help=f"Across {total_timed} office days (hours default to "
                     f"{fmt_time(DEFAULT_WORK_START)}–{fmt_time(DEFAULT_WORK_END)} "
                     "until corrected).")
    k[1].metric("Late arrivals", f"{total_late}",
                help=f"{total_perm} with permission · {total_noperm} without.")
    k[2].metric("Short days", f"{total_short}", help=f"Office days under {MIN_DAY_HOURS:.0f}h.")
    k[3].metric("Invalid times", f"{total_invalid}",
                help="Office days where the end is not after the start.")

    if total_noperm:
        st.markdown(
            f"<div class='hours-note'>· {total_noperm} lateness(es) without permission</div>",
            unsafe_allow_html=True,
        )

    # Average office-day length vs the 8h target (scaled to a 12h track).
    st.markdown(f"<div class='board-title'>Average office day vs {MIN_DAY_HOURS:.0f}h</div>",
                unsafe_allow_html=True)
    scale = 12.0
    bars = []
    order = sorted(members, key=lambda m: (tstats[m]["avg_hours"] is None,
                                           -(tstats[m]["avg_hours"] or 0)))
    for m in order:
        t = tstats[m]
        if t["avg_hours"] is None:
            bars.append(f"<div class='bar-row'><div class='bar-name'>{m}</div>"
                        f"<div class='bar-track'><div class='bar-target' "
                        f"style='left:{MIN_DAY_HOURS/scale*100:.0f}%'></div></div>"
                        f"<div class='bar-val muted'>—</div></div>")
            continue
        w = max(2, min(100, round(100 * t["avg_hours"] / scale)))
        good = t["avg_hours"] >= MIN_DAY_HOURS
        cls = "ok" if good else "bad"
        tip = (f"{m}: avg {t['avg_hours']:.1f}h over {t['timed']} timed day(s), "
               f"avg start {fmt_time(t['avg_start'])}")
        bars.append(
            f"<div class='bar-row'><div class='bar-name'>{m}</div>"
            f"<div class='bar-track' title='{tip}'>"
            f"<div class='bar-fill {cls}' style='width:{w}%'></div>"
            f"<div class='bar-target' style='left:{MIN_DAY_HOURS/scale*100:.0f}%'></div></div>"
            f"<div class='bar-val {cls}'>{t['avg_hours']:.1f}h</div></div>"
        )
    st.markdown(f"<div class='board'>{''.join(bars)}</div>", unsafe_allow_html=True)
    st.caption(f"Track scaled to {scale:.0f}h; the dark marker is the {MIN_DAY_HOURS:.0f}h target.")

    summary = pd.DataFrame([{
        "Member": m,
        "Office days": tstats[m]["office_days"],
        "Timed days": tstats[m]["timed"],
        "Avg start": fmt_time(tstats[m]["avg_start"]),
        "Avg hours": (f"{tstats[m]['avg_hours']:.1f}h"
                      if tstats[m]["avg_hours"] is not None else "—"),
        "Late": tstats[m]["late"],
        "· with permission": tstats[m]["late_perm"],
        "· no permission": tstats[m]["late_noperm"],
        "Short days": tstats[m]["short"],
        "Invalid times": tstats[m]["missing"],
    } for m in members])
    st.dataframe(summary, hide_index=True, use_container_width=True)

    d1, d2 = st.columns(2)
    d1.download_button(
        "⬇️ Export summary (CSV)", summary.to_csv(index=False).encode(),
        file_name=f"hours_summary_{period_tag}.csv", mime="text/csv",
        use_container_width=True,
    )
    detail = pd.DataFrame(_hours_detail_rows(res, days, members))
    d2.download_button(
        "⬇️ Export day-level detail (CSV)",
        (detail.to_csv(index=False) if not detail.empty else "").encode(),
        file_name=f"hours_detail_{period_tag}.csv", mime="text/csv",
        use_container_width=True, disabled=detail.empty,
    )
    if detail.empty:
        st.info("No office days in this period yet — hours build up from "
                "office-IP sessions and the Fix-my-attendance editor in My Space.")


def render_reports(res: Resolver, start: date, end: date, viewer: str | None,
                   members: List[str] | None = None):
    members = members or list(TEAM_MEMBERS)
    days = workdays_between(start, end)
    stats = {m: member_stats(res, m, days) for m in members}
    period_tag = f"{start.isoformat()}_{end.isoformat()}"

    kind = st.radio(
        "Report",
        ["🗂 Management — actual attendance", "🎯 Team — plan vs actual",
         "⏱ Hours & punctuality", "🔬 Detection quality — corrections vs IP"],
        horizontal=True, key="report_kind", label_visibility="collapsed",
    )

    if kind.startswith("🗂"):
        st.markdown(
            "<div class='report-head'><div class='report-title'>Management report — actual attendance</div>"
            f"<div class='report-sub'>All members · {start:%d %b %Y} → {end:%d %b %Y} · "
            "actual WFO/WFH regardless of plan · measured against the ≥50% office policy</div></div>",
            unsafe_allow_html=True,
        )
        total_office = sum(s["attended"] for s in stats.values())
        total_sunday = sum(s["sunday_ok"] for s in stats.values())
        total_home = sum(s["home"] for s in stats.values())
        total_known = sum(s["known"] for s in stats.values())
        total_assumed = sum(s["unknown_past"] for s in stats.values())
        rated = total_known + total_sunday + total_assumed
        team_rate = ((total_office + total_sunday) / rated) if rated else None
        rated = [s for s in stats.values() if s["rate"] is not None]
        k = st.columns(4)
        k[0].metric("Team attendance", rate_pct(team_rate),
                    help="Office days + credited WFH Sundays over all counted days.")
        k[1].metric("Office days", f"{total_office}")
        k[2].metric("Home days", f"{total_home}")
        k[3].metric("Below policy", f"{sum(1 for s in rated if not s['compliant'])}",
                    help="Members under 50% office in this period.")

        st.markdown("<div class='board-title'>Attendance vs the 50% policy (WFH Sundays credited)</div>",
                    unsafe_allow_html=True)
        bars_vs_policy(stats, "rate", "attendance")

        summary = pd.DataFrame([{
            "Member": m,
            "Eligible days": stats[m]["eligible"],
            "Office": stats[m]["attended"],
            "Home": stats[m]["home"],
            "Sundays ✓": stats[m]["sunday_ok"],
            "Public holidays": stats[m]["public_off"],
            "Days off": stats[m]["personal_off"],
            "No data (= Home)": stats[m]["unknown_past"],
            "Attendance": rate_pct(stats[m]["rate"]),
            "Policy (≥50%)": ("✅ met" if stats[m]["compliant"]
                              else "❌ below" if stats[m]["rate"] is not None else "— no data"),
        } for m in members])
        st.dataframe(summary, hide_index=True, use_container_width=True)

        st.markdown("<div class='board-title'>Day by day (actuals)</div>", unsafe_allow_html=True)
        st.markdown(legend_html(), unsafe_allow_html=True)
        render_day_grid(res, members, days,
                        {m: (stats[m]["rate"] is not None and not stats[m]["compliant"])
                         for m in members})
        st.caption(
            "Dashed ? cells have no data and count as HOME in the attendance score "
            "until corrected — members fix their own days in My Space → Fix my "
            "attendance (management can backfill via Team & Schedule); marking a "
            "Day off removes the day from the score entirely. Sundays with no data "
            "show as assumed Home. Days before the detection start fall back to the "
            "IP signal where one exists (marked low-confidence in the tooltip)."
        )

        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ Export summary (CSV)", summary.to_csv(index=False).encode(),
            file_name=f"attendance_summary_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
        detail = pd.DataFrame(_daily_detail_rows(res, days, members))
        d2.download_button(
            "⬇️ Export day-level detail (CSV)", detail.to_csv(index=False).encode(),
            file_name=f"attendance_detail_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
    elif kind.startswith("🎯"):
        st.markdown(
            "<div class='report-head'><div class='report-title'>Team report — plan vs actual</div>"
            f"<div class='report-sub'>{start:%d %b %Y} → {end:%d %b %Y} · how closely reality "
            "followed the rotation + overrides · orange marks deviations</div></div>",
            unsafe_allow_html=True,
        )
        total_known = sum(s["known"] + s["sunday_known"] for s in stats.values())
        total_dev = sum(s["deviations"] for s in stats.values())
        team_adh = ((total_known - total_dev) / total_known) if total_known else None
        k = st.columns(4)
        k[0].metric("Team adherence", rate_pct(team_adh),
                    help="Known days where actual matched the plan.")
        k[1].metric("Deviations", f"{total_dev}")
        k[2].metric("Skipped office", f"{sum(s['dev_skipped'] for s in stats.values())}",
                    help="Planned office, stayed home.")
        k[3].metric("Unplanned office", f"{sum(s['dev_extra'] for s in stats.values())}",
                    help="Planned home, came in.")

        st.markdown("<div class='board-title'>Plan adherence by member</div>",
                    unsafe_allow_html=True)
        bars_vs_policy(stats, "adherence", "adherence")

        summary = pd.DataFrame([{
            "Member": m,
            "Planned office": stats[m]["planned_office"],
            "Actual office": stats[m]["attended"],
            "Followed plan": stats[m]["known"] - stats[m]["deviations"],
            "Skipped office": stats[m]["dev_skipped"],
            "Unplanned office": stats[m]["dev_extra"],
            "Adherence": rate_pct(stats[m]["adherence"]),
        } for m in members])
        st.dataframe(summary, hide_index=True, use_container_width=True)

        st.markdown("<div class='board-title'>Day by day (deviations in orange)</div>",
                    unsafe_allow_html=True)
        st.markdown(legend_html(plan_focus=True), unsafe_allow_html=True)
        render_day_grid(res, members, days)

        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ Export summary (CSV)", summary.to_csv(index=False).encode(),
            file_name=f"plan_vs_actual_summary_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
        detail = pd.DataFrame(_daily_detail_rows(res, days, members))
        d2.download_button(
            "⬇️ Export day-level detail (CSV)", detail.to_csv(index=False).encode(),
            file_name=f"plan_vs_actual_detail_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
    elif kind.startswith("⏱"):
        _render_hours_report(res, start, end, members, stats, period_tag)
    else:
        st.markdown(
            "<div class='report-head'><div class='report-title'>Detection quality — corrections vs IP</div>"
            f"<div class='report-sub'>{start:%d %b %Y} → {end:%d %b %Y} · every manual correction "
            "compared against what IP detection said, to find where detection needs improving "
            f"(office prefix <code>{OFFICE_IP_PREFIX}</code>, active since {res.cutover:%d %b %Y})</div></div>",
            unsafe_allow_html=True,
        )
        manual_meta = load_manual_meta(start, min(end, res.today))
        ana = _detection_analysis(res, manual_meta, days, members)

        k = st.columns(4)
        k[0].metric("Corrections reviewed", f"{ana['reviewed']}",
                    help="Manual records on days where detection was active.")
        acc = (ana["agree"] / (ana["agree"] + len(ana["conflicts"]))
               if (ana["agree"] + len(ana["conflicts"])) else None)
        k[1].metric("Detection accuracy", rate_pct(acc),
                    help="Of corrections with an IP signal, how many detection already had right.")
        k[2].metric("Conflicts", f"{len(ana['conflicts'])}",
                    help="Corrections that contradict detection — the enhancement backlog.")
        k[3].metric("No IP signal", f"{ana['no_signal']}",
                    help="Corrections on days detection saw nothing — coverage gaps.")

        b = st.columns(3)
        b[0].metric("🏢 Missed office", f"{ana['missed']}",
                    help="Was in office, detected Home → likely a missing office egress IP.")
        b[1].metric("👻 Ghost office", f"{ana['ghost']}",
                    help="Detected Office, was home → VPN/NAT inside the prefix or a shared login.")
        b[2].metric("🌴 Day-off activity", f"{ana['dayoff']}",
                    help="Sessions detected on a self-recorded day off.")

        if ana["conflicts"]:
            df = pd.DataFrame(ana["conflicts"])[
                ["date", "weekday", "member", "detected", "corrected_to",
                 "edited_by", "category", "hint"]
            ].rename(columns={
                "date": "Date", "weekday": "Day", "member": "Member",
                "detected": "Detected", "corrected_to": "Corrected to",
                "edited_by": "Edited by", "category": "Category",
                "hint": "Enhancement hint",
            }).sort_values(["Category", "Date", "Member"])
            st.markdown("<div class='board-title'>Conflicts (each is an enhancement lead)</div>",
                        unsafe_allow_html=True)
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.download_button(
                "⬇️ Export detection conflicts (CSV)", df.to_csv(index=False).encode(),
                file_name=f"detection_conflicts_{period_tag}.csv", mime="text/csv",
            )
            st.markdown(
                "<div class='vbox vbox-warn'><b>How to use this</b><ul>"
                "<li><b>Missed office</b> clusters on the same dates/members → capture the office's "
                "real egress IPs those days and extend the prefix list.</li>"
                "<li><b>Ghost office</b> entries → find which network (VPN pool, NAT) matched the "
                "prefix from home, or check for shared logins.</li>"
                "<li><b>Day-off activity</b> with office IPs → stale sessions or someone else on the "
                "account; with home IPs it's usually just working while off.</li></ul></div>",
                unsafe_allow_html=True,
            )
        elif ana["reviewed"]:
            st.success("No conflicts — every correction in this period agrees with detection "
                       "(or falls where detection has no signal).")
        else:
            st.info("No manual corrections in this period on detection-active days, so there is "
                    "nothing to compare yet. Corrections made in My Space or by management feed "
                    "this report automatically.")


# =============================================================================
# SECTION: METHOD & SETTINGS
# =============================================================================
def render_method(res: Resolver, can_edit: bool):
    umap = " · ".join(f"{k}→<code>{v}</code>" for k, v in MEMBER_TO_SESSION_USER.items())
    st.markdown(
        f"""
        <div class='algo'>
          <ol>
            <li><b>Plan</b> — the repeating two-week rotation (each rotating member in office 2
                of the 4 office days, Mon–Thu), plus per-day <i>overrides</i>. Full-office members
                ({", ".join(sorted(FULL_OFFICE_MEMBERS))}) are planned in office every office day.
                Sundays appear in the plan and default to <b>Home</b>, but a Sunday can be
                overridden (and Sunday attendance is detected like any other day). During
                onboarding {NEW_JOINER} is planned in office every office day through
                {NEW_JOINER_FULL_OFFICE_UNTIL:%d %b %Y}.</li>
            <li><b>Actual</b> — your own saved record wins (explicit, audit-stamped). Otherwise
                IP detection from <code>{SESSION_STATES_TABLE}</code>: in office when any session's
                <code>client_ip</code> starts with <code>{OFFICE_IP_PREFIX}</code>
                (impersonated sessions excluded; matched case-insensitively by short name: {umap}).
                Detection is authoritative from <b>{res.cutover:%d %b %Y}</b>; before that date an
                IP signal is still used as a <i>low-confidence fallback</i> when it exists.
                Sundays with no signal are <b>assumed Home</b>. Remaining days show as dashed
                <b>?</b> — record them in My Space → Fix my attendance.</li>
            <li><b>Eligible office days</b> — Mon–Thu that aren't public holidays or days off
                (from the holiday register <i>or</i> self-recorded as "Day off" in Fix my
                attendance). Off-days never count toward the policy.</li>
            <li><b>HR policy (red)</b> — attendance ≥ 50%, per member, over the selected period.
                Attendance = (office days attended + credited Sundays) ÷ (known eligible office
                days + credited Sundays + no-data days). Every past, non-off <b>Sunday counts as
                good attendance</b> — it's the mandated WFH day, so working it from home lifts the
                score (and coming in on a Sunday still counts as good). A past office day with
                <b>no data counts as Home</b> until corrected — record Office to raise the score,
                or a Day off to remove the day from it.</li>
            <li><b>Plan deviation (orange)</b> — a known day where the actual differed from the
                effective plan (rotation + overrides). Deviations are visible, not punitive; the
                red policy line is the one that matters.</li>
            <li><b>Working hours</b> — every tracked day carries hours, edited in the same
                Fix-my-attendance table: they default to
                {fmt_time(DEFAULT_WORK_START)}–{fmt_time(DEFAULT_WORK_END)}, and office days pick
                up the first/last office-IP session where detected (times shown in
                {LOCAL_TZ_NAME}). Office starts after <b>{fmt_time(res.late_threshold)}</b> count
                as late — the member ticks “Late permission” when a time permission covered it
                (unticked = no permission); days under {MIN_DAY_HOURS:.0f}h are flagged short.
                The ⏱ Hours &amp; punctuality report aggregates office days only.</li>
          </ol>
          <div class='algo-colours'>
            <b>Colours:</b> <span style='color:{C_OFFICE};font-weight:700'>green in office</span> ·
            <span style='color:{C_HOME};font-weight:700'>blue home</span> ·
            <span style='color:#b45309;font-weight:700'>orange plan deviation</span> ·
            <span style='color:{C_BREACH};font-weight:700'>red below 50% policy</span> ·
            grey unknown · <span style='color:#6d28d9;font-weight:700'>violet public holiday (P)</span> ·
            <span style='color:#be185d;font-weight:700'>pink personal holiday (V)</span> ·
            slate self-recorded day off (D). Every cell also carries a letter (O/H/?/P/V/D).
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div class='board-title'>IP detection start date</div>", unsafe_allow_html=True)
    st.caption("Before this date only self-reported records count; after it, IP detection fills the gaps.")
    if can_edit:
        c1, c2 = st.columns([2, 3])
        with c1:
            new_d = st.date_input("Detection start", value=res.cutover, key="cutover_edit")
        with c2:
            st.write("")
            st.write("")
            if st.button("Save detection date", type="secondary"):
                if isinstance(new_d, date) and set_detection_start(new_d):
                    st.success(f"Detection start set to {new_d:%d %b %Y}.")
                    st.rerun()
    else:
        st.info(f"Current detection start: **{res.cutover:%d %b %Y}** (management can change this).")

    st.markdown("<div class='board-title'>⏰ Lateness threshold</div>", unsafe_allow_html=True)
    st.caption(
        "Office starts after this time count as late; the member is asked to "
        "confirm whether the lateness had a time permission."
    )
    if can_edit:
        c1, c2 = st.columns([2, 3])
        with c1:
            new_t = st.time_input("Late after", value=res.late_threshold,
                                  key="late_threshold_edit", step=300)
        with c2:
            st.write("")
            st.write("")
            if st.button("Save lateness threshold", type="secondary"):
                if isinstance(new_t, time) and set_late_threshold(new_t):
                    st.success(f"Lateness threshold set to {fmt_time(new_t)}.")
                    st.rerun()
    else:
        st.info(f"Current threshold: **{fmt_time(res.late_threshold)}** "
                "(management can change this).")

    _render_company_holidays(res, can_edit)

    st.caption(
        "Personal days off are self-recorded in Fix my attendance. All data "
        "lives in the platform Postgres (vault-credentialed)."
    )


def _render_company_holidays(res: Resolver, can_edit: bool):
    """View — and for management, edit — the company holiday calendar.
    These dates are excluded from everyone's eligible office days."""
    st.markdown("<div class='board-title'>🎉 Company holidays</div>", unsafe_allow_html=True)
    st.caption(
        "Days off given by the company — excluded from eligible office days "
        "for the whole team, in every stat and report."
    )

    effective = dict(sorted(res.public.items()))
    if effective:
        hol_df = pd.DataFrame([
            {"Date": iso, "Day": date.fromisoformat(iso).strftime("%a"), "Holiday": name}
            for iso, name in effective.items()
        ])
        st.dataframe(hol_df, hide_index=True, use_container_width=True,
                     height=min(320, 64 + 35 * len(hol_df)))
    else:
        st.info("No company holidays registered.")

    if not can_edit:
        st.info("Management can edit the holiday calendar here.", icon="🔒")
        return

    db = load_public_holidays_db()
    if db is None:
        st.warning("Database unavailable — the calendar above is the built-in "
                   "suggestion list and cannot be edited right now.", icon="⚠️")
        return

    def _seed_defaults_if_needed() -> bool:
        """First edit on an empty table: persist the effective (suggested)
        list first, so the visible calendar and the stored one match and a
        single add/remove doesn't wipe out the suggestions."""
        if db:
            return True
        return upsert_public_holidays(effective)

    c_add, c_del = st.columns(2)
    with c_add:
        st.markdown("**Add or rename a holiday**")
        hd = st.date_input("Date", value=res.today, key="ph_date")
        hn = st.text_input("Name", key="ph_name",
                           placeholder="e.g. National Day (company day off)")
        if st.button("💾 Save holiday", type="primary", key="ph_save"):
            if isinstance(hd, date) and hn.strip():
                if _seed_defaults_if_needed() and upsert_public_holidays({hd.isoformat(): hn.strip()}):
                    st.success(f"{hd:%d %b %Y} saved as “{hn.strip()}”.")
                    st.rerun()
            else:
                st.warning("Pick a date and a non-empty name.")
    with c_del:
        st.markdown("**Remove holidays**")
        to_remove = st.multiselect(
            "Select dates to remove", list(effective.keys()),
            format_func=lambda iso: f"{iso} — {effective.get(iso, '')}",
            key="ph_remove", label_visibility="collapsed",
        )
        if to_remove and st.button("🗑 Remove selected", type="secondary", key="ph_remove_btn"):
            if _seed_defaults_if_needed() and delete_public_holidays(to_remove):
                st.success(f"Removed {len(to_remove)} holiday(s).")
                st.rerun()

    if not db:
        st.caption(
            "ℹ️ The calendar above is the suggested list — it isn't stored yet. "
            "Your first change saves it to the database, then edits apply on top."
        )


# =============================================================================
# STYLES
# =============================================================================
def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        :root {{
            --ink:#0f2942; --sub:#64748b; --line:#e6edf5;
            --office:{C_OFFICE}; --home:{C_HOME}; --dev:{C_DEV}; --breach:{C_BREACH};
        }}
        .block-container {{ padding-top: 1.1rem; max-width: 1280px; }}
        .hub-hero {{
            background: linear-gradient(120deg,#0b3c5d 0%, #14538a 55%, #1b6ca8 100%);
            color:#fff; border-radius:18px; padding:1.15rem 1.5rem; margin-bottom:.9rem;
            box-shadow:0 12px 32px rgba(11,60,93,.28);
        }}
        .hub-hero h1 {{ font-size:1.45rem; margin:0 0 .15rem 0; font-weight:800; letter-spacing:.2px; }}
        .hub-hero p {{ margin:0; opacity:.92; font-size:.88rem; }}
        .who-pill {{
            display:inline-block; margin-top:.55rem; background:rgba(255,255,255,.16);
            border:1px solid rgba(255,255,255,.32); border-radius:999px;
            padding:.2rem .75rem; font-size:.78rem; font-weight:700;
        }}
        .panel {{
            background:#fff; border:1px solid var(--line); border-radius:14px;
            padding:1rem 1.15rem; box-shadow:0 4px 14px rgba(15,41,66,.06); margin-bottom:.7rem;
        }}
        .panel-head {{ display:flex; align-items:center; gap:.75rem; }}
        .avatar {{
            width:46px; height:46px; border-radius:50%; flex:0 0 46px;
            background:linear-gradient(135deg,#1b6ca8,#0b3c5d); color:#fff;
            display:flex; align-items:center; justify-content:center; font-weight:800; font-size:1.25rem;
        }}
        .panel-name {{ font-weight:800; color:var(--ink); font-size:1.08rem; }}
        .panel-sub {{ color:var(--sub); font-size:.8rem; }}
        .panel-flag {{ margin-left:auto; padding:.32rem .85rem; border-radius:999px; font-weight:800; font-size:.8rem; }}
        .panel-flag.ok {{ background:#e7f6ec; color:#15803d; }}
        .panel-flag.breach {{ background:#fdecec; color:#b91c1c; }}
        .strip .strip-title {{ font-weight:800; color:var(--ink); margin-bottom:.35rem; }}
        .strip-live {{ margin-left:.6rem; font-size:.68rem; font-weight:700;
                       color:#15803d; opacity:.75; }}
        .strip-row {{ display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; margin:.22rem 0; }}
        .strip-k {{ font-size:.74rem; color:var(--sub); font-weight:700; width:130px; }}
        .pill {{ padding:.16rem .6rem; border-radius:999px; font-size:.8rem; font-weight:700; }}
        .pill-plan {{ background:#e8f1fb; color:#1d4ed8; border:1px solid #c7dcf5; }}
        .pill-live {{ background:#e7f6ec; color:#15803d; border:1px solid #b9e3c6; }}
        .pill-off  {{ background:#eef1f5; color:#7c8ba1; border:1px solid #dde3ea; }}
        .pill-empty {{ font-size:.78rem; color:#9ca3af; font-style:italic; }}
        .gauge {{ margin:.65rem 0 .1rem 0; }}
        .gauge-track {{ position:relative; height:14px; border-radius:999px; background:#eef2f7; overflow:hidden; }}
        .gauge-fill {{ height:100%; border-radius:999px; }}
        .gauge-fill.ok {{ background:linear-gradient(90deg,#22b45f,var(--office)); }}
        .gauge-fill.bad {{ background:linear-gradient(90deg,#ef6a6a,var(--breach)); }}
        .gauge-target {{ position:absolute; top:-3px; bottom:-3px; left:50%; width:2px; background:#0f2942; opacity:.55; }}
        .gauge-cap {{ font-size:.75rem; margin-top:.25rem; font-weight:700; color:var(--sub); }}
        .gauge-cap.ok {{ color:#15803d; }} .gauge-cap.bad {{ color:#b91c1c; }} .gauge-cap.muted {{ color:var(--sub); }}
        .legend {{ display:flex; flex-wrap:wrap; gap:.4rem; margin:.45rem 0 .6rem 0; }}
        .chip {{ font-size:.72rem; padding:.18rem .55rem; border-radius:999px; font-weight:700; border:1px solid transparent; }}
        .chip-office {{ background:#e2f4e8; color:#15803d; }}
        .chip-home {{ background:#e8effc; color:#1d4ed8; }}
        .chip-dev {{ background:#fef3c7; color:#92600a; border-color:var(--dev); }}
        .chip-breach {{ background:#fee2e2; color:#b91c1c; border-color:var(--breach); }}
        .chip-override {{ background:#fff7e6; color:#92600a; border-color:#f0c36d; }}
        .chip-unknown {{ background:#fdf6ec; color:#b45309; border-color:#e3cfa4; }}
        .chip-public {{ background:#ede9fe; color:#6d28d9; }}
        .chip-vac {{ background:#fce7f3; color:#be185d; }}
        .chip-off {{ background:#e9edf2; color:#7c8ba1; }}
        .board-title {{ font-weight:800; color:var(--ink); margin:1rem 0 .35rem; font-size:.98rem; }}
        .board {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:.8rem 1rem; box-shadow:0 4px 14px rgba(15,41,66,.05); }}
        .bar-row {{ display:flex; align-items:center; gap:.6rem; margin:.35rem 0; }}
        .bar-name {{ width:70px; font-weight:700; color:var(--ink); font-size:.85rem; }}
        .bar-track {{ position:relative; flex:1; height:16px; background:#eef2f7; border-radius:999px; overflow:hidden; }}
        .bar-fill {{ height:100%; border-radius:999px; }}
        .bar-fill.ok {{ background:linear-gradient(90deg,#22b45f,var(--office)); }}
        .bar-fill.bad {{ background:linear-gradient(90deg,#ef6a6a,var(--breach)); }}
        .bar-target {{ position:absolute; top:-2px; bottom:-2px; left:50%; width:2px; background:#0f2942; opacity:.5; }}
        .bar-val {{ width:110px; text-align:right; font-weight:800; font-size:.82rem; }}
        .bar-val.ok {{ color:#15803d; }} .bar-val.bad {{ color:#b91c1c; }} .bar-val.muted {{ color:var(--sub); }}
        .mini-breach {{ font-size:.64rem; background:#fee2e2; color:#b91c1c; padding:.05rem .3rem; border-radius:6px; font-weight:800; }}
        .mini-full {{ font-size:.62rem; background:#e2f4e8; color:#15803d; padding:.05rem .3rem; border-radius:6px; font-weight:800; }}
        .grid-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:14px; background:#fff; box-shadow:0 4px 14px rgba(15,41,66,.05); }}
        .hgrid {{ border-collapse:separate; border-spacing:0; font-size:.72rem; }}
        .hgrid th, .hgrid td {{ text-align:center; }}
        .hhdr {{ padding:.35rem .3rem; color:var(--sub); font-weight:700; background:#f7fafc; }}
        .hhdr .mini {{ display:block; font-size:.6rem; opacity:.7; font-weight:600; }}
        .hhdr-sun {{ background:#eef2f9; color:#8593ab; font-style:italic; }}
        .hhdr.sticky, .hlabel.sticky {{ position:sticky; left:0; z-index:2; background:#f7fafc; }}
        .hlabel {{ text-align:left; padding:.3rem .6rem; font-weight:700; color:var(--ink); white-space:nowrap; background:#fff; }}
        .hlabel.breach {{ color:#b91c1c; }}
        .breach-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--breach); margin-left:.25rem; }}
        .hcell {{ width:27px; height:27px; font-weight:800; color:#fff; border:2px solid #fff; border-radius:7px; }}
        .c-office {{ background:var(--office); }}
        .c-home {{ background:var(--home); }}
        .c-off {{ background:#e2e8f0; color:#64748b; }}
        .c-public {{ background:#ede9fe; color:#6d28d9; }}
        .c-vac {{ background:#fce7f3; color:#be185d; }}
        .c-unknown {{ background:#fdf6ec; color:#b45309; font-weight:800;
                      border:2px dashed #e3cfa4; }}
        .c-future {{ background:#fbfdff; color:#b6c4d4; border-style:dashed; border-color:#dbe4ee; }}
        .c-dev {{ box-shadow:0 0 0 3px var(--dev) inset; }}
        .c-assumed {{ opacity:.6; }}
        .vbox {{ border-radius:10px; padding:.55rem .8rem; font-size:.82rem; margin:.4rem 0; }}
        .vbox ul {{ margin:.25rem 0 0 1rem; padding:0; }}
        .vbox-err {{ background:#fef2f2; border:1px solid #fecaca; color:#7f1d1d; }}
        .vbox-warn {{ background:#fffbeb; border:1px solid #fde68a; color:#78350f; }}
        .vbox-ok {{ background:#f0fdf4; border:1px solid #bbf7d0; color:#14532d; }}
        .report-head {{ margin:.2rem 0 .7rem 0; }}
        .report-title {{ font-size:1.12rem; font-weight:800; color:var(--ink); }}
        .report-sub {{ font-size:.82rem; color:var(--sub); }}
        .algo {{ background:#fff; border:1px solid var(--line); border-left:4px solid #1b6ca8; border-radius:12px; padding:.75rem 1rem; }}
        .algo ol {{ margin:0; padding-left:1.1rem; font-size:.85rem; color:#334155; }}
        .algo li {{ margin-bottom:.32rem; }}
        .algo code {{ background:#eef2f7; padding:.02rem .3rem; border-radius:4px; color:#0b3c5d; font-size:.8rem; }}
        .algo-colours {{ margin-top:.5rem; font-size:.8rem; color:#475569; }}
        .filter-cap {{ color:var(--sub); font-size:.8rem; margin:.05rem 0 .5rem; font-weight:600; }}
        .hours-note {{ color:#9a8f7d; font-size:.78rem; margin:.15rem 0 .35rem;
                       font-style:italic; }}
        .fill-cta {{ background:#fdf6ec; border:1px dashed #e3cfa4; color:#92600a;
                     border-radius:10px; padding:.5rem .8rem; font-size:.84rem;
                     margin:.5rem 0 .2rem 0; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# MAIN
# =============================================================================
def main() -> None:
    st.set_page_config(page_title="WFH Hub", layout="wide", page_icon="🗓️")
    inject_css()

    today = local_today()
    viewer = resolve_member(st.session_state.get("username"))

    st.markdown(
        "<div class='hub-hero'><h1>🗓️ WFH Hub</h1>"
        "<p>Plan the two-week rotation, track real attendance against the 50% office "
        "policy, and export management-ready reports.</p>"
        + (f"<span class='who-pill'>Signed in as {viewer}"
           + (" · management" if is_mgmt(viewer) else "") + "</span>"
           if viewer else "<span class='who-pill'>Team view — sign in for your personal space</span>")
        + "</div>",
        unsafe_allow_html=True,
    )

    # The cutover feeds the "Since detection" preset, so it loads first.
    cutover = get_detection_start()
    start, end, members = render_filter_bar(today, cutover)

    # Load everything once per render. The plan horizon needs future overrides
    # too, so overrides load across both the report period and the next month.
    rotation = load_rotation()
    public = load_public_holidays()
    holidays = load_personal_holidays()
    ip = load_ip_actuals(start, min(end, today))
    manual = load_manual(start, min(end, today))
    ov_end = max(end, today + timedelta(days=35))
    overrides = load_overrides(start, ov_end)
    ip_times = load_ip_times(start, min(end, today))
    day_times = load_day_times(start, min(end, today))
    late_threshold = get_late_threshold()
    res = Resolver(rotation, ip, manual, overrides, holidays, public, cutover, today,
                   ip_times=ip_times, day_times=day_times, late_threshold=late_threshold)

    labels = ["🏠 My Space", "📅 Team & Schedule", "📊 Reports", "⚙️ Method & Settings"]
    tabs = st.tabs(labels)

    with tabs[0]:
        if viewer:
            render_my_space(res, viewer, start, end)
        else:
            st.info("Sign in with your account to see your personal attendance, "
                    "fix past days, and adjust your plan.", icon="👤")
            st.markdown(legend_html(), unsafe_allow_html=True)
            render_day_grid(res, members, workdays_between(start, end))
    with tabs[1]:
        render_schedule(res, viewer, start, end, members)
    with tabs[2]:
        render_reports(res, start, end, viewer, members)
    with tabs[3]:
        render_method(res, is_mgmt(viewer))


if __name__ == "__main__":
    main()
