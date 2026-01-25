from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import TrivyMCPServerConfig
from .utils.client import TrivyClient


mcp = FastMCP("trivy-mcp")

_CLIENT: Optional[TrivyClient] = None


def _client_from_env() -> TrivyClient:
    """Get or create a TrivyClient from environment configuration."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = TrivyMCPServerConfig.from_env()
    _CLIENT = TrivyClient(
        cache_dir=cfg.cache_dir,
        timeout_seconds=cfg.timeout_seconds,
        severity=cfg.severity,
        ignore_unfixed=cfg.ignore_unfixed,
        skip_db_update=cfg.skip_db_update,
    )
    return _CLIENT


@mcp.tool
def trivy_health_check() -> Dict[str, Any]:
    """Check Trivy availability and version."""
    c = _client_from_env()
    return c.version()


@mcp.tool
def trivy_update_db() -> Dict[str, Any]:
    """Update Trivy's vulnerability database.

    This downloads the latest vulnerability database from the internet.
    """
    c = _client_from_env()
    return c.db_update()


@mcp.tool
def trivy_scan_image(
    image: str,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
    vuln_type: Optional[str] = None,
    scanners: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a container image for vulnerabilities.

    Args:
        image: Image reference (e.g., nginx:latest, alpine:3.18, registry/image:tag)
        severity: Comma-separated severities to report (CRITICAL,HIGH,MEDIUM,LOW,UNKNOWN)
        ignore_unfixed: Only show vulnerabilities with available fixes
        skip_db_update: Skip database update before scan (faster but may miss new vulns)
        vuln_type: Comma-separated vulnerability types (os,library)
        scanners: Comma-separated scanners to use (vuln,secret,misconfig)
    """
    c = _client_from_env()
    return c.scan_image(
        image=image,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
        vuln_type=vuln_type,
        scanners=scanners,
    )


@mcp.tool
def trivy_scan_filesystem(
    path: str,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
    scanners: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a filesystem path for vulnerabilities.

    Scans application dependencies (package.json, requirements.txt, etc.)
    and can detect secrets and misconfigurations.

    Args:
        path: Path to scan (directory or file)
        severity: Comma-separated severities to report
        ignore_unfixed: Only show vulnerabilities with available fixes
        skip_db_update: Skip database update before scan
        scanners: Comma-separated scanners (vuln,secret,misconfig,license)
    """
    c = _client_from_env()
    return c.scan_filesystem(
        path=path,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
        scanners=scanners,
    )


@mcp.tool
def trivy_scan_repo(
    repo_url: str,
    branch: Optional[str] = None,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
    scanners: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a git repository for vulnerabilities.

    Clones and scans a remote git repository.

    Args:
        repo_url: Git repository URL (https://github.com/user/repo)
        branch: Specific branch to scan (default: default branch)
        severity: Comma-separated severities to report
        ignore_unfixed: Only show vulnerabilities with available fixes
        skip_db_update: Skip database update before scan
        scanners: Comma-separated scanners to use
    """
    c = _client_from_env()
    return c.scan_repo(
        repo_url=repo_url,
        branch=branch,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
        scanners=scanners,
    )


@mcp.tool
def trivy_scan_config(
    path: str,
    severity: Optional[str] = None,
    skip_db_update: bool = False,
) -> Dict[str, Any]:
    """Scan configuration files for misconfigurations (IaC scanning).

    Scans Terraform, Kubernetes manifests, Dockerfiles, CloudFormation,
    and other infrastructure-as-code files.

    Args:
        path: Path to scan (directory with IaC files)
        severity: Comma-separated severities to report
        skip_db_update: Skip database update before scan
    """
    c = _client_from_env()
    return c.scan_config(
        path=path,
        severity=severity,
        skip_db_update=skip_db_update,
    )


@mcp.tool
def trivy_scan_sbom(
    sbom_path: str,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
) -> Dict[str, Any]:
    """Scan an SBOM (Software Bill of Materials) for vulnerabilities.

    Args:
        sbom_path: Path to SBOM file (CycloneDX or SPDX format)
        severity: Comma-separated severities to report
        ignore_unfixed: Only show vulnerabilities with available fixes
        skip_db_update: Skip database update before scan
    """
    c = _client_from_env()
    return c.scan_sbom(
        sbom_path=sbom_path,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
    )


@mcp.tool
def trivy_generate_sbom(
    target: str,
    target_type: str = "image",
    output_format: str = "cyclonedx",
) -> Dict[str, Any]:
    """Generate an SBOM (Software Bill of Materials) for a target.

    Args:
        target: Target to scan (image name, path, etc.)
        target_type: Type of target (image, filesystem, repo)
        output_format: SBOM format (cyclonedx, spdx, spdx-json)
    """
    c = _client_from_env()
    return c.generate_sbom(
        target=target,
        target_type=target_type,
        output_format=output_format,
    )


@mcp.tool
def trivy_list_plugins() -> Dict[str, Any]:
    """List installed Trivy plugins."""
    c = _client_from_env()
    return c.list_plugins()


@mcp.tool
def trivy_clean_cache() -> Dict[str, Any]:
    """Clean Trivy cache (vulnerability database, etc.)."""
    c = _client_from_env()
    return c.clean_cache()


def run_stdio() -> None:
    """Run the Trivy MCP server over HTTP.

    The function name is kept for backwards compatibility with existing
    entrypoints, but the server no longer supports stdio transport.
    """

    cfg = TrivyMCPServerConfig.from_env()

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
