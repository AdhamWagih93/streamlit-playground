"""Team Attendance — policy-first tracking of office vs. home.

A redesigned companion to the WFH Schedule page. Where that page focuses on
*building* the rotation, this one focuses on **measuring reality against HR
policy**: every member should be in the office at least 50% of their eligible
office days. It blends three sources of truth:

* the repeating two-week **rotation plan** (what was planned),
* **IP-based detection** from the shared ``session_states`` table (on-site when
  a session's ``client_ip`` starts with the office prefix), active only from a
  configurable start date, and
* **self-reported** attendance that members fill in for days before detection.

Colour language used throughout:
    green  = in office            slate = day off / holiday
    blue   = home (as expected)   grey  = unknown (needs filling)
    orange = deviated from PLAN   red   = below the 50% HR policy

Everything degrades gracefully when Postgres / vault are unavailable (local or
CI): the plan is generated deterministically in memory and reads return empty.
"""

from __future__ import annotations

import os
import random
import re
from datetime import date, timedelta
from itertools import combinations
from typing import Dict, List, Sequence, Set, Tuple

import streamlit as st

# --- Optional infra deps (mirrors 3_WFH_Schedule.py) ------------------------
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
# TEAM RULES & CONFIG  (kept consistent with 3_WFH_Schedule.py)
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
OFFICE_WEEKDAYS = {0, 1, 2, 3}   # Mon–Thu (Sundays are always WFH)
DAILY_OFFICE_MIN, DAILY_OFFICE_MAX = 2, 3
WEEKLY_WFO = 2                    # 2 of 4 office days == the 50% policy
WFO_PER_MEMBER_FORTNIGHT = 4

POLICY_MIN_RATE = 0.50           # HR policy: >= 50% of eligible office days
ROTATION_SEED = 20260101         # deterministic, stable repeating rotation
GRID_MAX_COLS = 46               # guard against absurdly wide day grids

# Session-state / IP detection
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

# Postgres / vault
POSTGRES_VAULT_PATH = os.environ.get("WFH_POSTGRES_VAULT_PATH", "postgres").strip()
POSTGRES_CONNECT_TIMEOUT = 10
POSTGRES_DATA_TTL = 60

WFH_ROTATION_TABLE = os.environ.get("WFH_ROTATION_TABLE", "wfh_rotation").strip()
WFH_MANUAL_TABLE = os.environ.get("WFH_MANUAL_ATTENDANCE_TABLE", "wfh_manual_attendance").strip()
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


# =============================================================================
# CALENDAR / RULE HELPERS
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


# =============================================================================
# ROTATION GENERATOR  (validated 10-slot Sun–Thu×2 pattern; copied logic)
# =============================================================================
def generate_two_week_pattern(rotating=None, forced_office=None) -> List[Set[str]]:
    """Return a 10-workday office pattern honouring the hard rules: 2–3 people
    per office day, a mgmt-support member always present, each rotating member
    in exactly ``WEEKLY_WFO`` of the 4 office days per week (== 50%), and no 3
    consecutive office days. Sundays are empty (WFH)."""
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
        day_opts = [s for s in all_subsets if (mgmt_indices & (forced_idx | set(s)))]
        options_per_day.append(day_opts)

    best_schedule: List[Set[int]] | None = None
    found = False

    def backtrack(day, schedule, counts, week_counts, streaks):
        nonlocal best_schedule, found
        if found:
            return
        if day == 10:
            if any(counts[p] != WFO_PER_MEMBER_FORTNIGHT for p in r_idx):
                return
            best_schedule = [set(s) for s in schedule]
            found = True
            return
        week_idx = 0 if day < 5 else 1
        remaining = sum(1 for k in range(day, 10) if weekdays[k] != "Sun")
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
            after = remaining - 1
            for p in r_idx:
                if new_counts[p] + after < WFO_PER_MEMBER_FORTNIGHT:
                    feasible = False
                    break
            if not feasible:
                continue
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
        raise RuntimeError("Unable to find a valid 2-week pattern.")

    name_pattern: List[Set[str]] = []
    for day, s in enumerate(best_schedule):
        if weekdays[day] == "Sun":
            name_pattern.append(set())
        else:
            name_pattern.append(set(forced_office) | {TEAM_MEMBERS[i] for i in s})
    return name_pattern


def generate_stable_rotation() -> List[Set[str]]:
    """Deterministic (seeded) steady-state rotation — everyone on the 50/50
    split. Same output every run so the "plan" never shifts under people."""
    state = random.getstate()
    try:
        random.seed(ROTATION_SEED)
        for _ in range(200):
            try:
                return generate_two_week_pattern(rotating=list(TEAM_MEMBERS))
            except RuntimeError:
                continue
        raise RuntimeError("could not generate rotation")
    finally:
        random.setstate(state)


# =============================================================================
# POSTGRES LAYER
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
    for _name in (WFH_ROTATION_TABLE, WFH_MANUAL_TABLE, WFH_SETTINGS_TABLE):
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


