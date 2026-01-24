# AI Coding Agent Instructions (best-streamlit-website)

## Documentation-first rule (applies to all tasks)
- **Follow official documentation first**, then match existing patterns in this repo.
- When uncertain about an API/flag/behavior, **prefer the official docs** over guesses.
- When integrating a new tool, **add a small “works out-of-the-box” path**:
	- env vars and defaults
	- a minimal smoke test (CLI command or a tiny page/button)
	- clear error messages and hints

### Official docs (primary references)
- Streamlit: https://docs.streamlit.io/
- FastMCP: https://github.com/jlowin/fastmcp (and any official FastMCP docs site used by the project)
- LangChain (Python): https://python.langchain.com/
- LangGraph: https://langchain-ai.github.io/langgraph/
- Kubernetes: https://kubernetes.io/docs/
- Kubernetes Python client: https://github.com/kubernetes-client/python
- Docker: https://docs.docker.com/
- Helm: https://helm.sh/docs/

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

## Streamlit development standards (beauty + performance)
Use Streamlit’s official APIs and patterns as much as possible.

### Page structure
- Call `set_theme(...)` at the very top of each page module.
- Prefer modern layout primitives:
	- `st.sidebar`, `st.columns`, `st.container`, `st.tabs`, `st.expander`
	- `st.form` for grouped inputs (reduces reruns)
	- `st.dataframe`/`st.data_editor` with `column_config` for rich tables
- Use `use_container_width=True` for responsive visuals.
- Use `st.session_state` for UI state instead of global variables.

### Performance
- Cache intentionally:
	- `st.cache_data` for pure data transforms / expensive computations
	- `st.cache_resource` for clients (K8s, Docker, HTTP sessions) that are expensive to create
- Minimize reruns and expensive work on every interaction:
	- move long operations behind buttons/forms
	- show progress via `st.spinner`, `st.status`, `st.progress`
- Keep network calls robust and fast:
	- timeouts
	- friendly errors + hints
	- avoid repeated calls (cache + memoization)

### UI/UX quality bar
- Aim for a “product-grade” page:
	- clear hierarchy: title → summary → main actions → details
	- good defaults and validation
	- graceful empty states
	- consistent styling using [assets/custom_theme.css](../assets/custom_theme.css)
- Prefer Streamlit’s built-in widgets and `column_config` over heavy custom HTML.
- If custom HTML/CSS is required, keep it minimal and compatible with Streamlit updates.

### Streamlit references
- API reference: https://docs.streamlit.io/develop/api-reference
- Caching: https://docs.streamlit.io/develop/concepts/architecture/caching
- Session state: https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state
- Multipage apps: https://docs.streamlit.io/develop/concepts/multipage-apps

## MCP/Agents integration (don’t break these contracts)
- MCP tools commonly require `_client_token` (see Kubernetes auth in [src/ai/mcp_servers/kubernetes/mcp.py](../src/ai/mcp_servers/kubernetes/mcp.py)); tokens are env-configured (e.g., `JENKINS_MCP_CLIENT_TOKEN`, `KUBERNETES_MCP_CLIENT_TOKEN`).
- Streamlit can run MCP servers via stdio or connect remotely via URL; overrides are `STREAMLIT_*_MCP_TRANSPORT` / `STREAMLIT_*_MCP_URL` (see [src/streamlit_config.py](../src/streamlit_config.py)).
- Kubernetes MCP re-exposes Helm tools (same endpoint) — keep Helm+K8s tool naming/dispatch tolerant (pages match tools by suffix).
- Agents use Ollama via `OLLAMA_BASE_URL` / `OLLAMA_MODEL` (see [src/ai/agents/tool_agent_runner.py](../src/ai/agents/tool_agent_runner.py)).

## FastMCP server development standards
- Follow FastMCP documentation and existing server patterns in [src/ai/mcp_servers/](../src/ai/mcp_servers).
- Prefer small, composable tools with clear input/output schemas.
- Return structured JSON-friendly dicts with explicit `ok: bool` and actionable errors.
- Keep tool execution safe:
	- validate file paths and restrict to allowed directories (see existing pattern in Kubernetes MCP)
	- set timeouts for subprocess calls
	- avoid leaking secrets/tokens in logs or tool results
- Keep transports configurable via env vars, consistent with `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, and `STREAMLIT_*_MCP_URL`.
- Token/auth pattern:
	- support `_client_token` in tool args when appropriate
	- compare against an env-configured expected token

### FastMCP references
- FastMCP repo/docs: https://github.com/jlowin/fastmcp

## Agent development standards (LangChain + LangGraph)
- Prefer official LangChain + LangGraph docs for architecture and API usage.
- Keep agent code observable and debuggable:
	- capture tool-call events (see current interceptor pattern)
	- redact secrets (tokens, API keys)
- Keep model/runtime configuration env-driven (base URLs, model names, temperatures).
- Favor deterministic tool routing and structured outputs where possible.
- When workflows become multi-step or stateful, prefer LangGraph graphs over ad-hoc loops.

### LangChain/LangGraph references
- LangChain docs: https://python.langchain.com/
- LangGraph docs: https://langchain-ai.github.io/langgraph/

## Kubernetes / Docker / other tool integrations
- Treat each external system as an integration with:
	- env-first config
	- connectivity/health check
	- timeouts + retries where appropriate
	- clear UX in Streamlit pages (status, errors, next steps)

### Kubernetes guidance
- Prefer the official Kubernetes API patterns and safe defaults.
- When shelling out to `kubectl`/`helm`, validate inputs and constrain file access.
- Provide both:
	- a “high-level API” tool (structured params)
	- and an optional “raw” tool guarded by an allowlist/flag (similar to `HELM_ALLOW_RAW`)

### Docker guidance
- Prefer Docker’s official guidance and secure defaults.
- Avoid long-running blocking calls in the UI; wrap in button-triggered actions.
- When calling Docker via SDK/CLI, set timeouts and sanitize user-supplied args.

### General integration references
- Kubernetes docs: https://kubernetes.io/docs/
- Docker docs: https://docs.docker.com/
- Helm docs: https://helm.sh/docs/

## Deployment touchpoints
- Helm chart injects runtime env vars for Streamlit (example: [deploy/helm/best-streamlit-website/templates/streamlit.yaml](../deploy/helm/best-streamlit-website/templates/streamlit.yaml)). Keep env var names stable when changing config.
- Deployables have separate requirements under `deploy/*` (MCP servers / agents / UI).
