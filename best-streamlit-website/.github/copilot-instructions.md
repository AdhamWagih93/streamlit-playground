# AI Coding Agent Instructions (best-streamlit-website)

## Quick start (local)
- Run from repo root: `python -m streamlit run .\app.py`
- Auto-reload is enabled in [.streamlit/config.toml](../.streamlit/config.toml) (`watchdog`, `runOnSave=true`). Prefer “save → rerun” over manual reload code.

## Big picture
- Multi-page Streamlit UI: landing page [app.py](../app.py); feature pages in [pages/](../pages) (Tasks, DevOps CV parsing, WFH schedule, DataGen agent, Agent/MCP management, Kubernetes, setup, Docker MCP test).
- Shared business logic lives in [src/](../src): theming ([src/theme.py](../src/theme.py)), tasks DB ([src/tasks_repo.py](../src/tasks_repo.py)), resume parsing ([src/resume_parser.py](../src/resume_parser.py)), UI/runtime config ([src/streamlit_config.py](../src/streamlit_config.py)).
- AI tooling is first-class: [src/ai/](../src/ai) contains LangChain+Ollama agents and FastMCP servers (Jenkins/Kubernetes/Docker/SonarQube).

## Project conventions to follow
- **Theming/CSS**: call `set_theme(...)` once near the top of each page; global styles live in [assets/custom_theme.css](../assets/custom_theme.css).
- **Env-first config**: prefer `StreamlitAppConfig.from_env()` ([src/streamlit_config.py](../src/streamlit_config.py)) over ad-hoc `os.environ` access in pages.
- **Tasks = repository source of truth**: all mutations go through [src/tasks_repo.py](../src/tasks_repo.py) (`create_task`, `update_task_status`, checklist/comment helpers). If you add DB fields, also update `init_db()` migration logic.
- **WFH schedule invariants**: keep `validate_schedule()` in [pages/3_WFH_Schedule.py](../pages/3_WFH_Schedule.py) passing. Persisted week overrides live under `data/wfh_schedules/2026/` and should be edited via `load_week()/save_week()`.

## MCP/Agents integration (don’t break these contracts)
- MCP tools commonly require `_client_token` (see Kubernetes auth in [src/ai/mcp_servers/kubernetes/mcp.py](../src/ai/mcp_servers/kubernetes/mcp.py)); tokens are env-configured (e.g., `JENKINS_MCP_CLIENT_TOKEN`, `KUBERNETES_MCP_CLIENT_TOKEN`).
- Streamlit can run MCP servers via stdio or connect remotely via URL; overrides are `STREAMLIT_*_MCP_TRANSPORT` / `STREAMLIT_*_MCP_URL` (see [src/streamlit_config.py](../src/streamlit_config.py)).
- Kubernetes MCP re-exposes Helm tools (same endpoint) — keep Helm+K8s tool naming/dispatch tolerant (pages match tools by suffix).
- Agents use Ollama via `OLLAMA_BASE_URL` / `OLLAMA_MODEL` (see [src/ai/agents/tool_agent_runner.py](../src/ai/agents/tool_agent_runner.py)).

## Deployment touchpoints
- Helm chart injects runtime env vars for Streamlit (example: [deploy/helm/best-streamlit-website/templates/streamlit.yaml](../deploy/helm/best-streamlit-website/templates/streamlit.yaml)). Keep env var names stable when changing config.
- Deployables have separate requirements under `deploy/*` (MCP servers / agents / UI).
