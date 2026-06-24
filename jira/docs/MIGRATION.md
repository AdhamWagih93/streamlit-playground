# Jira → Trackly Migration

Trackly imports projects, users, issues, comments and worklogs from a **Jira
Cloud** (or **Server/Data Center**) instance via its REST API. There are two
ways to run it, both driven by the **same UI-configured Jira connections**:

1. **Live, per-project sync (recommended)** — managed entirely in the web UI by
   project admins. Match a Trackly project to a Jira project by key, then
   start/pause/resume a **resumable** sync. Also imports issue types, statuses,
   project details and the **Jira permission scheme**.
2. **Bulk migrator CLI** — `python -m app.migration.cli`, for an initial bulk
   import or scripted top-ups across many projects at once.

Both are **idempotent**: every imported row carries the originating Jira id in an
`external_id` column, so re-running **updates existing rows instead of creating
duplicates**. Jira issue keys are preserved verbatim (`ENG-123` stays `ENG-123`),
which makes a phased cutover possible.

---

## 1. Configure a Jira connection (UI, instance admin)

Jira credentials are **not** set via environment variables. An instance
administrator adds them once, through the interface:

**Administration → Jira Connections → Add connection**

| Field | Notes |
|-------|-------|
| Name | A label, e.g. `Prod Jira`. |
| Base URL | e.g. `https://your-org.atlassian.net` (no trailing slash). |
| Auth mode | `cloud` (HTTP Basic: email + API token) or `server` (Bearer PAT). |
| Email | Cloud only — your Atlassian account email. |
| API token / PAT | Cloud: create at <https://id.atlassian.com/manage-profile/security/api-tokens>. Server/DC: a *Personal Access Token* from your profile. |
| Verify SSL | Turn off only for self-signed Server/DC certificates. |
| Default | Mark one connection as the default used when none is specified. |

