"""Parse and format human time-tracking durations like '1w 2d 3h 30m'."""
from __future__ import annotations

import re

_UNIT_SECONDS = {
    "w": 5 * 8 * 3600,  # 1 week = 5 working days
    "d": 8 * 3600,      # 1 day  = 8 working hours
    "h": 3600,
    "m": 60,
}
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([wdhm])", re.IGNORECASE)


def parse_duration(text: str | None) -> int | None:
    """Convert '2h 30m' -> seconds. Returns None for empty/invalid input."""
    if not text:
        return None
    text = text.strip()
    if text.isdigit():
        return int(text)
    total = 0
    matched = False
    for value, unit in _TOKEN.findall(text):
        matched = True
        total += int(float(value) * _UNIT_SECONDS[unit.lower()])
    return total if matched else None


def format_duration(seconds: int | None) -> str:
    """Convert seconds -> '2h 30m' (working-day aware)."""
    if not seconds or seconds <= 0:
        return "0m"
    parts: list[str] = []
    for unit in ("w", "d", "h", "m"):
        size = _UNIT_SECONDS[unit]
        if seconds >= size:
            count = seconds // size
            seconds -= count * size
            parts.append(f"{count}{unit}")
    return " ".join(parts) if parts else "0m"
