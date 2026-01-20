from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Sequence, Set
import random

import pandas as pd
import streamlit as st


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

# Personal, soft attendance preferences (validated as warnings only).
# Weekday indices follow datetime.weekday(): Monday=0, ..., Sunday=6.
PREFERS_WFO_DAYS: Dict[str, Set[int]] = {
    # Existing preference: Karam likes to be in-office on Mon & Tue.
    "Karam": {0, 1},
}

DISLIKES_WFO_DAYS: Dict[str, Set[int]] = {
    # New constraints: dislike being in-office on specific weekdays.
    "Hesham": {0, 3},
    "Abdelkhalek": {6, 3},  # Sunday (6), Thursday (3)
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


def generate_two_week_pattern() -> List[Set[str]]:
    """Generate a 10-workday (Sun-Thu x2) pattern satisfying hard rules.

        Hard rules encoded:
        - Exactly 5 WFO days per member over 10 working days.
        - Each day has 2 or 3 people WFO.
        - No member is WFO 3 days in a row.
        - Each pair of members meets at least once in the office.
        - Karam WFO on every Monday and Tuesday within the 2-week window.
        - No member works more than one Sunday and one Thursday in-office
            within the 2-week window (avoids recurring Sun/Thu duties for
            everyone).

        Preferences (soft):
        - Avoiding additional consecutive WFO days is not enforced (it
            often conflicts with Karam's Mon/Tue preference), but tends to be
            reasonable in typical solutions.
    """

    n = len(TEAM_MEMBERS)
    people = list(range(n))
    idx = {name: i for i, name in enumerate(TEAM_MEMBERS)}
    karam = idx["Karam"]

    # 10 working days: Sun,Mon,Tue,Wed,Thu,Sun,Mon,Tue,Wed,Thu
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu"] * 2

    # All subsets with 2 or 3 members
    all_subsets: List[Sequence[int]] = []
    for r in (2, 3):
        all_subsets.extend(list(combinations(people, r)))
    # Add randomness to daily options to avoid always picking the same pattern
    random.shuffle(all_subsets)

    # Pre-filtered options per day for hard constraints about Karam on Mon/Tue
    options_per_day: List[List[Sequence[int]]] = []
    for day in range(10):
        wd = weekdays[day]
        day_opts: List[Sequence[int]] = []
        for subset in all_subsets:
            s = set(subset)
            # Karam should be WFO on Mondays and Tuesdays (hardened preference)
            if wd in {"Mon", "Tue"} and karam not in s:
                continue
            day_opts.append(subset)
        options_per_day.append(day_opts)

    # Pairs for "meet at least once in two weeks" rule
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]

    # Indices of mgmt-support members for role-based daily coverage
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
            # Verify hard constraints at the end
            if any(c != 5 for c in counts):
                return
            if not all(pair_seen[i][j] for i, j in pairs):
                return
            best_schedule = [set(s) for s in schedule]
            found = True
            return

        wd = weekdays[day]
        # Copy and shuffle candidates for some randomness while respecting rules
        candidates = list(options_per_day[day])
        random.shuffle(candidates)

        remaining_days = 10 - day - 1

        for subset in candidates:
            s = set(subset)

            # Interim per-member WFO caps
            new_counts = counts[:]
            feasible = True
            for p in s:
                new_counts[p] += 1
                if new_counts[p] > 5:
                    feasible = False
                    break
            if not feasible:
                continue

            # At least one mgmt-support present in the office each day
            if not (mgmt_indices & s):
                continue

            # Can each member still potentially reach 5?
            for p in range(n):
                max_possible = new_counts[p] + remaining_days
                if max_possible < 5:
                    feasible = False
                    break
            if not feasible:
                continue

            # No 3 consecutive WFO days
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

            # Limit recurring Sundays/Thursdays: at most one Sunday and
            # one Thursday WFO per member per 2-week window.
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

            # Update pair coverage
            new_pair_seen = [row[:] for row in pair_seen]
            for i in s:
                for j in s:
                    if i < j:
                        new_pair_seen[i][j] = True

            schedule.append(s)
            backtrack(
                day + 1,
                schedule,
                new_counts,
                new_streaks,
                new_pair_seen,
                new_sun,
                new_thu,
            )
            schedule.pop()

    initial_counts = [0] * n
    initial_streaks = [0] * n
    initial_pairs = [[False] * n for _ in range(n)]
    initial_sun = [0] * n
    initial_thu = [0] * n

    backtrack(0, [], initial_counts, initial_streaks, initial_pairs, initial_sun, initial_thu)

    if not found or best_schedule is None:
        raise RuntimeError("Unable to find a valid 2-week WFH/WFO pattern with given rules.")

    # Convert to member-name sets in weekday order
    name_pattern: List[Set[str]] = []
    for s in best_schedule:
        name_pattern.append({TEAM_MEMBERS[i] for i in s})
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


