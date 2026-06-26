"""Seed a local Postgres so the Teams tab + Architecture tab show real data.

Creates and populates the LDAP tables the dashboard reads:
  - ldap_users         : one row per member (directory fields)
  - ldap_team_members  : (team_cn, username) rosters
  - ldap_member_resolutions / ldap_sync_log : created empty

Member names / teams / companies match seed_git.py + seed_es_fixtures.py so the
Teams company×role matrix lines up, and the seeded team_cns (DEVJAVA / DEVDOTNET)
are exactly the Control config repos — which is also what the Architecture tab's
repo discovery falls back to, so it finds them.

Connection comes from LOCALDEV_PG_* env (defaults below). Skips cleanly (exit 0)
if Postgres isn't reachable or psycopg isn't installed — the harness still works
without it (those tabs just stay in their empty state).

    LOCALDEV_PG_HOST=localhost python localdev/seed_pg.py
"""

from __future__ import annotations

import os
import sys

HOST = os.environ.get("LOCALDEV_PG_HOST", "localhost")
PORT = int(os.environ.get("LOCALDEV_PG_PORT", "5432"))
DB = os.environ.get("LOCALDEV_PG_DB", "devops")
USER = os.environ.get("LOCALDEV_PG_USER", "devops")
PW = os.environ.get("LOCALDEV_PG_PASSWORD", "devops")

# (username, display, email, company, title, team_cns…)
MEMBERS = [
    ("alice.dev", "Alice Dev", "alice.dev@acme.local",  "ACME",   "Senior Engineer",   ["DEVJAVA"]),
    ("bob.dev",   "Bob Dev",   "bob.dev@acme.local",    "ACME",   "Engineer",          ["DEVJAVA"]),
    ("carol.qc",  "Carol QC",  "carol.qc@acme.local",   "ACME",   "QA Engineer",       ["QCJAVA"]),
    ("dan.net",   "Dan Net",   "dan.net@globex.local",  "GLOBEX", "Engineer",          ["DEVDOTNET"]),
    ("nina.net",  "Nina Net",  "nina.net@globex.local", "GLOBEX", "QA Engineer",       ["QCNET"]),
    ("eve.ops",   "Eve Ops",   "eve.ops@acme.local",    "ACME",   "Operations Lead",   ["OPS"]),
    ("omar.ops",  "Omar Ops",  "omar.ops@globex.local", "GLOBEX", "SRE",               ["OPS"]),
]

DDL = """
CREATE TABLE IF NOT EXISTS ldap_users (
  username TEXT PRIMARY KEY, email TEXT, display_name TEXT, title TEXT,
  department TEXT, company TEXT, manager TEXT, when_created TEXT,
  when_changed TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS ldap_team_members (
  team_cn TEXT NOT NULL, username TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (team_cn, username));
CREATE TABLE IF NOT EXISTS ldap_member_resolutions (
  alias_key TEXT PRIMARY KEY, kind TEXT NOT NULL, target_username TEXT,
  team_cn TEXT, note TEXT, resolved_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS ldap_sync_log (
  id BIGSERIAL PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ, status TEXT NOT NULL, teams_count INT,
  users_count INT, delta_summary TEXT, error_msg TEXT);
CREATE TABLE IF NOT EXISTS devops_projects (
  company TEXT, project TEXT, dev_team TEXT, qc_team TEXT, ops_team TEXT);
"""

# (company, project, dev_team, qc_team, ops_team) — for the inventory↔Postgres
# compare panel in the Sync Check tab.
DEVOPS_PROJECTS = [
    ("ACME",   "payments", "DEVJAVA",   "QCJAVA", "OPS"),
    ("GLOBEX", "billing",  "DEVDOTNET", "QCNET",  "OPS"),
]


def _connect():
    try:
        import psycopg  # v3
        return psycopg.connect(host=HOST, port=PORT, dbname=DB, user=USER,
                               password=PW, connect_timeout=5)
    except ImportError:
        pass
    except Exception as exc:
        print(f"[seed_pg] cannot connect ({type(exc).__name__}: {exc}); skipping.")
        return None
    try:
        import psycopg2
        return psycopg2.connect(host=HOST, port=PORT, dbname=DB, user=USER,
                                password=PW, connect_timeout=5)
    except ImportError:
        print("[seed_pg] psycopg not installed; skipping (Teams/Architecture "
              "tabs stay in their empty state).")
        return None
    except Exception as exc:
        print(f"[seed_pg] cannot connect ({type(exc).__name__}: {exc}); skipping.")
        return None


def main() -> int:
    conn = _connect()
    if conn is None:
        return 0
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        cur.execute("TRUNCATE ldap_users, ldap_team_members, "
                    "ldap_member_resolutions, devops_projects")
        for co, proj, dev, qc, ops in DEVOPS_PROJECTS:
            cur.execute("INSERT INTO devops_projects "
                        "(company,project,dev_team,qc_team,ops_team) "
                        "VALUES (%s,%s,%s,%s,%s)", (co, proj, dev, qc, ops))
        for un, disp, email, co, title, teams in MEMBERS:
            cur.execute(
                "INSERT INTO ldap_users (username,email,display_name,title,"
                "department,company,manager,when_created,when_changed) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (un, email, disp, title, "Engineering", co, "eve.ops",
                 "2025-01-15 09:00:00", "2026-06-01 09:00:00"))
            for t in teams:
                cur.execute(
                    "INSERT INTO ldap_team_members (team_cn,username) "
                    "VALUES (%s,%s) ON CONFLICT DO NOTHING", (t, un))
        conn.commit()
        cur.execute("SELECT count(*) FROM ldap_users")
        n_u = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT team_cn) FROM ldap_team_members")
        n_t = cur.fetchone()[0]
        print(f"[seed_pg] seeded {n_u} users across {n_t} teams "
              f"at {HOST}:{PORT}/{DB}")
        return 0
    except Exception as exc:
        print(f"[seed_pg] seed failed ({type(exc).__name__}: {exc}); skipping.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
