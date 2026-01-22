"""SonarQube MCP Server

Comprehensive Model Context Protocol server for SonarQube code analysis platform.
Provides tools for project management, code quality analysis, issue tracking,
security scanning, and git repository analysis.
"""

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import SonarQubeMCPServerConfig
from .utils.auth import auth_or_error
from .utils.client import SonarQubeAuthConfig, SonarQubeMCPServer


# Global client instance
_CLIENT: Optional[SonarQubeMCPServer] = None


def _sonarqube_client_from_env() -> SonarQubeMCPServer:
    """Create SonarQube client from environment configuration."""
    global _CLIENT
    if _CLIENT is None:
        config = SonarQubeMCPServerConfig.from_env()
        auth_config = SonarQubeAuthConfig(
            base_url=config.base_url,
            token=config.token,
            username=config.username,
            password=config.password,
            verify_ssl=config.verify_ssl,
        )
        _CLIENT = SonarQubeMCPServer(auth_config)
    return _CLIENT


# Initialize FastMCP server
config = SonarQubeMCPServerConfig.from_env()
mcp = FastMCP(
    "SonarQube MCP Server",
    dependencies=["requests"],
)

# ========== System & Server Tools ==========

@mcp.tool()
def sonarqube_get_system_status(_client_token: str) -> Dict[str, Any]:
    """Get SonarQube system status.
    
    Returns overall system status (UP, DOWN, STARTING).
    
    Args:
        _client_token: MCP client authentication token
        
    Returns:
        System status information
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_system_status()


@mcp.tool()
def sonarqube_get_system_health(_client_token: str) -> Dict[str, Any]:
    """Get SonarQube system health.
    
    Returns health status of system components (Web, Compute Engine, Database).
    
    Args:
        _client_token: MCP client authentication token
        
    Returns:
        System health information with component statuses
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_system_health()


@mcp.tool()
def sonarqube_ping_server(_client_token: str) -> Dict[str, Any]:
    """Ping SonarQube server to check availability.
    
    Simple connectivity check.
    
    Args:
        _client_token: MCP client authentication token
        
    Returns:
        Ping response
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.ping_server()


# ========== Project Management Tools ==========

@mcp.tool()
def sonarqube_list_projects(
    _client_token: str,
    page: int = 1,
    page_size: int = 100,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """List all SonarQube projects with pagination.
    
    Args:
        _client_token: MCP client authentication token
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        query: Search query to filter projects by name or key
        
    Returns:
        List of projects with metadata
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.list_projects(page=page, page_size=page_size, query=query)


@mcp.tool()
def sonarqube_create_project(
    _client_token: str,
    project_key: str,
    project_name: str,
    visibility: str = "private",
) -> Dict[str, Any]:
    """Create a new SonarQube project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Unique project key/identifier
        project_name: Human-readable project name
        visibility: Project visibility - "private" or "public" (default: "private")
        
    Returns:
        Created project details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.create_project(project_key, project_name, visibility)


@mcp.tool()
def sonarqube_delete_project(_client_token: str, project_key: str) -> Dict[str, Any]:
    """Delete a SonarQube project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key to delete
        
    Returns:
        Deletion confirmation
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.delete_project(project_key)


