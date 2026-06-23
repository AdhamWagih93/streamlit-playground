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
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("test-connection", help="Verify credentials by calling /myself.")
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


def _load_config() -> MigrationConfig:
    config = MigrationConfig.from_env()
    config.validate()
    return config


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

    try:
        config = _load_config()
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
