# SonarQube MCP Server

Comprehensive Model Context Protocol (MCP) server for SonarQube code analysis platform. Provides AI assistants with full access to SonarQube API for code quality analysis, security scanning, project management, and git repository analysis.

## Features

### Complete SonarQube API Coverage

- **System & Server Management**: Health checks, system status, server information
- **Project Management**: Create, list, delete, and configure projects
- **Code Analysis & Scanning**: Trigger analyses, scan git repositories, track analysis history
- **Quality Gates**: Manage and monitor quality gate statuses
- **Issue Management**: Search, filter, update, and comment on code issues
- **Metrics & Measures**: Access code quality metrics, coverage, complexity, and more
- **Security Scanning**: Track security hotspots, vulnerabilities, and security debt
- **Duplication Detection**: Monitor code duplication across projects
- **Rules Management**: Search and configure coding rules
- **Quality Profiles**: Manage and associate quality profiles with projects
- **User & Permission Management**: User administration and permission control
- **Component Navigation**: Browse project structure and source code
- **Webhooks**: Configure and manage webhook integrations

### Git Repository Scanning

Special functionality to scan git repositories directly:
- Clone repositories from any git URL
- Analyze specific branches
- Generate complete scan workflows
- Configurable scanner properties

## Installation

### Prerequisites

- Python 3.8+
- SonarQube server (v8.0+)
- SonarQube authentication token or username/password
- `sonar-scanner` CLI tool (for actual scanning operations)

### Python Dependencies

```bash
pip install fastmcp requests
```

## Configuration

Configure via environment variables:

```bash
# SonarQube Server Configuration
export SONARQUBE_BASE_URL="https://sonarqube.example.com"
export SONARQUBE_TOKEN="your_token_here"  # Recommended

# OR use username/password (less secure)
# export SONARQUBE_USERNAME="your_username"
# export SONARQUBE_PASSWORD="your_password"

# SSL Verification (default: true)
export SONARQUBE_VERIFY_SSL="true"

# MCP Configuration
export SONARQUBE_MCP_CLIENT_TOKEN="dev-sonarqube-mcp-token"
export SONARQUBE_MCP_TRANSPORT="stdio"  # or "http" or "sse"
export SONARQUBE_MCP_HOST="0.0.0.0"
export SONARQUBE_MCP_PORT="8002"
```

### Getting a SonarQube Token

1. Log into SonarQube web interface
2. Go to **User → My Account → Security**
3. Generate a new token
4. Copy and use as `SONARQUBE_TOKEN`

## Usage

### Starting the MCP Server

```python
from src.ai.mcp_servers.sonarqube import mcp

# Server auto-configures from environment variables
# Use with MCP-compatible clients
```

### Example Tool Calls

#### List All Projects

```python
result = sonarqube_list_projects(
    _client_token="dev-sonarqube-mcp-token",
    page=1,
    page_size=100,
    query="my-project"
)
```

#### Scan a Git Repository

```python
result = sonarqube_scan_git_repository(
    _client_token="dev-sonarqube-mcp-token",
    project_key="my-project",
    git_url="https://github.com/user/repo.git",
    branch="main",
    additional_properties={
        "sonar.sources": "src",
        "sonar.tests": "tests",
        "sonar.java.binaries": "target/classes"
    }
)

# Returns shell commands to execute:
# git clone https://github.com/user/repo.git /tmp/sonar-scan-my-project
# cd /tmp/sonar-scan-my-project
# git checkout main
# sonar-scanner -Dsonar.projectKey=my-project -Dsonar.host.url=... -Dsonar.login=... -Dsonar.sources=src -Dsonar.tests=tests -Dsonar.java.binaries=target/classes
```

#### Search Issues by Severity

```python
result = sonarqube_search_issues(
    _client_token="dev-sonarqube-mcp-token",
    component_keys=["my-project"],
    severities=["CRITICAL", "BLOCKER"],
    types=["BUG", "VULNERABILITY"],
    statuses=["OPEN", "REOPENED"]
)
```

