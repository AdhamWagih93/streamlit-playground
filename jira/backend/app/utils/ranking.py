"""Lexicographic ranking for stable drag-and-drop ordering on boards.

We use a base-62 "LexoRank"-style midpoint algorithm: every item carries a
short string rank, and to move an item between two neighbours we compute a
string that sorts strictly between them. This avoids renumbering siblings on
every reorder.
"""
from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
_MIN = ALPHABET[0]
_MAX = ALPHABET[-1]


def _char_at(s: str, i: int) -> int:
    return ALPHABET.index(s[i]) if i < len(s) else 0


def rank_between(low: str | None, high: str | None) -> str:
    """Return a rank string strictly between *low* and *high*.

    Either bound may be None (open interval). The result always sorts strictly
    after *low* and strictly before *high* under plain string comparison.
    """
    low = low or ""
    high = high or ""

    rank = ""
    i = 0
    while True:
        lo = _char_at(low, i)
        hi = _char_at(high, i) if high else BASE
        if high and i >= len(high):
            hi = BASE
        if lo == hi:
            rank += ALPHABET[lo]
            i += 1
            continue
        mid = (lo + hi) // 2
        if mid == lo:
            # Adjacent digits (hi == lo + 1): take low's digit and descend for
            # more precision. Having chosen a digit strictly below high's digit
            # here, every deeper digit keeps the result < high, so the upper
            # bound no longer constrains us. Keep `low` intact and advance the
            # index — slicing it would desync the position used by _char_at.
            rank += ALPHABET[lo]
            high = ""
            i += 1
            continue
        rank += ALPHABET[mid]
        return rank


def initial_rank() -> str:
    return ALPHABET[BASE // 2]
