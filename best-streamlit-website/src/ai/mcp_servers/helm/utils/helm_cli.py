from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class HelmExecConfig:
    helm_bin: str
    auto_install: bool = True
    auto_install_version: str = "v3.14.4"
    auto_install_dir: Optional[str] = None
    kubeconfig: Optional[str] = None
    kubecontext: Optional[str] = None


class HelmCliError(RuntimeError):
    pass


def _build_env(cfg: HelmExecConfig, base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(base_env or {})
    if cfg.kubeconfig:
        env["KUBECONFIG"] = cfg.kubeconfig
    if cfg.kubecontext:
        env["HELM_KUBECONTEXT"] = cfg.kubecontext
    return env


def probe_helm_binary(cfg: HelmExecConfig) -> Dict[str, Any]:
    """Check whether Helm is available without attempting installation."""

    helm_bin = cfg.helm_bin
    p = Path(helm_bin)
    if p.is_file():
        return {"ok": True, "source": "path", "path": str(p)}

    found = shutil.which(helm_bin)
    if found:
        return {"ok": True, "source": "which", "path": found}

    # Check cached auto-install location
    cache_dir = _auto_install_dir(cfg)
    cached = cache_dir / ("helm.exe" if platform.system().lower().startswith("win") else "helm")
    if cached.is_file():
        return {"ok": True, "source": "cache", "path": str(cached)}

    return {"ok": False, "error": f"Helm binary not found: {helm_bin}", "auto_install": bool(cfg.auto_install), "cache_dir": str(cache_dir)}


def _auto_install_dir(cfg: HelmExecConfig) -> Path:
    if cfg.auto_install_dir:
        return Path(cfg.auto_install_dir)

    # Keep it project-local when possible (nice for dev), else fall back to user cache.
    # Streamlit runs from repo root, so ./data is available.
    repo_data = Path("data")
    if repo_data.exists() and repo_data.is_dir():
        return repo_data / "_bin"

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "helm-mcp"

    return Path.home() / ".cache" / "helm-mcp"


def _download_helm(*, version: str, dest_dir: Path) -> Path:
    sys_name = platform.system().lower()
    arch = platform.machine().lower()

    if arch in {"x86_64", "amd64"}:
        arch = "amd64"
    elif arch in {"aarch64", "arm64"}:
        arch = "arm64"

    if sys_name.startswith("win"):
        os_part = "windows"
        ext = "zip"
        bin_name = "helm.exe"
    elif sys_name.startswith("linux"):
        os_part = "linux"
        ext = "tar.gz"
        bin_name = "helm"
    elif sys_name.startswith("darwin"):
        os_part = "darwin"
        ext = "tar.gz"
        bin_name = "helm"
    else:
        raise HelmCliError(f"Unsupported OS for auto-install: {platform.system()}")

    version = version if version.startswith("v") else f"v{version}"
    url = f"https://get.helm.sh/helm-{version}-{os_part}-{arch}.{ext}"

    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / f"helm-{version}-{os_part}-{arch}.{ext}"

    try:
        urllib.request.urlretrieve(url, archive_path)  # noqa: S310
    except Exception as exc:
        raise HelmCliError(f"Failed to download Helm from {url}: {exc}") from exc

    target_path = dest_dir / bin_name
    try:
        if ext == "zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                member = next((m for m in zf.namelist() if m.endswith(f"/{bin_name}") or m.endswith(f"\\{bin_name}")), None)
                if not member:
                    raise HelmCliError(f"Downloaded Helm archive missing {bin_name}")
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive_path, "r:gz") as tf:
                member = next((m for m in tf.getmembers() if m.name.endswith(f"/{bin_name}")), None)
                if not member:
                    raise HelmCliError(f"Downloaded Helm archive missing {bin_name}")
                f = tf.extractfile(member)
                if f is None:
                    raise HelmCliError(f"Failed to extract {bin_name} from archive")
                with f, open(target_path, "wb") as dst:
                    shutil.copyfileobj(f, dst)
    finally:
        try:
            archive_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass

    if not sys_name.startswith("win"):
        try:
            target_path.chmod(0o755)
        except Exception:
            pass

    return target_path


def _resolve_helm_bin(cfg: HelmExecConfig) -> str:
    # Explicit path
    p = Path(cfg.helm_bin)
    if p.is_file():
        return str(p)

    found = shutil.which(cfg.helm_bin)
    if found:
        return found

    cache_dir = _auto_install_dir(cfg)
    cached = cache_dir / ("helm.exe" if platform.system().lower().startswith("win") else "helm")
    if cached.is_file():
        return str(cached)

    if not cfg.auto_install:
        raise HelmCliError(f"Helm binary not found: {cfg.helm_bin}")

    installed = _download_helm(version=cfg.auto_install_version, dest_dir=cache_dir)
    return str(installed)


