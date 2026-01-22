# SonarQube MCP Server - Quick Reference

## Environment Setup

```bash
export SONARQUBE_BASE_URL="https://sonarqube.example.com"
export SONARQUBE_TOKEN="your_token_here"
export SONARQUBE_MCP_CLIENT_TOKEN="dev-sonarqube-mcp-token"
```

## Most Used Tools

### 1. Scan Git Repository
```python
sonarqube_scan_git_repository(
    _client_token="token",
    project_key="my-app",
    git_url="https://github.com/org/repo.git",
    branch="main",
    additional_properties={"sonar.sources": "src"}
)
```

### 2. Check Quality Gate
```python
sonarqube_get_quality_gate_status(
    _client_token="token",
    project_key="my-app"
)
```

### 3. List Critical Issues
```python
sonarqube_search_issues(
    _client_token="token",
    component_keys=["my-app"],
    severities=["CRITICAL", "BLOCKER"],
    types=["BUG", "VULNERABILITY"]
)
```

### 4. Get Code Coverage
```python
sonarqube_get_coverage(
    _client_token="token",
    component_key="my-app"
)
```

### 5. List All Projects
```python
sonarqube_list_projects(
    _client_token="token",
    page=1,
    page_size=100
)
```

## Tool Categories (60+ tools)

### System (4)
- sonarqube_get_system_status
- sonarqube_get_system_health
- sonarqube_ping_server
- sonarqube_get_system_info

### Projects (5)
- sonarqube_list_projects
- sonarqube_create_project
- sonarqube_delete_project
- sonarqube_get_project_info
- sonarqube_update_project_visibility

### Analysis (4)
- sonarqube_trigger_analysis
- **sonarqube_scan_git_repository** ⭐
- sonarqube_get_analysis_status
- sonarqube_get_analysis_history

### Quality Gates (5)
- sonarqube_get_quality_gate_status
- sonarqube_list_quality_gates
- sonarqube_create_quality_gate
- sonarqube_associate_quality_gate

### Issues (8)
- sonarqube_search_issues
- sonarqube_get_issue
- sonarqube_add_comment_to_issue
- sonarqube_change_issue_status
- sonarqube_assign_issue
- sonarqube_set_issue_severity
- sonarqube_set_issue_type

### Metrics (5)
- sonarqube_get_measures
- sonarqube_get_component_tree_measures
- sonarqube_list_metrics
- sonarqube_get_coverage
- sonarqube_get_duplications

### Security (3)
- sonarqube_search_hotspots
- sonarqube_get_hotspot
- sonarqube_change_hotspot_status

### Rules (2)
- sonarqube_search_rules
- sonarqube_get_rule

### Quality Profiles (3)
- sonarqube_search_quality_profiles
- sonarqube_get_quality_profile
- sonarqube_associate_project_with_profile

### Users (4)
- sonarqube_list_users
- sonarqube_get_current_user
- sonarqube_create_user
- sonarqube_get_project_permissions

### Components (4)
- sonarqube_search_components
- sonarqube_get_component
- sonarqube_get_component_tree
- sonarqube_get_source_code

### Webhooks (3)
- sonarqube_list_webhooks
- sonarqube_create_webhook
- sonarqube_delete_webhook

## Common Metric Keys

### Quality Metrics
- `bugs`: Number of bugs
- `vulnerabilities`: Number of vulnerabilities
- `code_smells`: Number of code smells
- `security_hotspots`: Number of security hotspots

### Coverage Metrics
- `coverage`: Overall coverage %
- `line_coverage`: Line coverage %
- `branch_coverage`: Branch coverage %
- `lines_to_cover`: Total lines to cover
- `uncovered_lines`: Number of uncovered lines

### Complexity Metrics
- `complexity`: Cyclomatic complexity
- `cognitive_complexity`: Cognitive complexity
- `function_complexity`: Average complexity per function

### Size Metrics
- `ncloc`: Non-commenting lines of code
- `lines`: Total lines
- `statements`: Number of statements
- `functions`: Number of functions
- `classes`: Number of classes
- `files`: Number of files

