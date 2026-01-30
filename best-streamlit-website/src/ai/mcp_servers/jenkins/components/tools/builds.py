"""Jenkins build management tools."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import check_auth, jenkins_client_from_env


def list_builds(job_name: str, depth: int = 1, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """List builds for a job with limited depth."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().list_builds(job_name, depth=depth)


def get_last_build_info(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information about the last completed build for a job."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_last_build_info(job_name)


def get_build_info(job_name: str, build_number: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return information about a specific build number for a job."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_build_info(job_name, build_number)


def get_build_console(job_name: str, build_number: int, tail_lines: int = 200, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Fetch console output for a build (last N lines)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_build_console(job_name, build_number, tail_lines=tail_lines)


def trigger_build(job_name: str, parameters: Optional[Dict[str, Any]] = None, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Trigger a build for a job, optionally with parameters."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().trigger_build(job_name, parameters=parameters)


def get_queue(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return the current Jenkins build queue."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_queue()


def cancel_queue_item(queue_id: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Cancel a specific queue item by ID."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().cancel_queue_item(queue_id)


def list_artifacts(job_name: str, build_number: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """List artifacts produced by a build (paths and URLs)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().list_artifacts(job_name, build_number)


def get_build_changes(job_name: str, build_number: int, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return change set information for a build (SCM commits)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_build_changes(job_name, build_number)
