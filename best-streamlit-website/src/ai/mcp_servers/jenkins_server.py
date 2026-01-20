from __future__ import annotations

import os
from dataclasses import dataclass
import inspect
from typing import Any, Dict, List, Optional

import requests
from fastmcp import FastMCP


@dataclass
class JenkinsAuthConfig:
    base_url: str
    username: Optional[str] = None
    api_token: Optional[str] = None
    verify_ssl: bool = True


class JenkinsMCPServer:
    """Lightweight Jenkins client used as an MCP-style tool server.

    This class exposes a set of small, composable methods that map onto
    common Jenkins REST endpoints. Callers (agents or Streamlit pages)
    can use these directly or wrap them as tools.
    """

    def __init__(self, config: JenkinsAuthConfig) -> None:
        self.config = config
        base = config.base_url.rstrip("/")
        # Public base info endpoint
        self._root_url = f"{base}"
        # JSON API helper root
        self._api_url = f"{base}/api/json"

    # ---- Low-level request helper ----

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        auth = None
        if self.config.username and self.config.api_token:
            auth = (self.config.username, self.config.api_token)

        try:
            resp = requests.request(
                method,
                url,
                auth=auth,
                verify=self.config.verify_ssl,
                timeout=15,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "url": url}

        content_type = resp.headers.get("content-type", "")
        body: Any
        if "application/json" in content_type:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = resp.text
        else:
            body = resp.text

        return {
            "ok": resp.ok,
            "status": resp.status_code,
            "url": url,
            "body": body,
        }

    # ---- Small helpers for job creation ----

    def _create_job_with_xml(self, job_name: str, xml_config: str, folder_path: str | None = None) -> Dict[str, Any]:
        """Create a Jenkins job from raw XML config.

        If ``folder_path`` is provided, the job is created under that
        folder hierarchy using the *Folders* plugin semantics, e.g.::

            folder_path="teamA/services" -> /job/teamA/job/services/createItem

        This is a thin wrapper around the ``/createItem`` endpoint that
        returns a stable, JSON-serialisable payload suitable for MCP tools.
        """

        headers = {"Content-Type": "application/xml"}

        if folder_path:
            parts = [p for p in folder_path.strip("/").split("/") if p]
            folder_segment = "/".join(f"job/{p}" for p in parts)
            create_path = f"/{folder_segment}/createItem?name={job_name}"
            job_url = f"{self.config.base_url.rstrip('/')}/{folder_segment}/job/{job_name}/"
        else:
            create_path = f"/createItem?name={job_name}"
            job_url = f"{self.config.base_url.rstrip('/')}/job/{job_name}/"

        resp = self._request(
            "POST",
            create_path,
            data=xml_config.encode("utf-8"),
            headers=headers,
        )
        return {
            "ok": bool(resp.get("ok")),
            "status": resp.get("status"),
            "job_name": job_name,
            "job_url": job_url,
            "raw": {
                "url": resp.get("url"),
                "body": resp.get("body"),
            },
        }

    # ---- Core informational endpoints ----

    def get_server_info(self) -> Dict[str, Any]:
        """Return Jenkins root API information."""

        return self._request("GET", "/api/json")

    def list_jobs(self) -> Dict[str, Any]:
        """List top-level jobs configured on the Jenkins instance."""

        params = {"tree": "jobs[name,color,url]"}
        return self._request("GET", "/api/json", params=params)

    def get_job_info(self, job_name: str) -> Dict[str, Any]:
        """Return metadata for a given job (buildable, last builds, etc.)."""

        path = f"/job/{job_name}/api/json"
        params = {"depth": 1}
        return self._request("GET", path, params=params)

    def list_builds(self, job_name: str, depth: int = 1) -> Dict[str, Any]:
        """List builds for a job with limited depth."""

        path = f"/job/{job_name}/api/json"
        params = {"tree": "builds[number,url,result,timestamp,duration]", "depth": depth}
        return self._request("GET", path, params=params)

    def get_last_build_info(self, job_name: str) -> Dict[str, Any]:
        """Return information about the last completed build for a job."""

        path = f"/job/{job_name}/lastBuild/api/json"
        return self._request("GET", path)

    def get_build_info(self, job_name: str, build_number: int) -> Dict[str, Any]:
        """Return information about a specific build number for a job."""

        path = f"/job/{job_name}/{build_number}/api/json"
        return self._request("GET", path)

    def get_build_console(self, job_name: str, build_number: int, tail_lines: int = 200) -> Dict[str, Any]:
        """Fetch console output for a build (last N lines)."""

        path = f"/job/{job_name}/{build_number}/logText/progressiveText"
        params = {"start": 0}
        # Jenkins does not support tail via API directly; callers can truncate.
        result = self._request("GET", path, params=params)
        if isinstance(result.get("body"), str) and tail_lines > 0:
            lines = result["body"].splitlines()[-tail_lines:]
            result["body"] = "\n".join(lines)
        return result

    # ---- Build triggers & queue ----

    def trigger_build(self, job_name: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Trigger a build for a job, optionally with parameters."""

        if parameters:
            path = f"/job/{job_name}/buildWithParameters"
            return self._request("POST", path, params=parameters)
        path = f"/job/{job_name}/build"
        return self._request("POST", path)

    def get_queue(self) -> Dict[str, Any]:
        """Return the current Jenkins build queue."""

        return self._request("GET", "/queue/api/json")

    def cancel_queue_item(self, queue_id: int) -> Dict[str, Any]:
        """Cancel a specific queue item by ID."""

        path = f"/queue/cancelItem?id={queue_id}"
        return self._request("POST", path)

    # ---- Nodes & executors ----

    def list_nodes(self) -> Dict[str, Any]:
        """List nodes (agents) connected to Jenkins."""

        path = "/computer/api/json"
        params = {"tree": "computer[displayName,offline,temporarilyOffline,numExecutors,monitorData[*]]"}
        return self._request("GET", path, params=params)

    def get_node_info(self, node_name: str) -> Dict[str, Any]:
        """Return information about a specific node."""

        path = f"/computer/{node_name}/api/json"
        return self._request("GET", path)

    # ---- Views ----

    def list_views(self) -> Dict[str, Any]:
        """List all Jenkins views and their URLs."""

        params = {"tree": "views[name,url]"}
        return self._request("GET", "/api/json", params=params)

    def get_view_info(self, view_name: str) -> Dict[str, Any]:
        """Return information and job list for a specific view."""

        path = f"/view/{view_name}/api/json"
        return self._request("GET", path)

    # ---- Job management ----

    def disable_job(self, job_name: str) -> Dict[str, Any]:
        """Disable a job (it will not accept new builds)."""

        path = f"/job/{job_name}/disable"
        return self._request("POST", path)

    def enable_job(self, job_name: str) -> Dict[str, Any]:
        """Re-enable a previously disabled job."""

        path = f"/job/{job_name}/enable"
        return self._request("POST", path)

    def delete_job(self, job_name: str) -> Dict[str, Any]:
        """Delete a job permanently (use with care)."""

        path = f"/job/{job_name}/doDelete"
        return self._request("POST", path)

    def copy_job(self, from_job: str, new_job: str) -> Dict[str, Any]:
        """Copy an existing job to a new name (same configuration)."""

        params = {"name": new_job, "mode": "copy", "from": from_job}
        return self._request("POST", "/createItem", params=params)

    def get_job_config_xml(self, job_name: str) -> Dict[str, Any]:
        """Retrieve the raw XML configuration for a job."""

        path = f"/job/{job_name}/config.xml"
        return self._request("GET", path)

    def update_job_config_xml(self, job_name: str, xml_config: str) -> Dict[str, Any]:
        """Update a job's XML configuration."""

        path = f"/job/{job_name}/config.xml"
        headers = {"Content-Type": "application/xml"}
        return self._request("POST", path, data=xml_config.encode("utf-8"), headers=headers)

    # ---- Pipeline job creation helpers ----

    def create_inline_pipeline_job(
        self,
        job_name: str,
        pipeline_script: str,
        description: str | None = None,
        disable_concurrent_builds: bool = False,
        folder_path: str | None = None,
    ) -> Dict[str, Any]:
        """Create a new Jenkins *Pipeline* job with an inline script.

        MCP-style inputs (all JSON-serialisable):
        - job_name: unique Jenkins job name to create.
        - pipeline_script: full declarative or scripted pipeline definition.
        - description: optional human-readable description.
        - disable_concurrent_builds: whether to block concurrent builds.
        - folder_path: optional Jenkins folder path ("teamA/services").
        """

        desc = (description or "Created via JenkinsMCPServer.create_inline_pipeline_job").strip()
        # Minimal, generic pipeline job config using workflow-job + workflow-cps
        concurrency_block = (
            "<properties>\n"
            "  <org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty/>\n"
            "</properties>\n"
            if disable_concurrent_builds
            else "<properties/>\n"
        )
        xml = f"""<flow-definition plugin="workflow-job">
    <description>{desc}</description>
    <keepDependencies>false</keepDependencies>
    {concurrency_block.rstrip()}\n
    <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
        <script><![CDATA[{pipeline_script}]]></script>
        <sandbox>true</sandbox>
    </definition>
    <triggers/>
    <disabled>false</disabled>
</flow-definition>
"""
        return self._create_job_with_xml(job_name, xml, folder_path=folder_path)

    def create_scm_pipeline_job(
        self,
        job_name: str,
        git_url: str,
        branch: str = "*/main",
        script_path: str = "Jenkinsfile",
        credentials_id: str | None = None,
        description: str | None = None,
        folder_path: str | None = None,
    ) -> Dict[str, Any]:
        """Create a *Pipeline from SCM* job (Git + Jenkinsfile).

        Inputs mirror a typical Jenkins configuration:
        - job_name: unique Jenkins job name to create.
        - git_url: repository URL (HTTP(S) or SSH).
        - branch: branch or refspec pattern (default "*/main").
        - script_path: relative path to Jenkinsfile inside the repo.
        - credentials_id: optional Jenkins credentials ID for the Git remote.
        - description: optional human-readable description.
        - folder_path: optional Jenkins folder path ("teamA/services").
        """

        desc = (description or "Created via JenkinsMCPServer.create_scm_pipeline_job").strip()

        cred_block = f"      <credentialsId>{credentials_id}</credentialsId>\n" if credentials_id else ""

        xml = f"""<flow-definition plugin="workflow-job">
    <description>{desc}</description>
    <keepDependencies>false</keepDependencies>
    <properties/>
    <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">
        <scm class="hudson.plugins.git.GitSCM" plugin="git">
            <configVersion>2</configVersion>
            <userRemoteConfigs>
                <hudson.plugins.git.UserRemoteConfig>
                    <url>{git_url}</url>
{cred_block}        </hudson.plugins.git.UserRemoteConfig>
            </userRemoteConfigs>
            <branches>
                <hudson.plugins.git.BranchSpec>
                    <name>{branch}</name>
                </hudson.plugins.git.BranchSpec>
            </branches>
            <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>
            <submoduleCfg class="list"/>
            <extensions/>
        </scm>
        <scriptPath>{script_path}</scriptPath>
        <lightweight>true</lightweight>
    </definition>
    <triggers/>
    <disabled>false</disabled>
</flow-definition>
"""
        return self._create_job_with_xml(job_name, xml, folder_path=folder_path)

    # ---- Plugins & system ----

    def list_plugins(self) -> Dict[str, Any]:
        """List installed plugins and their versions."""

        path = "/pluginManager/api/json?depth=1"
        return self._request("GET", path)

    def get_system_info(self) -> Dict[str, Any]:
        """Return basic system info (version, node mode, quieting, etc.)."""

        params = {"tree": "mode,nodeDescription,numExecutors,quietingDown,useSecurity"}
        return self._request("GET", "/api/json", params=params)

    # ---- Artifacts & changes ----

    def list_artifacts(self, job_name: str, build_number: int) -> Dict[str, Any]:
        """List artifacts produced by a build (paths and URLs)."""

        info = self.get_build_info(job_name, build_number)
        body = info.get("body", {}) if isinstance(info, dict) else {}
        artifacts = []
        if isinstance(body, dict):
            for art in body.get("artifacts", []):
                relative = art.get("relativePath")
                file_name = art.get("fileName")
                if relative and file_name:
                    artifacts.append({
                        "fileName": file_name,
                        "relativePath": relative,
                    })
        return {"ok": info.get("ok", False), "url": info.get("url"), "artifacts": artifacts}

    def get_build_changes(self, job_name: str, build_number: int) -> Dict[str, Any]:
        """Return change set information for a build (SCM commits)."""

        info = self.get_build_info(job_name, build_number)
        body = info.get("body", {}) if isinstance(info, dict) else {}
        changes: List[Dict[str, Any]] = []
        if isinstance(body, dict):
            for cs in body.get("changeSets", []):
                for item in cs.get("items", []):
                    changes.append(
                        {
                            "author": item.get("author", {}).get("fullName"),
                            "msg": item.get("msg"),
                            "id": item.get("commitId"),
                        }
                    )
        return {"ok": info.get("ok", False), "url": info.get("url"), "changes": changes}

    # ---- Search helpers ----

    def search_jobs(self, query: str, max_results: int = 20) -> Dict[str, Any]:
        """Client-side search across job names using the root job list."""

        jobs_resp = self.list_jobs()
        body = jobs_resp.get("body", {}) if isinstance(jobs_resp, dict) else {}
        results: List[Dict[str, Any]] = []
        q = (query or "").lower()
        if isinstance(body, dict):
            for job in body.get("jobs", []):
                name = str(job.get("name", ""))
                if q in name.lower():
                    results.append({"name": name, "url": job.get("url")})
                if len(results) >= max_results:
                    break
        return {"ok": jobs_resp.get("ok", False), "results": results}


def _schema_from_signature(sig: inspect.Signature) -> Dict[str, Any]:
    """Build a simple JSON-style input schema from a function signature.

    This mirrors the MCP tool schema shape used by LangChain's
    MultiServerMCPClient.getTools(), but is evaluated in-process for the
    Python JenkinsMCPServer so Streamlit and the agent can share it.
    """

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        param_type = "string"
        lname = name.lower()
        if "number" in lname or lname.endswith("_id") or lname.endswith("id") or "count" in lname:
            param_type = "integer"
        elif lname.startswith("is_") or lname.startswith("has_") or "enable" in lname or "disable" in lname:
            param_type = "boolean"

        properties[name] = {
            "type": param_type,
            "description": f"Argument '{name}' for tool call.",
        }
        if param.default is inspect._empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def describe_jenkins_tools() -> Dict[str, Dict[str, Any]]:
    """Describe JenkinsMCPServer methods as MCP-style tools.

    Returns a mapping of tool name to a lightweight MCP-style description:
    - name: tool name
    - description: first line of the method docstring
    - input_schema: JSON-serialisable schema built from the method signature

    This is intentionally similar to what MultiServerMCPClient.getTools()
    would return for an MCP server, but is evaluated locally against the
    Python class so that both the Jenkins agent and the Streamlit UI can
    stay in sync without hard-coding tool lists in multiple places.
    """

    tool_defs: Dict[str, Dict[str, Any]] = {}

    for name, fn in inspect.getmembers(JenkinsMCPServer, predicate=callable):
        # Ignore private helpers and the constructor
        if name.startswith("_") or name == "__init__":
            continue

        doc = (inspect.getdoc(fn) or "Jenkins tool.").strip().splitlines()[0]
        try:
            sig = inspect.signature(fn)
        except Exception:  # noqa: BLE001
            input_schema = {"type": "object", "properties": {}, "required": []}
        else:
            input_schema = _schema_from_signature(sig)

        tool_defs[name] = {
            "name": name,
            "description": doc,
            "input_schema": input_schema,
        }

    return tool_defs


# ---- FastMCP server definition ----

"""FastMCP-compatible server exposing JenkinsMCPServer tools.

This allows external MCP-aware clients (including LangChain's MultiServerMCPClient
on the JavaScript side) to connect to Jenkins via a standard MCP transport.

Jenkins connection details for the MCP server are sourced from environment
variables so the same code can be used both inside the Streamlit app and as a
standalone MCP server process:

- JENKINS_BASE_URL (required)
- JENKINS_USERNAME (optional)
- JENKINS_API_TOKEN (optional)
- JENKINS_VERIFY_SSL (optional, "true"/"false", default true)
"""

mcp = FastMCP("jenkins-mcp")


def _jenkins_client_from_env() -> JenkinsMCPServer:
    base_url = os.environ.get("JENKINS_BASE_URL", "http://localhost:8080")
    username = os.environ.get("JENKINS_USERNAME") or None
    api_token = os.environ.get("JENKINS_API_TOKEN") or None
    verify_raw = os.environ.get("JENKINS_VERIFY_SSL", "true").lower().strip()
    verify_ssl = verify_raw not in {"false", "0", "no"}

    cfg = JenkinsAuthConfig(
        base_url=base_url,
        username=username,
        api_token=api_token,
        verify_ssl=verify_ssl,
    )
    return JenkinsMCPServer(cfg)


@mcp.tool
def get_server_info() -> Dict[str, Any]:
    """Return Jenkins root API information."""

    return _jenkins_client_from_env().get_server_info()


@mcp.tool
def list_jobs() -> Dict[str, Any]:
    """List top-level jobs configured on the Jenkins instance."""

    return _jenkins_client_from_env().list_jobs()


@mcp.tool
def get_job_info(job_name: str) -> Dict[str, Any]:
    """Return metadata for a given job (buildable, last builds, etc.)."""

    return _jenkins_client_from_env().get_job_info(job_name)


@mcp.tool
def list_builds(job_name: str, depth: int = 1) -> Dict[str, Any]:
    """List builds for a job with limited depth."""

    return _jenkins_client_from_env().list_builds(job_name, depth=depth)


@mcp.tool
def get_last_build_info(job_name: str) -> Dict[str, Any]:
    """Return information about the last completed build for a job."""

    return _jenkins_client_from_env().get_last_build_info(job_name)


@mcp.tool
def get_build_info(job_name: str, build_number: int) -> Dict[str, Any]:
    """Return information about a specific build number for a job."""

    return _jenkins_client_from_env().get_build_info(job_name, build_number)


@mcp.tool
def get_build_console(job_name: str, build_number: int, tail_lines: int = 200) -> Dict[str, Any]:
    """Fetch console output for a build (last N lines)."""

    return _jenkins_client_from_env().get_build_console(job_name, build_number, tail_lines=tail_lines)


@mcp.tool
def trigger_build(job_name: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Trigger a build for a job, optionally with parameters."""

    return _jenkins_client_from_env().trigger_build(job_name, parameters=parameters)


@mcp.tool
def get_queue() -> Dict[str, Any]:
    """Return the current Jenkins build queue."""

    return _jenkins_client_from_env().get_queue()


@mcp.tool
def cancel_queue_item(queue_id: int) -> Dict[str, Any]:
    """Cancel a specific queue item by ID."""

    return _jenkins_client_from_env().cancel_queue_item(queue_id)


@mcp.tool
def list_nodes() -> Dict[str, Any]:
    """List nodes (agents) connected to Jenkins."""

    return _jenkins_client_from_env().list_nodes()


@mcp.tool
def get_node_info(node_name: str) -> Dict[str, Any]:
    """Return information about a specific node."""

    return _jenkins_client_from_env().get_node_info(node_name)


@mcp.tool
def list_views() -> Dict[str, Any]:
    """List all Jenkins views and their URLs."""

    return _jenkins_client_from_env().list_views()


@mcp.tool
def get_view_info(view_name: str) -> Dict[str, Any]:
    """Return information and job list for a specific view."""

    return _jenkins_client_from_env().get_view_info(view_name)


@mcp.tool
def disable_job(job_name: str) -> Dict[str, Any]:
    """Disable a job (it will not accept new builds)."""

    return _jenkins_client_from_env().disable_job(job_name)


@mcp.tool
def enable_job(job_name: str) -> Dict[str, Any]:
    """Re-enable a previously disabled job."""

    return _jenkins_client_from_env().enable_job(job_name)


@mcp.tool
def delete_job(job_name: str) -> Dict[str, Any]:
    """Delete a job permanently (use with care)."""

    return _jenkins_client_from_env().delete_job(job_name)


@mcp.tool
def copy_job(from_job: str, new_job: str) -> Dict[str, Any]:
    """Copy an existing job to a new name (same configuration)."""

    return _jenkins_client_from_env().copy_job(from_job, new_job)


@mcp.tool
def get_job_config_xml(job_name: str) -> Dict[str, Any]:
    """Retrieve the raw XML configuration for a job."""

    return _jenkins_client_from_env().get_job_config_xml(job_name)


@mcp.tool
def update_job_config_xml(job_name: str, xml_config: str) -> Dict[str, Any]:
    """Update a job's XML configuration."""

    return _jenkins_client_from_env().update_job_config_xml(job_name, xml_config)


@mcp.tool
def list_plugins() -> Dict[str, Any]:
    """List installed plugins and their versions."""

    return _jenkins_client_from_env().list_plugins()


@mcp.tool
def get_system_info() -> Dict[str, Any]:
    """Return basic system info (version, node mode, quieting, etc.)."""

    return _jenkins_client_from_env().get_system_info()


@mcp.tool
def list_artifacts(job_name: str, build_number: int) -> Dict[str, Any]:
    """List artifacts produced by a build (paths and URLs)."""

    return _jenkins_client_from_env().list_artifacts(job_name, build_number)


@mcp.tool
def get_build_changes(job_name: str, build_number: int) -> Dict[str, Any]:
    """Return change set information for a build (SCM commits)."""

    return _jenkins_client_from_env().get_build_changes(job_name, build_number)


@mcp.tool
def search_jobs(query: str, max_results: int = 20) -> Dict[str, Any]:
    """Client-side search across job names using the root job list."""

    return _jenkins_client_from_env().search_jobs(query, max_results=max_results)


@mcp.tool
def create_inline_pipeline_job(
    job_name: str,
    pipeline_script: str,
    description: str | None = None,
    disable_concurrent_builds: bool = False,
    folder_path: str | None = None,
) -> Dict[str, Any]:
    """Create a new Jenkins *Pipeline* job with an inline script."""

    return _jenkins_client_from_env().create_inline_pipeline_job(
        job_name=job_name,
        pipeline_script=pipeline_script,
        description=description,
        disable_concurrent_builds=disable_concurrent_builds,
        folder_path=folder_path,
    )


@mcp.tool
def create_scm_pipeline_job(
    job_name: str,
    git_url: str,
    branch: str = "*/main",
    script_path: str = "Jenkinsfile",
    credentials_id: str | None = None,
    description: str | None = None,
    folder_path: str | None = None,
) -> Dict[str, Any]:
    """Create a *Pipeline from SCM* job (Git + Jenkinsfile)."""

    return _jenkins_client_from_env().create_scm_pipeline_job(
        job_name=job_name,
        git_url=git_url,
        branch=branch,
        script_path=script_path,
        credentials_id=credentials_id,
        description=description,
        folder_path=folder_path,
    )


if __name__ == "__main__":
    # Allow running this module directly as an MCP server, e.g.:
    #   fastmcp run src/ai/mcp_servers/jenkins_server.py
    mcp.run()