def build_base_schedule(pattern: List[Set[str]]) -> Dict[str, Dict[str, str]]:
    """Build the default schedule for all workdays in YEAR using the pattern.

    Returns a mapping: iso-date -> {member: "WFO" or "WFH"}.
    """

    anchor = get_anchor_sunday()
    end = date(YEAR, 12, 31)

    pattern_len = len(pattern)  # should be 10
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
    # Find first Sunday in YEAR
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
        # Build this week's schedule from base
        week_days: Dict[str, Dict[str, str]] = {}
        for offset in range(5):  # Sun-Thu
            d = ws + timedelta(days=offset)
            iso = d.isoformat()
            if iso in base_schedule:
                week_days[iso] = base_schedule[iso]
        payload = {"week_start": ws.isoformat(), "days": week_days}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_week(week_start: date, base_schedule: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    path = week_file_path(week_start)
    if not path.exists():
        # Fall back to generating from base schedule (and caching it)
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
        days: Dict[str, Dict[str, str]] = payload.get("days", {})
        return days
    except Exception:  # noqa: BLE001
        # If file is corrupted, regenerate from base
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
    """Load per-date holidays as {iso_date: [member, ...]} mapping."""

    if not HOLIDAYS_FILE.exists():
        return {}

    try:
        raw = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}

    holidays: Dict[str, List[str]] = {}
    if isinstance(raw, dict):
        for iso, members in raw.items():
            if not isinstance(members, list):
                continue
            # Keep only known team members and normalise duplicates
            unique = sorted({m for m in members if isinstance(m, str)})
            if unique:
                holidays[iso] = unique
    return holidays


def save_holidays(holidays: Dict[str, List[str]]) -> None:
    """Persist holidays mapping to disk."""

    HOLIDAYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOLIDAYS_FILE.write_text(json.dumps(holidays, indent=2), encoding="utf-8")


