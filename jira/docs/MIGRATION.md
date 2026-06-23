# Jira → Trackly Migration

This tool pulls projects, users, issues, comments and worklogs from a **Jira
Cloud** (or **Server/Data Center**) instance via its REST API and imports them
into Trackly's PostgreSQL database.

It is **idempotent**: every imported row carries the originating Jira id in an
`external_id` column (`User.external_id`, `Project.external_id`,
`Issue.external_id`, `Comment.external_id`), so re-running the migration
**updates existing rows instead of creating duplicates**. Jira issue keys are
preserved verbatim (`ENG-123` stays `ENG-123`), which makes a phased cutover
possible.

The package lives at `backend/app/migration/`:

| File | Responsibility |
|------|----------------|
| `config.py` | `MigrationConfig.from_env()` — reads all settings from env vars. |
| `jira_client.py` | `JiraClient` — thin httpx REST client with pagination + retry/backoff. |
| `mapper.py` | Pure JSON → model-kwargs transforms (ADF flattening, status/priority mapping). |
| `importer.py` | `Importer` — orchestrates the idempotent upsert into Postgres. |
| `cli.py` | `python -m app.migration.cli` entry point. |

---

## 1. Prerequisites

1. **A Jira account** with read access to the projects you want to migrate.
2. **An API token** (Jira Cloud) or **Personal Access Token** (Server/DC):
   - **Cloud:** create one at
     <https://id.atlassian.com/manage-profile/security/api-tokens>.
     Auth is HTTP Basic using your **email** + the token.
   - **Server / Data Center:** create a *Personal Access Token* from your
     profile → *Personal Access Tokens*. Auth is a **Bearer** token; no email.
3. **A reachable Trackly Postgres database.** The migration runs
   `run_bootstrap()` first, so it will create tables and seed the default
   issue types / statuses / priorities / admin user if they do not yet exist.

---

## 2. Environment variables

The migration reads **Jira** settings from the variables below, and the
**database** connection from Trackly's normal settings
(`DATABASE_URL`, or the discrete `POSTGRES_*` vars — see `app/core/config.py`).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JIRA_BASE_URL` | yes | — | e.g. `https://your-org.atlassian.net` |
| `JIRA_API_TOKEN` | yes | — | API token (Cloud) or PAT (Server/DC) |
| `JIRA_EMAIL` | cloud only | — | Atlassian account email (Basic auth) |
| `JIRA_AUTH_MODE` | no | `cloud` | `cloud` (Basic) or `server` (Bearer PAT) |
| `JIRA_PROJECT_KEYS` | no | *(all)* | Comma list, e.g. `ENG,OPS`. Empty ⇒ every visible project |
| `JIRA_JQL` | no | — | Extra JQL AND-ed onto each project's filter |
| `JIRA_VERIFY_SSL` | no | `true` | Set `false` for self-signed Server/DC certs |

Example `.env` (placeholders — do **not** commit real tokens):

```dotenv
# --- Jira source ---
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=__your_api_token__
JIRA_AUTH_MODE=cloud
JIRA_PROJECT_KEYS=ENG,OPS
JIRA_JQL=
JIRA_VERIFY_SSL=true

# --- Trackly target DB (same as the app) ---
DATABASE_URL=postgresql+psycopg://trackly:trackly@db:5432/trackly
```

For **Server/Data Center** with a PAT:

```dotenv
JIRA_BASE_URL=https://jira.internal.example.com
JIRA_AUTH_MODE=server
JIRA_API_TOKEN=__your_personal_access_token__
# JIRA_EMAIL is ignored in server mode
JIRA_VERIFY_SSL=false
```

---

## 3. Running

### 3a. Locally (inside the backend virtualenv)

```bash
cd backend
pip install -r requirements.txt          # provides httpx, sqlalchemy, psycopg…

# 1. Sanity-check credentials
python -m app.migration.cli test-connection

# 2. See which projects are visible
python -m app.migration.cli list-projects

# 3. Run the import (scope via flags or env)
python -m app.migration.cli run --projects ENG
python -m app.migration.cli run --projects ENG,OPS --jql 'labels = migrate'
python -m app.migration.cli run --since 2024-01-01      # only recently-updated
python -m app.migration.cli run                          # every visible project
```

Add `-v` / `--verbose` for DEBUG logging.

### 3b. Via Docker Compose

Define a one-off `migrator` service in `docker-compose.yml` that reuses the
backend image and shares the app's env file, e.g.:

```yaml
  migrator:
    build: ./backend
    env_file: .env
    depends_on:
      - db
    entrypoint: ["python", "-m", "app.migration.cli"]
    profiles: ["tools"]   # don't start with the normal stack
```

Then run on demand:

```bash
docker compose run --rm migrator test-connection
docker compose run --rm migrator list-projects
docker compose run --rm migrator run --projects ENG
```

`--rm` discards the container after each run; the database keeps the data.

---

## 4. What gets imported

| Jira | Trackly | Notes |
|------|---------|-------|
| Users (`/users/search` + issue/comment authors) | `users` | New users get a random hashed password; existing passwords are never overwritten. Missing username/email are synthesized (`<accountId>@imported.local`). |
| Statuses | `statuses` (global) | Upserted by name; `statusCategory` mapped → `todo` / `in_progress` / `done`. |
| Issue types | `issue_types` (global) | Upserted by name; `subtask` flag preserved. |
| Priorities | `priorities` | Upserted by name; rank inferred from the name. |
| Projects | `projects` | Upserted by `external_id`/key; a default **scrum board** is created if none exists. |
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
- Use `--since YYYY-MM-DD` (or `JIRA_JQL`) to limit each pass to recently
  changed issues for fast top-ups during a migration window.
- `project.issue_counter` is advanced to the **highest imported issue number**,
  so issues created natively in Trackly after the migration won't collide with
  preserved Jira keys.

## 6. Phased cutover

Because keys and ids are preserved and re-runs are idempotent, you can:

1. Do an initial bulk import (`run`).
2. Keep both systems live and re-run `--since <last run>` periodically to sync
   deltas.
3. Cut over to Trackly; existing `ENG-123` references (links, commits, docs)
   still resolve to the same issue key.

---

## 7. Limitations

- **Attachments** are *not* downloaded by default. The `attachments` table is
  left untouched; binaries stay in Jira. (Filenames/metadata are available in
  the API if you choose to extend the importer.)
- **Sprint / board history** is not migrated. A single default scrum board is
  created per project; issues are not assigned to historical sprints, and
  `issue_history` (the change log) is **not** back-filled.
- **Custom fields** are limited to **Story Points** (auto-detected by field
  name, with common `customfield_*` id fallbacks) and the **Epic Link**. Other
  custom fields are ignored.
- **Issue links** (blocks / relates-to / duplicates) are not imported; only the
  parent/sub-task and epic hierarchy is.
- **Components / fix versions** on issues are not imported.
- User-account state (active/inactive, groups, permissions) is not synced;
  imported users are created active with a random password and must reset it (or
  be wired to your SSO).
