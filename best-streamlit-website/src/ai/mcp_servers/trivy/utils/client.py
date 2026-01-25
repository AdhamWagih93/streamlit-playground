from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _subprocess_creationflags() -> int:
    """Avoid flashing a console window on Windows."""
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


@dataclass(frozen=True)
class TrivyResult:
    """Result of a trivy command execution."""

    ok: bool
    stdout: str
    stderr: str
    returncode: int
    command: List[str]
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "ok": self.ok,
            "returncode": self.returncode,
            "command": self.command,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        if not self.ok and self.stderr:
            result["stderr"] = self.stderr
        return result


class TrivyClient:
    """Trivy CLI wrapper for MCP operations."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        timeout_seconds: int = 300,
        severity: str = "CRITICAL,HIGH,MEDIUM,LOW",
        ignore_unfixed: bool = False,
        skip_db_update: bool = False,
    ):
        self.cache_dir = cache_dir
        self.timeout_seconds = timeout_seconds
        self.severity = severity
        self.ignore_unfixed = ignore_unfixed
        self.skip_db_update = skip_db_update
        self._trivy_bin: Optional[str] = None

    def _find_trivy(self) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Locate the trivy binary."""
        if self._trivy_bin:
            return self._trivy_bin, None

        trivy_bin = shutil.which("trivy")
        if not trivy_bin:
            return None, {"ok": False, "error": "trivy CLI not found in PATH"}

        self._trivy_bin = trivy_bin
        return trivy_bin, None

    def _build_base_args(self) -> List[str]:
        """Build common trivy arguments."""
        args = []
        if self.cache_dir:
            args.extend(["--cache-dir", self.cache_dir])
        return args

    def _run(
        self,
        args: List[str],
        timeout: Optional[int] = None,
        parse_json: bool = True,
    ) -> TrivyResult:
        """Run a trivy command and return the result."""
        trivy_bin, err = self._find_trivy()
        if err:
            return TrivyResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                command=["trivy"] + args,
                error=err.get("error", "trivy not found"),
            )

        cmd = [trivy_bin] + self._build_base_args() + list(args)
        timeout_s = timeout if timeout is not None else self.timeout_seconds

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                creationflags=_subprocess_creationflags(),
            )
        except subprocess.TimeoutExpired:
            return TrivyResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                command=cmd,
                error=f"Command timed out after {timeout_s}s",
            )
        except Exception as exc:
            return TrivyResult(
                ok=False,
                stdout="",
                stderr="",
                returncode=-1,
                command=cmd,
                error=str(exc),
            )

        # Parse JSON output if requested and available
        data = None
        if parse_json and proc.returncode == 0 and proc.stdout:
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                # Output might not be JSON, that's okay
                pass

        return TrivyResult(
            ok=proc.returncode == 0,
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
            returncode=proc.returncode,
            command=cmd,
            data=data,
            error=None if proc.returncode == 0 else (proc.stderr or "").strip(),
        )

    def version(self) -> Dict[str, Any]:
        """Get trivy version."""
        result = self._run(["--version"], parse_json=False)
        if result.ok:
            return {"ok": True, "version": result.stdout}
        return result.to_dict()

    def db_update(self) -> Dict[str, Any]:
        """Update the vulnerability database."""
        result = self._run(["image", "--download-db-only"], timeout=600, parse_json=False)
        if result.ok:
            return {"ok": True, "message": "Database updated successfully"}
        return result.to_dict()

    def scan_image(
        self,
        image: str,
        severity: Optional[str] = None,
        ignore_unfixed: Optional[bool] = None,
        skip_db_update: Optional[bool] = None,
        vuln_type: Optional[str] = None,
        scanners: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scan a container image for vulnerabilities.

        Args:
            image: Image reference (e.g., nginx:latest, registry/image:tag)
            severity: Comma-separated severities (CRITICAL,HIGH,MEDIUM,LOW,UNKNOWN)
            ignore_unfixed: Only show fixed vulnerabilities
            skip_db_update: Skip database update before scan
            vuln_type: Comma-separated vuln types (os,library)
            scanners: Comma-separated scanners (vuln,secret,misconfig)
        """
        args = ["image", "--format", "json"]

        sev = severity or self.severity
        if sev:
            args.extend(["--severity", sev])

        if ignore_unfixed if ignore_unfixed is not None else self.ignore_unfixed:
            args.append("--ignore-unfixed")

        if skip_db_update if skip_db_update is not None else self.skip_db_update:
            args.append("--skip-db-update")

        if vuln_type:
            args.extend(["--vuln-type", vuln_type])

        if scanners:
            args.extend(["--scanners", scanners])

        args.append(image)

        result = self._run(args)
        return result.to_dict()

    def scan_filesystem(
        self,
        path: str,
        severity: Optional[str] = None,
        ignore_unfixed: Optional[bool] = None,
        skip_db_update: Optional[bool] = None,
        scanners: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scan a filesystem path for vulnerabilities.

        Args:
            path: Path to scan (directory or file)
            severity: Comma-separated severities
            ignore_unfixed: Only show fixed vulnerabilities
            skip_db_update: Skip database update before scan
            scanners: Comma-separated scanners (vuln,secret,misconfig,license)
        """
        args = ["filesystem", "--format", "json"]

        sev = severity or self.severity
        if sev:
            args.extend(["--severity", sev])

        if ignore_unfixed if ignore_unfixed is not None else self.ignore_unfixed:
            args.append("--ignore-unfixed")

        if skip_db_update if skip_db_update is not None else self.skip_db_update:
            args.append("--skip-db-update")

        if scanners:
            args.extend(["--scanners", scanners])

        args.append(path)

        result = self._run(args)
        return result.to_dict()

    def scan_repo(
        self,
        repo_url: str,
        branch: Optional[str] = None,
        severity: Optional[str] = None,
        ignore_unfixed: Optional[bool] = None,
        skip_db_update: Optional[bool] = None,
        scanners: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scan a git repository for vulnerabilities.

        Args:
            repo_url: Git repository URL
            branch: Branch to scan
            severity: Comma-separated severities
            ignore_unfixed: Only show fixed vulnerabilities
            skip_db_update: Skip database update before scan
            scanners: Comma-separated scanners
        """
        args = ["repository", "--format", "json"]

        sev = severity or self.severity
        if sev:
            args.extend(["--severity", sev])

        if ignore_unfixed if ignore_unfixed is not None else self.ignore_unfixed:
            args.append("--ignore-unfixed")

        if skip_db_update if skip_db_update is not None else self.skip_db_update:
            args.append("--skip-db-update")

        if scanners:
            args.extend(["--scanners", scanners])

        if branch:
            args.extend(["--branch", branch])

        args.append(repo_url)

        result = self._run(args, timeout=600)  # Repos can take longer
        return result.to_dict()

    def scan_config(
        self,
        path: str,
        severity: Optional[str] = None,
        skip_db_update: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Scan configuration files for misconfigurations (IaC scanning).

        Args:
            path: Path to scan (directory with Terraform, K8s manifests, Dockerfiles, etc.)
            severity: Comma-separated severities
            skip_db_update: Skip database update before scan
        """
        args = ["config", "--format", "json"]

        sev = severity or self.severity
        if sev:
            args.extend(["--severity", sev])

        if skip_db_update if skip_db_update is not None else self.skip_db_update:
            args.append("--skip-db-update")

        args.append(path)

        result = self._run(args)
        return result.to_dict()

    def scan_sbom(
        self,
        sbom_path: str,
        severity: Optional[str] = None,
        ignore_unfixed: Optional[bool] = None,
        skip_db_update: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Scan an SBOM file for vulnerabilities.

        Args:
            sbom_path: Path to SBOM file (CycloneDX or SPDX format)
            severity: Comma-separated severities
            ignore_unfixed: Only show fixed vulnerabilities
            skip_db_update: Skip database update before scan
        """
        args = ["sbom", "--format", "json"]

        sev = severity or self.severity
        if sev:
            args.extend(["--severity", sev])

        if ignore_unfixed if ignore_unfixed is not None else self.ignore_unfixed:
            args.append("--ignore-unfixed")

        if skip_db_update if skip_db_update is not None else self.skip_db_update:
            args.append("--skip-db-update")

        args.append(sbom_path)

        result = self._run(args)
        return result.to_dict()

    def generate_sbom(
        self,
        target: str,
        target_type: str = "image",
        output_format: str = "cyclonedx",
    ) -> Dict[str, Any]:
        """Generate an SBOM for a target.

        Args:
            target: Target to scan (image name, path, etc.)
            target_type: Type of target (image, filesystem, repo)
            output_format: SBOM format (cyclonedx, spdx, spdx-json)
        """
        args = [target_type, "--format", output_format]
        args.append(target)

        result = self._run(args, parse_json=False)
        if result.ok:
            return {"ok": True, "sbom": result.stdout}
        return result.to_dict()

    def list_plugins(self) -> Dict[str, Any]:
        """List installed Trivy plugins."""
        result = self._run(["plugin", "list"], parse_json=False)
        if result.ok:
            plugins = []
            for line in result.stdout.split("\n"):
                if line.strip() and not line.startswith("Installed"):
                    plugins.append(line.strip())
            return {"ok": True, "plugins": plugins}
        return result.to_dict()

    def clean_cache(self) -> Dict[str, Any]:
        """Clean Trivy cache (vulnerability database, etc.)."""
        result = self._run(["clean", "--all"], parse_json=False)
        if result.ok:
            return {"ok": True, "message": "Cache cleaned successfully"}
        return result.to_dict()