def load_public_holidays() -> Dict[str, str]:
    """Load public holidays for YEAR as {iso_date: name}.

    If no custom file exists or it cannot be parsed, fall back to a
    built-in list for Egypt in YEAR.
    """

    if not PUBLIC_HOLIDAYS_FILE.exists():
        return DEFAULT_PUBLIC_HOLIDAYS.copy()

    try:
        raw = json.loads(PUBLIC_HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
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
    """Persist public holidays mapping to disk."""

    PUBLIC_HOLIDAYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_HOLIDAYS_FILE.write_text(
        json.dumps(public_holidays, indent=2),
        encoding="utf-8",
    )


def load_full_year_from_weeks(base_schedule: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Merge all persisted week files into a year schedule map.

    Weeks without files will be generated from the base schedule.
    """

    year_sched: Dict[str, Dict[str, str]] = {}
    for ws in iter_sundays_in_year():
        days = load_week(ws, base_schedule)
        for iso, members in days.items():
            if iso.startswith(str(YEAR)):
                year_sched[iso] = members
    return year_sched


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
            day_label = (
                f"{day_label} "
                f"<span class='public-holiday-tag'>{public_name}</span>"
            )

        row_cells = [f"<td class='day-cell{day_extra_cls}'>{day_label}</td>"]
        assignments = days[iso]
        for member in TEAM_MEMBERS:
            is_public = public_holidays is not None and public_holidays.get(iso)
            is_member_holiday = holidays is not None and member in holidays.get(iso, [])
            status = assignments.get(member, "WFH")
            if is_public or is_member_holiday:
                css_class = "holiday-cell"
                label = "Public holiday" if is_public else "Holiday"
            else:
                if status == "WFO":
                    is_skippable = (iso, member) in skippable_cells
                    css_class = "wfo-cell wfo-skippable-cell" if is_skippable else "wfo-cell"
                else:
                    css_class = "wfh-cell"
                label = "Office" if status == "WFO" else "Home"
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


def render_grid_for_dates(
    year_sched: Dict[str, Dict[str, str]],
    dates: List[date],
    title: str,
    extra_class: str = "",
    holidays: Dict[str, List[str]] | None = None,
    public_holidays: Dict[str, str] | None = None,
) -> None:
    if not year_sched or not dates:
        return

    all_dates = sorted(d for d in dates if is_workday(d))
    if not all_dates:
        return

    today = date.today()

    # Build header (dates) and rows (members)
    header_cells = ["<th class='header-cell member-col'>Member</th>"]
    for d in all_dates:
        is_today = d == today
        header_class = "grid-day-header today-header" if is_today else "grid-day-header"
        header_cells.append(
            f"<th class='{header_class}'>{d.strftime('%d %b')}</th>"
        )

    body_rows: List[str] = []
    for member in TEAM_MEMBERS:
        row_cells = [f"<td class='member-label'>{member}</td>"]
        for d in all_dates:
            iso = d.isoformat()
            day_assign = year_sched.get(iso, {})
            status = day_assign.get(member, "WFH")
            is_public = public_holidays is not None and public_holidays.get(iso)
            is_member_holiday = holidays is not None and member in holidays.get(iso, [])
            if is_public or is_member_holiday:
                base_class = "grid-holiday"
            else:
                base_class = "grid-wfo" if status == "WFO" else "grid-wfh"
            extra = " grid-today" if d == today else ""
            row_cells.append(f"<td class='{base_class}{extra}'></td>")
        body_rows.append("<tr>" + "".join(row_cells) + "</tr>")

    wrapper_class = f"year-grid-wrapper {extra_class}".strip()
    grid_html = f"""
    <div class='{wrapper_class}'>
        <h3 class='section-title'>{title}</h3>
        <div class='year-grid-scroll'>
            <table class='year-grid-table'>
                <thead>
                    <tr>{''.join(header_cells)}</tr>
                </thead>
                <tbody>
                    {''.join(body_rows)}
                </tbody>
            </table>
        </div>
    </div>
    """
    st.markdown(grid_html, unsafe_allow_html=True)


def render_year_grid(
    year_sched: Dict[str, Dict[str, str]],
    holidays: Dict[str, List[str]] | None = None,
    public_holidays: Dict[str, str] | None = None,
) -> None:
    if not year_sched:
        return

    all_dates = sorted(
        d for d in (date.fromisoformat(k) for k in year_sched.keys()) if is_workday(d)
    )
    if not all_dates:
        return

    render_grid_for_dates(
        year_sched,
        all_dates,
        "Rest of 2026 overview",
        holidays=holidays,
        public_holidays=public_holidays,
    )


def compute_pair_meet_counts(
    year_sched: Dict[str, Dict[str, str]]
) -> pd.DataFrame:
    """Return a symmetric matrix of how often members meet in-office.

    A "meeting" is counted for each working day where both members are
    scheduled WFO.
    """

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
    """Aggregate total office days per member across the year."""

    counts: Dict[str, int] = {m: 0 for m in TEAM_MEMBERS}
    for assignments in year_sched.values():
        for m in TEAM_MEMBERS:
            if assignments.get(m) == "WFO":
                counts[m] += 1

    records = [
        {
            "Member": m,
            "Office days": counts[m],
            "Approx. weeks in office": round(counts[m] / 5.0, 1),
        }
        for m in TEAM_MEMBERS
    ]
    df = pd.DataFrame(records).sort_values("Member").reset_index(drop=True)
    return df


def compute_role_mix(year_sched: Dict[str, Dict[str, str]]) -> Dict[str, int]:
    """Summarise how often mgmt-support and engineering are together in office."""

    stats = {
        "mgmt_only": 0,
        "engineering_only": 0,
        "mixed": 0,
        "no_office": 0,
    }

    for iso, assignments in year_sched.items():
        try:
            _ = date.fromisoformat(iso)
        except ValueError:
            continue

        wfo_members = [m for m, v in assignments.items() if v == "WFO"]
        if not wfo_members:
            stats["no_office"] += 1
            continue

        mgmt_present = any(ROLE_BY_MEMBER.get(m) == "mgmt-support" for m in wfo_members)
        eng_present = any(ROLE_BY_MEMBER.get(m) == "engineering" for m in wfo_members)

        if mgmt_present and eng_present:
            stats["mixed"] += 1
        elif mgmt_present:
            stats["mgmt_only"] += 1
        elif eng_present:
            stats["engineering_only"] += 1
        else:
            stats["no_office"] += 1

    return stats


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

    # --- Daily hard rule: 2-3 people in the office ---
    for d in all_dates:
        assign = year_sched.get(d.isoformat(), {})
        wfo = [m for m, v in assign.items() if v == "WFO"]
        if len(wfo) < 2 or len(wfo) > 3:
            errors.append(
                (d, f"{d:%a %d %b %Y}: {len(wfo)} people in office (expected 2–3).")
            )

        # Role-based hard rule: at least one mgmt-support member must be
        # present in the office on every working day.
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

    # --- Global 3+ consecutive WFO days (soft warning) ---
    for member in TEAM_MEMBERS:
        streak = 0
        for d in all_dates:
            status = year_sched.get(d.isoformat(), {}).get(member, "WFH")
            if status == "WFO":
                streak += 1
                if streak >= 3:
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

        # Per-member weekly 50% rule (2–3 WFO per Sun–Thu week) – hard
        for week_idx, wdays in enumerate([week1_days, week2_days], start=1):
            if not wdays:
                continue
            for member in TEAM_MEMBERS:
                wfo_count = sum(
                    1
                    for d in wdays
                    if year_sched.get(d.isoformat(), {}).get(member) == "WFO"
                )
                if wfo_count < 2 or wfo_count > 3:
                    errors.append(
                        (
                            ws,
                            f"Week {week_idx} starting {ws:%Y-%m-%d}: {member} has "
                            f"{wfo_count} WFO days (expected 2–3).",
                        )
                    )

        # Per-member 2-week total exactly 5 WFO – hard
        for member in TEAM_MEMBERS:
            total_wfo = sum(
                1
                for d in window_days
                if year_sched.get(d.isoformat(), {}).get(member) == "WFO"
            )
            if total_wfo != 5:
                errors.append(
                    (
                        ws,
                        f"2-week window starting {ws:%Y-%m-%d}: {member} has "
                        f"{total_wfo} WFO days (expected exactly 5).",
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

    for _ in range(max_attempts):
        pattern = generate_two_week_pattern()
        base_schedule = build_base_schedule(pattern)

        # Validate the pure rule-based schedule (without manual overrides
        # from persisted week files) to ensure the generator and validator
        # agree on hard constraints.
        tmp_year_sched: Dict[str, Dict[str, str]] = {
            iso: members for iso, members in base_schedule.items()
        }
        errors, _ = validate_schedule(tmp_year_sched)
        if not errors:
            return pattern, base_schedule

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
        if today_public_name:
            today_body = (
                "<span class='office-empty'>Public holiday: "
                f"{today_public_name}</span>"
            )
        else:
            today_assign = year_sched[today_iso]
            today_wfo = [m for m, v in today_assign.items() if v == "WFO" and m not in todays_holidays]
            today_holiday_members = [m for m in todays_holidays if m in TEAM_MEMBERS]
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
                today_body = today_html + extra
            else:
                today_body = "<span class='office-empty'>No one scheduled in office.</span>"
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
            st.success("Week schedule saved. Validations and grids updated.")
            st.rerun()

        st.markdown("---")
        st.markdown("**Propagate this 2-week pattern to the rest of 2026**")

        if st.button(
            "Apply current + next week pattern to all future weeks",
            type="secondary",
            key="propagate_pattern_2026",
        ):
            curr_week_days = load_week(current_ws, base_schedule)
            if not curr_week_days:
                st.warning("Current week has no working days; nothing to propagate.")
                return

            def build_pattern(week_start: date, week_days: Dict[str, Dict[str, str]]
                              ) -> Dict[int, Dict[str, str]]:
                pattern: Dict[int, Dict[str, str]] = {}
                for offset in range(5):
                    d = week_start + timedelta(days=offset)
                    iso = d.isoformat()
                    if iso in week_days:
                        pattern[offset] = week_days[iso]
                return pattern

            curr_pattern = build_pattern(current_ws, curr_week_days)

            next_pattern: Dict[int, Dict[str, str]] | None = None
            if next_ws is not None:
                nxt_week_days = load_week(next_ws, base_schedule)
                if nxt_week_days:
                    next_pattern = build_pattern(next_ws, nxt_week_days)

            for ws in iter_sundays_in_year():
                if ws <= current_ws:
                    continue

                offset_weeks = (ws - current_ws).days // 7
                use_curr = offset_weeks % 2 == 0

                if use_curr or not next_pattern:
                    pattern = curr_pattern
                else:
                    pattern = next_pattern

                new_week_days: Dict[str, Dict[str, str]] = {}
                for offset, members in pattern.items():
                    d = ws + timedelta(days=offset)
                    iso = d.isoformat()
                    new_week_days[iso] = members

                if new_week_days:
                    save_week(ws, new_week_days)

            st.success(
                "Current and next week patterns have been propagated to all future weeks in 2026.",
            )
            st.rerun()

def render_holidays_tab(
    holidays: Dict[str, List[str]],
    public_holidays: Dict[str, str],
) -> Dict[str, List[str]]:
    st.markdown("<h3 class='section-title'>Holidays</h3>", unsafe_allow_html=True)
    st.caption(
        "Define personal holidays and review public holidays; both overlay "
        "the base schedule in all grids and week views."
    )

    tabs = st.tabs(["Public holidays", "Personal holidays"])

    # --- Public holidays tab ---
    with tabs[0]:
        st.markdown(
            "<p class='info-line'>Egypt 2026 public holidays used to "
            "decorate week views and grids.</p>",
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
        .public-holiday-tag {
            display: inline-block;
            margin-left: 0.35rem;
            padding: 0.06rem 0.45rem;
            border-radius: 999px;
            background: #fee2e2;
            color: #b91c1c;
            font-size: 0.7rem;
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
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="WFH/WFO Schedule 2026",
        layout="wide",
        page_icon="📅",
    )

    inject_css()

    st.markdown("<h1 class='section-title'>WFH vs WFO Schedule - 2026</h1>", unsafe_allow_html=True)
    st.caption(
        "Auto-generated two-week rotation respecting attendance rules and preferences, "
        "with per-week overrides and a full-year visualization."
    )

    # Core schedule generation (pattern + base year schedule).
    # Keep regenerating until the base schedule has no hard-rule
    # validation errors (warnings are allowed).
    pattern, base_schedule = build_error_free_base_schedule()
    ensure_week_files(base_schedule)
    year_sched = load_full_year_from_weeks(base_schedule)
    holidays = load_holidays()
    public_holidays = load_public_holidays()

    # Current date context
    today = date.today()
    if today.year == YEAR:
        current_week_start = get_week_start_for_date(today)
        current_month = today.month
    else:
        sundays = iter_sundays_in_year()
        current_week_start = sundays[0] if sundays else date(YEAR, 1, 1)
        current_month = 1

    next_week_start = current_week_start + timedelta(days=7)

    # Pre-compute validations so both the tab label and the
    # week-level warnings share the same data.
    val_errors, val_warnings = validate_schedule(year_sched)
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
        current_week_days = load_week(current_week_start, base_schedule)
        render_week_table("Current week", current_week_days, holidays, public_holidays, highlight_today=True)
        render_week_warnings(current_week_start, val_errors, val_warnings, "Current week")
    with col2:
        if next_week_start.year == YEAR:
            next_week_days = load_week(next_week_start, base_schedule)
            render_week_table("Next week", next_week_days, holidays, public_holidays)
            render_week_warnings(next_week_start, val_errors, val_warnings, "Next week")
        else:
            st.markdown("<p>No next week within 2026.</p>", unsafe_allow_html=True)

    # Who's in the office today / next working day
    render_today_and_next(year_sched, holidays, public_holidays)

    tabs = st.tabs(["Grids", "Editing", "Statistics", val_tab_label, "Holidays"])

    # --- Grids tab ---
    with tabs[0]:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("<h3 class='section-title'>Grids overview</h3>", unsafe_allow_html=True)
        st.caption(
            "Colored squares show WFH (blue), WFO (green), and holidays (peach) "
            "for each team member across the selected month."
        )

        # Pre-compute all workdays in the year
        all_dates = sorted(
            d
            for d in (date.fromisoformat(k) for k in year_sched.keys())
            if is_workday(d)
        )

        months_with_days = sorted({d.month for d in all_dates})
        if current_month in months_with_days:
            default_month = current_month
        else:
            default_month = months_with_days[0] if months_with_days else 1

        month_labels = {m: date(YEAR, m, 1).strftime("%B") for m in months_with_days}
        selected_month = st.selectbox(
            "Select month",
            months_with_days,
            index=months_with_days.index(default_month) if months_with_days else 0,
            format_func=lambda m: month_labels.get(m, str(m)),
        )

        # Per-week grids within the selected month, arranged in Streamlit columns
        sundays = iter_sundays_in_year()
        week_entries = []
        for ws in sundays:
            week_dates = [
                ws + timedelta(days=offset)
                for offset in range(5)
                if is_workday(ws + timedelta(days=offset))
            ]
            # Only keep weeks that intersect the selected month
            if any(d.month == selected_month for d in week_dates):
                week_entries.append((ws, week_dates))

        if week_entries:
            # Show two weekly grids per row where possible
            for i in range(0, len(week_entries), 2):
                row_entries = week_entries[i : i + 2]
                cols = st.columns(len(row_entries))
                for col, (ws, week_dates) in zip(cols, row_entries):
                    with col:
                        render_grid_for_dates(
                            year_sched,
                            week_dates,
                            f"Week of {ws.strftime('%d %b %Y')}",
                            extra_class="week-grid",
                            holidays=holidays,
                            public_holidays=public_holidays,
                        )
        else:
            st.info("No working days found for the selected month.")

    # --- Editing tab ---
    with tabs[1]:
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

        # Optional: allow regenerating all weeks from the current base schedule
        with st.expander("Advanced: regenerate all 2026 weeks", expanded=False):
            st.caption(
                "Rebuild every persisted week file from the current two-week "
                "rotation. This will discard all manual edits."
            )
            if st.button("Rebuild all weeks from rules", type="secondary"):
                # Build a fresh, validated schedule and overwrite all
                # persisted weeks from these rules.
                _, refreshed_base = build_error_free_base_schedule()
                for ws in iter_sundays_in_year():
                    week_days: Dict[str, Dict[str, str]] = {}
                    for offset in range(5):
                        d = ws + timedelta(days=offset)
                        iso = d.isoformat()
                        if iso in refreshed_base:
                            week_days[iso] = refreshed_base[iso]
                    save_week(ws, week_days)
                st.success("All week files regenerated from the updated rules.")
                st.rerun()

    # --- Statistics tab ---
    with tabs[2]:
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("<h3 class='section-title'>Statistics</h3>", unsafe_allow_html=True)
        st.caption(
            "Pairwise office meetings, individual office loads, and role-mix "
            "insights based on the current 2026 schedule.",
        )

        pair_matrix = compute_pair_meet_counts(year_sched)
        member_totals = compute_member_totals(year_sched)
        role_mix = compute_role_mix(year_sched)

        st.markdown("<h4 class='section-title'>Pairwise office meetings</h4>", unsafe_allow_html=True)
        st.caption(
            "Number of working days where each pair of team members were both "
            "scheduled in the office.",
        )
        st.dataframe(pair_matrix, use_container_width=True)

        st.markdown("<h4 class='section-title'>Per-member office load</h4>", unsafe_allow_html=True)
        col_table, col_chart = st.columns([2, 3])
        with col_table:
            st.dataframe(member_totals, hide_index=True, use_container_width=True)
        with col_chart:
            chart_df = member_totals.set_index("Member")["Office days"]
            st.bar_chart(chart_df)

        st.markdown("<h4 class='section-title'>Role mix in the office</h4>", unsafe_allow_html=True)
        total_days = sum(role_mix.values()) or 1
        cols_stats = st.columns(4)
        labels = [
            ("Mixed (mgmt + engineering)", "mixed"),
            ("Mgmt-support only", "mgmt_only"),
            ("Engineering only", "engineering_only"),
            ("No one in office", "no_office"),
        ]
        for col_stat, (title, key) in zip(cols_stats, labels):
            count = role_mix.get(key, 0)
            pct = 100.0 * count / total_days
            with col_stat:
                st.metric(title, f"{count}", help=f"{pct:.1f}% of all scheduled working days")

    with tabs[3]:
        render_validations(year_sched)

    with tabs[4]:
        render_holidays_tab(holidays, public_holidays)


if __name__ == "__main__":
    main()
