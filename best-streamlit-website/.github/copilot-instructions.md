# Copilot instructions (Best Streamlit Website)

## Big picture (how the app hangs together)
- Streamlit entrypoint is [best-streamlit-website/app.py](../app.py): builds grouped top-nav via `st.navigation()` from [best-streamlit-website/src/page_catalog.py](../src/page_catalog.py).
- Pages live in [best-streamlit-website/pages/](../pages) and should call `set_theme(...)` at the top (see [best-streamlit-website/src/theme.py](../src/theme.py)); global CSS is [best-streamlit-website/assets/custom_theme.css](../assets/custom_theme.css).
- UI/runtime config is env-first, with non-secret admin overrides persisted to `data/admin_config.json` via [best-streamlit-website/src/admin_config.py](../src/admin_config.py) and applied in [best-streamlit-website/src/streamlit_config.py](../src/streamlit_config.py).
- MCP is a first-class integration: servers in [best-streamlit-website/src/ai/mcp_servers/](../src/ai/mcp_servers) (FastMCP) and a Streamlit-side HTTP client in [best-streamlit-website/src/mcp_client.py](../src/mcp_client.py).

## Developer workflows (use repo scripts)
- Recommended: `./scripts/dev-start.ps1` (Windows) or `./scripts/dev-start.sh` (Linux/macOS). Compose maps host `:8502` → container `:8501` for Streamlit.
- Direct UI run: from [best-streamlit-website/](../) run `python -m streamlit run .\\app.py` (auto-rerun is enabled in [best-streamlit-website/.streamlit/config.toml](../.streamlit/config.toml)).
- Deployment has per-component deps under `deploy/*/requirements.txt` (don’t mix them unless you intend to run the monolith).

## Project-specific conventions (follow these)
- Navigation is data-driven: add/remove pages by editing [best-streamlit-website/src/page_catalog.py](../src/page_catalog.py). Page visibility is controlled by `data/admin_config.json` (loaded in [best-streamlit-website/src/admin_config.py](../src/admin_config.py)).
- Prefer config helpers over raw env access: use `StreamlitAppConfig.load()` (env + admin overrides) from [best-streamlit-website/src/streamlit_config.py](../src/streamlit_config.py).
- MCP URL/transport expectations:
  - `http` normalizes to `streamable-http` and the URL is normalized to include `/mcp` (see `*_normalise_streamlit_*` in [best-streamlit-website/src/streamlit_config.py](../src/streamlit_config.py)).
  - Many tools require `_client_token` that must match the server’s env-configured token.
- Tasks persistence: treat [best-streamlit-website/src/tasks_repo.py](../src/tasks_repo.py) as the mutation layer. Default DB is Postgres from `POSTGRES_*` (or override with `DATABASE_URL`); schema changes must update `init_db()` lightweight migrations.
- WFH schedules are file-backed under `data/wfh_schedules/2026/` with holidays in `data/holidays_2026.json` + `data/public_holidays_2026.json`; keep validation helpers in [best-streamlit-website/pages/3_WFH_Schedule.py](../pages/3_WFH_Schedule.py) passing when changing scheduling logic.

## Primary docs to trust when unsure
- Streamlit: https://docs.streamlit.io/
- FastMCP: https://github.com/jlowin/fastmcp
- MCP (Python): https://pypi.org/project/mcp/
- LangChain / LangGraph: https://python.langchain.com/ and https://langchain-ai.github.io/langgraph/
