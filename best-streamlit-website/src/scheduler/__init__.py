"""Scheduler service package.

This package contains the out-of-process scheduler runtime that:
- Persists job definitions and execution history to a DB (PostgreSQL by default).
- Executes due jobs on a real timer loop.
- Exposes a small, safe control surface via FastMCP tools (no direct UI access).
"""
