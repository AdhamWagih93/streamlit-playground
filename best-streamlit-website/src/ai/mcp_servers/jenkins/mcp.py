from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import JenkinsMCPServerConfig
from .utils.auth import auth_or_error
from .utils.client import JenkinsAuthConfig, JenkinsMCPServer

mcp = FastMCP("jenkins-mcp")

_CLIENT: Optional[JenkinsMCPServer] = None


def _jenkins_client_from_env() -> JenkinsMCPServer:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg_from_env = JenkinsMCPServerConfig.from_env()
    cfg = JenkinsAuthConfig(
        base_url=cfg_from_env.base_url,
        username=cfg_from_env.username,
        api_token=cfg_from_env.api_token,
        verify_ssl=cfg_from_env.verify_ssl,
    )
    _CLIENT = JenkinsMCPServer(cfg)
    return _CLIENT


@mcp.tool
def get_server_info(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return Jenkins root API information."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_server_info()


@mcp.tool
def list_jobs(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List top-level jobs configured on the Jenkins instance."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().list_jobs()


@mcp.tool
def get_job_info(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return metadata for a given job (buildable, last builds, etc.)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_job_info(job_name)


@mcp.tool
def list_builds(job_name: str, depth: int = 1, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """List builds for a job with limited depth."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().list_builds(job_name, depth=depth)


@mcp.tool
def get_last_build_info(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information about the last completed build for a job."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_last_build_info(job_name)


@mcp.tool
def get_build_info(job_name: str, build_number: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information about a specific build number for a job."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_build_info(job_name, build_number)


@mcp.tool
def get_build_console(job_name: str, build_number: int, tail_lines: int = 200, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Fetch console output for a build (last N lines)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_build_console(job_name, build_number, tail_lines=tail_lines)


@mcp.tool
def trigger_build(job_name: str, parameters: Optional[Dict[str, Any]] = None, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Trigger a build for a job, optionally with parameters."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().trigger_build(job_name, parameters=parameters)


@mcp.tool
def get_queue(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return the current Jenkins build queue."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_queue()


@mcp.tool
def cancel_queue_item(queue_id: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Cancel a specific queue item by ID."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().cancel_queue_item(queue_id)


@mcp.tool
def list_nodes(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List nodes (agents) connected to Jenkins."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().list_nodes()


@mcp.tool
def get_node_info(node_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information about a specific node."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_node_info(node_name)


@mcp.tool
def list_views(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List all Jenkins views and their URLs."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().list_views()


@mcp.tool
def get_view_info(view_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information and job list for a specific view."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_view_info(view_name)


@mcp.tool
def disable_job(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Disable a job (it will not accept new builds)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().disable_job(job_name)


@mcp.tool
def enable_job(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Re-enable a previously disabled job."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().enable_job(job_name)


@mcp.tool
def delete_job(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Delete a job permanently (use with care)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().delete_job(job_name)


@mcp.tool
def copy_job(from_job: str, new_job: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Copy an existing job to a new name (same configuration)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().copy_job(from_job, new_job)


@mcp.tool
def get_job_config_xml(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve the raw XML configuration for a job."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_job_config_xml(job_name)


@mcp.tool
def update_job_config_xml(job_name: str, xml_config: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Update a job's XML configuration."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().update_job_config_xml(job_name, xml_config)


@mcp.tool
def list_plugins(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List installed plugins and their versions."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().list_plugins()


@mcp.tool
def get_system_info(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return basic system info (version, node mode, quieting, etc.)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_system_info()


@mcp.tool
def list_artifacts(job_name: str, build_number: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """List artifacts produced by a build (paths and URLs)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().list_artifacts(job_name, build_number)


@mcp.tool
def get_build_changes(job_name: str, build_number: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return change set information for a build (SCM commits)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().get_build_changes(job_name, build_number)


@mcp.tool
def search_jobs(query: str, max_results: int = 20, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Client-side search across job names using the root job list."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().search_jobs(query, max_results=max_results)


@mcp.tool
def create_inline_pipeline_job(
    job_name: str,
    pipeline_script: str,
    description: str | None = None,
    disable_concurrent_builds: bool = False,
    folder_path: str | None = None,
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new Jenkins *Pipeline* job with an inline script."""

    err = auth_or_error(_client_token)
    if err:
        return err
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
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a *Pipeline from SCM* job (Git + Jenkinsfile)."""

    err = auth_or_error(_client_token)
    if err:
        return err
    return _jenkins_client_from_env().create_scm_pipeline_job(
        job_name=job_name,
        git_url=git_url,
        branch=branch,
        script_path=script_path,
        credentials_id=credentials_id,
        description=description,
        folder_path=folder_path,
    )


def run_stdio() -> None:
    """Run the Jenkins MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = JenkinsMCPServerConfig.from_env()

    # Best-effort network transport support (depends on fastmcp version).
    # We only pass kwargs that `FastMCP.run()` actually accepts.
    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    sig = inspect.signature(mcp.run)
    kwargs: Dict[str, Any] = {"transport": "http"}
    if "host" in sig.parameters:
        kwargs["host"] = host
    if "port" in sig.parameters:
        kwargs["port"] = port

    mcp.run(**kwargs)


if __name__ == "__main__":
    run_stdio()
