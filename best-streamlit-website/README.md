# Best Streamlit Website

Welcome to the Best Streamlit Website project! This application is designed to provide an interactive and user-friendly experience with multiple pages, including a home page, an about page, and a dashboard.

## Project Structure

The project is organized as follows:

```
best-streamlit-website
├── pages
│   ├── 1_Home.py         # Home page of the application
│   ├── 2_About.py        # Information about the project
│   └── 3_Dashboard.py     # Interactive dashboard with visualizations
├── src
│   ├── auth.py           # User authentication functions
│   ├── theme.py          # Theme settings for the application
│   └── utils.py          # Utility functions for the application
├── assets
│   └── custom_theme.css   # Custom CSS styles for the application
├── .streamlit
│   ├── config.toml       # Configuration settings for Streamlit
│   └── secrets.toml      # Secure storage for sensitive information
├── requirements.txt       # List of required Python packages
└── README.md              # Documentation for the project
```

## Features

- **Multi-page Application**: Navigate through different pages seamlessly.
- **User Authentication**: Secure login functionality for users.
- **Custom Themes**: Enhanced visual appearance with custom themes and styles.
- **Interactive Dashboard**: Visualizations and data displays tailored to user needs.

## Setup Instructions

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/best-streamlit-website.git
   cd best-streamlit-website
   ```

2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

3. Run the Streamlit application (from the project root):
    ```powershell
    python -m streamlit run .\app.py
    ```

## Scheduler service (local dev)

The MCP scheduler runs as a separate process (it does not run inside Streamlit). Locally, it uses SQLite at `data/scheduler.db` by default.

- Start the scheduler MCP server (recommended):
   - VS Code Task: **Dev: Run Scheduler MCP** (or **Dev: Run Streamlit + Scheduler**)
   - PowerShell helper: `./scripts/start_scheduler_background.ps1`

When using the repo-local SQLite scheduler DB, the scheduler auto-creates a small set of default jobs if the DB is empty (disable with `SCHEDULER_BOOTSTRAP_JOBS=false`).

## Configuration (env-first with local defaults)

This repo uses small Python config modules that read from environment variables first and fall back to local-dev defaults when env vars are missing.

- Streamlit UI config: `src/streamlit_config.py`
- Jenkins MCP server config: `src/ai/mcp_servers/jenkins/config.py`
- Kubernetes MCP server config: `src/ai/mcp_servers/kubernetes/config.py`
- Agent configs:
   - Jenkins tool-agent: `src/ai/agents/jenkins_agent_config.py`
   - Kubernetes tool-agent: `src/ai/agents/kubernetes_agent_config.py`
   - DataGen agent: `src/ai/agents/datagen_agent_config.py`

Common env vars you may want to set for deployments:

- Jenkins MCP:
   - `JENKINS_BASE_URL`
   - `JENKINS_VERIFY_SSL`
   - `JENKINS_USERNAME` / `JENKINS_API_TOKEN` (server-side)
   - `JENKINS_MCP_CLIENT_TOKEN` (shared secret between MCP server and clients)
- Kubernetes MCP:
   - `K8S_KUBECONFIG`
   - `K8S_CONTEXT`
- Agents / Ollama:
   - `OLLAMA_BASE_URL`
   - `OLLAMA_MODEL`
   - `OLLAMA_TEMPERATURE`

## Deployment-specific requirements

If you plan to deploy Streamlit, MCP servers, and agents separately, install dependencies from the corresponding requirements file:

- Streamlit UI: `deploy/streamlit/requirements.txt`
- Jenkins MCP server: `deploy/mcp-jenkins/requirements.txt`
- Kubernetes MCP server: `deploy/mcp-kubernetes/requirements.txt`
- Tool agents (MCP tools): `deploy/agents/tool-agent/requirements.txt`
- DataGen agent: `deploy/agents/datagen/requirements.txt`

### Enable auto re-run on code changes

This project is configured to automatically re-run when you save changes to Python files.

- The configuration lives in `.streamlit/config.toml`:
   - `server.runOnSave = true`
   - `server.fileWatcherType = "watchdog"`
- If you prefer a different port:
   ```powershell
   python -m streamlit run .\app.py --server.port 8502
   ```
- On Windows PowerShell, you can join flags on one line as shown above.

Troubleshooting:

- If auto-reload doesn’t trigger, ensure `watchdog` is installed:
   ```powershell
   pip install watchdog
   ```
- For network drives or WSL, switching to polling may help. Edit `.streamlit/config.toml`:
   ```toml
   [server]
   fileWatcherType = "poll"
   ```

## Usage Guidelines

- Access the home page to get started.
- Navigate to the about page for more information about the project.
- Use the dashboard to interact with data visualizations.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any suggestions or improvements.

## License

This project is licensed under the MIT License. See the LICENSE file for details.