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
# 1. one-time: seed the local git repos (idempotent; re-run after editing seed_git.py)
python localdev/seed_git.py

# 2a. run it interactively as admin
streamlit run localdev/front_local.py

# 2b. or run the automated smoke test (renders every admin tab, fails on any exception)
pytest localdev/test_smoke.py -q
```

Requires only: `pip install streamlit pytest pyyaml pandas` (+ whatever else
your `cicd_dashboard.py` imports at module load). No `elasticsearch` package
needed — the fake replaces it.

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
- **Postgres:** run a local PG (`docker run -e POSTGRES_PASSWORD=devops -p
  5432:5432 postgres:16`), fill in the `postgres` block of
  `secrets.local.json`, and create the tables/rows the History/Teams tabs read.

## How the seam is injected

`front_local.py` (and `test_smoke.py`) put `localdev/` first on `sys.path`, so
`import utils...` / `from mypages.cc_docchat import ...` resolve to the shims
here, then run the **real repo-root `cicd_dashboard.py`** verbatim — your edits
are what gets tested. Git redirection uses process-scoped `GIT_CONFIG_*` env
vars (no global git config is touched).