#### Get Quality Gate Status

```python
result = sonarqube_get_quality_gate_status(
    _client_token="dev-sonarqube-mcp-token",
    project_key="my-project"
)
```

#### Get Code Coverage

```python
result = sonarqube_get_coverage(
    _client_token="dev-sonarqube-mcp-token",
    component_key="my-project"
)
```

#### Search Security Hotspots

```python
result = sonarqube_search_hotspots(
    _client_token="dev-sonarqube-mcp-token",
    project_key="my-project",
    statuses=["TO_REVIEW"]
)
```

## Available Tools (60+ Tools)

### System & Server (4 tools)
- `sonarqube_get_system_status`
- `sonarqube_get_system_health`
- `sonarqube_ping_server`
- `sonarqube_get_system_info` (admin only)

### Project Management (5 tools)
- `sonarqube_list_projects`
- `sonarqube_create_project`
- `sonarqube_delete_project`
- `sonarqube_get_project_info`
- `sonarqube_update_project_visibility`

### Code Analysis & Scanning (4 tools)
- `sonarqube_trigger_analysis`
- `sonarqube_scan_git_repository` ⭐ **Git integration**
- `sonarqube_get_analysis_status`
- `sonarqube_get_analysis_history`

### Quality Gates (5 tools)
- `sonarqube_get_quality_gate_status`
- `sonarqube_list_quality_gates`
- `sonarqube_get_quality_gate`
- `sonarqube_create_quality_gate`
- `sonarqube_associate_quality_gate`

### Issue Management (8 tools)
- `sonarqube_search_issues`
- `sonarqube_get_issue`
- `sonarqube_add_comment_to_issue`
- `sonarqube_change_issue_status`
- `sonarqube_assign_issue`
- `sonarqube_set_issue_severity`
- `sonarqube_set_issue_type`

### Metrics & Measures (5 tools)
- `sonarqube_get_measures`
- `sonarqube_get_component_tree_measures`
- `sonarqube_list_metrics`
- `sonarqube_get_coverage`
- `sonarqube_get_duplications`

### Security (3 tools)
- `sonarqube_search_hotspots`
- `sonarqube_get_hotspot`
- `sonarqube_change_hotspot_status`

### Rules (2 tools)
- `sonarqube_search_rules`
- `sonarqube_get_rule`

### Quality Profiles (3 tools)
- `sonarqube_search_quality_profiles`
- `sonarqube_get_quality_profile`
- `sonarqube_associate_project_with_profile`

### Users & Permissions (4 tools)
- `sonarqube_list_users`
- `sonarqube_get_current_user`
- `sonarqube_create_user`
- `sonarqube_get_project_permissions`

### Components & Source Code (4 tools)
- `sonarqube_search_components`
- `sonarqube_get_component`
- `sonarqube_get_component_tree`
- `sonarqube_get_source_code`

### Webhooks (3 tools)
- `sonarqube_list_webhooks`
- `sonarqube_create_webhook`
- `sonarqube_delete_webhook`

## Common Use Cases

### 1. Comprehensive Project Analysis

```python
# Create project
sonarqube_create_project(_client_token=token, project_key="new-app", project_name="New Application")

# Scan git repository
scan_result = sonarqube_scan_git_repository(
    _client_token=token,
    project_key="new-app",
    git_url="https://github.com/org/new-app.git",
    branch="develop"
)

# Execute the generated commands
# (Run scan_result["full_script"] in your shell)

# Check quality gate
gate_status = sonarqube_get_quality_gate_status(_client_token=token, project_key="new-app")

# Get metrics
metrics = sonarqube_get_measures(
    _client_token=token,
    component_key="new-app",
    metric_keys=["bugs", "vulnerabilities", "code_smells", "coverage", "duplicated_lines_density"]
)
```

### 2. Security Audit

