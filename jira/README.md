# Trackly

**Trackly** is an original, open-source issue & project tracker — a clean-room
work for teams who want a self-hostable place to plan work, triage bugs, run
sprints, and track delivery. It is **not affiliated with, endorsed by, or
derived from Atlassian or Jira**; it is an independent implementation released
under the [MIT License](LICENSE).

A one-way migration tool is provided so teams already on Jira can bring their
projects and issues across — see [Jira migration](#jira-migration).

---

## Features

- **Projects** with components, versions (fix-versions), and per-project members
- **Issues** with types, priorities, labels, configurable statuses & status
  categories, parent/child and issue links, attachments, comments, worklogs,
  and a full change-history audit trail
- **Agile** boards and sprints
- **Custom fields** and saved filters
- **TQL** — a Trackly query language for slicing issues
- **Notifications** for activity that matters to you
- **Auth** with JWT bearer tokens, roles, and a first-run bootstrap admin
- **Jira → Trackly migration** CLI (projects + issues), cloud or server/DC auth
- **REST API** with interactive docs at `/api/docs`

## Architecture

```
                            ┌──────────────────────────────┐
   Browser  ──HTTP 8080──►  │  frontend (nginx :80)        │
                            │  • serves the React SPA      │
                            │  • /api/  → proxy ─────────┐ │
                            │  • /health → proxy ────────┤ │
                            └────────────────────────────┼─┘
                                                         │ :8000
                            ┌────────────────────────────▼─┐
                            │  backend (FastAPI/uvicorn)    │
                            │  • REST API under /api        │
                            │  • bootstrap admin + tables   │
                            │    on first boot (lifespan)   │
                            │  • attachments → volume       │
                            └───────────────┬───────────────┘
                                            │ psycopg
                            ┌───────────────▼───────────────┐
                            │  db (postgres:16)  [pgdata]   │
                            └───────────────────────────────┘

   migrator (profile: migrate, one-shot)  ──►  db
   `docker compose run --rm migrator run --projects ENG`
```

## Tech stack

| Layer     | Technology                                              |
|-----------|---------------------------------------------------------|
| Backend   | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, uvicorn |
| Auth      | JWT (PyJWT), passlib + bcrypt                            |
| Database  | PostgreSQL 16 (psycopg v3)                               |
| Migrations| Alembic (bootstrap `create_all` for first boot)         |
| Frontend  | React + TypeScript, Vite, served by nginx               |
| Ops       | Docker, Docker Compose, Makefile                         |

---

## Quickstart (Docker)

Prerequisites: Docker + the Docker Compose plugin.

```bash
cp .env.example .env          # then edit secrets (see the warning below)
docker compose up --build     # add -d to run in the background
```

Then open **http://localhost:8080** and sign in with the bootstrap admin.

- API docs (Swagger UI): **http://localhost:8080/api/docs**
- Backend liveness: **http://localhost:8080/health**

### Default credentials

The first boot creates an admin from your `.env`:

| Setting    | Env var                     | Default              |
|------------|-----------------------------|----------------------|
| Email      | `BOOTSTRAP_ADMIN_EMAIL`     | `admin@trackly.local`|
| Username   | `BOOTSTRAP_ADMIN_USERNAME`  | `admin`              |
| Password   | `BOOTSTRAP_ADMIN_PASSWORD`  | `admin`              |

> ⚠️ **Change these before exposing Trackly.** At minimum, set a strong
> `BOOTSTRAP_ADMIN_PASSWORD` and a random `SECRET_KEY`
> (`openssl rand -hex 32`) in `.env` *before* the first `docker compose up`,
> and set `APP_ENV=production` / `DEBUG=false`. The bootstrap admin is only
> created when no users exist, so changing the password after first boot must
> be done in-app.

### Common operations (Makefile)

```bash
make up            # build + start in the background
make logs          # tail logs
make down          # stop (keeps the pgdata / attachments volumes)
make psql          # psql prompt against the db
make backend-shell # shell into the backend container
make migrate PROJECTS=ENG   # run a Jira import (see below)
```

---

## Dev setup (without Docker)

Run Postgres yourself (or just `docker compose up -d db`), then:

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env        # POSTGRES_HOST=localhost is correct here
uvicorn app.main:app --reload  # http://localhost:8000  (docs at /api/docs)
```

**Frontend**

```bash
cd frontend
npm install
npm run dev                    # Vite dev server, e.g. http://localhost:5173
```

During local dev the Vite server talks to the backend on `:8000`. Make sure the
backend's `CORS_ORIGINS` (in `.env`) includes your Vite origin
(`http://localhost:5173` is included by default).

---

## Jira migration

Trackly ships a one-way Jira → Trackly importer. Configure the `JIRA_*` values
in `.env`, then drive it through the `migrator` service (it lives behind the
`migrate` compose profile, so it never starts with `docker compose up`):

```bash
docker compose run --rm migrator test-connection      # verify credentials
docker compose run --rm migrator list-projects        # see what's importable
docker compose run --rm migrator run --projects ENG   # import project(s)
```

`--projects` is comma-separated (`--projects ENG,OPS`); omit it to import every
visible project. You can also use `make migrate PROJECTS=ENG`.

Full details — cloud vs. server/DC auth, JQL filters, incremental runs, field
mapping, and caveats — are in **[docs/MIGRATION.md](docs/MIGRATION.md)**.

---

## API documentation

With the stack running, interactive API docs are available at:

- Swagger UI: **http://localhost:8080/api/docs**
- ReDoc:      **http://localhost:8080/api/redoc**
- OpenAPI JSON: **http://localhost:8080/api/openapi.json**

(Or directly against the backend at `http://localhost:8000/api/docs` in dev.)

---

## Project layout

```
jira/
├── docker-compose.yml      # db, backend, frontend, migrator (profile)
├── .env.example            # copy to .env
├── Makefile                # up / down / logs / migrate / psql / ...
├── LICENSE                 # MIT
├── README.md
├── docs/
│   └── MIGRATION.md        # Jira → Trackly migration guide
├── scripts/                # helper scripts
├── backend/                # FastAPI app (app.main:app)
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── requirements.txt
│   ├── alembic.ini         # migrations wired to app settings/models
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   └── app/
│       ├── main.py         # FastAPI app + lifespan bootstrap
│       ├── core/           # config, database
│       ├── models/         # SQLAlchemy models (Base.metadata)
│       ├── schemas/        # Pydantic schemas
│       ├── api/            # routers
│       ├── services/       # business logic (incl. TQL)
│       ├── migration/      # Jira import CLI (python -m app.migration.cli)
│       └── utils/
└── frontend/               # React + TS + Vite SPA
    ├── Dockerfile          # build -> nginx
    ├── nginx.conf          # SPA fallback + /api proxy to backend:8000
    └── src/
```

---

## Legal / License

Trackly is an **independent, original work** licensed under the
[MIT License](LICENSE) (© 2026 Trackly contributors). It is **not affiliated
with, sponsored by, or endorsed by Atlassian Pty Ltd**. "Jira" is a trademark
of Atlassian and is referenced here only to describe the optional one-way data
migration tool's source system; no Jira source code is used or included.
