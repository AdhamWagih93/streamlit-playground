"""WFH Hub — the combined schedule + attendance workspace.

Merges the WFH Schedule (rotation building/editing) and Team Attendance
(policy tracking) pages into one professional workspace:

* **My Space** — personal status vs the 50% policy, fix past attendance,
  adjust your own upcoming plan.
* **Team & Schedule** — who's in today, an *editable* repeating two-week
  rotation (management), and per-day plan overrides.
* **Reports** — over any selected duration:
  - *Management report*: actual WFO/WFH per member, regardless of plan
    (CSV export, summary + day level).
  - *Team report*: plan vs actual, deviation-focused (CSV export).
* **Method & Settings** — the measurement algorithm, IP-detection start date.

Data sources (all in the same vault-credentialed Postgres as the platform):
    wfh_rotation           the repeating 2-week pattern (10 Sun–Thu slots)
    wfh_plan_overrides     per-day, per-member plan deviations from rotation
    wfh_manual_attendance  self-reported actuals (fills/corrects IP gaps)
    wfh_settings           key/value settings (ip_detection_start)
    wfh_personal_holidays  / wfh_public_holidays  (read-only here)
    session_states         shared platform table → IP-based presence

Actual-attendance precedence: a member's own saved record wins (it is an
explicit, audited correction), otherwise IP detection (only on/after the
detection start date), otherwise unknown.

Colour language (validated for colour-vision deficiency on light surfaces):
    green #16a34a  in office        orange #f59e0b  deviation from PLAN
    blue  #3b82f6  home             red    #dc2626  below the 50% HR POLICY
    every coloured cell also carries a glyph (O/H/·/?) so colour is never
    the only channel.

Degrades gracefully without Postgres/vault: deterministic in-memory rotation,
empty actuals, saves warn instead of failing.
"""

from __future__ import annotations

import os
import random
import re
from datetime import date, timedelta
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
TEAM_MEMBERS: List[str] = ["Adham", "Karam", "Hesham", "Salma", "Zanaty"]
YEAR = 2026

ROLE_BY_MEMBER: Dict[str, str] = {
    "Adham": "mgmt-support",
    "Karam": "mgmt-support",
    "Hesham": "mgmt-support",
    "Salma": "engineering",
    "Zanaty": "engineering",
}

NEW_JOINER = "Zanaty"
NEW_JOINER_FULL_OFFICE_UNTIL = date(2026, 8, 14)

WORKDAYS = {6, 0, 1, 2, 3}       # Sun(6)–Thu(3)
OFFICE_WEEKDAYS = {0, 1, 2, 3}   # Mon–Thu (Sundays always WFH)
DAILY_OFFICE_MIN, DAILY_OFFICE_MAX = 2, 3
WEEKLY_WFO = 2
WFO_PER_MEMBER_FORTNIGHT = 4

POLICY_MIN_RATE = 0.50
ROTATION_SEED = 20260101
GRID_MAX_COLS = 46

SESSION_STATES_TABLE = os.environ.get("WFH_SESSION_STATES_TABLE", "session_states").strip()
OFFICE_IP_PREFIX = os.environ.get("WFH_OFFICE_IP_PREFIX", "10.26").strip()
MEMBER_TO_SESSION_USER: Dict[str, str] = {
    "Adham": "Adham_Wagih",
    "Karam": "Karam_Mohamed",
    "Hesham": "Hesham_Mostafa",
    "Salma": "Salma_Adel",
    "Zanaty": "Ahmed_Zanaty",
}
SESSION_USER_TO_MEMBER: Dict[str, str] = {v: k for k, v in MEMBER_TO_SESSION_USER.items()}

POSTGRES_VAULT_PATH = os.environ.get("WFH_POSTGRES_VAULT_PATH", "postgres").strip()
POSTGRES_CONNECT_TIMEOUT = 10
POSTGRES_DATA_TTL = 60

WFH_ROTATION_TABLE = os.environ.get("WFH_ROTATION_TABLE", "wfh_rotation").strip()
WFH_MANUAL_TABLE = os.environ.get("WFH_MANUAL_ATTENDANCE_TABLE", "wfh_manual_attendance").strip()
WFH_OVERRIDES_TABLE = os.environ.get("WFH_PLAN_OVERRIDES_TABLE", "wfh_plan_overrides").strip()
WFH_SETTINGS_TABLE = os.environ.get("WFH_SETTINGS_TABLE", "wfh_settings").strip()
WFH_PERSONAL_HOLIDAYS_TABLE = os.environ.get(
    "WFH_PERSONAL_HOLIDAYS_TABLE", "wfh_personal_holidays").strip()
