from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig
from src.ai.mcp_servers.kubernetes.config import KubernetesMCPServerConfig
from src.ai.mcp_servers.docker.config import DockerMCPServerConfig
from src.ai.mcp_servers.nexus.config import NexusMCPServerConfig
from src.ai.mcp_servers.scheduler.config import SchedulerMCPServerConfig
from src.ai.mcp_servers.git.config import GitMCPServerConfig
from src.ai.mcp_servers.trivy.config import TrivyMCPServerConfig
from src.config_utils import env_str
from src.admin_config import AdminConfig, load_admin_config
from src.page_catalog import known_page_paths


def _normalise_streamlit_transport(transport: str) -> str:
    t = (transport or "").lower().strip()
    if t == "http":
        return "streamable-http"
    return t or "streamable-http"


def _normalise_streamlit_url(url: str, transport: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if _normalise_streamlit_transport(transport) != "streamable-http":
        return u
    base = u.rstrip("/")
    if base.endswith("/mcp"):
        return base
    return base + "/mcp"


@dataclass(frozen=True)
class StreamlitAppConfig:
    """Streamlit app configuration.

    This is UI/runtime configuration (not Streamlit's config.toml).
    Env-first with safe local-dev defaults.

    Notes:
    - Jenkins MCP tool calls require the same token configured on the Jenkins
      MCP server. For local dev, both default to the same value.
    """

    jenkins: JenkinsMCPServerConfig
    kubernetes: KubernetesMCPServerConfig
    docker: DockerMCPServerConfig
    nexus: NexusMCPServerConfig
    scheduler: SchedulerMCPServerConfig
    git: GitMCPServerConfig
    trivy: TrivyMCPServerConfig

    @classmethod
    def from_env(cls) -> "StreamlitAppConfig":
        # Allow Streamlit to override the Jenkins token independently if desired,
        # while still defaulting to the same dev token as the server config.
        jenkins = JenkinsMCPServerConfig.from_env()
        streamlit_token = env_str("STREAMLIT_JENKINS_MCP_CLIENT_TOKEN", jenkins.mcp_client_token)
        j_transport = _normalise_streamlit_transport(env_str("STREAMLIT_JENKINS_MCP_TRANSPORT", jenkins.mcp_transport))
        j_url = _normalise_streamlit_url(env_str("STREAMLIT_JENKINS_MCP_URL", jenkins.mcp_url), j_transport)
        jenkins = JenkinsMCPServerConfig(
            base_url=jenkins.base_url,
            username=jenkins.username,
            api_token=jenkins.api_token,
            verify_ssl=jenkins.verify_ssl,
            mcp_client_token=streamlit_token,
            mcp_transport=j_transport,
            mcp_host=jenkins.mcp_host,
            mcp_port=jenkins.mcp_port,
            mcp_url=j_url,
        )

        kubernetes = KubernetesMCPServerConfig.from_env()
        k_transport = _normalise_streamlit_transport(env_str("STREAMLIT_KUBERNETES_MCP_TRANSPORT", kubernetes.mcp_transport))
        k_url = _normalise_streamlit_url(env_str("STREAMLIT_KUBERNETES_MCP_URL", kubernetes.mcp_url), k_transport)
        kubernetes = KubernetesMCPServerConfig(
            kubeconfig=kubernetes.kubeconfig,
            context=kubernetes.context,
            mcp_transport=k_transport,
            mcp_host=kubernetes.mcp_host,
            mcp_port=kubernetes.mcp_port,
            mcp_url=k_url,
        )

        docker = DockerMCPServerConfig.from_env()
        d_transport = _normalise_streamlit_transport(env_str("STREAMLIT_DOCKER_MCP_TRANSPORT", docker.mcp_transport))
        d_url = _normalise_streamlit_url(env_str("STREAMLIT_DOCKER_MCP_URL", docker.mcp_url), d_transport)
        docker = DockerMCPServerConfig(
            docker_host=docker.docker_host,
            docker_tls_verify=docker.docker_tls_verify,
            docker_cert_path=docker.docker_cert_path,
            docker_timeout_seconds=docker.docker_timeout_seconds,
            mcp_transport=d_transport,
            mcp_host=docker.mcp_host,
            mcp_port=docker.mcp_port,
            mcp_url=d_url,
        )

        nexus = NexusMCPServerConfig.from_env()
        n_transport = _normalise_streamlit_transport(env_str("STREAMLIT_NEXUS_MCP_TRANSPORT", nexus.mcp_transport))
        n_url = _normalise_streamlit_url(env_str("STREAMLIT_NEXUS_MCP_URL", nexus.mcp_url), n_transport)
        nexus = NexusMCPServerConfig(
            base_url=nexus.base_url,
            username=nexus.username,
            password=nexus.password,
            token=nexus.token,
            verify_ssl=nexus.verify_ssl,
            mcp_client_token=env_str("STREAMLIT_NEXUS_MCP_CLIENT_TOKEN", nexus.mcp_client_token or "") or None,
            allow_raw=nexus.allow_raw,
            mcp_transport=n_transport,
            mcp_host=nexus.mcp_host,
            mcp_port=nexus.mcp_port,
            mcp_url=n_url,
        )

        scheduler = SchedulerMCPServerConfig.from_env()
        s_transport = _normalise_streamlit_transport(env_str("STREAMLIT_SCHEDULER_MCP_TRANSPORT", scheduler.mcp_transport))
        s_url = _normalise_streamlit_url(env_str("STREAMLIT_SCHEDULER_MCP_URL", scheduler.mcp_url), s_transport)
        scheduler = SchedulerMCPServerConfig(
            mcp_transport=s_transport,
            mcp_url=s_url,
            mcp_host=scheduler.mcp_host,
            mcp_port=scheduler.mcp_port,
        )

        git = GitMCPServerConfig.from_env()
        g_transport = _normalise_streamlit_transport(env_str("STREAMLIT_GIT_MCP_TRANSPORT", git.mcp_transport))
        g_url = _normalise_streamlit_url(env_str("STREAMLIT_GIT_MCP_URL", git.mcp_url), g_transport)
        git = GitMCPServerConfig(
            repo_path=git.repo_path,
            default_branch=git.default_branch,
            timeout_seconds=git.timeout_seconds,
            mcp_transport=g_transport,
            mcp_host=git.mcp_host,
            mcp_port=git.mcp_port,
            mcp_url=g_url,
        )

        trivy = TrivyMCPServerConfig.from_env()
        t_transport = _normalise_streamlit_transport(env_str("STREAMLIT_TRIVY_MCP_TRANSPORT", trivy.mcp_transport))
        t_url = _normalise_streamlit_url(env_str("STREAMLIT_TRIVY_MCP_URL", trivy.mcp_url), t_transport)
        trivy = TrivyMCPServerConfig(
            cache_dir=trivy.cache_dir,
            timeout_seconds=trivy.timeout_seconds,
            severity=trivy.severity,
            ignore_unfixed=trivy.ignore_unfixed,
            skip_db_update=trivy.skip_db_update,
            mcp_transport=t_transport,
            mcp_host=trivy.mcp_host,
            mcp_port=trivy.mcp_port,
            mcp_url=t_url,
        )

        return cls(jenkins=jenkins, kubernetes=kubernetes, docker=docker, nexus=nexus, scheduler=scheduler, git=git, trivy=trivy)

    @classmethod
    def load(cls) -> "StreamlitAppConfig":
        """Load config from env + admin overrides (if configured).

        Admin overrides are stored in data/admin_config.json and are intended
        for UI/runtime configuration (not secrets).
        """

        base = cls.from_env()
        admin = load_admin_config(known_pages=known_page_paths())
        return _apply_admin_overrides(base, admin)

    def build_jenkins_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.jenkins.to_env_overrides()}

    def build_kubernetes_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.kubernetes.to_env_overrides()}

    def build_docker_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.docker.to_env_overrides()}

    def build_nexus_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.nexus.to_env_overrides()}

    def build_git_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.git.to_env_overrides()}

    def build_trivy_mcp_subprocess_env(self, base_env: Dict[str, str]) -> Dict[str, str]:
        return {**base_env, **self.trivy.to_env_overrides()}


