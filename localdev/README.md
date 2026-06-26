# Local dev / CI harness

Test the CI/CD dashboard **on your laptop and in CI with no VPN, no Docker, and
no access to Elasticsearch / ADO / Postgres / Jenkins / Ollama / Jira.**

It works by replacing the platform `utils` seam (the only place the dashboard
reaches out) with self-contained fakes, redirecting git clones to local seeded
repos, and injecting an admin session so every feature is visible.

## What's faked vs real

| Dependency | Real in prod | Local harness |
|---|---|---|
| Elasticsearch (`utils.elasticsearch.es_prd`) | live cluster | **FakeES** — valid empty responses + optional JSON fixtures |
| Vault (`utils.vault.VaultClient`) | platform vault | reads `secrets.local.json` |
| ADO git repos (clone over http) | ADO Server | **local git** under `gitsrv/` (redirected via `git insteadOf`) |
| Postgres | live DB | *unconfigured* → features degrade gracefully (or point `secrets.local.json` at a local PG you run) |
| S3 / Jenkins / Ollama / ADO REST / LDAP | live | stubbed / unconfigured → graceful empty states |
| Login session | front.py | injected admin session |

The Inventory, Architecture, Config-editor and Control-repos surfaces read from
the **seeded local git**, so they show real data. ES-backed tiles (security,
jira, builds aggregations) render but are mostly empty until you add fixtures.

## Quick start

```bash
# 1. seed local git repos + ES fixtures (idempotent; re-run after editing seeds)
python localdev/seed_git.py
python localdev/seed_es_fixtures.py

# 2a. run it interactively as admin (with realistic fake data)
streamlit run localdev/front_local.py

# 2b. automated smoke test — renders every admin tab, fails on any exception
pytest localdev/test_smoke.py -q

# 2c. screenshot every tab with a real browser (Playwright)
pip install playwright && playwright install chromium   # one-time
python localdev/screenshot.py        # → localdev/screenshots/*.png

# 2d. run the WHOLE CI and report to Discord (like jira/ci)
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/…" python localdev/run_ci.py
python localdev/run_ci.py --dry-run   # prints the Discord payload, doesn't send
python localdev/run_ci.py --no-screens # skip the browser step
```

`run_ci.py` runs compile → seed → smoke → screenshots, writes
`localdev/ci_report/report.{json,md}`, and posts a pass/fail embed (+ report.md
and the tab screenshots) to Discord when `DISCORD_WEBHOOK_URL` is set — the same
env var / convention the `jira/ci` pipeline uses. GitHub Actions passes it from
the `DISCORD_WEBHOOK_URL` repo secret.

Requires only: `pip install streamlit pytest pyyaml pandas playwright` (+
whatever else your `cicd_dashboard.py` imports at module load). No
`elasticsearch` package needed — the fake replaces it.

`seed_es_fixtures.py` writes per-index JSON the **FakeES computes aggregations
over** (terms / composite / filter / top_hits / metrics / date_histogram), so
tiles, charts and tables populate with realistic data from one dataset — names
match `seed_git.py`. Edit the entity table at the top of `seed_es_fixtures.py`
and re-run to reshape the data.

## CI

`.github/workflows/ci.yml` runs the same smoke test on every push/PR. It would
have caught the architecture-diff `int < None` crash before it ever reached the
VPN. Extend `test_smoke.py` with assertions on specific tabs as you go.

## Adding data fidelity

- **ES data:** drop `localdev/fixtures/<index>.json` (a list of `_source`
  docs, e.g. `ef-bs-jira-issues.json`) — FakeES returns them as hits.
  Add `localdev/fixtures/<index>.aggs.json` (`{agg_name: agg_result}`) for
  canned aggregation tiles.
- **Git data:** edit `seed_git.py` (inventory tree, Control `config.yml`s,
  mirror repos) and re-run it. The inventory tree is best-effort — tweak it to
  match your real `inventories` repo layout if rows don't parse.
- **Postgres (Teams + Architecture data):** the Teams tab reads members from
  `ldap_users`/`ldap_team_members`. Run a local PG and seed it:
  ```bash
  docker run -d -e POSTGRES_USER=devops -e POSTGRES_PASSWORD=devops \
    -e POSTGRES_DB=devops -p 5432:5432 postgres:16
  LOCALDEV_PG_HOST=localhost python localdev/seed_pg.py
  LOCALDEV_PG_HOST=localhost streamlit run localdev/front_local.py
  ```
  The vault shim picks up `LOCALDEV_PG_HOST/PORT/DB/USER/PASSWORD` (unset → the
  tab stays empty, no error). CI runs a throwaway `postgres:16` service and
  seeds it automatically, so the Teams + Architecture screenshots show data.

## How the seam is injected

`front_local.py` (and `test_smoke.py`) put `localdev/` first on `sys.path`, so
`import utils...` / `from mypages.cc_docchat import ...` resolve to the shims
here, then run the **real repo-root `cicd_dashboard.py`** verbatim — your edits
are what gets tested. Git redirection uses process-scoped `GIT_CONFIG_*` env
vars (no global git config is touched).