@mcp.tool()
def sonarqube_get_project_info(_client_token: str, project_key: str) -> Dict[str, Any]:
    """Get detailed information about a project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        
    Returns:
        Project details including metadata and configuration
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_project_info(project_key)


# ========== Code Analysis & Scanning Tools ==========

@mcp.tool()
def sonarqube_trigger_analysis(
    _client_token: str,
    project_key: str,
    scanner_params: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Trigger code analysis for a project.
    
    Returns sonar-scanner command to execute externally.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key to analyze
        scanner_params: Additional scanner parameters as key-value pairs
                       (e.g., {"sonar.sources": "src", "sonar.tests": "tests"})
        
    Returns:
        Scanner command and instructions for execution
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.trigger_analysis(project_key, scanner_params)


@mcp.tool()
def sonarqube_scan_git_repository(
    _client_token: str,
    project_key: str,
    git_url: str,
    branch: str = "main",
    working_directory: Optional[str] = None,
    additional_properties: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Generate commands to scan a git repository with SonarQube.
    
    Creates a complete workflow to:
    1. Clone the git repository
    2. Run sonar-scanner analysis
    
    Args:
        _client_token: MCP client authentication token
        project_key: SonarQube project key
        git_url: Git repository URL (HTTPS or SSH)
        branch: Git branch to scan (default: "main")
        working_directory: Directory to clone into (optional, auto-generated if not provided)
        additional_properties: Additional sonar-scanner properties
                              (e.g., {"sonar.sources": "src", "sonar.java.binaries": "target/classes"})
        
    Returns:
        Shell commands and script to execute the scan workflow
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.scan_git_repository(
        project_key=project_key,
        git_url=git_url,
        branch=branch,
        working_directory=working_directory,
        additional_properties=additional_properties,
    )


@mcp.tool()
def sonarqube_get_analysis_status(_client_token: str, component_key: str) -> Dict[str, Any]:
    """Get status of the last analysis for a component.
    
    Args:
        _client_token: MCP client authentication token
        component_key: Component key (usually project key)
        
    Returns:
        Analysis task status and details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_analysis_status(component_key)


