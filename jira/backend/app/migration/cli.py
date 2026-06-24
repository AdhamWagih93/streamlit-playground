"""Command-line entry point for the Jira -> Trackly migration.

Usage::

    python -m app.migration.cli test-connection
    python -m app.migration.cli list-projects
    python -m app.migration.cli run --projects ENG,OPS --jql "updated >= -90d"
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.migration.config import MigrationConfig


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.migration.cli",
        description="Migrate Jira projects/issues into Trackly's Postgres database.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging.")
    parser.add_argument(
        "--connection", default=None,
        help="Name or id of a Jira connection configured in the admin UI. "
             "If omitted, the default UI connection is used (env vars are a fallback).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("test-connection", help="Verify credentials by calling /myself.")
    sub.add_parser("list-connections", help="List Jira connections configured in the UI.")
    sub.add_parser("list-projects", help="List projects visible to the configured account.")

    run_p = sub.add_parser("run", help="Import projects and issues.")
    run_p.add_argument("--projects", default=None,
                       help="Comma-separated project keys to import (overrides JIRA_PROJECT_KEYS). "
                            "Omit to import every visible project.")
    run_p.add_argument("--jql", default=None,
                       help="Extra JQL AND-ed onto each project filter (overrides JIRA_JQL).")
    run_p.add_argument("--since", default=None,
                       help="Only import issues updated on/after this date (YYYY-MM-DD); "
                            "shorthand for --jql 'updated >= \"<date>\"'.")
    return parser


def _resolve_connection_row(selector: str | None):
    """Load a JiraConnection from the DB by id, name, or (None) the default."""
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.identity import JiraConnection

    with SessionLocal() as db:
        if selector is not None:
            if selector.isdigit():
                row = db.get(JiraConnection, int(selector))
            else:
                row = db.scalars(
                    select(JiraConnection).where(JiraConnection.name == selector)
                ).first()
        else:
            row = db.scalars(
                select(JiraConnection)
                .where(JiraConnection.enabled.is_(True))
                .order_by(JiraConnection.is_default.desc(), JiraConnection.id.asc())
            ).first()
        if row is None:
            return None
        # Detach a plain config so the session can close.
        return MigrationConfig.from_connection(row)


def _load_config(selector: str | None) -> MigrationConfig:
    """Prefer a UI-configured Jira connection; fall back to env vars."""
    config = None
    try:
        config = _resolve_connection_row(selector)
    except Exception as exc:  # DB not reachable, etc. — fall back to env.
        logging.getLogger("trackly.migration").debug("DB connection lookup failed: %s", exc)
    if config is None:
        if selector is not None:
            raise ValueError(
                f"No Jira connection named/id '{selector}' found. Configure one in the admin UI."
            )
        config = MigrationConfig.from_env()
    config.validate()
    return config


def cmd_list_connections() -> int:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.identity import JiraConnection

    with SessionLocal() as db:
        rows = db.scalars(select(JiraConnection).order_by(JiraConnection.id.asc())).all()
    if not rows:
        print("No Jira connections configured. Add one in the admin UI (Administration -> Jira Connections).")
        return 0
    for r in rows:
        flags = []
        if r.is_default:
            flags.append("default")
        if not r.enabled:
            flags.append("disabled")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        print(f"  #{r.id}  {r.name}  ->  {r.base_url}  ({r.auth_mode}){suffix}")
    return 0


def cmd_test_connection(config: MigrationConfig) -> int:
    from app.migration import build_client

    client = build_client(config)
    try:
        me = client.get_myself()
    finally:
        client.close()
    name = me.get("displayName") or me.get("name") or "(unknown)"
    account = me.get("accountId") or me.get("key") or me.get("name") or "-"
    email = me.get("emailAddress") or "-"
    print(f"Connected to {config.base_url}")
    print(f"  Authenticated as: {name}")
    print(f"  Account id:       {account}")
    print(f"  Email:            {email}")
    return 0


def cmd_list_projects(config: MigrationConfig) -> int:
    from app.migration import build_client

    client = build_client(config)
    try:
        rows = [
            (p.get("key", "-"), p.get("projectTypeKey", "-"), p.get("name", "-"))
            for p in client.iter_projects()
        ]
    finally:
        client.close()
    if not rows:
        print("No projects visible to this account.")
        return 0
    key_w = max(len(r[0]) for r in rows) + 2
    type_w = max(len(r[1]) for r in rows) + 2
    print(f"{'KEY':<{key_w}}{'TYPE':<{type_w}}NAME")
    for key, ptype, name in rows:
        print(f"{key:<{key_w}}{ptype:<{type_w}}{name}")
    print(f"\n{len(rows)} project(s).")
    return 0


def cmd_run(config: MigrationConfig, args: argparse.Namespace) -> int:
    from app.migration import run as run_migration

    project_keys = None
    if args.projects:
        project_keys = [k.strip() for k in args.projects.split(",") if k.strip()]

    jql_extra = args.jql or ""
    if args.since:
        clause = f'updated >= "{args.since}"'
        jql_extra = f"({jql_extra}) AND {clause}" if jql_extra else clause

    stats = run_migration(config=config, project_keys=project_keys, jql_extra=jql_extra)
    # Summary already printed by the importer; echo a one-liner for piping.
    print(
        f"Done: users={stats.users} projects={stats.projects} "
        f"issues={stats.issues} comments={stats.comments} worklogs={stats.worklogs}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    # list-connections doesn't need a resolved Jira config.
    if args.command == "list-connections":
        return cmd_list_connections()

    try:
        config = _load_config(getattr(args, "connection", None))
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "test-connection":
            return cmd_test_connection(config)
        if args.command == "list-projects":
            return cmd_list_projects(config)
        if args.command == "run":
            return cmd_run(config, args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the user
        logging.getLogger("trackly.migration").exception("Migration failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
