# ⚡ QuestOps — the team alignment deck

A gamified, AI-powered harness for a DevOps/platform team: one screen that answers
**"what should I do right now?"**, keeps the present and the past visible, and turns the
grind (tickets, red builds, reviews) into XP, quests, streaks and badges.

- **Overview** — the landing page: the whole picture summarized (board counts,
  objectives, red/stuck builds, KPI success %, pending approvals, team pulse,
  latest activity), every number linking into its detail page.
- **Quick add** — a global "+ Ticket" button drops work into the pool (assigned
  or unassigned) with importance/urgency presets that map to Jira priority + due date.
- **Focus feed** — Jira tickets, Jenkins failures/long-runners and pending approvals
  merged into one ranked list, each with a "why now" reason.
- **Gamification** — XP per action, levels & ranks, daily quests with bonuses, 🔥 streaks,
  badge wall, weekly leaderboard and team recap. History is a first-class timeline
  (every XP event *is* an activity record). Tickets closed **directly in Jira** are
  reconciled in too (deduped by key, credited to the assignee, backdated to the Jira
  resolution date) so stats, quests and achievements never miss them.
- **AI copilot (local Ollama)** — daily briefing, contextual chat over your live
  Jira/Jenkins state, prompt-template refinement, and drafting of repo changes.
- **Jira Data Center** — one project, PAT auth: board, transitions (drag & drop),
  comments, claim-a-ticket.
- **Jenkins** — recent failures and long-running (possibly stuck) builds; "I'm on it"
  claims; the fix bounty only pays out when Jenkins reports green. A red pipeline is
  any job with a failed run **in the window** — a later green run (another project on
  the same pipeline) never hides an earlier failure; those rows carry a "latest ✓" chip.
- **Failure Dive** — 🔎 on any failure pulls the console log (error lines highlighted)
  and reads the job's config.xml to find the pipeline-from-SCM scriptPath, resolving
  the groovy source from your defined **Engine** repository (or whichever defined repo
  matches the job's SCM URL). AI root-cause guidance (symptom → evidence → root cause
  → fix steps) runs **only after you confirm** — log + groovy + known error patterns
  go to your local Ollama; offline it degrades to heuristic error extraction.
- **Repositories, defined from the UI** — add repos from your ADO instance (browse or
  paste a URL; config holds only the shared ADO credentials). **Every member gets
  their own git worktree** (shared objects, isolated files) so edits never overlap;
  the page auto-watches the server (throttled fetch each minute) and banners new
  commits with one-click "update my workspace", shows commit history (whole repo or
  current file) with per-commit diffs on demand, and colorizes your local diffs.
  Nothing is ever pushed. Each repo gets a **tech scan**
  (deterministic detection of Python/Node/Docker/Helm/Jenkins/Terraform/… with
  concrete recommendations) and a **repo agent** — a LangChain agent on your Ollama
  that explores with whitelisted read-only commands (ls/grep/find/git log…).
  **Every command the agent proposes waits for human approval before it runs**, and
  every proposal/decision/output is logged to the database (agent audit log on the
  page). Write tools (local workspace only) appear only when you flip "enable write
  actions" — and each write still needs its own approval.
- **Dependency matrix** — static analysis of the Engine repo: pipelines (groovy
  roots) → playbooks / roles (units, with their tasks) / scripts, following full
  paths, filename tokens, `podman_run_script.sh` / `podman_run_playbook.sh` caller
  arguments and ansible semantics (`roles:`, `include_role`, `import_playbook`,
  role dependencies). Per-pipeline dependency trees, a **used vs unused** report
  (cleanup candidates), a searchable node matrix, and ambiguous/dynamic-call notes.
- **Access Management** — one page for who-can-do-what across the WHOLE ADO instance
  (all collections), Jira and Jenkins: ADO per-project teams + per-repository ACLs
  (allow/deny bitmasks decoded; grants to the QuestOps service account hidden; deep
  links to projects/repos), Jira permission schemes with every assigned project
  (paginated over all projects — no cap), per-holder grants, JIRAUSER-keyed user
  grants flagged, and links to each scheme and project, and Jenkins matrix-based
  RBAC including the GLOBAL strategy (root config.xml) plus per-job/folder matrices.
  Aggressively cached (15 min, lazy per-project expansion, bounded-parallel fetches).
- **Upgrade checker** — detects the running version of each integration (Jenkins,
  Elasticsearch, Jira, PostgreSQL, Ollama) and compares it against the latest LTS /
  supported line (endoflife.date / GitHub releases); one click files a prioritized
  upgrade ticket into the pool (EOL → Highest).
- **Repo actions with a human gate** — a saved prompt template + params → AI drafts a
  plan + full file contents → an **approver** (LDAP group) reviews the diff → only then
  is anything cloned, committed and pushed (to a branch, never to main). Actions can
  only target repositories defined on the Repositories page.
- **Prompt templates** — visible, editable, `{{variable}}`-parameterized, improvable via
  AI (proposal first, human saves).
- **LDAP auth** — one team group gates login and defines the roster (everyone appears on
  the leaderboard); per-username roles default to approver, `MEMBER_USERNAMES` demotes.

## Run it locally (demo mode, zero external services)

