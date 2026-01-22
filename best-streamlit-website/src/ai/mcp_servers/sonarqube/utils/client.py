from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import base64

import requests


@dataclass
class SonarQubeAuthConfig:
    base_url: str
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: bool = True


class SonarQubeMCPServer:
    """Comprehensive SonarQube client exposing all API functionalities."""

    def __init__(self, config: SonarQubeAuthConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api"

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        """Make HTTP request to SonarQube API."""
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        
        # Set up authentication
        auth = None
        headers = kwargs.pop("headers", {})
        
        if self.config.token:
            # Token auth (token is used as username with empty password)
            auth = (self.config.token, "")
        elif self.config.username and self.config.password:
            # Basic auth
            auth = (self.config.username, self.config.password)

        try:
            resp = requests.request(
                method,
                url,
                auth=auth,
                verify=self.config.verify_ssl,
                timeout=30,
                headers=headers,
                **kwargs,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "url": url}

        content_type = resp.headers.get("content-type", "")
        body: Any
        if "application/json" in content_type:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
        else:
            body = resp.text

        return {
            "ok": resp.ok,
            "status": resp.status_code,
            "url": url,
            "body": body,
        }

    # ========== System & Server APIs ==========
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get SonarQube system status."""
        return self._request("GET", "system/status")

    def get_system_health(self) -> Dict[str, Any]:
        """Get SonarQube system health information."""
        return self._request("GET", "system/health")

    def get_system_info(self) -> Dict[str, Any]:
        """Get detailed system information (requires admin permissions)."""
        return self._request("GET", "system/info")

    def ping_server(self) -> Dict[str, Any]:
        """Ping the server to check if it's alive."""
        return self._request("GET", "system/ping")

    # ========== Project APIs ==========
    
    def list_projects(self, page: int = 1, page_size: int = 100, query: Optional[str] = None) -> Dict[str, Any]:
        """List all projects with pagination."""
        params = {"p": page, "ps": page_size}
        if query:
            params["q"] = query
        return self._request("GET", "projects/search", params=params)

    def create_project(self, project_key: str, project_name: str, visibility: str = "private") -> Dict[str, Any]:
        """Create a new project."""
        data = {
            "project": project_key,
            "name": project_name,
            "visibility": visibility,
        }
        return self._request("POST", "projects/create", data=data)

    def delete_project(self, project_key: str) -> Dict[str, Any]:
        """Delete a project."""
        return self._request("POST", "projects/delete", data={"project": project_key})

    def get_project_info(self, project_key: str) -> Dict[str, Any]:
        """Get detailed project information."""
        return self._request("GET", f"projects/search", params={"projects": project_key})

    def update_project_visibility(self, project_key: str, visibility: str) -> Dict[str, Any]:
        """Update project visibility (public/private)."""
        data = {"project": project_key, "visibility": visibility}
        return self._request("POST", "projects/update_visibility", data=data)

    # ========== Analysis/Scan APIs ==========
    
    def trigger_analysis(self, project_key: str, scanner_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Trigger a code analysis/scan.
        
        Note: This typically requires running sonar-scanner externally,
        but this method prepares the project and returns scanner command.
        """
        # Check project exists
        project_info = self.get_project_info(project_key)
        if not project_info.get("ok"):
            return project_info

        # Return scanner command template
        scanner_command = f"sonar-scanner -Dsonar.projectKey={project_key} -Dsonar.host.url={self.base_url}"
        if self.config.token:
            scanner_command += f" -Dsonar.login={self.config.token}"
        
        if scanner_params:
            for key, value in scanner_params.items():
                scanner_command += f" -D{key}={value}"

        return {
            "ok": True,
            "project_key": project_key,
            "scanner_command": scanner_command,
            "instructions": "Run this command in your project root directory with sonar-scanner installed",
        }

    def get_analysis_status(self, component_key: str) -> Dict[str, Any]:
        """Get the status of the last analysis."""
        return self._request("GET", "ce/component", params={"component": component_key})

    def get_analysis_history(self, project_key: str, page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """Get analysis/scan history for a project."""
        params = {"project": project_key, "p": page, "ps": page_size}
        return self._request("GET", "project_analyses/search", params=params)

    # ========== Quality Gate APIs ==========
    
    def get_quality_gate_status(self, project_key: str) -> Dict[str, Any]:
        """Get quality gate status for a project."""
        return self._request("GET", "qualitygates/project_status", params={"projectKey": project_key})

    def list_quality_gates(self) -> Dict[str, Any]:
        """List all quality gates."""
        return self._request("GET", "qualitygates/list")

    def get_quality_gate(self, gate_id: int) -> Dict[str, Any]:
        """Get details of a specific quality gate."""
        return self._request("GET", "qualitygates/show", params={"id": gate_id})

    def create_quality_gate(self, name: str) -> Dict[str, Any]:
        """Create a new quality gate."""
        return self._request("POST", "qualitygates/create", data={"name": name})

    def associate_quality_gate(self, project_key: str, gate_id: int) -> Dict[str, Any]:
        """Associate a quality gate with a project."""
        data = {"projectKey": project_key, "gateId": gate_id}
        return self._request("POST", "qualitygates/select", data=data)

    # ========== Issues APIs ==========
    
    def search_issues(
        self,
        component_keys: Optional[List[str]] = None,
        severities: Optional[List[str]] = None,
        types: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        resolutions: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Search for issues with various filters."""
        params: Dict[str, Any] = {"p": page, "ps": page_size}
        
        if component_keys:
            params["componentKeys"] = ",".join(component_keys)
        if severities:
            params["severities"] = ",".join(severities)
        if types:
            params["types"] = ",".join(types)
        if statuses:
            params["statuses"] = ",".join(statuses)
        if resolutions:
            params["resolutions"] = ",".join(resolutions)
            
        return self._request("GET", "issues/search", params=params)

    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get detailed information about a specific issue."""
        return self._request("GET", "issues/search", params={"issues": issue_key})

    def add_comment_to_issue(self, issue_key: str, comment: str) -> Dict[str, Any]:
        """Add a comment to an issue."""
        data = {"issue": issue_key, "text": comment}
        return self._request("POST", "issues/add_comment", data=data)

    def change_issue_status(self, issue_key: str, transition: str) -> Dict[str, Any]:
        """Change issue status (e.g., 'confirm', 'resolve', 'reopen')."""
        data = {"issue": issue_key, "transition": transition}
        return self._request("POST", "issues/do_transition", data=data)

    def assign_issue(self, issue_key: str, assignee: str) -> Dict[str, Any]:
        """Assign an issue to a user."""
        data = {"issue": issue_key, "assignee": assignee}
        return self._request("POST", "issues/assign", data=data)

    def set_issue_severity(self, issue_key: str, severity: str) -> Dict[str, Any]:
        """Set issue severity (BLOCKER, CRITICAL, MAJOR, MINOR, INFO)."""
        data = {"issue": issue_key, "severity": severity}
        return self._request("POST", "issues/set_severity", data=data)

    def set_issue_type(self, issue_key: str, issue_type: str) -> Dict[str, Any]:
        """Set issue type (BUG, VULNERABILITY, CODE_SMELL)."""
        data = {"issue": issue_key, "type": issue_type}
        return self._request("POST", "issues/set_type", data=data)

    # ========== Measures & Metrics APIs ==========
    
    def get_measures(
        self,
        component_key: str,
        metric_keys: List[str],
        additional_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get measures for a component."""
        params = {
            "component": component_key,
            "metricKeys": ",".join(metric_keys),
        }
        if additional_fields:
            params["additionalFields"] = ",".join(additional_fields)
        return self._request("GET", "measures/component", params=params)

    def get_component_tree_measures(
        self,
        component_key: str,
        metric_keys: List[str],
        strategy: str = "children",
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Get measures for component tree (files, directories)."""
        params = {
            "component": component_key,
            "metricKeys": ",".join(metric_keys),
            "strategy": strategy,
            "p": page,
            "ps": page_size,
        }
        return self._request("GET", "measures/component_tree", params=params)

    def list_metrics(self, page: int = 1, page_size: int = 500) -> Dict[str, Any]:
        """List all available metrics."""
        params = {"p": page, "ps": page_size}
        return self._request("GET", "metrics/search", params=params)

    # ========== Code Coverage APIs ==========
    
    def get_coverage(self, component_key: str) -> Dict[str, Any]:
        """Get code coverage metrics for a component."""
        metric_keys = ["coverage", "line_coverage", "branch_coverage", "lines_to_cover", "uncovered_lines"]
        return self.get_measures(component_key, metric_keys)

    def get_detailed_coverage(self, component_key: str, from_line: Optional[int] = None, to_line: Optional[int] = None) -> Dict[str, Any]:
        """Get line-by-line coverage information."""
        params = {"key": component_key}
        if from_line:
            params["from"] = from_line
        if to_line:
            params["to"] = to_line
        return self._request("GET", "sources/lines", params=params)

    # ========== Duplication APIs ==========
    
    def get_duplications(self, component_key: str) -> Dict[str, Any]:
        """Get code duplication metrics."""
        metric_keys = ["duplicated_lines", "duplicated_lines_density", "duplicated_blocks", "duplicated_files"]
        return self.get_measures(component_key, metric_keys)

    def get_duplication_details(self, component_key: str) -> Dict[str, Any]:
        """Get detailed duplication information."""
        return self._request("GET", "duplications/show", params={"key": component_key})

    # ========== Security Hotspots APIs ==========
    
    def search_hotspots(
        self,
        project_key: str,
        statuses: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Search security hotspots."""
        params: Dict[str, Any] = {"projectKey": project_key, "p": page, "ps": page_size}
        if statuses:
            params["status"] = ",".join(statuses)
        return self._request("GET", "hotspots/search", params=params)

    def get_hotspot(self, hotspot_key: str) -> Dict[str, Any]:
        """Get details of a specific security hotspot."""
        return self._request("GET", "hotspots/show", params={"hotspot": hotspot_key})

    def change_hotspot_status(self, hotspot_key: str, status: str, resolution: Optional[str] = None) -> Dict[str, Any]:
        """Change security hotspot status."""
        data = {"hotspot": hotspot_key, "status": status}
        if resolution:
            data["resolution"] = resolution
        return self._request("POST", "hotspots/change_status", data=data)

    # ========== Rules APIs ==========
    
    def search_rules(
        self,
        languages: Optional[List[str]] = None,
        rule_types: Optional[List[str]] = None,
        severities: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Search coding rules."""
        params: Dict[str, Any] = {"p": page, "ps": page_size}
        
        if languages:
            params["languages"] = ",".join(languages)
        if rule_types:
            params["types"] = ",".join(rule_types)
        if severities:
            params["severities"] = ",".join(severities)
        if tags:
            params["tags"] = ",".join(tags)
            
        return self._request("GET", "rules/search", params=params)

    def get_rule(self, rule_key: str) -> Dict[str, Any]:
        """Get details of a specific rule."""
        return self._request("GET", "rules/show", params={"key": rule_key})

    # ========== Quality Profiles APIs ==========
    
    def search_quality_profiles(self, language: Optional[str] = None) -> Dict[str, Any]:
        """Search quality profiles."""
        params = {}
        if language:
            params["language"] = language
        return self._request("GET", "qualityprofiles/search", params=params)

    def get_quality_profile(self, profile_key: str) -> Dict[str, Any]:
        """Get quality profile details."""
        return self._request("GET", "qualityprofiles/show", params={"key": profile_key})

    def associate_project_with_profile(self, project_key: str, profile_key: str) -> Dict[str, Any]:
        """Associate a project with a quality profile."""
        data = {"project": project_key, "key": profile_key}
        return self._request("POST", "qualityprofiles/add_project", data=data)

    # ========== User & Permission APIs ==========
    
    def list_users(self, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        """List all users."""
        params = {"p": page, "ps": page_size}
        return self._request("GET", "users/search", params=params)

    def get_current_user(self) -> Dict[str, Any]:
        """Get current authenticated user."""
        return self._request("GET", "users/current")

    def create_user(self, login: str, name: str, email: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        """Create a new user."""
        data = {"login": login, "name": name}
        if email:
            data["email"] = email
        if password:
            data["password"] = password
        return self._request("POST", "users/create", data=data)

    def get_project_permissions(self, project_key: str) -> Dict[str, Any]:
        """Get permissions for a project."""
        return self._request("GET", "permissions/search_project_permissions", params={"projectKey": project_key})

    # ========== Component & Source Code APIs ==========
    
    def search_components(
        self,
        qualifiers: Optional[List[str]] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Search components (projects, files, directories)."""
        params: Dict[str, Any] = {"p": page, "ps": page_size}
        if qualifiers:
            params["qualifiers"] = ",".join(qualifiers)
        if query:
            params["q"] = query
        return self._request("GET", "components/search", params=params)

    def get_component(self, component_key: str) -> Dict[str, Any]:
        """Get component details."""
        return self._request("GET", "components/show", params={"component": component_key})

    def get_component_tree(self, component_key: str, strategy: str = "children", page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """Get component tree (navigate project structure)."""
        params = {"component": component_key, "strategy": strategy, "p": page, "ps": page_size}
        return self._request("GET", "components/tree", params=params)

    def get_source_code(self, component_key: str, from_line: Optional[int] = None, to_line: Optional[int] = None) -> Dict[str, Any]:
        """Get source code for a file."""
        params = {"key": component_key}
        if from_line:
            params["from"] = from_line
        if to_line:
            params["to"] = to_line
        return self._request("GET", "sources/lines", params=params)

    # ========== Webhooks APIs ==========
    
    def list_webhooks(self, project_key: Optional[str] = None) -> Dict[str, Any]:
        """List webhooks."""
        params = {}
        if project_key:
            params["project"] = project_key
        return self._request("GET", "webhooks/list", params=params)

    def create_webhook(self, name: str, url: str, project_key: Optional[str] = None, secret: Optional[str] = None) -> Dict[str, Any]:
        """Create a webhook."""
        data = {"name": name, "url": url}
        if project_key:
            data["project"] = project_key
        if secret:
            data["secret"] = secret
        return self._request("POST", "webhooks/create", data=data)

    def delete_webhook(self, webhook_key: str) -> Dict[str, Any]:
        """Delete a webhook."""
        return self._request("POST", "webhooks/delete", data={"webhook": webhook_key})

    # ========== Git Repository Scan Helper ==========
    
    def scan_git_repository(
        self,
        project_key: str,
        git_url: str,
        branch: str = "main",
        sonar_scanner_path: str = "sonar-scanner",
        working_directory: Optional[str] = None,
        additional_properties: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Generate commands to scan a git repository.
        
        Returns shell commands to:
        1. Clone the git repository
        2. Run sonar-scanner
        
        Note: This returns commands to execute; actual execution should be done externally.
        """
        import os
        import tempfile
        
        # Generate temporary directory name if not provided
        if not working_directory:
            working_directory = os.path.join(tempfile.gettempdir(), f"sonar-scan-{project_key}")
        
        commands = []
        
        # Git clone command
        commands.append(f"git clone {git_url} {working_directory}")
        commands.append(f"cd {working_directory}")
        
        if branch != "main":
            commands.append(f"git checkout {branch}")
        
        # Build sonar-scanner command
        scanner_params = [
            f"-Dsonar.projectKey={project_key}",
            f"-Dsonar.host.url={self.base_url}",
        ]
        
        if self.config.token:
            scanner_params.append(f"-Dsonar.login={self.config.token}")
        
        if additional_properties:
            for key, value in additional_properties.items():
                scanner_params.append(f"-D{key}={value}")
        
        scanner_command = f"{sonar_scanner_path} " + " ".join(scanner_params)
        commands.append(scanner_command)
        
        return {
            "ok": True,
            "project_key": project_key,
            "git_url": git_url,
            "branch": branch,
            "working_directory": working_directory,
            "commands": commands,
            "full_script": " && ".join(commands),
            "instructions": "Execute these commands in your shell to scan the git repository",
        }
