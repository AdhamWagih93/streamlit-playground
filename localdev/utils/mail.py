"""Fake mail module for local/CI testing — captures instead of sending.

``cc_docchat`` imports ``send_email`` (guarded). Locally it appends to a list so
tests can assert on it; it never reaches a real SMTP server.
"""

from __future__ import annotations

SENT: list = []


def send_email(*args, **kwargs) -> bool:
    SENT.append({"args": args, "kwargs": kwargs})
    return True