The token is **encrypted at rest** (via the app's `SECRET_KEY`-derived key) and
is never returned to the browser. Use the **Test** button to verify
connectivity, and **Browse projects** to see which Jira projects are visible and
which already exist locally.

> The same applies to **mail** (Administration → Mail) and **external auth**
> (Administration → Identity Providers / Authentication) — all UI-managed, no
> env vars. The only auth-related secret in the environment is `SECRET_KEY`,
> which signs JWTs and encrypts every stored credential.

---

## 2. Live per-project sync (recommended)

In a project's **Settings → Jira Sync** tab:

1. **Pick a connection** and confirm the matched Jira key (defaults to the
   Trackly project key) → **Link**.
2. **Start** the sync. Progress (processed / total) is shown live; you can
   **Pause** and **Resume** at any time.

What the live sync does:

- Imports issue types, statuses (category-mapped), priorities, project details,
  components/versions, issues, comments and worklogs.
- Optionally imports the Jira project's **permission scheme** → a Trackly
  permission scheme + grants (groups, project roles and special holders are
  mapped 1:1; the project is pointed at the imported scheme).
- Is **resumable**: it tracks an `updated >= <watermark>` cursor and a page
  position, commits every 25 issues, and re-reads its pause flag between
  batches — so an interrupted or paused run continues where it stopped.
- Is **idempotent**: issues are upserted by `external_id`, Jira keys/numbers are
  preserved, and re-running pulls only what changed since the last watermark.

Sync state and a run history are visible in the same tab. Access requires
`ADMINISTER_PROJECTS` on the project (or site admin).

---

## 3. Bulk migrator CLI

The CLI reads the **same UI-configured connections** from the database (no
`JIRA_*` env vars). It runs `run_bootstrap()` first, so tables and default
issue types / statuses / priorities / admin user are created if absent.

The package lives at `backend/app/migration/`:

| File | Responsibility |
|------|----------------|
| `config.py` | `MigrationConfig.from_connection()` (DB) / `.from_env()` (fallback). |
| `jira_client.py` | `JiraClient` — httpx REST client with pagination + retry/backoff. |
| `mapper.py` | JSON → model-kwargs transforms (ADF flattening, status/priority mapping). |
| `importer.py` | `Importer` — orchestrates the idempotent upsert into Postgres. |
| `cli.py` | `python -m app.migration.cli` entry point. |

### 3a. Via Docker Compose (uses the UI-configured connection)

The `migrator` service is defined in `docker-compose.yml` behind the `migrate`
profile, so it never starts with the normal stack. It shares the database (and
`SECRET_KEY`, to decrypt the stored token):

```bash
# List the Jira connections configured in the UI
docker compose run --rm migrator list-connections

# Verify the default connection's credentials
docker compose run --rm migrator test-connection

# Verify a specific connection (by name or id)
docker compose run --rm migrator --connection "Prod Jira" test-connection

# Import (default connection)
docker compose run --rm migrator run --projects ENG
docker compose run --rm migrator run --projects ENG,OPS --jql 'labels = migrate'
docker compose run --rm migrator run --since 2024-01-01      # recently-updated only
docker compose run --rm migrator run                          # every visible project

# Import using a named connection
docker compose run --rm migrator --connection "Prod Jira" run --projects ENG
```

`--rm` discards the container after each run; the database keeps the data.
Add `-v` / `--verbose` for DEBUG logging.

### 3b. Locally (inside the backend virtualenv)

```bash
cd backend
pip install -r requirements.txt
python -m app.migration.cli list-connections
python -m app.migration.cli test-connection
python -m app.migration.cli run --projects ENG
```

The CLI resolves the Jira connection in this order: `--connection <name|id>` if
given, otherwise the **default** enabled connection. (Legacy `JIRA_*` env vars
are honored only as a fallback when no DB connection exists.)

---

## 4. What gets imported

| Jira | Trackly | Notes |
|------|---------|-------|
| Users (`/users/search` + issue/comment authors) | `users` | New users get a random hashed password; existing passwords are never overwritten. Missing username/email are synthesized (`<accountId>@imported.local`). |
| Statuses | `statuses` (global) | Upserted by name; `statusCategory` mapped → `todo` / `in_progress` / `done`. |
| Issue types | `issue_types` (global) | Upserted by name; `subtask` flag preserved. |
| Priorities | `priorities` | Upserted by name; rank inferred from the name. |
| Projects | `projects` | Upserted by `external_id`/key; a default **scrum board** is created if none exists. |
| Permission scheme *(live sync only)* | `permission_schemes` + `permission_grants` | Holders mapped via the Jira → Trackly holder map; groups/roles auto-created; project pointed at the imported scheme. |
| Issues | `issues` | Key + number preserved; ADF description flattened to text; labels, estimates, due date, resolution, story points imported. |
| Parent / Epic links | `issues.parent_id` / `issues.epic_id` | Resolved in a **second pass** after all issues exist (parents may be paged later). |
| Comments | `comments` | Upserted by `external_id`; body flattened from ADF; author mapped. |
| Worklogs | `worklogs` | `timeSpentSeconds` + `started` + author. |

### Status-category mapping

| Jira `statusCategory.key` | Trackly category |
|---------------------------|------------------|
| `new`, `undefined` | `todo` |
| `indeterminate` | `in_progress` |
| `done` | `done` |

---

## 5. Idempotency & re-running

- Re-running is safe and incremental — rows are matched by `external_id` (or by
  natural key / email as a fallback) and **updated in place**.
- The live sync stores an `updated` watermark per project and only re-pulls
  issues changed since then. For the CLI, use `--since YYYY-MM-DD` (or `--jql`)
  to limit each pass to recently changed issues.
- `project.issue_counter` is advanced to the **highest imported issue number**,
  so issues created natively in Trackly after the migration won't collide with
  preserved Jira keys.

## 6. Phased cutover

Because keys and ids are preserved and re-runs are idempotent, you can:

1. Do an initial bulk import (CLI `run`) or link + start the live sync.
2. Keep both systems live and re-sync periodically to pull deltas.
3. Cut over to Trackly; existing `ENG-123` references (links, commits, docs)
   still resolve to the same issue key.

---

## 7. Limitations

- **Attachments** are *not* downloaded. The `attachments` table is left
  untouched; binaries stay in Jira. (Filenames/metadata are available in the API
  if you choose to extend the importer.)
- **Sprint / board history** is not migrated. A single default scrum board is
  created per project; issues are not assigned to historical sprints, and
  `issue_history` (the change log) is **not** back-filled.
- **Custom fields** are limited to **Story Points** (auto-detected by field name)
  and the **Epic Link**. Other custom fields are ignored.
- **Issue links**, **components/fix-versions on issues**, voters and watchers are
  not imported by the CLI importer.
- The **CLI importer** does not import permission schemes — use the **live
  per-project sync** for permissions.
- User-account state (active/inactive) is not synced; imported users are created
  active with a random password and must reset it (or be wired to your SSO via
  Administration → Identity Providers).

---

## Appendix: schema changes & data safety

The app persists everything in the PostgreSQL `pgdata` Docker volume; it survives
`restart` / `up` / `down`. Only `docker compose down -v` (which deletes volumes)
erases it — avoid that flag unless you intend to wipe.

On startup the app additively reconciles the schema
(`app/core/schema_sync.py`): new tables are created and new columns are added to
existing tables (as nullable), so version upgrades that add fields apply with **no
data loss** and **no manual migration**. Genuinely destructive changes (drops,
type changes, renames) still warrant an Alembic migration (scaffolding is in
`backend/alembic/`).