### Duplication Metrics
- `duplicated_lines`: Number of duplicated lines
- `duplicated_lines_density`: Duplication density %
- `duplicated_blocks`: Number of duplicated blocks
- `duplicated_files`: Number of files with duplications

### Maintainability Metrics
- `sqale_rating`: Maintainability rating (A-E)
- `sqale_index`: Technical debt (minutes)
- `sqale_debt_ratio`: Debt ratio %

### Reliability Metrics
- `reliability_rating`: Reliability rating (A-E)
- `bugs`: Number of bugs

### Security Metrics
- `security_rating`: Security rating (A-E)
- `security_review_rating`: Security review rating (A-E)
- `vulnerabilities`: Number of vulnerabilities
- `security_hotspots`: Number of security hotspots

## Issue Severities
- `BLOCKER`: Must fix immediately
- `CRITICAL`: High priority
- `MAJOR`: Normal priority
- `MINOR`: Low priority
- `INFO`: Informational

## Issue Types
- `BUG`: Bug/defect
- `VULNERABILITY`: Security vulnerability
- `CODE_SMELL`: Maintainability issue
- `SECURITY_HOTSPOT`: Security review needed

## Issue Statuses
- `OPEN`: Open issue
- `CONFIRMED`: Confirmed by user
- `REOPENED`: Re-opened issue
- `RESOLVED`: Resolved issue
- `CLOSED`: Closed issue

## Issue Transitions
- `confirm`: Confirm an issue
- `unconfirm`: Unconfirm an issue
- `reopen`: Reopen a resolved issue
- `resolve`: Resolve an issue
- `falsepositive`: Mark as false positive
- `wontfix`: Mark as won't fix

## Quality Gate Statuses
- `OK`: Passed
- `ERROR`: Failed
- `WARN`: Warning (deprecated)
- `NONE`: No quality gate

## Quick Scripts

### Full Project Analysis
```bash
# 1. Create project
sonarqube_create_project(_client_token, "app", "My App")

# 2. Scan git repo
result = sonarqube_scan_git_repository(
    _client_token,
    "app",
    "https://github.com/org/app.git"
)
# Execute: result["full_script"]

# 3. Check status
sonarqube_get_analysis_status(_client_token, "app")

# 4. Get quality gate
sonarqube_get_quality_gate_status(_client_token, "app")

# 5. Get metrics
sonarqube_get_measures(
    _client_token,
    "app",
    ["bugs", "vulnerabilities", "coverage"]
)
```

### Security Audit
```bash
# Find vulnerabilities
sonarqube_search_issues(
    _client_token,
    component_keys=["app"],
    types=["VULNERABILITY"],
    severities=["CRITICAL", "BLOCKER"]
)

# Find hotspots
sonarqube_search_hotspots(
    _client_token,
    "app",
    statuses=["TO_REVIEW"]
)
```

## API Rate Limits

SonarQube has no built-in rate limits, but recommended practices:
- Batch operations when possible
- Use pagination for large result sets
- Cache results when appropriate
- Schedule heavy operations during off-peak hours

## Troubleshooting

### Common Errors

**401 Unauthorized**
- Check token is valid
- Verify token has required permissions
- Token may have expired

**403 Forbidden**
- User lacks permission for operation
- Project may be private
- Admin permissions required

**404 Not Found**
- Project/component doesn't exist
- Check project key spelling
- Resource may have been deleted

**500 Internal Server Error**
- SonarQube server issue
- Check SonarQube logs
- Verify database connectivity

### Debug Mode

Enable detailed logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Test Connection

```python
# Quick connectivity test
result = sonarqube_ping_server(_client_token="token")
print(result)  # Should return {"ok": True, ...}
```

## Support Resources

- [SonarQube API Docs](https://docs.sonarqube.org/latest/extend/web-api/)
- [SonarQube User Guide](https://docs.sonarqube.org/)
- [Scanner Documentation](https://docs.sonarqube.org/latest/analysis/overview/)
