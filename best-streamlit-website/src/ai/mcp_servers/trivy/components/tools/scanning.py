"""Trivy scanning tools implementation."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import client_from_env


def trivy_health_check() -> Dict[str, Any]:
    """Check Trivy availability and version."""
    c = client_from_env()
    return c.version()


def trivy_update_db() -> Dict[str, Any]:
    """Update Trivy's vulnerability database."""
    c = client_from_env()
    return c.db_update()


def trivy_scan_image(
    image: str,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
    vuln_type: Optional[str] = None,
    scanners: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a container image for vulnerabilities."""
    c = client_from_env()
    return c.scan_image(
        image=image,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
        vuln_type=vuln_type,
        scanners=scanners,
    )


def trivy_scan_filesystem(
    path: str,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
    scanners: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a filesystem path for vulnerabilities."""
    c = client_from_env()
    return c.scan_filesystem(
        path=path,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
        scanners=scanners,
    )


def trivy_scan_repo(
    repo_url: str,
    branch: Optional[str] = None,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
    scanners: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a git repository for vulnerabilities."""
    c = client_from_env()
    return c.scan_repo(
        repo_url=repo_url,
        branch=branch,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
        scanners=scanners,
    )


def trivy_scan_config(
    path: str,
    severity: Optional[str] = None,
    skip_db_update: bool = False,
) -> Dict[str, Any]:
    """Scan configuration files for misconfigurations (IaC scanning)."""
    c = client_from_env()
    return c.scan_config(
        path=path,
        severity=severity,
        skip_db_update=skip_db_update,
    )


def trivy_scan_sbom(
    sbom_path: str,
    severity: Optional[str] = None,
    ignore_unfixed: bool = False,
    skip_db_update: bool = False,
) -> Dict[str, Any]:
    """Scan an SBOM (Software Bill of Materials) for vulnerabilities."""
    c = client_from_env()
    return c.scan_sbom(
        sbom_path=sbom_path,
        severity=severity,
        ignore_unfixed=ignore_unfixed,
        skip_db_update=skip_db_update,
    )


def trivy_generate_sbom(
    target: str,
    target_type: str = "image",
    output_format: str = "cyclonedx",
) -> Dict[str, Any]:
    """Generate an SBOM (Software Bill of Materials) for a target."""
    c = client_from_env()
    return c.generate_sbom(
        target=target,
        target_type=target_type,
        output_format=output_format,
    )


def trivy_list_plugins() -> Dict[str, Any]:
    """List installed Trivy plugins."""
    c = client_from_env()
    return c.list_plugins()


def trivy_clean_cache() -> Dict[str, Any]:
    """Clean Trivy cache (vulnerability database, etc.)."""
    c = client_from_env()
    return c.clean_cache()
