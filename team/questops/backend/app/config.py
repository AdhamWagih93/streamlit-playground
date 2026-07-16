from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All knobs are env vars (see .env.example / helm values.yaml)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "QuestOps"
    demo_mode: bool = True
    secret_key: str = "change-me-in-prod"
    token_ttl_hours: int = 12
    database_url: str = "sqlite:///./questops.db"
    demo_password: str = "demo"

    # --- AI / Ollama ---
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout: int = 120

    # --- Jira Data Center (one project) ---
    jira_base_url: str = ""          # e.g. https://jira.mycorp.local
    jira_user: str = ""              # basic auth
    jira_password: str = ""
    jira_project_key: str = "DEVOPS"
    # board columns, in flow order (classic Jira DC workflow)
    jira_board_statuses: str = "Open,In Progress,Resolved,Closed"
    jira_done_statuses: str = "Closed"        # landing here = ticket-closed XP
    jira_review_statuses: str = "Resolved"    # 'resolved' means awaiting review
    jira_reopened_statuses: str = "Reopened"  # shown in the first column, flagged as regression
    jira_closed_window_days: int = 30         # board shows only tickets closed this recently
    # instance-level Jira groups shown + cross-checked in Access Management
    jira_admin_group: str = "jira-administrators"
    jira_users_group: str = "jira-users"      # membership = a real licensed Jira user

    # --- Jenkins ---
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_token: str = ""
    # a running build is 'long-running' when elapsed > avg-of-recent-builds * factor;
    # the static minutes threshold is used only for jobs with no build history
    jenkins_long_running_factor: float = 1.5
    jenkins_long_running_minutes: int = 45
    jenkins_failure_window_days: int = 14  # failures older than this are not shown
    jenkins_ignore: str = "DevOps_Test"    # comma list; skip pipeline paths containing these

    # --- Elasticsearch (Jenkins KPI + error analysis indices) ---
    es_url: str = ""                 # e.g. https://es.mycorp.local:9200
    es_api_key: str = ""             # sent as 'Authorization: ApiKey <key>'
    es_verify_ssl: bool = True
    jenkins_kpi_index: str = "jenkins-kpi"
    error_analysis_index: str = "jenkins-error-analysis"
    kpi_sync_minutes: str = "5,35"   # minute marks each hour when the KPI loader runs
    kpi_max_docs: int = 10000        # per-request fetch cap (ES max_result_window)
    # comma-separated tokens; KPI docs whose jobpath/jobname contains one are
    # excluded from the KPI panel (stats, bars, loaded records) — the KPI
    # sibling of JENKINS_IGNORE, deliberately its own knob
    kpi_ignore: str = ""
    error_analysis_days: int = 14

    # --- Upgrade checker: outbound version lookups (endoflife.date / GitHub).
    # These are the ONLY outbound-internet calls QuestOps makes; on hosts that
    # reach the internet via a corporate proxy, set upgrades_proxy — internal
    # Jira/Jenkins/ES/LDAP calls never use it.
    upgrades_proxy: str = ""          # e.g. http://proxy.mycorp.local:8080
    upgrades_verify_ssl: bool = True  # false if the proxy re-signs TLS
    eol_api_base: str = "https://endoflife.date/api"   # or an internal mirror
    github_api_base: str = "https://api.github.com"

    # --- LDAP ---
    ldap_url: str = ""               # ldap(s)://host:389
    ldap_bind_dn: str = ""           # service account for the user search
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""
    ldap_user_attr: str = "sAMAccountName"
    ldap_required_group: str = ""    # THE team group: gates login + defines the roster
    # role is decided per username: everyone in the group is an APPROVER unless
    # listed here (comma-separated usernames -> plain member)
    member_usernames: str = ""
    # NOTE: [TEAM] group membership (Access Management) is NOT resolved via LDAP
    # here — it runs the cloned Engine repo's scripts/Tools/LDAP/getTeamMembers.sh
    # (see auth.ldap_group_members). The LDAP settings above gate LOGIN only.

    # --- Repositories page ---
    # repos are DEFINED FROM THE UI (stored in the database); config carries
    # only the Azure DevOps instance credentials. Some ADO setups want the
    # PAT for the REST API but the real account password for git-over-http —
    # so both are definable and each falls back to the other.
    repos_workdir: str = "./repos"
    ado_url: str = ""       # the ADO INSTANCE root, e.g. https://ado.mycorp.local
                            # (NOT a collection URL — collections are enumerated)
    ado_user: str = ""
    ado_password: str = ""  # used for GIT clone/pull/fetch
    ado_pat: str = ""       # used for the ADO REST API (repository browse)
    # usernames excluded from repo-specific-access analysis (repo creators /
    # admins expected to hold access on every repo — like the service account)
    ado_access_exclude: str = ""

    # --- Git (repo actions) ---
    git_token: str = ""              # https token used for clone/push
    git_user_name: str = "questops-bot"
    git_user_email: str = "questops-bot@local"

    @staticmethod
    def _csv(raw: str) -> list[str]:
        return [s.strip() for s in raw.split(",") if s.strip()]

    @property
    def board_statuses(self) -> list[str]:
        return self._csv(self.jira_board_statuses)

    @property
    def done_statuses(self) -> set[str]:
        return {s.lower() for s in self._csv(self.jira_done_statuses)}

    @property
    def review_statuses(self) -> set[str]:
        return {s.lower() for s in self._csv(self.jira_review_statuses)}

    @property
    def reopened_statuses(self) -> set[str]:
        return {s.lower() for s in self._csv(self.jira_reopened_statuses)}

    @property
    def jenkins_ignore_tokens(self) -> list[str]:
        return [t.lower() for t in self._csv(self.jenkins_ignore)]

    @property
    def member_users(self) -> set[str]:
        return {u.lower() for u in self._csv(self.member_usernames)}

    @property
    def kpi_sync_marks(self) -> list[int]:
        return sorted(int(m) % 60 for m in self._csv(self.kpi_sync_minutes)) or [5, 35]

    @property
    def kpi_ignore_tokens(self) -> list[str]:
        return [t.lower() for t in self._csv(self.kpi_ignore)]

    @property
    def ado_access_exclude_list(self) -> set[str]:
        return {u.strip().lower() for u in self._csv(self.ado_access_exclude)}

    @property
    def ldap_servers(self) -> list[dict]:
        """The LDAP server(s) surfaced on the Access page's health check — just
        the primary (login) directory. [TEAM] group members are resolved out of
        band via the Engine repo's getTeamMembers.sh, not by binding here."""
        primary = {"url": self.ldap_url, "bind_dn": self.ldap_bind_dn,
                   "bind_password": self.ldap_bind_password,
                   "base_dn": self.ldap_base_dn, "user_attr": self.ldap_user_attr}
        return [primary] if self.ldap_url else []

    @property
    def ado_git_password(self) -> str:
        """git clone/pull/fetch credential: the password, PAT as fallback."""
        return self.ado_password or self.ado_pat

    @property
    def ado_rest_password(self) -> str:
        """ADO REST API credential: the PAT, password as fallback."""
        return self.ado_pat or self.ado_password

settings = Settings()