@mcp.tool()
def sonarqube_get_analysis_history(
    _client_token: str,
    project_key: str,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Get analysis history for a project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        List of past analyses with timestamps and results
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_analysis_history(project_key, page, page_size)


# ========== Quality Gate Tools ==========

@mcp.tool()
def sonarqube_get_quality_gate_status(_client_token: str, project_key: str) -> Dict[str, Any]:
    """Get quality gate status for a project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        
    Returns:
        Quality gate status (OK, ERROR, WARN) with condition details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_quality_gate_status(project_key)


@mcp.tool()
def sonarqube_list_quality_gates(_client_token: str) -> Dict[str, Any]:
    """List all quality gates in SonarQube.
    
    Args:
        _client_token: MCP client authentication token
        
    Returns:
        List of quality gates with their configurations
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.list_quality_gates()


@mcp.tool()
def sonarqube_create_quality_gate(_client_token: str, name: str) -> Dict[str, Any]:
    """Create a new quality gate.
    
    Args:
        _client_token: MCP client authentication token
        name: Quality gate name
        
    Returns:
        Created quality gate details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.create_quality_gate(name)


@mcp.tool()
def sonarqube_associate_quality_gate(
    _client_token: str,
    project_key: str,
    gate_id: int,
) -> Dict[str, Any]:
    """Associate a quality gate with a project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        gate_id: Quality gate ID
        
    Returns:
        Association confirmation
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.associate_quality_gate(project_key, gate_id)


# ========== Issue Management Tools ==========

@mcp.tool()
def sonarqube_search_issues(
    _client_token: str,
    component_keys: Optional[List[str]] = None,
    severities: Optional[List[str]] = None,
    types: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    resolutions: Optional[List[str]] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Search for code issues with filters.
    
    Args:
        _client_token: MCP client authentication token
        component_keys: List of component keys to filter
        severities: List of severities (BLOCKER, CRITICAL, MAJOR, MINOR, INFO)
        types: List of issue types (BUG, VULNERABILITY, CODE_SMELL, SECURITY_HOTSPOT)
        statuses: List of statuses (OPEN, CONFIRMED, REOPENED, RESOLVED, CLOSED)
        resolutions: List of resolutions (FALSE-POSITIVE, WONTFIX, FIXED, REMOVED)
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        List of matching issues with details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.search_issues(
        component_keys=component_keys,
        severities=severities,
        types=types,
        statuses=statuses,
        resolutions=resolutions,
        page=page,
        page_size=page_size,
    )


@mcp.tool()
def sonarqube_get_issue(_client_token: str, issue_key: str) -> Dict[str, Any]:
    """Get detailed information about a specific issue.
    
    Args:
        _client_token: MCP client authentication token
        issue_key: Issue key/ID
        
    Returns:
        Detailed issue information including code snippets and history
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_issue(issue_key)


@mcp.tool()
def sonarqube_add_comment_to_issue(
    _client_token: str,
    issue_key: str,
    comment: str,
) -> Dict[str, Any]:
    """Add a comment to an issue.
    
    Args:
        _client_token: MCP client authentication token
        issue_key: Issue key
        comment: Comment text
        
    Returns:
        Updated issue with new comment
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.add_comment_to_issue(issue_key, comment)


@mcp.tool()
def sonarqube_change_issue_status(
    _client_token: str,
    issue_key: str,
    transition: str,
) -> Dict[str, Any]:
    """Change issue status/transition.
    
    Available transitions: confirm, unconfirm, reopen, resolve, falsepositive, wontfix
    
    Args:
        _client_token: MCP client authentication token
        issue_key: Issue key
        transition: Status transition to apply
        
    Returns:
        Updated issue with new status
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.change_issue_status(issue_key, transition)


@mcp.tool()
def sonarqube_assign_issue(
    _client_token: str,
    issue_key: str,
    assignee: str,
) -> Dict[str, Any]:
    """Assign an issue to a user.
    
    Args:
        _client_token: MCP client authentication token
        issue_key: Issue key
        assignee: Username to assign to
        
    Returns:
        Updated issue with new assignee
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.assign_issue(issue_key, assignee)


@mcp.tool()
def sonarqube_set_issue_severity(
    _client_token: str,
    issue_key: str,
    severity: str,
) -> Dict[str, Any]:
    """Set issue severity.
    
    Args:
        _client_token: MCP client authentication token
        issue_key: Issue key
        severity: Severity level (BLOCKER, CRITICAL, MAJOR, MINOR, INFO)
        
    Returns:
        Updated issue with new severity
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.set_issue_severity(issue_key, severity)


@mcp.tool()
def sonarqube_set_issue_type(
    _client_token: str,
    issue_key: str,
    issue_type: str,
) -> Dict[str, Any]:
    """Set issue type.
    
    Args:
        _client_token: MCP client authentication token
        issue_key: Issue key
        issue_type: Issue type (BUG, VULNERABILITY, CODE_SMELL)
        
    Returns:
        Updated issue with new type
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.set_issue_type(issue_key, issue_type)


# ========== Metrics & Measures Tools ==========

@mcp.tool()
def sonarqube_get_measures(
    _client_token: str,
    component_key: str,
    metric_keys: List[str],
    additional_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get measures/metrics for a component.
    
    Common metrics: ncloc, complexity, coverage, duplicated_lines_density,
                   bugs, vulnerabilities, code_smells, security_hotspots
    
    Args:
        _client_token: MCP client authentication token
        component_key: Component key (project, file, etc.)
        metric_keys: List of metric keys to retrieve
        additional_fields: Additional fields to include in response
        
    Returns:
        Component measures with metric values
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_measures(component_key, metric_keys, additional_fields)


@mcp.tool()
def sonarqube_get_component_tree_measures(
    _client_token: str,
    component_key: str,
    metric_keys: List[str],
    strategy: str = "children",
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Get measures for component tree (files, directories).
    
    Args:
        _client_token: MCP client authentication token
        component_key: Root component key
        metric_keys: List of metric keys
        strategy: Tree strategy - "children", "leaves", or "all" (default: "children")
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        Tree of components with their measures
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_component_tree_measures(
        component_key, metric_keys, strategy, page, page_size
    )


@mcp.tool()
def sonarqube_list_metrics(
    _client_token: str,
    page: int = 1,
    page_size: int = 500,
) -> Dict[str, Any]:
    """List all available metrics in SonarQube.
    
    Args:
        _client_token: MCP client authentication token
        page: Page number (default: 1)
        page_size: Items per page (default: 500)
        
    Returns:
        List of all metrics with descriptions and types
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.list_metrics(page, page_size)


@mcp.tool()
def sonarqube_get_coverage(_client_token: str, component_key: str) -> Dict[str, Any]:
    """Get code coverage metrics for a component.
    
    Args:
        _client_token: MCP client authentication token
        component_key: Component key
        
    Returns:
        Coverage metrics (overall, line, branch, uncovered lines)
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_coverage(component_key)


@mcp.tool()
def sonarqube_get_duplications(_client_token: str, component_key: str) -> Dict[str, Any]:
    """Get code duplication metrics for a component.
    
    Args:
        _client_token: MCP client authentication token
        component_key: Component key
        
    Returns:
        Duplication metrics (duplicated lines, density, blocks, files)
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_duplications(component_key)


# ========== Security Hotspot Tools ==========

@mcp.tool()
def sonarqube_search_hotspots(
    _client_token: str,
    project_key: str,
    statuses: Optional[List[str]] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Search security hotspots in a project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        statuses: List of statuses (TO_REVIEW, REVIEWED)
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        List of security hotspots
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.search_hotspots(project_key, statuses, page, page_size)


@mcp.tool()
def sonarqube_get_hotspot(_client_token: str, hotspot_key: str) -> Dict[str, Any]:
    """Get detailed information about a security hotspot.
    
    Args:
        _client_token: MCP client authentication token
        hotspot_key: Hotspot key
        
    Returns:
        Hotspot details including code context and review status
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_hotspot(hotspot_key)


@mcp.tool()
def sonarqube_change_hotspot_status(
    _client_token: str,
    hotspot_key: str,
    status: str,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    """Change security hotspot status.
    
    Args:
        _client_token: MCP client authentication token
        hotspot_key: Hotspot key
        status: New status (TO_REVIEW, REVIEWED)
        resolution: Resolution if status is REVIEWED (FIXED, SAFE, ACKNOWLEDGED)
        
    Returns:
        Updated hotspot
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.change_hotspot_status(hotspot_key, status, resolution)


# ========== Rules Tools ==========

@mcp.tool()
def sonarqube_search_rules(
    _client_token: str,
    languages: Optional[List[str]] = None,
    rule_types: Optional[List[str]] = None,
    severities: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Search coding rules in SonarQube.
    
    Args:
        _client_token: MCP client authentication token
        languages: List of language keys (java, python, js, etc.)
        rule_types: List of rule types (BUG, VULNERABILITY, CODE_SMELL, SECURITY_HOTSPOT)
        severities: List of severities (BLOCKER, CRITICAL, MAJOR, MINOR, INFO)
        tags: List of tags to filter by
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        List of matching rules with details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.search_rules(languages, rule_types, severities, tags, page, page_size)


@mcp.tool()
def sonarqube_get_rule(_client_token: str, rule_key: str) -> Dict[str, Any]:
    """Get detailed information about a coding rule.
    
    Args:
        _client_token: MCP client authentication token
        rule_key: Rule key (e.g., "java:S1234")
        
    Returns:
        Rule details including description, examples, and parameters
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_rule(rule_key)


# ========== Quality Profile Tools ==========

@mcp.tool()
def sonarqube_search_quality_profiles(
    _client_token: str,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Search quality profiles.
    
    Args:
        _client_token: MCP client authentication token
        language: Filter by language key (optional)
        
    Returns:
        List of quality profiles
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.search_quality_profiles(language)


@mcp.tool()
def sonarqube_get_quality_profile(_client_token: str, profile_key: str) -> Dict[str, Any]:
    """Get quality profile details.
    
    Args:
        _client_token: MCP client authentication token
        profile_key: Quality profile key
        
    Returns:
        Quality profile configuration and rules
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_quality_profile(profile_key)


@mcp.tool()
def sonarqube_associate_project_with_profile(
    _client_token: str,
    project_key: str,
    profile_key: str,
) -> Dict[str, Any]:
    """Associate a project with a quality profile.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        profile_key: Quality profile key
        
    Returns:
        Association confirmation
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.associate_project_with_profile(project_key, profile_key)


# ========== User & Permission Tools ==========

@mcp.tool()
def sonarqube_list_users(
    _client_token: str,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """List all users in SonarQube.
    
    Args:
        _client_token: MCP client authentication token
        page: Page number (default: 1)
        page_size: Items per page (default: 50)
        
    Returns:
        List of users with details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.list_users(page, page_size)


@mcp.tool()
def sonarqube_get_current_user(_client_token: str) -> Dict[str, Any]:
    """Get current authenticated user information.
    
    Args:
        _client_token: MCP client authentication token
        
    Returns:
        Current user details and permissions
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_current_user()


@mcp.tool()
def sonarqube_create_user(
    _client_token: str,
    login: str,
    name: str,
    email: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new user.
    
    Args:
        _client_token: MCP client authentication token
        login: User login/username
        name: User display name
        email: User email address (optional)
        password: User password (optional, auto-generated if not provided)
        
    Returns:
        Created user details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.create_user(login, name, email, password)


@mcp.tool()
def sonarqube_get_project_permissions(_client_token: str, project_key: str) -> Dict[str, Any]:
    """Get permissions for a project.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Project key
        
    Returns:
        Project permissions (users and groups)
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_project_permissions(project_key)


# ========== Component & Source Code Tools ==========

@mcp.tool()
def sonarqube_search_components(
    _client_token: str,
    qualifiers: Optional[List[str]] = None,
    query: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Search components (projects, files, directories).
    
    Args:
        _client_token: MCP client authentication token
        qualifiers: Component qualifiers (TRK=project, FIL=file, DIR=directory)
        query: Search query
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        List of matching components
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.search_components(qualifiers, query, page, page_size)


@mcp.tool()
def sonarqube_get_component(_client_token: str, component_key: str) -> Dict[str, Any]:
    """Get component details.
    
    Args:
        _client_token: MCP client authentication token
        component_key: Component key
        
    Returns:
        Component details and metadata
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_component(component_key)


@mcp.tool()
def sonarqube_get_component_tree(
    _client_token: str,
    component_key: str,
    strategy: str = "children",
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Get component tree (navigate project structure).
    
    Args:
        _client_token: MCP client authentication token
        component_key: Root component key
        strategy: Tree strategy - "children", "leaves", or "all" (default: "children")
        page: Page number (default: 1)
        page_size: Items per page (default: 100)
        
    Returns:
        Tree structure of components
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_component_tree(component_key, strategy, page, page_size)


@mcp.tool()
def sonarqube_get_source_code(
    _client_token: str,
    component_key: str,
    from_line: Optional[int] = None,
    to_line: Optional[int] = None,
) -> Dict[str, Any]:
    """Get source code for a file component.
    
    Args:
        _client_token: MCP client authentication token
        component_key: File component key
        from_line: Start line number (optional)
        to_line: End line number (optional)
        
    Returns:
        Source code lines with annotations
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.get_source_code(component_key, from_line, to_line)


# ========== Webhook Tools ==========

@mcp.tool()
def sonarqube_list_webhooks(
    _client_token: str,
    project_key: Optional[str] = None,
) -> Dict[str, Any]:
    """List webhooks.
    
    Args:
        _client_token: MCP client authentication token
        project_key: Filter by project key (optional, lists global webhooks if not provided)
        
    Returns:
        List of webhooks
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.list_webhooks(project_key)


@mcp.tool()
def sonarqube_create_webhook(
    _client_token: str,
    name: str,
    url: str,
    project_key: Optional[str] = None,
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a webhook.
    
    Args:
        _client_token: MCP client authentication token
        name: Webhook name
        url: Webhook URL
        project_key: Project key (optional, creates global webhook if not provided)
        secret: Webhook secret for signature validation (optional)
        
    Returns:
        Created webhook details
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.create_webhook(name, url, project_key, secret)


@mcp.tool()
def sonarqube_delete_webhook(_client_token: str, webhook_key: str) -> Dict[str, Any]:
    """Delete a webhook.
    
    Args:
        _client_token: MCP client authentication token
        webhook_key: Webhook key
        
    Returns:
        Deletion confirmation
    """
    err = auth_or_error(_client_token)
    if err:
        return err
    client = _sonarqube_client_from_env()
    return client.delete_webhook(webhook_key)
