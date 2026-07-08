# MERIDIAN — Engineering Platform (rebuild spec)

Rebuild of a 37.7k-line Streamlit "CI/CD Command Center" as **React + TypeScript (Vite)** frontend
+ **FastAPI** backend. Same external integrations (Elasticsearch, Postgres, Vault, Jenkins,
Azure DevOps git+REST, LDAP, S3/MinIO, Ollama), same capabilities, plus the AI capabilities from
the DevOpsDays vision (incident analysis, knowledge assistant, architecture discovery).

Two backend modes, chosen by `DATA_MODE`:
- `demo` — seeded, deterministic in-memory world; zero external dependencies; used locally.
- `live` — real integrations, credentials resolved via Vault (creds from env/mounted secrets).

Auth modes (`AUTH_MODE`): `none` (local dev; auto-login, role/team switcher), `entra` (OIDC
auth-code), `ldap` (bind + group→role mapping). All modes end in the same backend-issued session
JWT (httpOnly cookie). RBAC identical everywhere.

## RBAC model (must match original exactly)

- Roles: `Admin`, `CLevel`, `Developer`, `QC`, `Operations`. Strict mapping from raw role strings:
  admin→Admin, clevel/c-level/executive→CLevel, developer→Developer, quality-control→QC,
  operator/operations→Operations. Unknown → default Developer (least privilege).
- Pick priority Admin > CLevel > first-detected. `is_admin = role in {Admin, CLevel}`.
- Team ownership fields on inventory rows: `dev_team, qc_team, uat_team, prd_team, ops_team, preprod_team`.
  Role→field map (ACTIONS only): Developer→dev_team, QC→qc_team, Operations→uat_team+prd_team.
- **Visibility** for non-admins: user's teams matched against **any** `*_team` field (broad on purpose).
  Team matching is case- AND separator-insensitive ("My Team" == "my_team" == "My-Team").
- Env visibility: Admin/CLevel all; Developer dev; QC qc; Operations uat+prd. Multi-role = union.
- Event-type visibility: Admin/CLevel all; Developer commits+builds+deployments; QC/Ops
  deployments+releases+requests.
- Admins have `admin_view_all` toggle (default on) to scope down to their own teams.

## Navigation / pages

| Route | Page | Access |
|---|---|---|
| `/` | Overview — KPI counters, live event stream, fleet summary, integrations health, AI launcher | all |
| `/fleet` | Pipelines Inventory — per-app env×version matrix, filters/sort/search, detail drawer | all (scoped) |
| `/events` | Event Log — role-scoped, type pills, filters, pagination | all (scoped) |
| `/actions` | Actions — build/deploy/promote triggers + Jenkins status | admin (+QC release) |
| `/security` | Security posture — Prisma/Invicti/ZAP/TruffleHog + report viewer | all (scoped) |
| `/incidents` | AI incident analysis — failed deploys → evidence pipeline → verdict | all (scoped) |
| `/assistant` | Knowledge assistant — doc-grounded chat, personas, citations | all |
| `/architecture` | Environment architecture — topology per env, env compare, AI discovery | admin |
| `/technology` | Tech & Platforms analytics | admin |
| `/teams` | Teams & Members — LDAP roster, activity, team cards | admin |
| `/people` | People insights — per-user activity table, team rollup | admin |
| `/governance` | Governance — sync checks (git↔ES, inv↔PG, LDAP), ADO coverage, history→PG migration, tool-access RBAC audit, glossary | admin |
| `/login` | Login (entra redirect / ldap form); skipped in AUTH_MODE=none | public |

## API surface (all under `/api`, JSON, session cookie required except /auth)

Auth: `GET /auth/me`, `POST /auth/login` (ldap), `GET /auth/entra/login` + `/auth/entra/callback`,
`POST /auth/logout`, `POST /auth/dev/switch` (AUTH_MODE=none only: switch role/teams).

Every scoped endpoint applies RBAC server-side from the session — never trust client filters.

- `GET /overview/summary` — KPIs (apps, pipelines, teams, envs), health counts
- `GET /events?window=&types=&envs=&q=&user=&page=&size=` — event log rows + total
- `GET /inventory?…filters…` — inventory rows (app, project, company, app_type, technologies,
  platform, images, teams, per-env stage versions+status+dates)
- `GET /inventory/app/{project}/{app}` — detail: identity, stages, next versions, PRD liveness,
  security scan summary per env with Δ-vs-PRD, recent deploys, repo URLs, config presence
