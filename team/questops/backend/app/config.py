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
    jira_board_statuses: str = "To Do,In Progress,In Review,Done"

    # --- Jenkins ---
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_token: str = ""
    jenkins_long_running_minutes: int = 45

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

    @property
    def board_statuses(self) -> list[str]:
        return [s.strip() for s in self.jira_board_statuses.split(",") if s.strip()]


settings = Settings()
