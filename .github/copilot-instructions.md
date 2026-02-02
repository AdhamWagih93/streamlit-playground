# Copilot instructions (Best Streamlit Website)

## Big picture (what this repo is)
- The product is a multi-page Streamlit “platform” UI backed by multiple MCP servers (FastMCP) and a Postgres DB (see docker compose in docker-compose.yml).
- Streamlit entrypoint: best-streamlit-website/app.py builds top navigation via `st.navigation()` using the catalog in best-streamlit-website/src/page_catalog.py.
- Pages live in best-streamlit-website/pages/ and typically call `set_theme(...)` first (best-streamlit-website/src/theme.py). Global CSS is best-streamlit-website/assets/custom_theme.css.

## Config + data (env-first + admin overrides)
- Runtime/UI config is env-first with safe defaults via best-streamlit-website/src/streamlit_config.py (`StreamlitAppConfig.load()`).
- Non-secret admin overrides (page visibility, MCP server toggles, non-secret URLs) persist to best-streamlit-website/data/admin_config.json via best-streamlit-website/src/admin_config.py.
- When editing settings UI/query params, follow the compatibility helpers in best-streamlit-website/src/settings_ui.py (don’t blindly clear all query params).

## MCP integration (how tool calls work)
- Prefer best-streamlit-website/src/mcp_client.py for Streamlit→MCP calls (JSON-RPC over “streamable-http”, handles SSE responses, caches tool lists).
- URL normalization: Streamlit-side config treats `http` as `streamable-http` and normalizes URLs to include `/mcp` (see `_normalise_streamlit_*` in best-streamlit-website/src/streamlit_config.py).
- Many servers rely on a shared client token (env-driven); admin config is intentionally “non-secret”.

## Persistence layers
- Tasks: use best-streamlit-website/src/tasks_repo.py as the mutation/persistence boundary (SQLAlchemy model `Task`, JSON-in-text fields, `init_db()` lightweight migrations). Default DB uses `POSTGRES_*` unless `DATABASE_URL` is set.

## Developer workflows (use what the repo already provides)
- Recommended local stack: scripts/dev-start.ps1 (Windows) or scripts/dev-start.sh (Linux/macOS). It runs `docker compose -f docker-compose.yml -f docker-compose.dev.yml up` with profiles.
- Direct UI run (no compose): from best-streamlit-website/ run `python -m streamlit run app.py`.
- Scheduler MCP (no compose): VS Code tasks in best-streamlit-website/.vscode/tasks.json run `python -m src.scheduler.main` (and there’s a combined “Dev: Run Streamlit + Scheduler”).
- Tests: this repo has lightweight tests under tests/ (imports expect `src` to be importable; running from best-streamlit-website/ is the most reliable).

## Deployment layout
- Deployables are split under best-streamlit-website/deploy/ (streamlit, scheduler, MCP servers, agents). Avoid mixing requirement sets unless you intend a monolith.