def _apply_admin_overrides(cfg: StreamlitAppConfig, admin: AdminConfig) -> StreamlitAppConfig:
    """Return a new StreamlitAppConfig with admin-provided overrides applied."""

    def _get(srv: str, key: str) -> Optional[Any]:
        raw = (admin.mcp_servers or {}).get(srv, {})
        if not isinstance(raw, dict):
            return None
        return raw.get(key)

    # Jenkins
    jenkins = JenkinsMCPServerConfig(
        base_url=str(_get("jenkins", "base_url") or cfg.jenkins.base_url),
        username=cfg.jenkins.username,
        api_token=cfg.jenkins.api_token,
        verify_ssl=bool(_get("jenkins", "verify_ssl") if _get("jenkins", "verify_ssl") is not None else cfg.jenkins.verify_ssl),
        # tokens/secrets stay env-driven by default
        mcp_client_token=cfg.jenkins.mcp_client_token,
        mcp_transport=str(_get("jenkins", "transport") or cfg.jenkins.mcp_transport),
        mcp_host=cfg.jenkins.mcp_host,
        mcp_port=cfg.jenkins.mcp_port,
        mcp_url=str(_get("jenkins", "url") or cfg.jenkins.mcp_url),
    )

    # Kubernetes
    kubernetes = KubernetesMCPServerConfig(
        kubeconfig=_get("kubernetes", "kubeconfig") if _get("kubernetes", "kubeconfig") is not None else cfg.kubernetes.kubeconfig,
        context=_get("kubernetes", "context") if _get("kubernetes", "context") is not None else cfg.kubernetes.context,
        mcp_transport=str(_get("kubernetes", "transport") or cfg.kubernetes.mcp_transport),
        mcp_host=cfg.kubernetes.mcp_host,
        mcp_port=cfg.kubernetes.mcp_port,
        mcp_url=str(_get("kubernetes", "url") or cfg.kubernetes.mcp_url),
    )

    # Docker
    docker_timeout = _get("docker", "docker_timeout_seconds")
    try:
        docker_timeout_i = int(docker_timeout) if docker_timeout is not None else cfg.docker.docker_timeout_seconds
    except Exception:
        docker_timeout_i = cfg.docker.docker_timeout_seconds

    docker_tls = _get("docker", "docker_tls_verify")
    docker_tls_b = bool(docker_tls) if docker_tls is not None else cfg.docker.docker_tls_verify

    docker = DockerMCPServerConfig(
        docker_host=_get("docker", "docker_host") if _get("docker", "docker_host") is not None else cfg.docker.docker_host,
        docker_tls_verify=docker_tls_b,
        docker_cert_path=_get("docker", "docker_cert_path") if _get("docker", "docker_cert_path") is not None else cfg.docker.docker_cert_path,
        docker_timeout_seconds=docker_timeout_i,
        mcp_transport=str(_get("docker", "transport") or cfg.docker.mcp_transport),
        mcp_host=cfg.docker.mcp_host,
        mcp_port=cfg.docker.mcp_port,
        mcp_url=str(_get("docker", "url") or cfg.docker.mcp_url),
    )

    # Nexus
    allow_raw = _get("nexus", "allow_raw")
    allow_raw_b = bool(allow_raw) if allow_raw is not None else cfg.nexus.allow_raw
    nexus = NexusMCPServerConfig(
        base_url=str(_get("nexus", "base_url") or cfg.nexus.base_url).rstrip("/"),
        username=cfg.nexus.username,
        password=cfg.nexus.password,
        token=cfg.nexus.token,
        verify_ssl=bool(_get("nexus", "verify_ssl") if _get("nexus", "verify_ssl") is not None else cfg.nexus.verify_ssl),
        mcp_client_token=cfg.nexus.mcp_client_token,
        allow_raw=allow_raw_b,
        mcp_transport=str(_get("nexus", "transport") or cfg.nexus.mcp_transport),
        mcp_host=cfg.nexus.mcp_host,
        mcp_port=cfg.nexus.mcp_port,
        mcp_url=str(_get("nexus", "url") or cfg.nexus.mcp_url),
    )

    # Scheduler
    scheduler = SchedulerMCPServerConfig(
        mcp_transport=str(_get("scheduler", "transport") or cfg.scheduler.mcp_transport),
        mcp_url=str(_get("scheduler", "url") or cfg.scheduler.mcp_url),
        mcp_host=cfg.scheduler.mcp_host,
        mcp_port=cfg.scheduler.mcp_port,
    )

    # Git
    git_timeout = _get("git", "timeout_seconds")
    try:
        git_timeout_i = int(git_timeout) if git_timeout is not None else cfg.git.timeout_seconds
    except Exception:
        git_timeout_i = cfg.git.timeout_seconds

    git = GitMCPServerConfig(
        repo_path=_get("git", "repo_path") if _get("git", "repo_path") is not None else cfg.git.repo_path,
        default_branch=str(_get("git", "default_branch") or cfg.git.default_branch),
        timeout_seconds=git_timeout_i,
        mcp_transport=str(_get("git", "transport") or cfg.git.mcp_transport),
        mcp_host=cfg.git.mcp_host,
        mcp_port=cfg.git.mcp_port,
        mcp_url=str(_get("git", "url") or cfg.git.mcp_url),
    )

    # Trivy
    trivy_timeout = _get("trivy", "timeout_seconds")
    try:
        trivy_timeout_i = int(trivy_timeout) if trivy_timeout is not None else cfg.trivy.timeout_seconds
    except Exception:
        trivy_timeout_i = cfg.trivy.timeout_seconds

    trivy_ignore_unfixed = _get("trivy", "ignore_unfixed")
    trivy_ignore_unfixed_b = bool(trivy_ignore_unfixed) if trivy_ignore_unfixed is not None else cfg.trivy.ignore_unfixed

    trivy_skip_db = _get("trivy", "skip_db_update")
    trivy_skip_db_b = bool(trivy_skip_db) if trivy_skip_db is not None else cfg.trivy.skip_db_update

    trivy = TrivyMCPServerConfig(
        cache_dir=_get("trivy", "cache_dir") if _get("trivy", "cache_dir") is not None else cfg.trivy.cache_dir,
        timeout_seconds=trivy_timeout_i,
        severity=str(_get("trivy", "severity") or cfg.trivy.severity),
        ignore_unfixed=trivy_ignore_unfixed_b,
        skip_db_update=trivy_skip_db_b,
        mcp_transport=str(_get("trivy", "transport") or cfg.trivy.mcp_transport),
        mcp_host=cfg.trivy.mcp_host,
        mcp_port=cfg.trivy.mcp_port,
        mcp_url=str(_get("trivy", "url") or cfg.trivy.mcp_url),
    )

    return StreamlitAppConfig(jenkins=jenkins, kubernetes=kubernetes, docker=docker, nexus=nexus, scheduler=scheduler, git=git, trivy=trivy)


def get_app_config() -> StreamlitAppConfig:
    """Small helper used by pages to get the effective app config."""

    return StreamlitAppConfig.load()
