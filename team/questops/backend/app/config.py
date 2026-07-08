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

    # --- LDAP ---
    ldap_url: str = ""               # ldap(s)://host:389
    ldap_bind_dn: str = ""           # service account for the user search
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""
    ldap_user_attr: str = "sAMAccountName"
    ldap_required_group: str = ""    # group DN required to log in
    ldap_approver_group: str = ""    # group DN allowed to approve repo actions

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


settings = Settings()