def _run(
    cfg: HelmExecConfig,
    args: Sequence[str],
    *,
    base_env: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 120,
) -> Tuple[int, str, str]:
    """Run `helm <args>` and return (code, stdout, stderr)."""

    global_args: List[str] = []
    if cfg.kubeconfig:
        global_args.extend(["--kubeconfig", cfg.kubeconfig])
    if cfg.kubecontext:
        global_args.extend(["--kube-context", cfg.kubecontext])

    helm_bin = _resolve_helm_bin(cfg)
    cmd = [helm_bin, *global_args, *list(args)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=_build_env(cfg, base_env=base_env),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise HelmCliError(f"Helm command timed out after {timeout_seconds}s: {' '.join(cmd)}") from exc

    return int(proc.returncode), (proc.stdout or ""), (proc.stderr or "")


def run_json(cfg: HelmExecConfig, args: Sequence[str], *, timeout_seconds: int = 120) -> Dict[str, Any]:
    code, out, err = _run(cfg, args, timeout_seconds=timeout_seconds)
    if code != 0:
        return {"ok": False, "error": err.strip() or out.strip() or f"helm exited {code}", "command": [cfg.helm_bin, *list(args)]}

    text = out.strip()
    if not text:
        return {"ok": True, "data": None}
    try:
        return {"ok": True, "data": json.loads(text)}
    except Exception:
        # Some helm subcommands return non-JSON even with -o json; return as text.
        return {"ok": True, "data": None, "text": text}


def run_text(cfg: HelmExecConfig, args: Sequence[str], *, timeout_seconds: int = 120) -> Dict[str, Any]:
    code, out, err = _run(cfg, args, timeout_seconds=timeout_seconds)
    if code != 0:
        return {"ok": False, "error": err.strip() or out.strip() or f"helm exited {code}", "command": [cfg.helm_bin, *list(args)]}
    return {"ok": True, "text": out}


def _ns_args(namespace: Optional[str]) -> List[str]:
    return ["--namespace", namespace] if namespace else []


def list_releases(cfg: HelmExecConfig, *, namespace: Optional[str] = None, all_namespaces: bool = False) -> Dict[str, Any]:
    args = ["list", "-o", "json"]
    if all_namespaces:
        args.append("-A")
    else:
        args.extend(_ns_args(namespace))
    result = run_json(cfg, args)
    if not result.get("ok"):
        return result
    data = result.get("data")
    releases = data if isinstance(data, list) else []
    return {"ok": True, "releases": releases}


def get_status(cfg: HelmExecConfig, release: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
    result = run_json(cfg, ["status", release, "-o", "json", *_ns_args(namespace)])
    if not result.get("ok"):
        return result
    return {"ok": True, "status": result.get("data")}


def get_history(cfg: HelmExecConfig, release: str, *, namespace: Optional[str] = None, max_entries: int = 20) -> Dict[str, Any]:
    result = run_json(cfg, ["history", release, "-o", "json", "--max", str(int(max_entries)), *_ns_args(namespace)])
    if not result.get("ok"):
        return result
    data = result.get("data")
    history = data if isinstance(data, list) else []
    return {"ok": True, "history": history}


def get_values(cfg: HelmExecConfig, release: str, *, namespace: Optional[str] = None, all_values: bool = False) -> Dict[str, Any]:
    args = ["get", "values", release]
    if all_values:
        args.append("--all")
    args.extend(["-o", "json"])
    args.extend(_ns_args(namespace))
    result = run_json(cfg, args)
    if not result.get("ok"):
        # Fallback to yaml/text (some Helm builds may not support -o json here)
        args2: List[str] = ["get", "values", release]
        if all_values:
            args2.append("--all")
        args2.extend(_ns_args(namespace))
        result2 = run_text(cfg, args2)
        return result2 if not result2.get("ok") else {"ok": True, "values_text": result2.get("text", "")}

    return {"ok": True, "values": result.get("data")}


def get_manifest(cfg: HelmExecConfig, release: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
    return run_text(cfg, ["get", "manifest", release, *_ns_args(namespace)], timeout_seconds=180)


def uninstall(cfg: HelmExecConfig, release: str, *, namespace: Optional[str] = None, keep_history: bool = False, wait: bool = True, timeout: str = "5m") -> Dict[str, Any]:
    args = ["uninstall", release]
    args.extend(_ns_args(namespace))
    if keep_history:
        args.append("--keep-history")
    if wait:
        args.append("--wait")
    if timeout:
        args.extend(["--timeout", timeout])
    return run_text(cfg, args, timeout_seconds=300)


def upgrade_install(
    cfg: HelmExecConfig,
    release: str,
    chart: str,
    *,
    namespace: Optional[str] = None,
    create_namespace: bool = True,
    version: Optional[str] = None,
    values_yaml: Optional[str] = None,
    values_files: Optional[List[str]] = None,
    set_values: Optional[Dict[str, Any]] = None,
    wait: bool = True,
    atomic: bool = False,
    timeout: str = "10m",
    dry_run: bool = False,
) -> Dict[str, Any]:
    args: List[str] = ["upgrade", "--install", release, chart]
    args.extend(_ns_args(namespace))
    if create_namespace:
        args.append("--create-namespace")
    if version:
        args.extend(["--version", version])
    if wait:
        args.append("--wait")
    if atomic:
        args.append("--atomic")
    if timeout:
        args.extend(["--timeout", timeout])
    if dry_run:
        args.append("--dry-run")

    # Values files
    for vf in values_files or []:
        if vf:
            args.extend(["--values", vf])

    # Inline values YAML (passed via stdin using `--values -`)
    stdin_data: Optional[str] = None
    if values_yaml and values_yaml.strip():
        args.extend(["--values", "-"])
        stdin_data = values_yaml

    # --set values (flattened)
    if set_values:
        for k, v in set_values.items():
            if v is None:
                continue
            args.extend(["--set", f"{k}={v}"])

    try:
        cmd = [_resolve_helm_bin(cfg), *args]
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            env=_build_env(cfg),
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": f"Helm command timed out: {' '.join(cmd)}", "command": cmd}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip(), "command": cmd}

    return {"ok": True, "text": proc.stdout}


def repo_list(cfg: HelmExecConfig) -> Dict[str, Any]:
    result = run_json(cfg, ["repo", "list", "-o", "json"])
    if not result.get("ok"):
        return result
    data = result.get("data")
    repos = data if isinstance(data, list) else []
    return {"ok": True, "repos": repos}


def repo_add(cfg: HelmExecConfig, name: str, url: str, *, username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
    args: List[str] = ["repo", "add", name, url]
    if username:
        args.extend(["--username", username])
    if password:
        args.extend(["--password", password])
    return run_text(cfg, args)


def repo_update(cfg: HelmExecConfig) -> Dict[str, Any]:
    return run_text(cfg, ["repo", "update"], timeout_seconds=300)


def search_repo(cfg: HelmExecConfig, query: str, *, versions: bool = False) -> Dict[str, Any]:
    args = ["search", "repo", query, "-o", "json"]
    if versions:
        args.append("--versions")
    result = run_json(cfg, args)
    if not result.get("ok"):
        return result
    data = result.get("data")
    matches = data if isinstance(data, list) else []
    return {"ok": True, "matches": matches}


def lint(cfg: HelmExecConfig, chart: str, *, values_yaml: Optional[str] = None, values_files: Optional[List[str]] = None) -> Dict[str, Any]:
    args: List[str] = ["lint", chart]
    for vf in values_files or []:
        if vf:
            args.extend(["--values", vf])
    stdin_data: Optional[str] = None
    if values_yaml and values_yaml.strip():
        args.extend(["--values", "-"])
        stdin_data = values_yaml

    cmd = [_resolve_helm_bin(cfg), *args]
    try:
        proc = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True, env=_build_env(cfg), timeout=180)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "command": cmd}

    ok = proc.returncode == 0
    return {"ok": ok, "text": (proc.stdout or "") + (proc.stderr or ""), "command": cmd}


def template(cfg: HelmExecConfig, release: str, chart: str, *, namespace: Optional[str] = None, values_yaml: Optional[str] = None) -> Dict[str, Any]:
    args: List[str] = ["template", release, chart]
    args.extend(_ns_args(namespace))
    stdin_data: Optional[str] = None
    if values_yaml and values_yaml.strip():
        args.extend(["--values", "-"])
        stdin_data = values_yaml

    cmd = [_resolve_helm_bin(cfg), *args]
    try:
        proc = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True, env=_build_env(cfg), timeout=180)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "command": cmd}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip(), "command": cmd}
    return {"ok": True, "manifest": proc.stdout}


def raw(cfg: HelmExecConfig, args: List[str]) -> Dict[str, Any]:
    # Intentionally uses text output; caller can parse.
    return run_text(cfg, args, timeout_seconds=300)