- `GET /security/summary?…` + `GET /security/report/{scanner}/{project}/{app}/{version}` (HTML)
- `GET /jenkins/status` — pipelines (Build, Request_deploy, Request_promote): last build, running,
  queue, version
- `POST /actions/trigger` — {pipeline, params}; server-side role gate; returns queued build
- `GET /ai/incidents` — recent failed deployments as incident cards
- `POST /ai/incidents/{id}/analyze` — **SSE stream**: evidence steps → verdict (root cause,
  confidence, actions, MTTR)
- `POST /ai/assistant/chat` — **SSE stream**; body {messages, persona, doc_scope}; cites documents
- `GET /ai/assistant/sources` — doc corpus stats/categories
- `GET /architecture/model?env=&projects=&app=` — nodes+edges+provenance; `GET /architecture/envs`
- `GET /architecture/diff?envA=&envB=` — added/removed/changed connections, repeated-URL warnings
- `POST /architecture/discover` — **SSE**: AI discovery steps → findings + roadmap
- `GET /technology/summary?dim=&by=` — usage ranks, cross-ref matrix, consolidation notes
- `GET /teams/summary`, `GET /teams/{team}` — roster, roles, activity, new/updated badges
- `GET /people/summary?window=` — per-user counters + team rollup
- `GET /governance/sync/inventory` — git vs ES diff; `POST …/run` to refresh
- `GET /governance/sync/postgres` — inventory vs devops_projects diff (+ops inconsistency)
- `GET /governance/sync/ldap` + `POST …/run` — roster sync status + deltas
- `GET /governance/ado-coverage` — collections/projects/repos, pipelined %, hooks
  (develop/release/hotfixes/stress), team mismatches, azure-pipelines warnings, orphans
- `GET /governance/history` + `POST /governance/history/{index}/{action}` — ES→PG migration jobs
  (start/pause/resume/sync-new), progress
- `GET /governance/tool-access` — grants + unauthorized-access audit (ADO/JIRA vs owning team)
- `GET /meta/integrations` — health strip (ok/warn/down/skip per integration)
- `GET /meta/glossary`

## Demo world (backend/app/providers/demo)

Deterministic (seeded RNG), generated once at startup: ~6 companies, ~20 projects, ~120 apps,
18 teams, ~150 users; envs dev→qc→uat→prd; per-app version chains with builds/deploys/releases/
commits/jira/requests history over the past year (recent bias); scan results with severity counts;
a few deliberately interesting stories: a failed PRD deploy (config-parity incident), a legacy
system with EOL runtime + direct-DB coupling, drift rows for sync panels, unauthorized tool grants,
an un-pipelined repo set. Event stream endpoint synthesizes "live" events so the Overview ticks.
AI endpoints stream canned-but-parameterized analyses (real data from the world woven in).

## Design system (frontend) — MERIDIAN lapis-night+gold

Dark theme. Tokens in `src/styles/tokens.css`:
`--bg #0B1020`, surfaces `#101731/#121A36`, hairline `rgba(128,156,255,.13)`,
`--gold #E8B44A` (brand/CTA), `--teal #3AC6B4` (governance/positive), `--blue #7A9BFF` (info),
ok `#3DD68C`, warn `#F2B14C`, err `#F06A6A`. Radial glows + faint noise grain on body.
Fonts via @fontsource: Bricolage Grotesque (display/KPIs, 700/800 tight), Instrument Sans (body),
JetBrains Mono (versions, timestamps, tags, console). Numbers use tabular-nums.
Shell: fixed 232px sidebar (brand block, sectioned nav, governance badge footer) + topbar
(breadcrumb, env pill, live clock, user chip w/ role pills; dev-mode role switcher).
Components in `src/components/ui/`: Card, KpiTile (count-up), Chip (status dot), Tag (mono),
DataTable (uppercase micro-headers, hover), EventRow (slide-in), Console (typed lines),
StepList (progress steps), AiCard (gold-tinted ✦, confidence), Drawer, Pager, SegmentedControl,
Sparkbar. Motion: staggered reveal on page load, count-up, typewriter for AI; respect
prefers-reduced-motion.

## Non-negotiable server-side safeguards (from original)

Read-only PG connections for reads; identifier allow-listing before table interpolation;
parameterized SQL; git password redaction in any surfaced output; path-traversal guards on any
file read; commit rollback on push failure; QC-role+qc_team gate on Release triggers; misclick-safe
confirmation flow for all pipeline triggers (client confirms, server re-validates); secrets only
from env/Vault, never in code or image; container refuses AUTH_MODE=none unless
ALLOW_INSECURE_NO_AUTH=true.