```bash
cd questops
./dev.sh start        # wraps podman-compose (or docker compose), waits for health
# open http://localhost:8080  →  alice, bob, carol or dave / password: demo
./dev.sh stop|restart|status|logs|reset
```

Requires podman ≥ 4 (or docker) — podman 3.x/CNI can't resolve service names the
way podman-compose wires them. `docker compose up --build` works too; `dev.sh` just
adds health-waiting, stale-port cleanup and no-systemd (WSL/CI) handling.

Demo mode seeds a 4-person team (1 leader + 3 members — add the 4th in
`backend/app/auth.py:DEMO_USERS`), 3 weeks of history, a Jira board, Jenkins jobs and a
pending repo action. The only external call is Ollama (`host.docker.internal:11434` by
default); if it's down, every AI feature degrades to a deterministic fallback.

Bundled Ollama instead of a host one:

```bash
docker compose --profile ollama up --build
docker compose exec ollama ollama pull llama3.1
# and set OLLAMA_URL=http://ollama:11434 in .env
```

Without Docker: `cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 8080`
(uses SQLite).

## Go live

Copy `.env.example` → `.env`, set `DEMO_MODE=false` and fill Jira/Jenkins/LDAP/Git
credentials. Live-mode behavior per integration is in `backend/app/integrations/`.

| Concern | Config |
|---|---|
| Ollama | `OLLAMA_URL`, `OLLAMA_MODEL` |
| Jira DC (one project) | `JIRA_BASE_URL`, `JIRA_USER` + `JIRA_PASSWORD` (basic auth), `JIRA_PROJECT_KEY`, `JIRA_BOARD_STATUSES` |
| Jenkins | `JENKINS_URL`, `JENKINS_USER`, `JENKINS_TOKEN`, `JENKINS_LONG_RUNNING_FACTOR`, `JENKINS_FAILURE_WINDOW_DAYS`, `JENKINS_IGNORE` |
| Elasticsearch | `ES_URL`, `ES_API_KEY`, `JENKINS_KPI_INDEX` (+ `KPI_SYNC_MINUTES`, `TZ` for the load countdown), `ERROR_ANALYSIS_INDEX`, `ERROR_ANALYSIS_DAYS` |
| LDAP | `LDAP_URL`, service `LDAP_BIND_DN`/`LDAP_BIND_PASSWORD`, `LDAP_BASE_DN`, `LDAP_REQUIRED_GROUP` (one team group: login + roster), `MEMBER_USERNAMES` (everyone else is approver) |
| Repositories page | `ADO_URL` (ADO **instance** root — collections are enumerated), `ADO_USER`, `ADO_PASSWORD` (git clone/pull), `ADO_PAT` (REST browse; each falls back to the other) — repos added from the UI with a collection filter |
| Upgrade checker | `UPGRADES_PROXY` (corporate proxy for the *only* outbound-internet calls), `UPGRADES_VERIFY_SSL`, `EOL_API_BASE` / `GITHUB_API_BASE` (internal mirrors) |
| Repo actions | `GIT_TOKEN` (https push), `GIT_USER_NAME`, `GIT_USER_EMAIL` |

## Deploy with Helm

```bash
helm upgrade --install questops helm/questops \
  -n platform-tools --create-namespace \
  --set image.repository=registry.mycorp.local/platform/questops \
  --set ai.ollamaUrl=http://ollama.ai-tools.svc:11434 \
  --set ai.ollamaModel=llama3.1 \
  --set app.existingSecret=questops-secrets \
  --set ingress.enabled=true --set ingress.host=questops.mycorp.local
```

`values.yaml` mirrors the env table above (`ai.*`, `jira.*`, `jenkins.*`, `ldap.*`,
`git.*`). Point `database.url` at a managed Postgres for real use;
`database.builtin.enabled=true` runs a tiny in-cluster one for trials. For production,
create a Secret (`SECRET_KEY`, `DATABASE_URL`, `JIRA_PAT`, `JENKINS_TOKEN`,
`LDAP_BIND_PASSWORD`, `GIT_TOKEN`) and set `app.existingSecret`.

## Architecture

```
frontend/            static SPA (no build step) — served by the API container
backend/app/
  main.py            FastAPI app + static mount
  auth.py            LDAP (group-gated) or demo login → JWT
  gamification.py    XP rules, levels/ranks, badges, daily quests, streaks
  routers/           focus/board/ci · leaderboard/history/recap · prompts · actions · ai
  integrations/      jira.py · jenkins.py · ollama.py · gitops.py (each has a demo twin)
helm/questops/       chart (configmap+secret+deployment+service+ingress+optional pg)
```

Design decisions worth knowing:

- **Approval gate is server-side**: `POST /api/actions/{id}/approve` requires the
  approver role; execution only ever runs on an `approved` row. The AI can draft, never
  push.
- **XP is verified where possible**: "I fixed the build" is rejected unless Jenkins
  actually reports the job green (live mode).
- **Everything degrades gracefully**: demo mode for Jira/Jenkins/LDAP, rule-based
  fallbacks for AI — so the platform is demoable to stakeholders with `docker compose up`.

## Roadmap ideas

Webhooks (Jenkins → instant failure quests), Jira sprint burndown quest, seasonal
leaderboard resets, per-team theming, opening PRs automatically after push,
SSO (OIDC) in front of LDAP.