```python
# Find all security vulnerabilities
vulnerabilities = sonarqube_search_issues(
    _client_token=token,
    component_keys=["my-project"],
    types=["VULNERABILITY"],
    statuses=["OPEN", "REOPENED"],
    severities=["CRITICAL", "BLOCKER"]
)

# Check security hotspots
hotspots = sonarqube_search_hotspots(
    _client_token=token,
    project_key="my-project",
    statuses=["TO_REVIEW"]
)
```

### 3. Code Quality Monitoring

```python
# Get coverage trend
coverage = sonarqube_get_coverage(_client_token=token, component_key="my-project")

# Check duplications
duplications = sonarqube_get_duplications(_client_token=token, component_key="my-project")

# Get all issues by type
bugs = sonarqube_search_issues(_client_token=token, component_keys=["my-project"], types=["BUG"])
code_smells = sonarqube_search_issues(_client_token=token, component_keys=["my-project"], types=["CODE_SMELL"])
```

### 4. Continuous Integration Integration

```python
# In CI/CD pipeline:
# 1. Run scanner (use sonarqube_trigger_analysis or sonarqube_scan_git_repository)
# 2. Wait for analysis completion
status = sonarqube_get_analysis_status(_client_token=token, component_key="my-project")

# 3. Check quality gate
gate = sonarqube_get_quality_gate_status(_client_token=token, project_key="my-project")

# 4. Fail build if quality gate fails
if gate.get("body", {}).get("projectStatus", {}).get("status") == "ERROR":
    raise Exception("Quality gate failed!")
```

## Troubleshooting

### Authentication Errors

- Verify `SONARQUBE_TOKEN` is correct and has appropriate permissions
- Check token hasn't expired (regenerate if needed)
- Ensure user has "Execute Analysis" permission for scanning

### Connection Errors

- Verify `SONARQUBE_BASE_URL` is accessible
- Check SSL certificate if using HTTPS (set `SONARQUBE_VERIFY_SSL=false` for self-signed certs)
- Ensure firewall allows connections to SonarQube server

### Scanner Errors

- Install `sonar-scanner` CLI tool: https://docs.sonarqube.org/latest/analysis/scan/sonarscanner/
- Ensure `sonar-scanner` is in PATH
- Check project properties match your project structure

## Security Considerations

1. **Token Storage**: Store SonarQube tokens securely (use environment variables, never commit to git)
2. **MCP Client Token**: Use strong, unique tokens in production
3. **SSL/TLS**: Always use HTTPS in production (`SONARQUBE_VERIFY_SSL=true`)
4. **Permissions**: Grant minimum required permissions to service accounts
5. **Token Rotation**: Rotate tokens regularly

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     AI Assistant / Client                    │
└────────────────────────────┬────────────────────────────────┘
                             │ MCP Protocol
┌────────────────────────────▼────────────────────────────────┐
│                 SonarQube MCP Server (FastMCP)              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  60+ Tool Definitions (sonarqube_*)                  │   │
│  └────────────────┬─────────────────────────────────────┘   │
│  ┌────────────────▼─────────────────────────────────────┐   │
│  │  SonarQubeMCPServer Client                           │   │
│  │  - Authentication (Token/Basic)                      │   │
│  │  - HTTP Request Management                           │   │
│  │  - Response Parsing                                  │   │
│  └────────────────┬─────────────────────────────────────┘   │
└───────────────────┼──────────────────────────────────────────┘
                    │ HTTPS/HTTP
┌───────────────────▼──────────────────────────────────────────┐
│              SonarQube Server REST API                       │
│  - Projects, Issues, Metrics, Quality Gates                 │
│  - Security Hotspots, Rules, Users                          │
│  - Analysis Results, Source Code                            │
└──────────────────────────────────────────────────────────────┘
```

## References

- [SonarQube Documentation](https://docs.sonarqube.org/)
- [SonarQube Web API](https://docs.sonarqube.org/latest/extend/web-api/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [FastMCP Framework](https://github.com/jlowin/fastmcp)

## License

Part of the best-streamlit-website project.

## Support

For issues or questions:
1. Check SonarQube API documentation
2. Verify environment configuration
3. Review logs for detailed error messages
4. Test API endpoints directly using curl/Postman
