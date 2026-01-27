"""WFH/WFO Schedule - Professional team scheduling dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Sequence, Set
import random

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.theme import set_theme

set_theme(page_title="WFH Schedule", page_icon="🏠")


# =============================================================================
# CONFIGURATION
# =============================================================================

TEAM_MEMBERS: List[str] = [
    "Adham",
    "Karam",
    "Abdelkhalek",
    "Hesham",
    "Salma",
]
YEAR = 2026
WORKDAYS = {6, 0, 1, 2, 3}  # Sunday (6) through Thursday (3) in datetime.weekday()
WEEK_STORAGE_DIR = Path("data") / "wfh_schedules" / str(YEAR)
HOLIDAYS_FILE = Path("data") / f"holidays_{YEAR}.json"
PUBLIC_HOLIDAYS_FILE = Path("data") / f"public_holidays_{YEAR}.json"

# Role metadata used for role-based rules
ROLE_BY_MEMBER: Dict[str, str] = {
    "Adham": "mgmt-support",
    "Karam": "mgmt-support",
    "Abdelkhalek": "mgmt-support",
    "Hesham": "engineering",
    "Salma": "engineering",
}

# Member avatars (emoji-based for simplicity)
MEMBER_AVATARS: Dict[str, str] = {
    "Adham": "👨‍💼",
    "Karam": "👨‍💻",
    "Abdelkhalek": "👨‍🔧",
    "Hesham": "🧑‍💻",
    "Salma": "👩‍💻",
}

# Personal, soft attendance preferences (validated as warnings only).
PREFERS_WFO_DAYS: Dict[str, Set[int]] = {
    "Karam": {0, 1},
}

DISLIKES_WFO_DAYS: Dict[str, Set[int]] = {
    "Hesham": {0, 3},
    "Abdelkhalek": {6, 3},
    "Salma": {3},
}

# Default public holidays for Egypt 2026
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


# =============================================================================
# STYLES
# =============================================================================

st.markdown(
    """
    <style>
    /* Hero Section */
    .wfh-hero {
        background: linear-gradient(135deg, #059669 0%, #10b981 50%, #34d399 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(5, 150, 105, 0.3);
    }
    .wfh-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
    }
    .wfh-hero p {
        opacity: 0.9;
        margin: 0;
        font-size: 1rem;
    }

    /* Quick Stats */
    .quick-stats {
        display: flex;
        gap: 1rem;
        margin-top: 1.25rem;
    }
    .quick-stat {
        background: rgba(255,255,255,0.2);
        padding: 0.5rem 1rem;
        border-radius: 10px;
        backdrop-filter: blur(10px);
    }
    .quick-stat-value {
        font-size: 1.5rem;
        font-weight: 700;
    }
    .quick-stat-label {
        font-size: 0.75rem;
        opacity: 0.9;
    }

    /* Stat Cards */
    .stat-card {
        background: white;
        border-radius: 16px;
        padding: 1.25rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        text-align: center;
        transition: all 0.3s ease;
        height: 100%;
    }
    .stat-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    }
    .stat-value {
        font-size: 2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #059669, #10b981);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stat-label {
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 0.25rem;
        font-weight: 500;
    }

    /* Today Cards */
    .today-section {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        gap: 1rem;
        margin: 1.5rem 0;
    }
    .today-card {
        background: white;
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        transition: all 0.3s ease;
    }
    .today-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    }
    .today-card.current {
        border-left: 4px solid #10b981;
    }
    .today-card.next {
        border-left: 4px solid #6366f1;
    }
    .today-card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1rem;
    }
    .today-card-title {
        font-weight: 700;
        font-size: 1.1rem;
        color: #1f2937;
    }
    .today-card-badge {
        padding: 0.25rem 0.75rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .badge-today {
        background: #dcfce7;
        color: #166534;
    }
    .badge-upcoming {
        background: #e0e7ff;
        color: #3730a3;
    }
    .today-card-date {
        color: #64748b;
        font-size: 0.85rem;
        margin-bottom: 0.75rem;
    }
    .today-card-members {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
    }
    .member-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.4rem 0.75rem;
        border-radius: 999px;
        font-size: 0.85rem;
        font-weight: 500;
        transition: all 0.2s;
    }
    .member-chip.office {
        background: linear-gradient(135deg, #dcfce7, #d1fae5);
        color: #166534;
        border: 1px solid #86efac;
    }
    .member-chip.home {
        background: #f1f5f9;
        color: #64748b;
        border: 1px solid #e2e8f0;
    }
    .member-chip.holiday {
        background: linear-gradient(135deg, #fef3c7, #fde68a);
        color: #92400e;
        border: 1px solid #fcd34d;
    }
    .member-avatar {
        font-size: 1rem;
    }

    /* Week Table */
    .week-container {
        background: white;
        border-radius: 16px;
        padding: 1.25rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        margin-bottom: 1rem;
    }
    .week-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1rem;
    }
    .week-title {
        font-weight: 700;
        font-size: 1.1rem;
        color: #1f2937;
    }
    .week-dates {
        color: #64748b;
        font-size: 0.85rem;
    }
    .schedule-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 0.88rem;
    }
    .schedule-table th {
        background: linear-gradient(135deg, #f0fdf4, #ecfdf5);
        color: #166534;
        font-weight: 600;
        padding: 0.75rem 0.5rem;
        text-align: center;
        border-bottom: 2px solid #86efac;
    }
    .schedule-table th:first-child {
        border-radius: 8px 0 0 0;
        text-align: left;
        padding-left: 1rem;
    }
    .schedule-table th:last-child {
        border-radius: 0 8px 0 0;
    }
    .schedule-table td {
        padding: 0.6rem 0.5rem;
        text-align: center;
        border-bottom: 1px solid #f1f5f9;
    }
    .schedule-table tr:last-child td {
        border-bottom: none;
    }
    .schedule-table tr:hover td {
        background: #fafafa;
    }
    .day-cell {
        text-align: left !important;
        padding-left: 1rem !important;
        font-weight: 500;
        color: #374151;
    }
    .day-cell.is-today {
        background: linear-gradient(135deg, #fef3c7, #fde68a) !important;
        border-radius: 8px;
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .status-office {
        background: linear-gradient(135deg, #dcfce7, #bbf7d0);
        color: #166534;
    }
    .status-home {
        background: #f1f5f9;
        color: #64748b;
    }
    .status-holiday {
        background: linear-gradient(135deg, #fef3c7, #fde68a);
        color: #92400e;
    }
    .status-skippable {
        box-shadow: inset 0 0 0 2px #fbbf24;
    }

    /* Today indicator */
    .today-indicator {
        display: inline-block;
        margin-left: 0.5rem;
        padding: 0.15rem 0.5rem;
        border-radius: 999px;
        background: linear-gradient(135deg, #fbbf24, #f59e0b);
        color: white;
        font-size: 0.65rem;
        font-weight: 700;
        text-transform: uppercase;
    }

    /* Public holiday tag */
    .public-holiday-tag {
        display: inline-block;
        margin-left: 0.5rem;
        padding: 0.15rem 0.5rem;
        border-radius: 999px;
        background: #fee2e2;
        color: #b91c1c;
        font-size: 0.65rem;
        font-weight: 600;
    }

    /* My Schedule Card */
    .my-schedule-card {
        background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
        border-radius: 16px;
        padding: 1.5rem;
        border: 2px solid #86efac;
        margin-bottom: 1.5rem;
    }
    .my-schedule-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1rem;
    }
    .my-schedule-title {
        font-weight: 700;
        font-size: 1.2rem;
        color: #166534;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .my-schedule-stats {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 1rem;
    }
    .my-stat {
        background: white;
        border-radius: 10px;
        padding: 0.75rem;
        text-align: center;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    .my-stat-value {
        font-size: 1.5rem;
        font-weight: 700;
        color: #166534;
    }
    .my-stat-label {
        font-size: 0.75rem;
        color: #64748b;
    }
    .my-schedule-days {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-top: 1rem;
    }
    .my-day-chip {
        padding: 0.4rem 0.75rem;
        border-radius: 8px;
        font-size: 0.8rem;
        font-weight: 500;
    }
    .my-day-office {
        background: #dcfce7;
        color: #166534;
        border: 1px solid #86efac;
    }
    .my-day-home {
        background: #f1f5f9;
        color: #64748b;
    }

    /* Calendar Heatmap */
    .heatmap-container {
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        overflow-x: auto;
    }
    .heatmap-title {
        font-weight: 700;
        font-size: 1.1rem;
        color: #1f2937;
        margin-bottom: 1rem;
    }
    .heatmap-legend {
        display: flex;
        gap: 1rem;
        margin-top: 1rem;
        font-size: 0.8rem;
        color: #64748b;
    }
    .legend-item {
        display: flex;
        align-items: center;
        gap: 0.35rem;
    }
    .legend-box {
        width: 14px;
        height: 14px;
        border-radius: 3px;
    }
    .legend-office { background: #10b981; }
    .legend-home { background: #e2e8f0; }
    .legend-holiday { background: #fbbf24; }

    /* Validation Cards */
    .validation-card {
        background: white;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.75rem;
        border: 1px solid #e2e8f0;
        transition: all 0.2s;
    }
    .validation-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    }
    .validation-card.error {
        border-left: 4px solid #ef4444;
        background: #fef2f2;
    }
    .validation-card.warning {
        border-left: 4px solid #f59e0b;
        background: #fffbeb;
    }
    .validation-date {
        font-size: 0.75rem;
        color: #64748b;
        margin-bottom: 0.25rem;
    }
    .validation-text {
        font-size: 0.9rem;
        color: #374151;
    }

    /* Holiday Cards */
    .holiday-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 0.75rem;
        margin: 1rem 0;
    }
    .holiday-card {
        background: white;
        border-radius: 12px;
        padding: 0.75rem 1rem;
        border: 1px solid #e2e8f0;
        transition: all 0.2s;
    }
    .holiday-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    }
    .holiday-card.public {
        border-left: 3px solid #ef4444;
    }
    .holiday-card.personal {
        border-left: 3px solid #f59e0b;
    }
    .holiday-date {
        font-size: 0.8rem;
        color: #64748b;
        font-weight: 500;
    }
    .holiday-name {
        font-size: 0.9rem;
        color: #1f2937;
        font-weight: 600;
        margin-top: 0.25rem;
    }

    /* Form Section */
    .form-section {
        background: #f8fafc;
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        margin: 1rem 0;
    }

    /* Section Title */
    .section-title {
        font-weight: 700;
        font-size: 1.25rem;
        color: #1f2937;
        margin-bottom: 1rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    /* Empty State */
    .empty-state {
        text-align: center;
        padding: 2rem;
        color: #64748b;
    }
    .empty-state-icon {
        font-size: 3rem;
        margin-bottom: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# DATA CLASSES AND HELPERS
# =============================================================================


@dataclass
class DayAssignment:
    dt: date
    members_wfo: Set[str]


def is_workday(d: date) -> bool:
    return d.year == YEAR and d.weekday() in WORKDAYS


def generate_two_week_pattern() -> List[Set[str]]:
    """Generate a 10-workday (Sun-Thu x2) pattern satisfying hard rules."""
    n = len(TEAM_MEMBERS)
    people = list(range(n))
    idx = {name: i for i, name in enumerate(TEAM_MEMBERS)}
    karam = idx["Karam"]
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"] * 2

    all_subsets: List[Sequence[int]] = []
    for r in (2, 3):
        all_subsets.extend(list(combinations(people, r)))
    random.shuffle(all_subsets)

    options_per_day: List[List[Sequence[int]]] = []
    for day in range(10):
        wd = weekdays[day]
        day_opts: List[Sequence[int]] = []
        for subset in all_subsets:
            s = set(subset)
            if wd in {"Mon", "Tue"} and karam not in s:
                continue
            day_opts.append(subset)
        options_per_day.append(day_opts)

    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    mgmt_indices = {
        i for i, name in enumerate(TEAM_MEMBERS) if ROLE_BY_MEMBER.get(name) == "mgmt-support"
    }

    best_schedule: List[Set[int]] | None = None
    found = False

    def backtrack(
        day: int,
        schedule: List[Set[int]],
        counts: List[int],
        streaks: List[int],
        pair_seen: List[List[bool]],
        sun_counts: List[int],
        thu_counts: List[int],
    ) -> None:
        nonlocal best_schedule, found
        if found:
            return

        if day == 10:
            if any(c != 5 for c in counts):
                return
            if not all(pair_seen[i][j] for i, j in pairs):
                return
            best_schedule = [set(s) for s in schedule]
            found = True
            return

        wd = weekdays[day]
        candidates = list(options_per_day[day])
        random.shuffle(candidates)
        remaining_days = 10 - day - 1

        for subset in candidates:
            s = set(subset)
            new_counts = counts[:]
            feasible = True
            for p in s:
                new_counts[p] += 1
                if new_counts[p] > 5:
                    feasible = False
                    break
            if not feasible:
                continue

            if not (mgmt_indices & s):
                continue

            for p in range(n):
                max_possible = new_counts[p] + remaining_days
                if max_possible < 5:
                    feasible = False
                    break
            if not feasible:
                continue

            new_streaks = streaks[:]
            for p in range(n):
                if p in s:
                    new_streaks[p] = streaks[p] + 1
                    if new_streaks[p] >= 3:
                        feasible = False
                        break
                else:
                    new_streaks[p] = 0
            if not feasible:
                continue

            new_sun = sun_counts[:]
            new_thu = thu_counts[:]
            if wd == "Sun":
                for p in s:
                    new_sun[p] += 1
                    if new_sun[p] > 1:
                        feasible = False
                        break
            elif wd == "Thu":
                for p in s:
                    new_thu[p] += 1
                    if new_thu[p] > 1:
                        feasible = False
                        break
            if not feasible:
                continue

            new_pair_seen = [row[:] for row in pair_seen]
            for i in s:
                for j in s:
                    if i < j:
                        new_pair_seen[i][j] = True

            schedule.append(s)
            backtrack(day + 1, schedule, new_counts, new_streaks, new_pair_seen, new_sun, new_thu)
            schedule.pop()

    initial_counts = [0] * n
    initial_streaks = [0] * n
    initial_pairs = [[False] * n for _ in range(n)]
    initial_sun = [0] * n
    initial_thu = [0] * n

    backtrack(0, [], initial_counts, initial_streaks, initial_pairs, initial_sun, initial_thu)

    if not found or best_schedule is None:
        raise RuntimeError("Unable to find a valid 2-week WFH/WFO pattern with given rules.")

    name_pattern: List[Set[str]] = []
    for s in best_schedule:
        name_pattern.append({TEAM_MEMBERS[i] for i in s})
    return name_pattern


def get_anchor_sunday() -> date:
    first_of_year = date(YEAR, 1, 1)
    offset = (first_of_year.weekday() - 6) % 7
    return first_of_year - timedelta(days=offset)


def build_base_schedule(pattern: List[Set[str]]) -> Dict[str, Dict[str, str]]:
    anchor = get_anchor_sunday()
    end = date(YEAR, 12, 31)
    pattern_len = len(pattern)
    workday_index = 0
    d = anchor
    schedule: Dict[str, Dict[str, str]] = {}

    while d <= end:
        if d.weekday() in WORKDAYS:
            members_wfo = pattern[workday_index % pattern_len]
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
    while d.weekday() != 6:
        d += timedelta(days=1)
    sundays: List[date] = []
    while d.year == YEAR:
        sundays.append(d)
        d += timedelta(days=7)
    return sundays


def week_file_path(week_start: date) -> Path:
    return WEEK_STORAGE_DIR / f"week_{week_start.isoformat()}.json"


def ensure_week_files(base_schedule: Dict[str, Dict[str, str]]) -> None:
    WEEK_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    for ws in iter_sundays_in_year():
        path = week_file_path(ws)
        if path.exists():
            continue
        week_days: Dict[str, Dict[str, str]] = {}
        for offset in range(5):
            d = ws + timedelta(days=offset)
            iso = d.isoformat()
            if iso in base_schedule:
                week_days[iso] = base_schedule[iso]
        payload = {"week_start": ws.isoformat(), "days": week_days}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_week(week_start: date, base_schedule: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    path = week_file_path(week_start)
    if not path.exists():
        WEEK_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        week_days: Dict[str, Dict[str, str]] = {}
        for offset in range(5):
            d = week_start + timedelta(days=offset)
            iso = d.isoformat()
            if iso in base_schedule:
                week_days[iso] = base_schedule[iso]
        payload = {"week_start": week_start.isoformat(), "days": week_days}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return week_days

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("days", {})
    except Exception:
        week_days = {}
        for offset in range(5):
            d = week_start + timedelta(days=offset)
            iso = d.isoformat()
            if iso in base_schedule:
                week_days[iso] = base_schedule[iso]
        payload = {"week_start": week_start.isoformat(), "days": week_days}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return week_days


def save_week(week_start: date, days: Dict[str, Dict[str, str]]) -> None:
    WEEK_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"week_start": week_start.isoformat(), "days": days}
    path = week_file_path(week_start)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_holidays() -> Dict[str, List[str]]:
    if not HOLIDAYS_FILE.exists():
        return {}
    try:
        raw = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

    holidays: Dict[str, List[str]] = {}
    if isinstance(raw, dict):
        for iso, members in raw.items():
            if not isinstance(members, list):
                continue
            unique = sorted({m for m in members if isinstance(m, str)})
            if unique:
                holidays[iso] = unique
    return holidays


def save_holidays(holidays: Dict[str, List[str]]) -> None:
    HOLIDAYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOLIDAYS_FILE.write_text(json.dumps(holidays, indent=2), encoding="utf-8")


def load_public_holidays() -> Dict[str, str]:
    if not PUBLIC_HOLIDAYS_FILE.exists():
        return DEFAULT_PUBLIC_HOLIDAYS.copy()
    try:
        raw = json.loads(PUBLIC_HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_PUBLIC_HOLIDAYS.copy()

    holidays: Dict[str, str] = {}
    if isinstance(raw, dict):
        for iso, name in raw.items():
            if not isinstance(iso, str):
                continue
            try:
                d = date.fromisoformat(iso)
            except ValueError:
                continue
            if d.year != YEAR:
                continue
            holidays[iso] = str(name)
    return holidays or DEFAULT_PUBLIC_HOLIDAYS.copy()


def save_public_holidays(public_holidays: Dict[str, str]) -> None:
    PUBLIC_HOLIDAYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_HOLIDAYS_FILE.write_text(json.dumps(public_holidays, indent=2), encoding="utf-8")


def load_full_year_from_weeks(base_schedule: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    year_sched: Dict[str, Dict[str, str]] = {}
    for ws in iter_sundays_in_year():
        days = load_week(ws, base_schedule)
        for iso, members in days.items():
            if iso.startswith(str(YEAR)):
                year_sched[iso] = members
    return year_sched


def get_week_start_for_date(d: date) -> date:
    ref = d
    if d.weekday() == 5:
        ref = d + timedelta(days=1)
    offset = (ref.weekday() - 6) % 7
    return ref - timedelta(days=offset)


def build_error_free_base_schedule(max_attempts: int = 50) -> tuple[List[Set[str]], Dict[str, Dict[str, str]]]:
    last_errors: List[tuple[date | None, str]] = []
    for _ in range(max_attempts):
        pattern = generate_two_week_pattern()
        base_schedule = build_base_schedule(pattern)
        tmp_year_sched: Dict[str, Dict[str, str]] = {iso: members for iso, members in base_schedule.items()}
        errors, _ = validate_schedule(tmp_year_sched)
        if not errors:
            return pattern, base_schedule
        last_errors = errors

    example = last_errors[0][1] if last_errors else "no details"
    raise RuntimeError(f"Unable to generate an error-free schedule after {max_attempts} attempts. Example error: {example}")


def validate_schedule(year_sched: Dict[str, Dict[str, str]]) -> tuple[List[tuple[date | None, str]], List[tuple[date | None, str]]]:
    errors: List[tuple[date | None, str]] = []
    warnings: List[tuple[date | None, str]] = []

    if not year_sched:
        return errors, warnings

    all_dates = sorted(d for d in (date.fromisoformat(k) for k in year_sched.keys()) if is_workday(d))
    if not all_dates:
        return errors, warnings

    # Daily hard rule: 2-3 people in office
    for d in all_dates:
        assign = year_sched.get(d.isoformat(), {})
        wfo = [m for m, v in assign.items() if v == "WFO"]
        if len(wfo) < 2 or len(wfo) > 3:
            errors.append((d, f"{d:%a %d %b %Y}: {len(wfo)} people in office (expected 2-3)."))

        mgmt_present = [m for m in wfo if ROLE_BY_MEMBER.get(m) == "mgmt-support"]
        if not mgmt_present:
            errors.append((d, f"{d:%a %d %b %Y}: no mgmt-support member in office."))

    # 3+ consecutive WFO days warning
    for member in TEAM_MEMBERS:
        streak = 0
        for d in all_dates:
            status = year_sched.get(d.isoformat(), {}).get(member, "WFH")
            if status == "WFO":
                streak += 1
                if streak >= 3:
                    warnings.append((d, f"{member}: 3+ consecutive WFO days ending on {d:%a %d %b %Y}."))
            else:
                streak = 0

    sundays = iter_sundays_in_year()

    for ws in sundays:
        week1_days = [d for d in all_dates if ws <= d <= ws + timedelta(days=4)]
        week2_start = ws + timedelta(days=7)
        week2_days = [d for d in all_dates if week2_start <= d <= week2_start + timedelta(days=4)]
        window_days = [d for d in all_dates if ws <= d < ws + timedelta(days=14)]
        if len(window_days) < 10:
            continue

        for week_idx, wdays in enumerate([week1_days, week2_days], start=1):
            if not wdays:
                continue
            for member in TEAM_MEMBERS:
                wfo_count = sum(1 for d in wdays if year_sched.get(d.isoformat(), {}).get(member) == "WFO")
                if wfo_count < 2 or wfo_count > 3:
                    errors.append((ws, f"Week {week_idx} starting {ws:%Y-%m-%d}: {member} has {wfo_count} WFO days (expected 2-3)."))

        for member in TEAM_MEMBERS:
            total_wfo = sum(1 for d in window_days if year_sched.get(d.isoformat(), {}).get(member) == "WFO")
            if total_wfo != 5:
                errors.append((ws, f"2-week window starting {ws:%Y-%m-%d}: {member} has {total_wfo} WFO days (expected exactly 5)."))

        member_pairs = list(combinations(TEAM_MEMBERS, 2))
        for a, b in member_pairs:
            met = False
            for d in window_days:
                assign = year_sched.get(d.isoformat(), {})
                if assign.get(a) == "WFO" and assign.get(b) == "WFO":
                    met = True
                    break
            if not met:
                warnings.append((ws, f"2-week window starting {ws:%Y-%m-%d}: {a} and {b} never share an office day."))

    # Day-of-week preferences
    for d in all_dates:
        iso = d.isoformat()
        weekday_idx = d.weekday()
        weekday_full = d.strftime("%A")
        assignments = year_sched.get(iso, {})

        for member, preferred_days in PREFERS_WFO_DAYS.items():
            if member not in TEAM_MEMBERS:
                continue
            if weekday_idx in preferred_days:
                status = assignments.get(member, "WFH")
                if status != "WFO":
                    warnings.append((d, f"{d:%a %d %b %Y}: {member} is {status} but prefers WFO on {weekday_full}."))

        for member, disliked_days in DISLIKES_WFO_DAYS.items():
            if member not in TEAM_MEMBERS:
                continue
            if weekday_idx in disliked_days:
                status = assignments.get(member, "WFH")
                if status == "WFO":
                    warnings.append((d, f"{d:%a %d %b %Y}: {member} is WFO but dislikes office on {weekday_full}."))

    # Deduplicate
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


def compute_pair_meet_counts(year_sched: Dict[str, Dict[str, str]]) -> pd.DataFrame:
    from collections import defaultdict
    pair_counts: Dict[tuple[str, str], int] = defaultdict(int)

    for _iso, assignments in year_sched.items():
        wfo_members = [m for m, v in assignments.items() if v == "WFO"]
        wfo_members = [m for m in wfo_members if m in TEAM_MEMBERS]
        wfo_members.sort()
        n = len(wfo_members)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = wfo_members[i], wfo_members[j]
                pair_counts[(a, b)] += 1

    matrix = pd.DataFrame(0, index=TEAM_MEMBERS, columns=TEAM_MEMBERS, dtype=int)
    for (a, b), count in pair_counts.items():
        matrix.loc[a, b] = count
        matrix.loc[b, a] = count

    for m in TEAM_MEMBERS:
        matrix.loc[m, m] = 0

    return matrix


def compute_member_totals(year_sched: Dict[str, Dict[str, str]]) -> pd.DataFrame:
    counts: Dict[str, int] = {m: 0 for m in TEAM_MEMBERS}
    for assignments in year_sched.values():
        for m in TEAM_MEMBERS:
            if assignments.get(m) == "WFO":
                counts[m] += 1

    records = [
        {"Member": m, "Office Days": counts[m], "Approx. Weeks": round(counts[m] / 5.0, 1)}
        for m in TEAM_MEMBERS
    ]
    return pd.DataFrame(records).sort_values("Member").reset_index(drop=True)


# =============================================================================
# UI COMPONENTS
# =============================================================================


def render_hero(year_sched: Dict[str, Dict[str, str]]):
    """Render the hero section with quick stats."""
    today = date.today()
    today_iso = today.isoformat()

    # Calculate stats
    total_workdays = len([d for d in year_sched.keys() if is_workday(date.fromisoformat(d))])

    # Count office days today
    today_wfo = 0
    if today_iso in year_sched:
        today_wfo = sum(1 for v in year_sched[today_iso].values() if v == "WFO")

    st.markdown(
        f"""
        <div class="wfh-hero">
            <h1>🏠 WFH Schedule {YEAR}</h1>
            <p>Smart team scheduling with automatic rotation, preferences, and holiday management</p>
            <div class="quick-stats">
                <div class="quick-stat">
                    <div class="quick-stat-value">{len(TEAM_MEMBERS)}</div>
                    <div class="quick-stat-label">Team Members</div>
                </div>
                <div class="quick-stat">
                    <div class="quick-stat-value">{total_workdays}</div>
                    <div class="quick-stat-label">Working Days</div>
                </div>
                <div class="quick-stat">
                    <div class="quick-stat-value">{today_wfo}</div>
                    <div class="quick-stat-label">In Office Today</div>
                </div>
                <div class="quick-stat">
                    <div class="quick-stat-value">50%</div>
                    <div class="quick-stat-label">Target WFO</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_today_cards(
    year_sched: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]],
    public_holidays: Dict[str, str],
):
    """Render today and next working day cards."""
    today = date.today()
    today_iso = today.isoformat()

    def next_working_day(start: date) -> date | None:
        d = start + timedelta(days=1)
        while d.year <= YEAR:
            if is_workday(d) and d.isoformat() in year_sched:
                return d
            d += timedelta(days=1)
        return None

    def render_members_for_day(d: date) -> str:
        iso = d.isoformat()
        if iso not in year_sched:
            return '<span style="color: #9ca3af;">No data</span>'

        assign = year_sched[iso]
        day_holidays = set(holidays.get(iso, []))
        is_public = public_holidays.get(iso)

        if is_public:
            return f'<span class="member-chip holiday">🎉 {is_public}</span>'

        chips = []
        for member in TEAM_MEMBERS:
            avatar = MEMBER_AVATARS.get(member, "👤")
            if member in day_holidays:
                chips.append(f'<span class="member-chip holiday"><span class="member-avatar">{avatar}</span>{member}</span>')
            elif assign.get(member) == "WFO":
                chips.append(f'<span class="member-chip office"><span class="member-avatar">{avatar}</span>{member}</span>')
            else:
                chips.append(f'<span class="member-chip home"><span class="member-avatar">{avatar}</span>{member}</span>')

        return "".join(chips)

    today_label = today.strftime("%A, %B %d")
    today_content = render_members_for_day(today) if is_workday(today) else '<span style="color: #9ca3af;">Weekend</span>'

    nxt = next_working_day(today)
    if nxt:
        next_label = nxt.strftime("%A, %B %d")
        next_content = render_members_for_day(nxt)
    else:
        next_label = "N/A"
        next_content = '<span style="color: #9ca3af;">No upcoming day in {YEAR}</span>'

    st.markdown(
        f"""
        <div class="today-section">
            <div class="today-card current">
                <div class="today-card-header">
                    <span class="today-card-title">Today</span>
                    <span class="today-card-badge badge-today">Current</span>
                </div>
                <div class="today-card-date">{today_label}</div>
                <div class="today-card-members">{today_content}</div>
            </div>
            <div class="today-card next">
                <div class="today-card-header">
                    <span class="today-card-title">Next Working Day</span>
                    <span class="today-card-badge badge-upcoming">Upcoming</span>
                </div>
                <div class="today-card-date">{next_label}</div>
                <div class="today-card-members">{next_content}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_my_schedule(year_sched: Dict[str, Dict[str, str]], member: str, current_week_start: date):
    """Render personal schedule card for a member."""
    # Get current week data
    week_end = current_week_start + timedelta(days=4)

    week_office_days = []
    week_home_days = []

    for offset in range(5):
        d = current_week_start + timedelta(days=offset)
        iso = d.isoformat()
        if iso in year_sched:
            status = year_sched[iso].get(member, "WFH")
            day_name = d.strftime("%a")
            if status == "WFO":
                week_office_days.append(day_name)
            else:
                week_home_days.append(day_name)

    # Calculate yearly stats
    total_office = sum(1 for assigns in year_sched.values() if assigns.get(member) == "WFO")
    total_days = len(year_sched)
    wfo_percent = round(total_office / total_days * 100, 1) if total_days > 0 else 0

    avatar = MEMBER_AVATARS.get(member, "👤")
    role = ROLE_BY_MEMBER.get(member, "team member")

    office_chips = "".join([f'<span class="my-day-chip my-day-office">{d}</span>' for d in week_office_days])
    home_chips = "".join([f'<span class="my-day-chip my-day-home">{d}</span>' for d in week_home_days])

    st.markdown(
        f"""
        <div class="my-schedule-card">
            <div class="my-schedule-header">
                <span class="my-schedule-title">{avatar} {member}'s Schedule</span>
                <span style="color: #64748b; font-size: 0.85rem;">{role.title()}</span>
            </div>
            <div class="my-schedule-stats">
                <div class="my-stat">
                    <div class="my-stat-value">{len(week_office_days)}</div>
                    <div class="my-stat-label">Office This Week</div>
                </div>
                <div class="my-stat">
                    <div class="my-stat-value">{len(week_home_days)}</div>
                    <div class="my-stat-label">Home This Week</div>
                </div>
                <div class="my-stat">
                    <div class="my-stat-value">{total_office}</div>
                    <div class="my-stat-label">Office Days {YEAR}</div>
                </div>
                <div class="my-stat">
                    <div class="my-stat-value">{wfo_percent}%</div>
                    <div class="my-stat-label">WFO Rate</div>
                </div>
            </div>
            <div style="margin-top: 1rem;">
                <div style="font-size: 0.85rem; color: #166534; font-weight: 600; margin-bottom: 0.5rem;">Office Days:</div>
                <div class="my-schedule-days">{office_chips if office_chips else '<span style="color: #9ca3af;">None</span>'}</div>
            </div>
            <div style="margin-top: 0.75rem;">
                <div style="font-size: 0.85rem; color: #64748b; font-weight: 600; margin-bottom: 0.5rem;">Home Days:</div>
                <div class="my-schedule-days">{home_chips if home_chips else '<span style="color: #9ca3af;">None</span>'}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_week_table(
    title: str,
    days: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]] | None = None,
    public_holidays: Dict[str, str] | None = None,
    week_start: date | None = None,
):
    """Render a professional week schedule table."""
    if not days:
        st.info("No working days in this week.")
        return

    sorted_dates = sorted(days.keys())
    today = date.today()

    # Calculate skippable cells
    member_week_wfo: Dict[str, int] = {m: 0 for m in TEAM_MEMBERS}
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
            continue
        for member in TEAM_MEMBERS:
            if assignments.get(member) == "WFO" and member_week_wfo.get(member, 0) >= 3:
                skippable_cells.add((iso, member))

    # Build header
    header_cells = ["<th>Day</th>"]
    for member in TEAM_MEMBERS:
        avatar = MEMBER_AVATARS.get(member, "👤")
        header_cells.append(f"<th>{avatar} {member}</th>")

    # Build rows
    body_rows: List[str] = []
    for iso in sorted_dates:
        d = date.fromisoformat(iso)
        friendly = d.strftime("%a %d")
        is_today = d == today

        day_cell_class = "day-cell is-today" if is_today else "day-cell"
        today_badge = '<span class="today-indicator">Today</span>' if is_today else ""

        public_name = public_holidays.get(iso) if public_holidays else None
        public_tag = f'<span class="public-holiday-tag">{public_name}</span>' if public_name else ""

        row_cells = [f'<td class="{day_cell_class}">{friendly}{today_badge}{public_tag}</td>']

        assignments = days[iso]
        for member in TEAM_MEMBERS:
            is_public = public_holidays is not None and public_holidays.get(iso)
            is_member_holiday = holidays is not None and member in holidays.get(iso, [])
            status = assignments.get(member, "WFH")

            if is_public or is_member_holiday:
                css_class = "status-badge status-holiday"
                label = "Holiday"
            elif status == "WFO":
                is_skippable = (iso, member) in skippable_cells
                css_class = "status-badge status-office" + (" status-skippable" if is_skippable else "")
                label = "Office"
            else:
                css_class = "status-badge status-home"
                label = "Home"

            row_cells.append(f'<td><span class="{css_class}">{label}</span></td>')

        body_rows.append("<tr>" + "".join(row_cells) + "</tr>")

    # Week dates
    if sorted_dates:
        start_d = date.fromisoformat(sorted_dates[0])
        end_d = date.fromisoformat(sorted_dates[-1])
        date_range = f"{start_d.strftime('%b %d')} - {end_d.strftime('%b %d, %Y')}"
    else:
        date_range = ""

    st.markdown(
        f"""
        <div class="week-container">
            <div class="week-header">
                <span class="week-title">{title}</span>
                <span class="week-dates">{date_range}</span>
            </div>
            <table class="schedule-table">
                <thead><tr>{''.join(header_cells)}</tr></thead>
                <tbody>{''.join(body_rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_calendar_heatmap(year_sched: Dict[str, Dict[str, str]], member: str):
    """Render a calendar heatmap for a member's schedule."""
    all_dates = sorted([date.fromisoformat(k) for k in year_sched.keys() if is_workday(date.fromisoformat(k))])

    if not all_dates:
        st.info("No schedule data available.")
        return

    # Build heatmap data
    data = []
    for d in all_dates:
        iso = d.isoformat()
        status = year_sched.get(iso, {}).get(member, "WFH")
        value = 1 if status == "WFO" else 0
        data.append({
            "date": d,
            "week": d.isocalendar()[1],
            "weekday": d.weekday(),
            "status": status,
            "value": value,
            "month": d.month,
        })

    df = pd.DataFrame(data)

    # Create heatmap using Plotly
    fig = px.density_heatmap(
        df,
        x="week",
        y="weekday",
        z="value",
        color_continuous_scale=["#e2e8f0", "#10b981"],
        labels={"week": "Week of Year", "weekday": "Day", "value": "Office"},
    )

    fig.update_layout(
        title=f"{MEMBER_AVATARS.get(member, '👤')} {member}'s Office Days Heatmap",
        height=200,
        margin=dict(t=40, b=20, l=20, r=20),
        yaxis=dict(
            tickmode="array",
            tickvals=[0, 1, 2, 3, 6],
            ticktext=["Mon", "Tue", "Wed", "Thu", "Sun"],
        ),
        coloraxis_showscale=False,
    )

    st.plotly_chart(fig, use_container_width=True)


def render_team_statistics(year_sched: Dict[str, Dict[str, str]]):
    """Render team statistics with Plotly charts."""
    # Per-member office days
    member_totals = compute_member_totals(year_sched)

    col1, col2 = st.columns(2)

    with col1:
        # Bar chart of office days
        fig = px.bar(
            member_totals,
            x="Member",
            y="Office Days",
            title="Office Days per Team Member",
            color="Office Days",
            color_continuous_scale=["#bbf7d0", "#059669"],
        )
        fig.update_layout(
            height=300,
            margin=dict(t=40, b=40, l=40, r=20),
            showlegend=False,
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Pair meeting heatmap
        pair_matrix = compute_pair_meet_counts(year_sched)

        fig = px.imshow(
            pair_matrix.values,
            x=pair_matrix.columns.tolist(),
            y=pair_matrix.index.tolist(),
            title="Pairwise Office Meetings",
            color_continuous_scale=["#f0fdf4", "#059669"],
            text_auto=True,
        )
        fig.update_layout(
            height=300,
            margin=dict(t=40, b=40, l=40, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Role mix pie chart
    role_counts = {"Management": 0, "Engineering": 0}
    for member in TEAM_MEMBERS:
        role = ROLE_BY_MEMBER.get(member, "")
        office_days = member_totals[member_totals["Member"] == member]["Office Days"].values[0]
        if role == "mgmt-support":
            role_counts["Management"] += office_days
        else:
            role_counts["Engineering"] += office_days

    col1, col2 = st.columns(2)

    with col1:
        fig = px.pie(
            values=list(role_counts.values()),
            names=list(role_counts.keys()),
            title="Office Days by Role",
            hole=0.4,
            color_discrete_sequence=["#10b981", "#6366f1"],
        )
        fig.update_layout(
            height=280,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Daily office count distribution
        daily_counts = []
        for iso, assigns in year_sched.items():
            wfo_count = sum(1 for v in assigns.values() if v == "WFO")
            daily_counts.append({"count": wfo_count})

        df_counts = pd.DataFrame(daily_counts)
        count_dist = df_counts["count"].value_counts().sort_index()

        fig = px.bar(
            x=count_dist.index.tolist(),
            y=count_dist.values.tolist(),
            title="Distribution of Daily Office Attendance",
            labels={"x": "People in Office", "y": "Number of Days"},
            color=count_dist.values.tolist(),
            color_continuous_scale=["#bbf7d0", "#059669"],
        )
        fig.update_layout(
            height=280,
            margin=dict(t=40, b=40, l=40, r=20),
            showlegend=False,
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)


def render_validations(year_sched: Dict[str, Dict[str, str]]):
    """Render validation results."""
    errors, warnings = validate_schedule(year_sched)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-value" style="color: {'#ef4444' if errors else '#10b981'};">{len(errors)}</div>
                <div class="stat-label">Rule Violations</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-value" style="color: {'#f59e0b' if warnings else '#10b981'};">{len(warnings)}</div>
                <div class="stat-label">Preference Warnings</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("")

    if not errors and not warnings:
        st.success("No rule violations or preference warnings detected.")
        return

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Rule Violations")
        if errors:
            for dt, msg in errors[:10]:
                dt_str = dt.strftime("%a %d %b") if dt else "Undated"
                st.markdown(
                    f"""
                    <div class="validation-card error">
                        <div class="validation-date">{dt_str}</div>
                        <div class="validation-text">{msg}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            if len(errors) > 10:
                st.caption(f"... and {len(errors) - 10} more")
        else:
            st.success("No rule violations")

    with col2:
        st.markdown("#### Preference Warnings")
        if warnings:
            for dt, msg in warnings[:10]:
                dt_str = dt.strftime("%a %d %b") if dt else "Undated"
                st.markdown(
                    f"""
                    <div class="validation-card warning">
                        <div class="validation-date">{dt_str}</div>
                        <div class="validation-text">{msg}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            if len(warnings) > 10:
                st.caption(f"... and {len(warnings) - 10} more")
        else:
            st.info("No preference warnings")


def render_holidays_management(holidays: Dict[str, List[str]], public_holidays: Dict[str, str]):
    """Render holiday management interface."""
    tab1, tab2 = st.tabs(["Public Holidays", "Personal Time Off"])

    with tab1:
        st.markdown("### Public Holidays")

        if public_holidays:
            st.markdown('<div class="holiday-grid">', unsafe_allow_html=True)
            for iso, name in sorted(public_holidays.items()):
                try:
                    d = date.fromisoformat(iso)
                    st.markdown(
                        f"""
                        <div class="holiday-card public">
                            <div class="holiday-date">{d.strftime('%a, %b %d')}</div>
                            <div class="holiday-name">{name}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                except ValueError:
                    pass
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("No public holidays defined.")

        with st.expander("Add Public Holiday"):
            with st.form("add_public_holiday"):
                col1, col2 = st.columns(2)
                with col1:
                    ph_date = st.date_input("Date", value=date(YEAR, 1, 1))
                with col2:
                    ph_name = st.text_input("Holiday Name")

                if st.form_submit_button("Add Holiday", type="primary"):
                    if isinstance(ph_date, date) and ph_date.year == YEAR and ph_name.strip():
                        public_holidays[ph_date.isoformat()] = ph_name.strip()
                        save_public_holidays(public_holidays)
                        st.success("Holiday added!")
                        st.rerun()

    with tab2:
        st.markdown("### Personal Time Off")

        if holidays:
            records = []
            for iso, members in holidays.items():
                try:
                    d = date.fromisoformat(iso)
                    for m in members:
                        records.append({"date": d, "member": m, "iso": iso})
                except ValueError:
                    pass

            if records:
                st.markdown('<div class="holiday-grid">', unsafe_allow_html=True)
                for rec in sorted(records, key=lambda x: x["date"]):
                    avatar = MEMBER_AVATARS.get(rec["member"], "👤")
                    st.markdown(
                        f"""
                        <div class="holiday-card personal">
                            <div class="holiday-date">{rec['date'].strftime('%a, %b %d')}</div>
                            <div class="holiday-name">{avatar} {rec['member']}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("No personal time off defined.")

        with st.expander("Add Time Off"):
            with st.form("add_personal_holiday"):
                col1, col2 = st.columns(2)
                with col1:
                    member = st.selectbox("Team Member", TEAM_MEMBERS)
                with col2:
                    pto_date = st.date_input("Date", value=date.today())

                if st.form_submit_button("Add Time Off", type="primary"):
                    if isinstance(pto_date, date) and pto_date.year == YEAR:
                        iso = pto_date.isoformat()
                        current = set(holidays.get(iso, []))
                        current.add(member)
                        holidays[iso] = sorted(current)
                        save_holidays(holidays)
                        st.success("Time off added!")
                        st.rerun()


def render_edit_interface(
    base_schedule: Dict[str, Dict[str, str]],
    current_week_start: date,
    next_week_start: date | None,
):
    """Render the schedule editing interface."""
    sundays = iter_sundays_in_year()

    editable_sundays: List[date] = []
    if current_week_start in sundays:
        editable_sundays.append(current_week_start)
    if next_week_start and next_week_start in sundays:
        editable_sundays.append(next_week_start)

    if not editable_sundays:
        st.info("No editable weeks available.")
        return

    label_map = {}
    for ws in editable_sundays:
        if ws == current_week_start:
            label_map[f"Current Week ({ws.isoformat()})"] = ws
        else:
            label_map[f"Next Week ({ws.isoformat()})"] = ws

    selected_label = st.selectbox("Select Week to Edit", list(label_map.keys()))
    week_start = label_map[selected_label]

    week_days = load_week(week_start, base_schedule)
    if not week_days:
        st.write("No working days for this week.")
        return

    st.markdown("### Quick Adjustments")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown("**Mark as Working from Home**")

        member_quick = st.selectbox("Team Member", TEAM_MEMBERS, key="quick_wfh_member")

        member_wfo_days = [
            (iso, date.fromisoformat(iso))
            for iso, members in sorted(week_days.items())
            if members.get(member_quick) == "WFO"
        ]

        if member_wfo_days:
            labels = [f"{d:%a %d %b}" for _iso, d in member_wfo_days]
            day_idx = st.selectbox("Office Day", range(len(labels)), format_func=lambda i: labels[i])
            iso_quick, d_quick = member_wfo_days[day_idx]

            if st.button(f"Mark {member_quick} as WFH on {d_quick:%a %d}", type="primary"):
                new_week_days = {k: v.copy() for k, v in week_days.items()}
                new_week_days[iso_quick][member_quick] = "WFH"
                save_week(week_start, new_week_days)
                st.success("Updated!")
                st.rerun()
        else:
            st.caption("No office days this week.")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown("**Swap Office Day**")

        member_out = st.selectbox("Person Stepping Out", TEAM_MEMBERS, key="swap_out")

        out_days = [
            (iso, date.fromisoformat(iso))
            for iso, members in sorted(week_days.items())
            if members.get(member_out) == "WFO"
        ]

        if out_days:
            labels_out = [f"{d:%a %d %b}" for _iso, d in out_days]
            idx_out = st.selectbox("Their Office Day", range(len(labels_out)), format_func=lambda i: labels_out[i])
            iso_swap, d_swap = out_days[idx_out]

            day_members_full = week_days.get(iso_swap, {})
            swap_candidates = [
                m for m in TEAM_MEMBERS
                if m != member_out and day_members_full.get(m, "WFH") == "WFH"
            ]

            if swap_candidates:
                member_in = st.selectbox("Swap With", swap_candidates)

                if st.button(f"Swap {member_out} ↔ {member_in}", type="primary"):
                    new_week_days = {k: v.copy() for k, v in week_days.items()}
                    new_week_days[iso_swap][member_out] = "WFH"
                    new_week_days[iso_swap][member_in] = "WFO"
                    save_week(week_start, new_week_days)
                    st.success("Swap applied!")
                    st.rerun()
            else:
                st.caption("No one available to swap.")
        else:
            st.caption("No office days to swap.")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### Full Week Editor")

    records = []
    for iso, members in sorted(week_days.items()):
        d = date.fromisoformat(iso)
        row = {"Day": d.strftime("%a %d %b"), "Date": iso}
        for member in TEAM_MEMBERS:
            row[member] = members.get(member, "WFH")
        records.append(row)

    df = pd.DataFrame(records)

    col_config = {"Day": st.column_config.TextColumn(disabled=True)}
    for member in TEAM_MEMBERS:
        col_config[member] = st.column_config.SelectboxColumn(options=["WFH", "WFO"], default="WFH")

    edited = st.data_editor(df, column_config=col_config, hide_index=True, num_rows="fixed")

    if st.button("Save Changes", type="primary"):
        new_week_days: Dict[str, Dict[str, str]] = {}
        for _, row in edited.iterrows():
            iso = row["Date"]
            members: Dict[str, str] = {}
            for member in TEAM_MEMBERS:
                val = str(row.get(member, "WFH")).upper()
                members[member] = "WFO" if val == "WFO" else "WFH"
            new_week_days[iso] = members
        save_week(week_start, new_week_days)
        st.success("Week schedule saved!")
        st.rerun()


# =============================================================================
# MAIN
# =============================================================================


def main():
    # Generate schedule
    pattern, base_schedule = build_error_free_base_schedule()
    ensure_week_files(base_schedule)
    year_sched = load_full_year_from_weeks(base_schedule)
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

    # Render hero
    render_hero(year_sched)

    # Sidebar
    with st.sidebar:
        st.markdown("### Quick View")
        selected_member = st.selectbox(
            "View Schedule For",
            ["Everyone"] + TEAM_MEMBERS,
            index=0,
        )

        st.divider()

        st.markdown("### Navigation")
        nav_options = ["Overview", "My Schedule", "Statistics", "Editing", "Holidays", "Validations"]
        selected_nav = st.radio("Go to", nav_options, label_visibility="collapsed")

    # Main content based on navigation
    if selected_nav == "Overview":
        # Today cards
        render_today_cards(year_sched, holidays, public_holidays)

        # Week tables
        col1, col2 = st.columns(2)

        with col1:
            current_week_days = load_week(current_week_start, base_schedule)
            render_week_table("Current Week", current_week_days, holidays, public_holidays, current_week_start)

        with col2:
            if next_week_start.year == YEAR:
                next_week_days = load_week(next_week_start, base_schedule)
                render_week_table("Next Week", next_week_days, holidays, public_holidays, next_week_start)

    elif selected_nav == "My Schedule":
        st.markdown('<div class="section-title">👤 Personal Schedule View</div>', unsafe_allow_html=True)

        member_to_view = st.selectbox("Select Team Member", TEAM_MEMBERS, key="my_schedule_member")

        render_my_schedule(year_sched, member_to_view, current_week_start)

        st.markdown("### Calendar Heatmap")
        render_calendar_heatmap(year_sched, member_to_view)

    elif selected_nav == "Statistics":
        st.markdown('<div class="section-title">📊 Team Statistics</div>', unsafe_allow_html=True)
        render_team_statistics(year_sched)

    elif selected_nav == "Editing":
        st.markdown('<div class="section-title">✏️ Edit Schedule</div>', unsafe_allow_html=True)
        render_edit_interface(base_schedule, current_week_start, next_week_start if next_week_start.year == YEAR else None)

    elif selected_nav == "Holidays":
        st.markdown('<div class="section-title">🎉 Holiday Management</div>', unsafe_allow_html=True)
        render_holidays_management(holidays, public_holidays)

    elif selected_nav == "Validations":
        st.markdown('<div class="section-title">✅ Schedule Validations</div>', unsafe_allow_html=True)
        render_validations(year_sched)

    # Footer
    st.divider()
    errors, warnings = validate_schedule(year_sched)
    status_icon = "✅" if not errors else "⚠️"
    st.caption(
        f"{status_icon} WFH Schedule {YEAR} | {len(TEAM_MEMBERS)} team members | "
        f"{len(errors)} violations | {len(warnings)} warnings | "
        f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
    )


if __name__ == "__main__":
    main()
else:
    main()