# ---- Rotation ---------------------------------------------------------------
def load_rotation() -> List[Set[str]]:
    """Read the canonical repeating rotation, generating + persisting it on
    first use. Falls back to a deterministic in-memory pattern on DB error."""
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT slot, members_wfo FROM {WFH_ROTATION_TABLE} ORDER BY slot")
        rows = cur.fetchall()
        by_slot = {int(s): (m or "") for s, m in rows}
        if len(by_slot) < 10:
            pattern = generate_stable_rotation()
            cur.executemany(
                f"INSERT INTO {WFH_ROTATION_TABLE} (slot, members_wfo) VALUES (%s, %s) "
                f"ON CONFLICT (slot) DO UPDATE SET members_wfo = EXCLUDED.members_wfo, "
                f"updated_at = NOW()",
                [(i, ",".join(sorted(pattern[i]))) for i in range(10)],
            )
            conn.commit()
            cur.close()
            return pattern
        cur.close()
        return [
            {m for m in by_slot.get(i, "").split(",") if m in TEAM_MEMBERS}
            for i in range(10)
        ]
    except Exception:  # noqa: BLE001
        return generate_stable_rotation()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def save_rotation(pattern: List[Set[str]]) -> bool:
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.executemany(
            f"INSERT INTO {WFH_ROTATION_TABLE} (slot, members_wfo) VALUES (%s, %s) "
            f"ON CONFLICT (slot) DO UPDATE SET members_wfo = EXCLUDED.members_wfo, "
            f"updated_at = NOW()",
            [(i, ",".join(sorted(pattern[i]))) for i in range(10)],
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save the rotation: {e}", icon="⚠️")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def planned_status(member: str, d: date, rotation: List[Set[str]]) -> str | None:
    """WFO/WFH for a member on any date, from the repeating rotation. None for
    non-workdays (Fri/Sat)."""
    if not is_workday(d):
        return None
    if in_full_office_period(d) and member == NEW_JOINER and is_office_day(d):
        return "WFO"
    anchor = anchor_sunday(d.year)
    delta = (d - anchor).days
    rem = delta % 7
    if rem > 4:
        return None
    slot = ((delta // 7) * 5 + rem) % 10
    return "WFO" if member in rotation[slot] else "WFH"


# ---- Settings ---------------------------------------------------------------
def get_detection_start() -> date:
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            f"SELECT value FROM {WFH_SETTINGS_TABLE} WHERE key = %s",
            (DETECTION_START_SETTING,),
        )
        row = cur.fetchone()
        cur.close()
        if row and row[0]:
            try:
                return date.fromisoformat(str(row[0]))
            except ValueError:
                return DEFAULT_DETECTION_START
        return DEFAULT_DETECTION_START
    except Exception:  # noqa: BLE001
        return DEFAULT_DETECTION_START
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def set_detection_start(d: date) -> bool:
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO {WFH_SETTINGS_TABLE} (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            (DETECTION_START_SETTING, d.isoformat()),
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save the detection start date: {e}", icon="⚠️")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---- Holidays (read-only here; edited on the WFH Schedule page) --------------
def load_public_holidays() -> Dict[str, str]:
    conn = None
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute(f"SELECT day, name FROM {WFH_PUBLIC_HOLIDAYS_TABLE}")
        rows = cur.fetchall()
        cur.close()
        out = {}
        for d, name in rows:
            dd = d if hasattr(d, "isoformat") else date.fromisoformat(str(d))
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


def load_personal_holidays() -> Dict[str, List[str]]:
    conn = None
    try:
        conn = _pg_connect()
        cur = conn.cursor()
        cur.execute(f"SELECT day, member FROM {WFH_PERSONAL_HOLIDAYS_TABLE}")
        rows = cur.fetchall()
        cur.close()
        out: Dict[str, List[str]] = {}
        for d, m in rows:
            iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
            if m in TEAM_MEMBERS:
                out.setdefault(iso, []).append(m)
        return out
    except Exception:  # noqa: BLE001
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---- Actuals: IP detection + manual -----------------------------------------
def load_ip_actuals(start: date, end: date) -> Dict[str, Dict[str, str]]:
    """{iso: {member: 'WFO'|'WFH'}} from session_states over [start, end]."""
    out: Dict[str, Dict[str, str]] = {}
    if not _pg_safe_ident(SESSION_STATES_TABLE):
        return out
    usernames = list(MEMBER_TO_SESSION_USER.values())
    conn = None
    try:
        conn = _pg_connect()
        cur = conn.cursor()
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
        for username, day, office_sessions in cur.fetchall():
            member = SESSION_USER_TO_MEMBER.get(username)
            if not member:
                continue
            iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
            out.setdefault(iso, {})[member] = "WFO" if int(office_sessions or 0) > 0 else "WFH"
        cur.close()
        return out
    except Exception:  # noqa: BLE001
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def load_manual(start: date, end: date) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            f"SELECT day, member, status FROM {WFH_MANUAL_TABLE} "
            f"WHERE day >= %s AND day <= %s",
            (start, end),
        )
        for d, m, statusv in cur.fetchall():
            iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
            if m in TEAM_MEMBERS:
                out.setdefault(iso, {})[m] = "WFO" if statusv == "WFO" else "WFH"
        cur.close()
        return out
    except Exception:  # noqa: BLE001
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def save_manual_entries(member: str, entries: Dict[str, str | None], by: str) -> bool:
    """entries: {iso: 'WFO'|'WFH'|None}. None clears the manual record."""
    ups = [(date.fromisoformat(iso), member, v, by) for iso, v in entries.items() if v]
    dels = [date.fromisoformat(iso) for iso, v in entries.items() if not v]
    conn = None
    try:
        conn = _pg_connect()
        _pg_ensure_schema(conn)
        cur = conn.cursor()
        if ups:
            cur.executemany(
                f"INSERT INTO {WFH_MANUAL_TABLE} (day, member, status, updated_by) "
                f"VALUES (%s, %s, %s, %s) "
                f"ON CONFLICT (day, member) DO UPDATE SET status = EXCLUDED.status, "
                f"updated_by = EXCLUDED.updated_by, updated_at = NOW()",
                ups,
            )
        if dels:
            cur.executemany(
                f"DELETE FROM {WFH_MANUAL_TABLE} WHERE day = %s AND member = %s",
                [(d, member) for d in dels],
            )
        conn.commit()
        cur.close()
        return True
    except Exception as e:  # noqa: BLE001
        st.warning(f"Could not save your attendance: {e}", icon="⚠️")
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# =============================================================================
# RESOLUTION & METRICS
# =============================================================================
def resolve_member(identity: str | None) -> str | None:
    """Map a login short-name (e.g. 'Adham_Wagih') to a team member. Direct hit
    first, then a permissive name-token match, then None."""
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
    """Bundles the data needed to answer plan/actual questions for a period."""

    def __init__(self, rotation, ip, manual, holidays, public, cutover, today):
        self.rotation = rotation
        self.ip = ip
        self.manual = manual
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

    def planned(self, member: str, d: date) -> str | None:
        return planned_status(member, d, self.rotation)

    def actual(self, member: str, d: date) -> Tuple[str | None, str | None]:
        """(status, source) — source in {'ip','manual',None}."""
        iso = d.isoformat()
        if d >= self.cutover:
            ipd = self.ip.get(iso, {}).get(member)
            if ipd:
                return ipd, "ip"
        man = self.manual.get(iso, {}).get(member)
        if man:
            return man, "manual"
        return None, None

    def cell(self, member: str, d: date) -> dict:
        """Full per-day state for grids."""
        off = self.is_off(member, d)
        planned = self.planned(member, d)
        if off:
            return {"cat": "off", "note": off, "planned": planned, "actual": None, "source": None}
        actual, source = self.actual(member, d)
        if actual is None:
            cat = "future" if d > self.today else "unknown"
            return {"cat": cat, "note": None, "planned": planned, "actual": None, "source": None}
        deviation = (planned is not None) and (actual != planned)
        return {
            "cat": "office" if actual == "WFO" else "home",
            "note": None, "planned": planned, "actual": actual,
            "source": source, "deviation": deviation,
        }


def member_stats(res: Resolver, member: str, days: List[date]) -> dict:
    """Policy-first metrics for one member over ``days``."""
    eligible = 0            # office days (Mon–Thu) not off
    known = 0               # eligible days with a known actual
    attended = 0            # known eligible days actually in office
    planned_office = 0      # eligible days planned as office
    deviations = 0          # known days where actual != plan
    unknown_past = 0        # eligible past days with no data (need filling)
    off_days = 0
    for d in days:
        if not is_office_day(d):     # Sundays excluded from office policy
            continue
        if res.is_off(member, d):
            off_days += 1
            continue
        eligible += 1
        if res.planned(member, d) == "WFO":
            planned_office += 1
        actual, _src = res.actual(member, d)
        if actual is None:
            if d <= res.today:
                unknown_past += 1
            continue
        known += 1
        if actual == "WFO":
            attended += 1
        if res.planned(member, d) is not None and actual != res.planned(member, d):
            deviations += 1
    rate = (attended / known) if known else None
    return {
        "member": member, "eligible": eligible, "known": known,
        "attended": attended, "planned_office": planned_office,
        "deviations": deviations, "unknown_past": unknown_past,
        "off_days": off_days, "rate": rate,
        "compliant": (rate is not None and rate >= POLICY_MIN_RATE),
        "low_sample": known < 2,
    }


# =============================================================================
# DATE FILTER
# =============================================================================
PRESETS = ["This week", "Last 14 days", "This month", "Last 30 days", "Custom"]


def resolve_period(preset: str, offset: int, today: date, custom) -> Tuple[date, date, str]:
    if preset == "This week":
        base = sunday_of(today) + timedelta(days=7 * offset)
        return base, base + timedelta(days=4), "week"
    if preset == "Last 14 days":
        end = today + timedelta(days=14 * offset)
        return end - timedelta(days=13), end, "14d"
    if preset == "This month":
        y, m = today.year, today.month
        m0 = m - 1 + offset
        y += m0 // 12
        m = m0 % 12 + 1
        start = date(y, m, 1)
        nxt = date(y + (m == 12), (m % 12) + 1, 1)
        return start, nxt - timedelta(days=1), "month"
    if preset == "Last 30 days":
        end = today + timedelta(days=30 * offset)
        return end - timedelta(days=29), end, "30d"
    # Custom
    if isinstance(custom, (tuple, list)) and len(custom) == 2 and all(isinstance(x, date) for x in custom):
        a, b = custom
        return (a, b, "custom") if a <= b else (b, a, "custom")
    return today - timedelta(days=13), today, "custom"


# =============================================================================
# UI HELPERS
# =============================================================================
def pct(a: int, b: int) -> str:
    return f"{round(100 * a / b)}%" if b else "—"


def rate_pct(rate: float | None) -> str:
    return f"{round(100 * rate)}%" if rate is not None else "—"


def gauge_html(rate: float | None, low_sample: bool = False) -> str:
    if rate is None:
        return (
            "<div class='gauge'><div class='gauge-track'>"
            "<div class='gauge-target'></div></div>"
            "<div class='gauge-cap muted'>no data yet</div></div>"
        )
    width = max(0, min(100, round(100 * rate)))
    cls = "ok" if rate >= POLICY_MIN_RATE else "bad"
    note = " · low sample" if low_sample else ""
    return (
        "<div class='gauge'><div class='gauge-track'>"
        f"<div class='gauge-fill {cls}' style='width:{width}%'></div>"
        "<div class='gauge-target'></div></div>"
        f"<div class='gauge-cap {cls}'>{width}% office · policy ≥ 50%{note}</div></div>"
    )


def legend_html() -> str:
    chips = [
        ("chip-office", "In office"),
        ("chip-home", "Home (as planned)"),
        ("chip-dev", "Deviated from plan"),
        ("chip-breach", "Below 50% policy"),
        ("chip-unknown", "Unknown / fill"),
        ("chip-off", "Day off / holiday"),
    ]
    inner = "".join(f"<span class='chip {c}'>{t}</span>" for c, t in chips)
    return f"<div class='legend'>{inner}</div>"


def cell_html(state: dict, member: str, d: date) -> str:
    cat = state["cat"]
    plan_lbl = {"WFO": "Office", "WFH": "Home", None: "—"}[state.get("planned")]
    if cat == "off":
        title = f"{member} · {d:%a %d %b} · {state['note']}"
        return f"<td class='hcell c-off' title='{title}'>·</td>"
    if cat in ("unknown", "future"):
        cls = "c-unknown" if cat == "unknown" else "c-future"
        glyph = "?" if cat == "unknown" else ""
        title = f"{member} · {d:%a %d %b} · {'no data' if cat=='unknown' else 'upcoming'} · plan: {plan_lbl}"
        return f"<td class='hcell {cls}' title='{title}'>{glyph}</td>"
    actual_lbl = "Office" if state["actual"] == "WFO" else "Home"
    base = "c-office" if state["cat"] == "office" else "c-home"
    dev = " c-dev" if state.get("deviation") else ""
    glyph = "O" if state["cat"] == "office" else "H"
    src = "IP" if state["source"] == "ip" else "self"
    title = f"{member} · {d:%a %d %b} · was {actual_lbl} ({src}) · plan: {plan_lbl}"
    return f"<td class='hcell {base}{dev}' title='{title}'>{glyph}</td>"


def render_day_grid(res: Resolver, members: List[str], days: List[date], breach: Dict[str, bool]):
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
        label_cls = "hlabel breach" if breach.get(m) else "hlabel"
        cells = "".join(cell_html(res.cell(m, d), m, d) for d in office_days)
        badge = " <span class='breach-dot' title='Below 50% policy'></span>" if breach.get(m) else ""
        rows.append(f"<tr><td class='{label_cls} sticky'>{m}{badge}</td>{cells}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    if truncated:
        st.caption(f"Showing the most recent {GRID_MAX_COLS} office days of the range.")


# =============================================================================
# SECTIONS
# =============================================================================
def render_personal(res: Resolver, member: str, days: List[date], is_self: bool):
    s = member_stats(res, member, days)
    role = "management" if is_mgmt(member) else "engineering"
    who = "You" if is_self else member
    st.markdown(
        f"<div class='panel personal'>"
        f"<div class='panel-head'><div class='avatar'>{member[0]}</div>"
        f"<div><div class='panel-name'>{who} · {member}</div>"
        f"<div class='panel-sub'>{role} · policy target ≥ 50% office</div></div>"
        f"<div class='panel-flag {'breach' if (s['rate'] is not None and not s['compliant']) else 'ok'}'>"
        f"{'Below policy' if (s['rate'] is not None and not s['compliant']) else 'On policy' if s['rate'] is not None else 'No data'}"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(gauge_html(s["rate"], s["low_sample"]), unsafe_allow_html=True)

    k = st.columns(4)
    k[0].metric("Office attendance", rate_pct(s["rate"]), help="Office days ÷ eligible office days with data.")
    k[1].metric("In office", f"{s['attended']}/{s['known']}", help="Attended vs known eligible office days.")
    k[2].metric("Plan deviations", f"{s['deviations']}", help="Days your actual differed from the rotation.")
    k[3].metric("Missing days", f"{s['unknown_past']}", help="Past office days with no data — fill them below.")

    render_day_grid(res, [member], days, {member: (s["rate"] is not None and not s["compliant"])})

    if is_self:
        _render_self_fill(res, member, days)
    elif s["unknown_past"]:
        st.caption(f"{member} has {s['unknown_past']} unfilled day(s) in this range.")


def _render_self_fill(res: Resolver, member: str, days: List[date]):
    fillable = [
        d for d in days
        if is_office_day(d) and d <= res.today and not res.is_off(member, d)
        and res.actual(member, d)[0] is None
    ]
    default_open = bool(fillable)
    title = f"✍️ Fill my attendance ({len(fillable)} missing)" if fillable else "✍️ Fill / correct my attendance"
    with st.expander(title, expanded=default_open):
        st.caption(
            "For days before IP detection started (or any gap), record where you "
            "actually were. Only affects your own record."
        )
        # Let members also correct manually-entered days that already have a value.
        manual_days = [
            d for d in days
            if is_office_day(d) and d <= res.today and not res.is_off(member, d)
            and res.actual(member, d) == (res.manual.get(d.isoformat(), {}).get(member), "manual")
            and res.manual.get(d.isoformat(), {}).get(member)
        ]
        editable = sorted(set(fillable) | set(manual_days))
        if not editable:
            st.info("Nothing to fill in this range — every office day already has data. 🎉")
            return
        choices = {}
        opts = ["— leave blank —", "Office", "Home"]
        cols = st.columns(3)
        for i, d in enumerate(editable):
            cur = res.manual.get(d.isoformat(), {}).get(member)
            default_idx = 1 if cur == "WFO" else (2 if cur == "WFH" else 0)
            with cols[i % 3]:
                choices[d.isoformat()] = st.selectbox(
                    d.strftime("%a %d %b"), opts, index=default_idx,
                    key=f"fill_{member}_{d.isoformat()}",
                )
        if st.button("Save my attendance", type="primary", key=f"save_fill_{member}"):
            entries = {
                iso: ("WFO" if v == "Office" else "WFH" if v == "Home" else None)
                for iso, v in choices.items()
            }
            if save_manual_entries(member, entries, by=member):
                st.success("Saved. Thanks for keeping it accurate!")
                st.rerun()


def render_team(res: Resolver, days: List[date]):
    stats = {m: member_stats(res, m, days) for m in TEAM_MEMBERS}
    breach = {m: (stats[m]["rate"] is not None and not stats[m]["compliant"]) for m in TEAM_MEMBERS}

    total_attended = sum(s["attended"] for s in stats.values())
    total_known = sum(s["known"] for s in stats.values())
    total_dev = sum(s["deviations"] for s in stats.values())
    total_unknown = sum(s["unknown_past"] for s in stats.values())
    rated = [s for s in stats.values() if s["rate"] is not None]
    compliant = sum(1 for s in rated if s["compliant"])
    team_rate = (total_attended / total_known) if total_known else None

    k = st.columns(4)
    k[0].metric("Team office rate", rate_pct(team_rate), help="All members' office days ÷ known eligible office days.")
    k[1].metric("On policy", f"{compliant}/{len(rated)}" if rated else "—",
                help="Members meeting the ≥50% office policy (with data).")
    k[2].metric("Plan deviations", f"{total_dev}")
    k[3].metric("Missing entries", f"{total_unknown}", help="Past office days across the team with no data.")

    st.markdown(legend_html(), unsafe_allow_html=True)

    st.markdown("<div class='board-title'>Office attendance vs 50% policy</div>", unsafe_allow_html=True)
    order = sorted(TEAM_MEMBERS, key=lambda m: (stats[m]["rate"] is None, -(stats[m]["rate"] or 0)))
    bars = []
    for m in order:
        s = stats[m]
        if s["rate"] is None:
            bars.append(
                f"<div class='bar-row'><div class='bar-name'>{m}</div>"
                f"<div class='bar-track'><div class='bar-target'></div></div>"
                f"<div class='bar-val muted'>—</div></div>"
            )
            continue
        w = max(2, min(100, round(100 * s["rate"])))
        cls = "ok" if s["compliant"] else "bad"
        tag = "" if s["compliant"] else " <span class='mini-breach'>below policy</span>"
        bars.append(
            f"<div class='bar-row'><div class='bar-name'>{m}</div>"
            f"<div class='bar-track'><div class='bar-fill {cls}' style='width:{w}%'></div>"
            f"<div class='bar-target'></div></div>"
            f"<div class='bar-val {cls}'>{w}%{tag}</div></div>"
        )
    st.markdown(f"<div class='board'>{''.join(bars)}</div>", unsafe_allow_html=True)

    st.markdown("<div class='board-title'>Day by day</div>", unsafe_allow_html=True)
    render_day_grid(res, TEAM_MEMBERS, days, breach)


def render_by_member(res: Resolver, days: List[date], focus: str | None):
    st.markdown("<div class='board-title'>Inspect a member</div>", unsafe_allow_html=True)
    default_idx = TEAM_MEMBERS.index(focus) if focus in TEAM_MEMBERS else 0
    m = st.selectbox("Member", TEAM_MEMBERS, index=default_idx, key="inspect_member")
    s = member_stats(res, m, days)

    k = st.columns(5)
    k[0].metric("Office rate", rate_pct(s["rate"]))
    k[1].metric("Attended", f"{s['attended']}/{s['known']}")
    k[2].metric("Planned office", f"{s['planned_office']}")
    k[3].metric("Deviations", f"{s['deviations']}")
    k[4].metric("Off days", f"{s['off_days']}")
    st.markdown(gauge_html(s["rate"], s["low_sample"]), unsafe_allow_html=True)
    render_day_grid(res, [m], days, {m: (s["rate"] is not None and not s["compliant"])})


def render_rotation(rotation: List[Set[str]], can_edit: bool):
    st.markdown("<div class='board-title'>Repeating two-week rotation</div>", unsafe_allow_html=True)
    st.caption(
        "The plan repeats every two weeks. Sundays are always home; each member "
        "is scheduled in the office exactly 2 of the 4 office days (the 50% baseline)."
    )
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"]
    header = "<th class='hhdr sticky'>Member</th>" + "".join(
        f"<th class='hhdr'>W{w+1} {wd}</th>" for w in range(2) for wd in weekdays
    )
    rows = []
    for m in TEAM_MEMBERS:
        cells = []
        for slot in range(10):
            wd = weekdays[slot % 5]
            if wd == "Sun":
                cells.append("<td class='hcell c-home'>H</td>")
            elif m in rotation[slot]:
                cells.append("<td class='hcell c-office'>O</td>")
            else:
                cells.append("<td class='hcell c-home'>H</td>")
        rows.append(f"<tr><td class='hlabel sticky'>{m}</td>{''.join(cells)}</tr>")
    st.markdown(
        f"<div class='grid-wrap'><table class='hgrid'><thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    if can_edit:
        with st.expander("Management: regenerate the rotation"):
            st.caption(
                "Generates a fresh policy-valid rotation and saves it as the new "
                "repeating plan. Past actuals are unaffected."
            )
            if st.button("Regenerate & save rotation", type="secondary"):
                seed_bump = st.session_state.get("rot_regen", 0) + 1
                st.session_state["rot_regen"] = seed_bump
                state = random.getstate()
                try:
                    random.seed(ROTATION_SEED + seed_bump)
                    new_pat = None
                    for _ in range(200):
                        try:
                            new_pat = generate_two_week_pattern(rotating=list(TEAM_MEMBERS))
                            break
                        except RuntimeError:
                            continue
                finally:
                    random.setstate(state)
                if new_pat and save_rotation(new_pat):
                    st.success("New rotation saved.")
                    st.rerun()


def render_method(res: Resolver, can_edit: bool):
    st.markdown("<div class='board-title'>How attendance is measured</div>", unsafe_allow_html=True)
    umap = " · ".join(f"{k}→<code>{v}</code>" for k, v in MEMBER_TO_SESSION_USER.items())
    st.markdown(
        f"""
        <div class='algo'>
          <ol>
            <li><b>Plan</b> — the repeating two-week rotation (each member in office 2 of 4 office
                days). Sundays are always home; during onboarding {NEW_JOINER} is planned in office
                every office day through {NEW_JOINER_FULL_OFFICE_UNTIL:%d %b %Y}.</li>
            <li><b>Actual (IP)</b> — from <code>{SESSION_STATES_TABLE}</code>: on a day
                <b>on/after {res.cutover:%d %b %Y}</b>, a member counts as <b>in office</b> when any
                session's <code>client_ip</code> starts with <code>{OFFICE_IP_PREFIX}</code>
                (impersonated sessions excluded). Matched by short name: {umap}.</li>
            <li><b>Actual (self)</b> — before the detection date, or any day IP has no signal, the
                member's own entry is used.</li>
            <li><b>Eligible office days</b> — Mon–Thu that aren't public holidays or personal days off.
                Sundays and off-days never count toward the policy.</li>
            <li><b>HR policy</b> — each member should be in office on <b>≥ 50%</b> of eligible office
                days over the selected period. Office rate = attended ÷ known eligible days.</li>
          </ol>
          <div class='algo-colours'>
            <b>Colours:</b> green = in office · blue = home as planned ·
            <span class='ora'>orange = deviated from the plan</span> ·
            <span class='red'>red = below the 50% policy</span> · grey = unknown · slate = off.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div class='board-title'>IP detection start date</div>", unsafe_allow_html=True)
    st.caption("Days before this date rely on members' self-reported attendance.")
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


# =============================================================================
# STYLES
# =============================================================================
def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink:#0f2942; --sub:#64748b; --line:#e6edf5;
            --office:#22c55e; --office-d:#15803d; --home:#93b7e4; --home-bg:#eef4fb;
            --dev:#f59e0b; --breach:#ef4444; --unknown:#cbd5e1; --off:#e2e8f0;
        }
        .block-container { padding-top: 1.2rem; max-width: 1250px; }
        .att-hero {
            background: linear-gradient(120deg,#0b3c5d 0%, #1b6ca8 100%);
            color:#fff; border-radius:16px; padding:1.1rem 1.4rem; margin-bottom:1rem;
            box-shadow:0 10px 30px rgba(11,60,93,.25);
        }
        .att-hero h1 { font-size:1.5rem; margin:0 0 .2rem 0; font-weight:700; }
        .att-hero p { margin:0; opacity:.9; font-size:.9rem; }
        .who-pill {
            display:inline-block; margin-top:.5rem; background:rgba(255,255,255,.16);
            border:1px solid rgba(255,255,255,.3); border-radius:999px;
            padding:.2rem .7rem; font-size:.8rem; font-weight:600;
        }
        .panel {
            background:#fff; border:1px solid var(--line); border-radius:14px;
            padding:1rem 1.15rem; box-shadow:0 4px 14px rgba(15,41,66,.06); margin-bottom:.6rem;
        }
        .panel-head { display:flex; align-items:center; gap:.75rem; }
        .avatar {
            width:44px; height:44px; border-radius:50%; flex:0 0 44px;
            background:linear-gradient(135deg,#1b6ca8,#0b3c5d); color:#fff;
            display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1.2rem;
        }
        .panel-name { font-weight:700; color:var(--ink); font-size:1.05rem; }
        .panel-sub { color:var(--sub); font-size:.82rem; }
        .panel-flag { margin-left:auto; padding:.3rem .8rem; border-radius:999px; font-weight:700; font-size:.8rem; }
        .panel-flag.ok { background:#e7f8ee; color:#15803d; }
        .panel-flag.breach { background:#fdecec; color:#b91c1c; }
        .gauge { margin:.7rem 0 .2rem 0; }
        .gauge-track {
            position:relative; height:14px; border-radius:999px; background:#eef2f7; overflow:hidden;
        }
        .gauge-fill { height:100%; border-radius:999px; }
        .gauge-fill.ok { background:linear-gradient(90deg,#34d399,#22c55e); }
        .gauge-fill.bad { background:linear-gradient(90deg,#f87171,#ef4444); }
        .gauge-target {
            position:absolute; top:-3px; bottom:-3px; left:50%; width:2px;
            background:#0f2942; opacity:.55;
        }
        .gauge-cap { font-size:.76rem; margin-top:.25rem; font-weight:600; color:var(--sub); }
        .gauge-cap.ok { color:#15803d; } .gauge-cap.bad { color:#b91c1c; } .gauge-cap.muted{color:var(--sub);}
        .legend { display:flex; flex-wrap:wrap; gap:.4rem; margin:.5rem 0 .7rem 0; }
        .chip { font-size:.72rem; padding:.18rem .55rem; border-radius:999px; font-weight:600; border:1px solid transparent; }
        .chip-office { background:#dcfce7; color:#15803d; }
        .chip-home { background:var(--home-bg); color:#31567e; }
        .chip-dev { background:#fef3c7; color:#92600a; border-color:#f59e0b; }
        .chip-breach { background:#fee2e2; color:#b91c1c; border-color:#ef4444; }
        .chip-unknown { background:#f1f5f9; color:#64748b; }
        .chip-off { background:#e9edf2; color:#7c8ba1; }
        .board-title { font-weight:700; color:var(--ink); margin:1rem 0 .35rem; font-size:1rem; }
        .board { background:#fff; border:1px solid var(--line); border-radius:14px; padding:.8rem 1rem; box-shadow:0 4px 14px rgba(15,41,66,.05); }
        .bar-row { display:flex; align-items:center; gap:.6rem; margin:.35rem 0; }
        .bar-name { width:70px; font-weight:600; color:var(--ink); font-size:.85rem; }
        .bar-track { position:relative; flex:1; height:16px; background:#eef2f7; border-radius:999px; overflow:hidden; }
        .bar-fill { height:100%; border-radius:999px; }
        .bar-fill.ok { background:linear-gradient(90deg,#34d399,#22c55e); }
        .bar-fill.bad { background:linear-gradient(90deg,#f87171,#ef4444); }
        .bar-target { position:absolute; top:-2px; bottom:-2px; left:50%; width:2px; background:#0f2942; opacity:.5; }
        .bar-val { width:120px; text-align:right; font-weight:700; font-size:.82rem; }
        .bar-val.ok { color:#15803d; } .bar-val.bad { color:#b91c1c; } .bar-val.muted { color:var(--sub); }
        .mini-breach { font-size:.66rem; background:#fee2e2; color:#b91c1c; padding:.05rem .3rem; border-radius:6px; font-weight:700; }
        .grid-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:14px; background:#fff; box-shadow:0 4px 14px rgba(15,41,66,.05); }
        .hgrid { border-collapse:separate; border-spacing:0; font-size:.72rem; }
        .hgrid th, .hgrid td { text-align:center; }
        .hhdr { padding:.35rem .3rem; color:var(--sub); font-weight:700; background:#f7fafc; position:sticky; top:0; }
        .hhdr .mini { display:block; font-size:.6rem; opacity:.7; font-weight:600; }
        .hhdr.sticky, .hlabel.sticky { position:sticky; left:0; z-index:2; background:#f7fafc; }
        .hlabel { text-align:left; padding:.3rem .6rem; font-weight:600; color:var(--ink); white-space:nowrap; background:#fff; }
        .hlabel.breach { color:#b91c1c; }
        .breach-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--breach); margin-left:.25rem; }
        .hcell { width:26px; height:26px; font-weight:700; color:#fff; border:2px solid #fff; border-radius:6px; }
        .c-office { background:var(--office); }
        .c-home { background:var(--home); color:#26456a; }
        .c-off { background:var(--off); color:#9aa7b6; font-weight:400; }
        .c-unknown { background:#f1f5f9; color:#94a3b8; }
        .c-future { background:#fafcff; color:#cbd5e1; }
        .c-dev { box-shadow:0 0 0 3px var(--dev) inset; }
        .algo { background:#fff; border:1px solid var(--line); border-left:4px solid #1b6ca8; border-radius:12px; padding:.7rem 1rem; }
        .algo ol { margin:0; padding-left:1.1rem; font-size:.85rem; color:#334155; }
        .algo li { margin-bottom:.3rem; }
        .algo code { background:#eef2f7; padding:.02rem .3rem; border-radius:4px; color:#0b3c5d; font-size:.8rem; }
        .algo-colours { margin-top:.5rem; font-size:.8rem; color:#475569; }
        .algo-colours .ora { color:#b45309; font-weight:600; }
        .algo-colours .red { color:#b91c1c; font-weight:600; }
        .filter-cap { color:var(--sub); font-size:.82rem; margin:.1rem 0 .4rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# MAIN
# =============================================================================
def main() -> None:
    st.set_page_config(page_title="Team Attendance", layout="wide", page_icon="🏢")
    inject_css()

    today = date.today()
    login = st.session_state.get("username")
    real_member = resolve_member(login)

    st.markdown(
        "<div class='att-hero'><h1>🏢 Team Attendance</h1>"
        "<p>Office vs. home tracking measured against the 50% HR policy — for you and the whole team.</p>"
        + (f"<span class='who-pill'>Signed in as {real_member}</span>" if real_member
           else "<span class='who-pill'>Team view</span>")
        + "</div>",
        unsafe_allow_html=True,
    )

    # ---- Smart date filter ----
    fc = st.columns([2, 1, 1, 1, 3])
    with fc[0]:
        preset = st.selectbox("Period", PRESETS, index=1, key="att_preset")
    if st.session_state.get("_att_last_preset") != preset:
        st.session_state["att_offset"] = 0
        st.session_state["_att_last_preset"] = preset
    offset = st.session_state.get("att_offset", 0)
    with fc[1]:
        st.write("")
        if st.button("◀ Prev", use_container_width=True, disabled=(preset == "Custom")):
            st.session_state["att_offset"] = offset - 1
            st.rerun()
    with fc[2]:
        st.write("")
        if st.button("Today", use_container_width=True, disabled=(preset == "Custom")):
            st.session_state["att_offset"] = 0
            st.rerun()
    with fc[3]:
        st.write("")
        nxt_disabled = preset == "Custom" or offset >= 0
        if st.button("Next ▶", use_container_width=True, disabled=nxt_disabled):
            st.session_state["att_offset"] = offset + 1
            st.rerun()
    custom = None
    if preset == "Custom":
        with fc[4]:
            custom = st.date_input(
                "Custom range",
                value=(today - timedelta(days=13), today),
                key="att_custom",
            )

    start, end, _kind = resolve_period(preset, st.session_state.get("att_offset", 0), today, custom)
    days = workdays_between(start, end)
    st.markdown(
        f"<div class='filter-cap'>📅 {start:%a %d %b %Y} → {end:%a %d %b %Y} "
        f"· {sum(1 for d in days if is_office_day(d))} office days</div>",
        unsafe_allow_html=True,
    )

    # ---- Load data once ----
    rotation = load_rotation()
    cutover = get_detection_start()
    public = load_public_holidays()
    holidays = load_personal_holidays()
    ip = load_ip_actuals(start, end)
    manual = load_manual(start, end)
    res = Resolver(rotation, ip, manual, holidays, public, cutover, today)

    # ---- Personalized panel (self) ----
    if real_member:
        render_personal(res, real_member, days, is_self=True)
    else:
        st.info(
            "You're viewing the team dashboard. Sign in with your name to see your "
            "personal attendance and fill missing days.",
            icon="👤",
        )

    can_edit = is_mgmt(real_member)
    tab_labels = ["📊 Team overview", "👥 By member", "🔁 Rotation plan", "ℹ️ How it works"]
    tabs = st.tabs(tab_labels)
    with tabs[0]:
        render_team(res, days)
    with tabs[1]:
        render_by_member(res, days, real_member)
    with tabs[2]:
        render_rotation(rotation, can_edit)
    with tabs[3]:
        render_method(res, can_edit)


if __name__ == "__main__":
    main()