WFH_PUBLIC_HOLIDAYS_TABLE = os.environ.get(
    "WFH_PUBLIC_HOLIDAYS_TABLE", "wfh_public_holidays").strip()

DETECTION_START_SETTING = "ip_detection_start"
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
    state = random.getstate()
    try:
        random.seed(ROTATION_SEED + seed_bump)
        for _ in range(200):
            try:
                return generate_two_week_pattern(rotating=list(TEAM_MEMBERS))
            except RuntimeError:
                continue
        raise RuntimeError("could not generate rotation")
    finally:
        random.setstate(state)


def validate_rotation(pattern: List[Set[str]]) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for an edited rotation. Errors are hard rules."""
    errors: List[str] = []
    warnings: List[str] = []
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"] * 2
    for i, members in enumerate(pattern):
        wd = weekdays[i]
        label = f"Week {1 + i // 5} {wd}"
        if wd == "Sun":
            if members:
                errors.append(f"{label}: Sundays are always home.")
            continue
        n = len(members)
        if n < DAILY_OFFICE_MIN or n > DAILY_OFFICE_MAX:
            errors.append(f"{label}: {n} in office (need {DAILY_OFFICE_MIN}–{DAILY_OFFICE_MAX}).")
        if not any(ROLE_BY_MEMBER.get(m) == "mgmt-support" for m in members):
            errors.append(f"{label}: no mgmt-support member present.")
    for m in TEAM_MEMBERS:
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
                  WFH_OVERRIDES_TABLE, WFH_SETTINGS_TABLE):
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


# ---- Actuals & overrides ------------------------------------------------------
def load_ip_actuals(start: date, end: date) -> Dict[str, Dict[str, str]]:
    if not _pg_safe_ident(SESSION_STATES_TABLE):
        return {}
    usernames = list(MEMBER_TO_SESSION_USER.values())

    def _q(cur):
        cur.execute(
            f"SELECT s.username, (s.timestamp)::date AS day, "
            f"COUNT(*) FILTER (WHERE s.client_ip LIKE %s) AS office_sessions "
            f"FROM {SESSION_STATES_TABLE} AS s "
            f"WHERE s.username = ANY(%s) "
            f"AND s.timestamp >= %s AND s.timestamp < %s "
            f"AND (s.original_user IS NULL OR s.original_user = s.username) "
            f"GROUP BY s.username, (s.timestamp)::date",
            (OFFICE_IP_PREFIX + "%", usernames, start, end + timedelta(days=1)),
        )
        return cur.fetchall()

    rows = _pg_read(_q)
    out: Dict[str, Dict[str, str]] = {}
    for username, day, office_sessions in rows or []:
        member = SESSION_USER_TO_MEMBER.get(username)
        if not member:
            continue
        iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
        out.setdefault(iso, {})[member] = "WFO" if int(office_sessions or 0) > 0 else "WFH"
    return out


def _load_day_member_status(table: str, start: date, end: date) -> Dict[str, Dict[str, str]]:
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
        if m in TEAM_MEMBERS:
            out.setdefault(iso, {})[m] = "WFO" if sv == "WFO" else "WFH"
    return out


def load_manual(start: date, end: date) -> Dict[str, Dict[str, str]]:
    return _load_day_member_status(WFH_MANUAL_TABLE, start, end)


def load_overrides(start: date, end: date) -> Dict[str, Dict[str, str]]:
    return _load_day_member_status(WFH_OVERRIDES_TABLE, start, end)


def _save_day_member_entries(table: str, member: str, entries: Dict[str, str | None],
                             by: str, label: str) -> bool:
    """entries: {iso: 'WFO'|'WFH'|None}; None deletes the row."""
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
    if ident in SESSION_USER_TO_MEMBER:
        return SESSION_USER_TO_MEMBER[ident]
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

    def __init__(self, rotation, ip, manual, overrides, holidays, public, cutover, today):
        self.rotation = rotation
        self.ip = ip
        self.manual = manual
        self.overrides = overrides
        self.holidays = holidays
        self.public = public
        self.cutover = cutover
        self.today = today

    def is_off(self, member: str, d: date) -> str | None:
        iso = d.isoformat()
        if self.public.get(iso):
            return "Public holiday"
        if member in self.holidays.get(iso, []):
            return "Day off"
        return None

    def rotation_default(self, member: str, d: date) -> str | None:
        """Pure rotation plan (no overrides). None on Fri/Sat."""
        if not is_workday(d):
            return None
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
        """(status, source). A member's own record wins (explicit, audited
        correction); otherwise IP detection (on/after the cutover)."""
        iso = d.isoformat()
        man = self.manual.get(iso, {}).get(member)
        if man:
            return man, "self"
        if d >= self.cutover:
            ipd = self.ip.get(iso, {}).get(member)
            if ipd:
                return ipd, "ip"
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
            cat = "future" if d > self.today else "unknown"
            return {"cat": cat, "note": None, "planned": planned, "actual": None,
                    "source": None, "overridden": overridden}
        deviation = (planned is not None) and (actual != planned)
        return {"cat": "office" if actual == "WFO" else "home", "note": None,
                "planned": planned, "actual": actual, "source": source,
                "deviation": deviation, "overridden": overridden}


def member_stats(res: Resolver, member: str, days: List[date]) -> dict:
    eligible = known = attended = planned_office = 0
    deviations = dev_skipped = dev_extra = unknown_past = off_days = 0
    for d in days:
        if not is_office_day(d):
            continue
        if res.is_off(member, d):
            off_days += 1
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
    rate = (attended / known) if known else None
    adherence = ((known - deviations) / known) if known else None
    return {
        "member": member, "eligible": eligible, "known": known,
        "attended": attended, "home": known - attended,
        "planned_office": planned_office, "deviations": deviations,
        "dev_skipped": dev_skipped, "dev_extra": dev_extra,
        "unknown_past": unknown_past, "off_days": off_days,
        "rate": rate, "adherence": adherence,
        "compliant": (rate is not None and rate >= POLICY_MIN_RATE),
        "low_sample": known < 2,
    }


# =============================================================================
# SMART DATE FILTER
# =============================================================================
PRESETS = ["This week", "Last 14 days", "This month", "Last 30 days", "Quarter", "Custom"]


def resolve_period(preset: str, offset: int, today: date, custom) -> Tuple[date, date]:
    if preset == "This week":
        base = sunday_of(today) + timedelta(days=7 * offset)
        return base, base + timedelta(days=4)
    if preset == "Last 14 days":
        end = today + timedelta(days=14 * offset)
        return end - timedelta(days=13), end
    if preset == "This month":
        m0 = today.month - 1 + offset
        y = today.year + m0 // 12
        m = m0 % 12 + 1
        start = date(y, m, 1)
        nxt = date(y + (m == 12), (m % 12) + 1, 1)
        return start, nxt - timedelta(days=1)
    if preset == "Last 30 days":
        end = today + timedelta(days=30 * offset)
        return end - timedelta(days=29), end
    if preset == "Quarter":
        q0 = (today.month - 1) // 3 + offset
        y = today.year + q0 // 4
        q = q0 % 4
        start = date(y, q * 3 + 1, 1)
        endm = q * 3 + 3
        nxt = date(y + (endm == 12), (endm % 12) + 1, 1)
        return start, nxt - timedelta(days=1)
    if isinstance(custom, (tuple, list)) and len(custom) == 2 and all(isinstance(x, date) for x in custom):
        a, b = custom
        return (a, b) if a <= b else (b, a)
    return today - timedelta(days=13), today


def render_period_filter(today: date, key: str = "hub") -> Tuple[date, date]:
    fc = st.columns([2.2, 1, 1, 1, 3])
    with fc[0]:
        preset = st.selectbox("Period", PRESETS, index=1, key=f"{key}_preset")
    if st.session_state.get(f"_{key}_last_preset") != preset:
        st.session_state[f"{key}_offset"] = 0
        st.session_state[f"_{key}_last_preset"] = preset
    offset = st.session_state.get(f"{key}_offset", 0)
    with fc[1]:
        st.write("")
        if st.button("◀ Prev", use_container_width=True, disabled=(preset == "Custom"), key=f"{key}_prev"):
            st.session_state[f"{key}_offset"] = offset - 1
            st.rerun()
    with fc[2]:
        st.write("")
        if st.button("Today", use_container_width=True, disabled=(preset == "Custom"), key=f"{key}_today"):
            st.session_state[f"{key}_offset"] = 0
            st.rerun()
    with fc[3]:
        st.write("")
        if st.button("Next ▶", use_container_width=True,
                     disabled=(preset == "Custom" or offset >= 0), key=f"{key}_next"):
            st.session_state[f"{key}_offset"] = offset + 1
            st.rerun()
    custom = None
    if preset == "Custom":
        with fc[4]:
            custom = st.date_input(
                "Custom range", value=(today - timedelta(days=13), today), key=f"{key}_custom",
            )
    start, end = resolve_period(preset, st.session_state.get(f"{key}_offset", 0), today, custom)
    n_office = sum(1 for d in workdays_between(start, end) if is_office_day(d))
    st.markdown(
        f"<div class='filter-cap'>📅 {start:%a %d %b %Y} → {end:%a %d %b %Y} · {n_office} office days</div>",
        unsafe_allow_html=True,
    )
    return start, end


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
            f"<div class='gauge-cap {cls}'>{width}% office · policy ≥ 50%{note}</div></div>")


def legend_html(plan_focus: bool = False) -> str:
    chips = [
        ("chip-office", "In office"),
        ("chip-home", "Home"),
        ("chip-dev", "Deviated from plan"),
        ("chip-breach", "Below 50% policy"),
        ("chip-unknown", "Unknown / fill"),
        ("chip-off", "Day off / holiday"),
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
        return (f"<td class='hcell c-off' title='{member} · {d:%a %d %b} · "
                f"{state['note']}'>·</td>")
    if cat in ("unknown", "future"):
        cls = "c-unknown" if cat == "unknown" else "c-future"
        glyph = "?" if cat == "unknown" else ("O" if state.get("planned") == "WFO" else "H")
        note = "no data" if cat == "unknown" else "upcoming"
        return (f"<td class='hcell {cls}' title='{member} · {d:%a %d %b} · {note} · "
                f"plan: {plan_lbl}{ov}'>{glyph}</td>")
    base = "c-office" if cat == "office" else "c-home"
    dev = " c-dev" if state.get("deviation") else ""
    glyph = "O" if cat == "office" else "H"
    src = "IP" if state["source"] == "ip" else "self-reported"
    actual_lbl = "Office" if state["actual"] == "WFO" else "Home"
    return (f"<td class='hcell {base}{dev}' title='{member} · {d:%a %d %b} · "
            f"was {actual_lbl} ({src}) · plan: {plan_lbl}{ov}'>{glyph}</td>")


def render_day_grid(res: Resolver, members: List[str], days: List[date],
                    breach: Dict[str, bool] | None = None):
    breach = breach or {}
    office_days = [d for d in days if is_office_day(d)]
    truncated = False
    if len(office_days) > GRID_MAX_COLS:
        office_days = office_days[-GRID_MAX_COLS:]
        truncated = True
    if not office_days:
        st.caption("No office days (Mon–Thu) in this range.")
        return
    header = "<th class='hhdr sticky'>Member</th>" + "".join(
        f"<th class='hhdr'><span>{d:%d}</span><span class='mini'>{d:%b}</span></th>"
        for d in office_days
    )
    rows = []
    for m in members:
        cls = "hlabel breach" if breach.get(m) else "hlabel"
        badge = " <span class='breach-dot' title='Below 50% policy'></span>" if breach.get(m) else ""
        cells = "".join(cell_html(res.cell(m, d), m, d) for d in office_days)
        rows.append(f"<tr><td class='{cls} sticky'>{m}{badge}</td>{cells}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    if truncated:
        st.caption(f"Showing the most recent {GRID_MAX_COLS} office days of the range.")


def bars_vs_policy(stats: Dict[str, dict], value_key: str = "rate",
                   suffix: str = "office") -> None:
    order = sorted(TEAM_MEMBERS, key=lambda m: (stats[m][value_key] is None,
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
    k[0].metric("Office attendance", rate_pct(s["rate"]),
                help="Days in office ÷ eligible office days with data.")
    k[1].metric("In office", f"{s['attended']}/{s['known']}")
    k[2].metric("Plan deviations", f"{s['deviations']}",
                help="Days your actual differed from your effective plan.")
    k[3].metric("Missing days", f"{s['unknown_past']}",
                help="Past office days with no data — fill them below.")

    st.markdown(legend_html(), unsafe_allow_html=True)
    render_day_grid(res, [member], days, {member: breach_flag})

    c1, c2 = st.columns(2)
    with c1:
        _render_fix_attendance(res, member, days)
    with c2:
        _render_adjust_plan(res, member)


def _render_fix_attendance(res: Resolver, member: str, days: List[date]):
    st.markdown("<div class='board-title'>✍️ Fix my attendance</div>", unsafe_allow_html=True)
    st.caption(
        "Record where you actually were — for days before IP detection, or to "
        "correct a wrong detection. Your record wins and is audit-stamped."
    )
    editable = [
        d for d in days
        if is_office_day(d) and d <= res.today and not res.is_off(member, d)
    ]
    if not editable:
        st.info("No past office days in the selected period.")
        return
    editable = editable[-20:]  # keep the editor focused
    recs = []
    for d in editable:
        iso = d.isoformat()
        ipd = res.ip.get(iso, {}).get(member) if d >= res.cutover else None
        mine = res.manual.get(iso, {}).get(member)
        recs.append({
            "Date": d.strftime("%a %d %b"),
            "_iso": iso,
            "Plan": {"WFO": "Office", "WFH": "Home", None: "—"}[res.planned(member, d)],
            "Detected": {"WFO": "Office", "WFH": "Home", None: "—"}[ipd],
            "My record": {"WFO": "Office", "WFH": "Home", None: "—"}[mine],
        })
    df = pd.DataFrame(recs)
    edited = st.data_editor(
        df.drop(columns=["_iso"]),
        column_config={
            "Date": st.column_config.TextColumn(disabled=True),
            "Plan": st.column_config.TextColumn(disabled=True, help="Your effective plan"),
            "Detected": st.column_config.TextColumn(disabled=True, help="IP-based detection"),
            "My record": st.column_config.SelectboxColumn(
                options=["—", "Office", "Home"], default="—",
                help="Your own record — overrides detection when set."),
        },
        hide_index=True, num_rows="fixed", use_container_width=True,
        key=f"fix_{member}",
    )
    if st.button("Save my attendance", type="primary", key=f"fix_save_{member}"):
        entries: Dict[str, str | None] = {}
        for i, row in edited.iterrows():
            v = str(row["My record"])
            entries[df.iloc[i]["_iso"]] = (
                "WFO" if v == "Office" else "WFH" if v == "Home" else None
            )
        if save_manual_entries(member, entries, by=member):
            st.success("Attendance saved.")
            st.rerun()


def _render_adjust_plan(res: Resolver, member: str, horizon_days: int = 21):
    st.markdown("<div class='board-title'>🔀 Adjust my plan</div>", unsafe_allow_html=True)
    st.caption(
        "Swap an upcoming day between office and home. Deviations from the "
        "rotation show in orange so the team can see coverage."
    )
    upcoming = [
        d for d in workdays_between(res.today, res.today + timedelta(days=horizon_days))
        if is_office_day(d) and not res.is_off(member, d)
    ]
    if not upcoming:
        st.info("No upcoming office days in the horizon.")
        return
    recs = []
    for d in upcoming:
        iso = d.isoformat()
        rot = res.rotation_default(member, d)
        ov = res.overrides.get(iso, {}).get(member)
        recs.append({
            "Date": d.strftime("%a %d %b"),
            "_iso": iso,
            "Rotation": {"WFO": "Office", "WFH": "Home", None: "—"}[rot],
            "My plan": {"WFO": "Office", "WFH": "Home", None: "Rotation"}[ov],
        })
    df = pd.DataFrame(recs)
    edited = st.data_editor(
        df.drop(columns=["_iso"]),
        column_config={
            "Date": st.column_config.TextColumn(disabled=True),
            "Rotation": st.column_config.TextColumn(disabled=True, help="Default from the 2-week rotation"),
            "My plan": st.column_config.SelectboxColumn(
                options=["Rotation", "Office", "Home"], default="Rotation",
                help="Pick Office/Home to override the rotation for that day."),
        },
        hide_index=True, num_rows="fixed", use_container_width=True,
        key=f"plan_{member}",
    )
    if st.button("Save my plan", type="primary", key=f"plan_save_{member}"):
        entries: Dict[str, str | None] = {}
        for i, row in edited.iterrows():
            v = str(row["My plan"])
            entries[df.iloc[i]["_iso"]] = (
                "WFO" if v == "Office" else "WFH" if v == "Home" else None
            )
        if save_override_entries(member, entries, by=member):
            st.success("Plan updated.")
            st.rerun()


# =============================================================================
# SECTION: TEAM & SCHEDULE
# =============================================================================
def render_today_strip(res: Resolver):
    d = res.today
    while not is_office_day(d) or res.public.get(d.isoformat()):
        d += timedelta(days=1)
        if (d - res.today).days > 14:
            return
    label = "Today" if d == res.today else f"Next office day · {d:%a %d %b}"
    planned_in = [m for m in TEAM_MEMBERS
                  if res.planned(m, d) == "WFO" and not res.is_off(m, d)]
    detected_in = [m for m in TEAM_MEMBERS
                   if res.ip.get(d.isoformat(), {}).get(m) == "WFO"]
    on_leave = [m for m in TEAM_MEMBERS if res.is_off(m, d)]
    pl = "".join(f"<span class='pill pill-plan'>{m}</span>" for m in planned_in) or \
         "<span class='pill-empty'>nobody planned</span>"
    dt = "".join(f"<span class='pill pill-live'>{m}</span>" for m in detected_in) or \
         "<span class='pill-empty'>none detected yet</span>"
    lv = ("".join(f"<span class='pill pill-off'>{m}</span>" for m in on_leave)) if on_leave else ""
    lv_row = f"<div class='strip-row'><span class='strip-k'>On leave</span>{lv}</div>" if lv else ""
    st.markdown(
        f"<div class='panel strip'><div class='strip-title'>{label}</div>"
        f"<div class='strip-row'><span class='strip-k'>Planned in office</span>{pl}</div>"
        f"<div class='strip-row'><span class='strip-k'>Detected in office</span>{dt}</div>"
        f"{lv_row}</div>",
        unsafe_allow_html=True,
    )


def _pattern_to_df(pattern: List[Set[str]]) -> pd.DataFrame:
    cols = [f"W{w + 1} {wd}" for w in range(2) for wd in ["Mon", "Tue", "Wed", "Thu"]]
    slots = [1, 2, 3, 4, 6, 7, 8, 9]  # office slots (skip Sundays 0 and 5)
    data = {}
    for c, slot in zip(cols, slots):
        data[c] = [m in pattern[slot] for m in TEAM_MEMBERS]
    df = pd.DataFrame(data, index=TEAM_MEMBERS)
    df.insert(0, "Member", TEAM_MEMBERS)
    return df


def _df_to_pattern(df: pd.DataFrame) -> List[Set[str]]:
    cols = [f"W{w + 1} {wd}" for w in range(2) for wd in ["Mon", "Tue", "Wed", "Thu"]]
    slots = [1, 2, 3, 4, 6, 7, 8, 9]
    pattern: List[Set[str]] = [set() for _ in range(10)]
    for c, slot in zip(cols, slots):
        for i, m in enumerate(TEAM_MEMBERS):
            if bool(df.iloc[i][c]):
                pattern[slot].add(m)
    return pattern


def render_schedule(res: Resolver, viewer: str | None):
    render_today_strip(res)

    st.markdown("<div class='board-title'>📆 Next two weeks — effective plan</div>",
                unsafe_allow_html=True)
    st.caption("Rotation + everyone's overrides + holidays. Orange ring = override.")
    horizon = workdays_between(sunday_of(res.today), sunday_of(res.today) + timedelta(days=13))
    _render_plan_grid(res, horizon)

    st.markdown("<div class='board-title'>🔁 The repeating two-week rotation</div>",
                unsafe_allow_html=True)
    can_edit = is_mgmt(viewer)
    if can_edit:
        st.caption(
            "Tick = in office. Save validates the team rules; you can save "
            "anyway if you accept the listed violations. Sundays are always home."
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


def _render_plan_grid(res: Resolver, days: List[date]):
    office_days = [d for d in days if is_office_day(d)]
    header = "<th class='hhdr sticky'>Member</th>" + "".join(
        f"<th class='hhdr'><span>{d:%a}</span><span class='mini'>{d:%d %b}</span></th>"
        for d in office_days
    )
    rows = []
    for m in TEAM_MEMBERS:
        cells = []
        for d in office_days:
            off = res.is_off(m, d)
            plan = res.planned(m, d)
            ov = bool(res.overrides.get(d.isoformat(), {}).get(m))
            if off:
                cells.append(f"<td class='hcell c-off' title='{m} · {d:%a %d %b} · {off}'>·</td>")
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
            if weekdays[slot % 5] == "Sun" or m not in pattern[slot]:
                cells.append("<td class='hcell c-home'>H</td>")
            else:
                cells.append("<td class='hcell c-office'>O</td>")
        rows.append(f"<tr><td class='hlabel sticky'>{m}</td>{''.join(cells)}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _render_member_overrides_editor(res: Resolver, viewer: str):
    target = st.selectbox("Member", TEAM_MEMBERS, key="ov_member")
    upcoming = [
        d for d in workdays_between(res.today, res.today + timedelta(days=28))
        if is_office_day(d) and not res.is_off(target, d)
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
def _daily_detail_rows(res: Resolver, days: List[date]) -> List[dict]:
    rows = []
    for d in days:
        if not is_office_day(d):
            continue
        for m in TEAM_MEMBERS:
            off = res.is_off(m, d)
            plan = res.planned(m, d)
            actual, source = res.actual(m, d)
            rows.append({
                "date": d.isoformat(),
                "weekday": d.strftime("%a"),
                "member": m,
                "planned": {"WFO": "Office", "WFH": "Home", None: ""}[plan],
                "actual": {"WFO": "Office", "WFH": "Home", None: ""}[actual],
                "source": source or "",
                "off": off or "",
                "deviation": ("yes" if (not off and actual and plan and actual != plan) else ""),
            })
    return rows


def render_reports(res: Resolver, start: date, end: date, viewer: str | None):
    days = workdays_between(start, end)
    stats = {m: member_stats(res, m, days) for m in TEAM_MEMBERS}
    period_tag = f"{start.isoformat()}_{end.isoformat()}"

    kind = st.radio(
        "Report", ["🗂 Management — actual attendance", "🎯 Team — plan vs actual"],
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
        total_home = sum(s["home"] for s in stats.values())
        total_known = sum(s["known"] for s in stats.values())
        total_unknown = sum(s["unknown_past"] for s in stats.values())
        team_rate = (total_office / total_known) if total_known else None
        rated = [s for s in stats.values() if s["rate"] is not None]
        k = st.columns(4)
        k[0].metric("Team office rate", rate_pct(team_rate))
        k[1].metric("Office days", f"{total_office}")
        k[2].metric("Home days", f"{total_home}")
        k[3].metric("Below policy", f"{sum(1 for s in rated if not s['compliant'])}",
                    help="Members under 50% office in this period.")

        st.markdown("<div class='board-title'>Office rate vs the 50% policy</div>",
                    unsafe_allow_html=True)
        bars_vs_policy(stats, "rate", "office")

        summary = pd.DataFrame([{
            "Member": m,
            "Eligible days": stats[m]["eligible"],
            "Office": stats[m]["attended"],
            "Home": stats[m]["home"],
            "Days off": stats[m]["off_days"],
            "No data": stats[m]["unknown_past"],
            "Office rate": rate_pct(stats[m]["rate"]),
            "Policy (≥50%)": ("✅ met" if stats[m]["compliant"]
                              else "❌ below" if stats[m]["rate"] is not None else "— no data"),
        } for m in TEAM_MEMBERS])
        st.dataframe(summary, hide_index=True, use_container_width=True)

        st.markdown("<div class='board-title'>Day by day (actuals)</div>", unsafe_allow_html=True)
        st.markdown(legend_html(), unsafe_allow_html=True)
        render_day_grid(res, TEAM_MEMBERS, days,
                        {m: (stats[m]["rate"] is not None and not stats[m]["compliant"])
                         for m in TEAM_MEMBERS})

        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ Export summary (CSV)", summary.to_csv(index=False).encode(),
            file_name=f"attendance_summary_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
        detail = pd.DataFrame(_daily_detail_rows(res, days))
        d2.download_button(
            "⬇️ Export day-level detail (CSV)", detail.to_csv(index=False).encode(),
            file_name=f"attendance_detail_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
    else:
        st.markdown(
            "<div class='report-head'><div class='report-title'>Team report — plan vs actual</div>"
            f"<div class='report-sub'>{start:%d %b %Y} → {end:%d %b %Y} · how closely reality "
            "followed the rotation + overrides · orange marks deviations</div></div>",
            unsafe_allow_html=True,
        )
        total_known = sum(s["known"] for s in stats.values())
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
        } for m in TEAM_MEMBERS])
        st.dataframe(summary, hide_index=True, use_container_width=True)

        st.markdown("<div class='board-title'>Day by day (deviations in orange)</div>",
                    unsafe_allow_html=True)
        st.markdown(legend_html(plan_focus=True), unsafe_allow_html=True)
        render_day_grid(res, TEAM_MEMBERS, days)

        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ Export summary (CSV)", summary.to_csv(index=False).encode(),
            file_name=f"plan_vs_actual_summary_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )
        detail = pd.DataFrame(_daily_detail_rows(res, days))
        d2.download_button(
            "⬇️ Export day-level detail (CSV)", detail.to_csv(index=False).encode(),
            file_name=f"plan_vs_actual_detail_{period_tag}.csv", mime="text/csv",
            use_container_width=True,
        )


# =============================================================================
# SECTION: METHOD & SETTINGS
# =============================================================================
def render_method(res: Resolver, can_edit: bool):
    umap = " · ".join(f"{k}→<code>{v}</code>" for k, v in MEMBER_TO_SESSION_USER.items())
    st.markdown(
        f"""
        <div class='algo'>
          <ol>
            <li><b>Plan</b> — the repeating two-week rotation (each member in office 2 of the 4
                office days, Mon–Thu; Sundays always home), plus per-day <i>overrides</i> that you
                or management set. During onboarding {NEW_JOINER} is planned in office every office
                day through {NEW_JOINER_FULL_OFFICE_UNTIL:%d %b %Y}.</li>
            <li><b>Actual</b> — your own saved record wins (explicit, audit-stamped). Otherwise, on
                days from <b>{res.cutover:%d %b %Y}</b>, IP detection from
                <code>{SESSION_STATES_TABLE}</code>: in office when any session's
                <code>client_ip</code> starts with <code>{OFFICE_IP_PREFIX}</code>
                (impersonated sessions excluded). Matched by short name: {umap}.</li>
            <li><b>Eligible office days</b> — Mon–Thu that aren't public holidays or personal days
                off. Sundays and off-days never count toward the policy.</li>
            <li><b>HR policy (red)</b> — ≥ 50% of eligible office days in office, per member, over
                the selected period. Office rate = office days ÷ known eligible days.</li>
            <li><b>Plan deviation (orange)</b> — a known day where the actual differed from the
                effective plan (rotation + overrides). Deviations are visible, not punitive; the
                red policy line is the one that matters.</li>
          </ol>
          <div class='algo-colours'>
            <b>Colours:</b> <span style='color:{C_OFFICE};font-weight:700'>green in office</span> ·
            <span style='color:{C_HOME};font-weight:700'>blue home</span> ·
            <span style='color:#b45309;font-weight:700'>orange plan deviation</span> ·
            <span style='color:{C_BREACH};font-weight:700'>red below 50% policy</span> ·
            grey unknown · slate day off. Every cell also carries a letter (O/H/?/·).
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
    st.caption(
        "Holidays are managed on the WFH Schedule page; this hub reads them for "
        "eligibility. All data lives in the platform Postgres (vault-credentialed)."
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
        .chip-unknown {{ background:#f1f5f9; color:#64748b; }}
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
        .grid-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:14px; background:#fff; box-shadow:0 4px 14px rgba(15,41,66,.05); }}
        .hgrid {{ border-collapse:separate; border-spacing:0; font-size:.72rem; }}
        .hgrid th, .hgrid td {{ text-align:center; }}
        .hhdr {{ padding:.35rem .3rem; color:var(--sub); font-weight:700; background:#f7fafc; }}
        .hhdr .mini {{ display:block; font-size:.6rem; opacity:.7; font-weight:600; }}
        .hhdr.sticky, .hlabel.sticky {{ position:sticky; left:0; z-index:2; background:#f7fafc; }}
        .hlabel {{ text-align:left; padding:.3rem .6rem; font-weight:700; color:var(--ink); white-space:nowrap; background:#fff; }}
        .hlabel.breach {{ color:#b91c1c; }}
        .breach-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--breach); margin-left:.25rem; }}
        .hcell {{ width:27px; height:27px; font-weight:800; color:#fff; border:2px solid #fff; border-radius:7px; }}
        .c-office {{ background:var(--office); }}
        .c-home {{ background:var(--home); }}
        .c-off {{ background:#e2e8f0; color:#9aa7b6; font-weight:400; }}
        .c-unknown {{ background:#f1f5f9; color:#94a3b8; }}
        .c-future {{ background:#fbfdff; color:#b6c4d4; border-style:dashed; border-color:#dbe4ee; }}
        .c-dev {{ box-shadow:0 0 0 3px var(--dev) inset; }}
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

    today = date.today()
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

    start, end = render_period_filter(today)

    # Load everything once per render. The plan horizon needs future overrides
    # too, so overrides load across both the report period and the next month.
    rotation = load_rotation()
    cutover = get_detection_start()
    public = load_public_holidays()
    holidays = load_personal_holidays()
    ip = load_ip_actuals(start, min(end, today))
    manual = load_manual(start, min(end, today))
    ov_end = max(end, today + timedelta(days=35))
    overrides = load_overrides(start, ov_end)
    res = Resolver(rotation, ip, manual, overrides, holidays, public, cutover, today)

    labels = ["🏠 My Space", "📅 Team & Schedule", "📊 Reports", "⚙️ Method & Settings"]
    tabs = st.tabs(labels)

    with tabs[0]:
        if viewer:
            render_my_space(res, viewer, start, end)
        else:
            st.info("Sign in with your account to see your personal attendance, "
                    "fix past days, and adjust your plan.", icon="👤")
            st.markdown(legend_html(), unsafe_allow_html=True)
            render_day_grid(res, TEAM_MEMBERS, workdays_between(start, end))
    with tabs[1]:
        render_schedule(res, viewer)
    with tabs[2]:
        render_reports(res, start, end, viewer)
    with tabs[3]:
        render_method(res, is_mgmt(viewer))


if __name__ == "__main__":
    main()
