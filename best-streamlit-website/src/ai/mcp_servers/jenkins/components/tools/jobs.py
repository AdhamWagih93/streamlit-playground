"""Jenkins job management tools."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import check_auth, jenkins_client_from_env


def list_jobs(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List top-level jobs configured on the Jenkins instance."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().list_jobs()


def get_job_info(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Return metadata for a given job (buildable, last builds, etc.)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_job_info(job_name)


def disable_job(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Disable a job (it will not accept new builds)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().disable_job(job_name)


def enable_job(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Re-enable a previously disabled job."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().enable_job(job_name)


def delete_job(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Delete a job permanently (use with care)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().delete_job(job_name)


def copy_job(from_job: str, new_job: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Copy an existing job to a new name (same configuration)."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().copy_job(from_job, new_job)


def get_job_config_xml(job_name: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve the raw XML configuration for a job."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().get_job_config_xml(job_name)


def update_job_config_xml(job_name: str, xml_config: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Update a job's XML configuration."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().update_job_config_xml(job_name, xml_config)


def search_jobs(query: str, max_results: int = 20, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Client-side search across job names using the root job list."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().search_jobs(query, max_results=max_results)


def create_inline_pipeline_job(
    job_name: str,
    pipeline_script: str,
    description: str | None = None,
    disable_concurrent_builds: bool = False,
    folder_path: str | None = None,
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new Jenkins *Pipeline* job with an inline script."""
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().create_inline_pipeline_job(
        job_name=job_name,
        pipeline_script=pipeline_script,
        description=description,
        disable_concurrent_builds=disable_concurrent_builds,
        folder_path=folder_path,
    )


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
    err = check_auth(_client_token)
    if err:
        return err
    return jenkins_client_from_env().create_scm_pipeline_job(
        job_name=job_name,
        git_url=git_url,
        branch=branch,
        script_path=script_path,
        credentials_id=credentials_id,
        description=description,
        folder_path=folder_path,
    )
