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
    # which document field(s) hold the build time, most-authoritative first.
    # The KPI window is applied on these — set it if your index names the
    # build timestamp differently (the diagnostics list the fields it found).
    kpi_date_fields: str = "builddate,@timestamp"

    # --- Logging health monitor (ELK) ---
    # Application log indices follow the pattern
    #   ${log_index_prefix}-${project}-${env}-${app}-${logtype}-yyyy.ww
    # The PRIMARY Elasticsearch connection above (ES_URL / ES_API_KEY) serves
    # PRD-environment log indices — the same connection the Jenkins KPI uses.
    # A SEPARATE non-prd connection holds every OTHER environment's log indices
    # (dev / qc / uat / …). Leave it blank if you only monitor prd.
    #
    # The ${index_prefix} is DERIVED PER APP from `deploy_platform` (app's
    # group_vars/<app>/*.yml, else project group_vars/all). The built-in map is
    # OCP→oc · LinuxVM→vmlin · WindowsVM→vmwin · K8s→k8s (always applied);
    # this setting only ADDS/overrides extra "Platform:prefix" pairs.
    log_platform_prefixes: str = ""  # optional extra deploy_platform:prefix pairs
    log_index_prefix: str = ""       # fallback ${index_prefix} (no deploy_platform)
    log_prd_envs: str = "prd"        # env token(s) served by the primary (prd) ES
    # CI/CD deployments index (on the PRIMARY/prd ES) — used to find each
    # app/env's last deployment date; an app/env never deployed is expected to
    # have no logs (filtered out by default). Fields: project/application/
    # environment (keyword), startdate/enddate (date).
    log_deploy_index: str = "ef-cicd-deployments"
    # only count deployments with this testflag as the "last deployment" (skip
    # test/dry-run/other rows); blank = count all
    log_deploy_testflag: str = "Normal"
    # only deployments whose `status` field equals this count as DEPLOYED
    # (failed/aborted runs don't make an env "deployed"); blank = count all
    log_deploy_status: str = "SUCCESS"
    # log retention policy (days): an app/env keeping logs OLDER than this is
    # flagged "over-retained" (a MINOR issue). prd defaults to 6 months, non-prd
    # (dev/qc/uat) to 3 weeks.
    log_retention_prd_days: int = 183
    log_retention_nonprd_days: int = 21
    # storage-hog detection: an app (or project) storing >= this multiple of
    # the fleet AVERAGE app (project) log size is flagged "over-sized" — a
    # MINOR issue (-10 on the app/project score), filterable in the UI
    log_oversize_factor: float = 2.0
    # environment display order inside each project (side-by-side columns).
    # MAIN_ENVS are the primary columns; EXTRA_ENVS are shown separately.
    log_main_envs: str = "dev,qc,uat,prd"
    log_extra_envs: str = ""
    log_stale_hours: int = 48        # an app with no new log later than this = stale
    es_nonprd_url: str = ""          # non-prd Elasticsearch (dev/qc/uat log indices)
    es_nonprd_api_key: str = ""      # sent as 'Authorization: ApiKey <key>'
    es_nonprd_verify_ssl: bool = True
    # comma-separated tokens; KPI docs whose jobpath/jobname contains one are
    # excluded from the KPI panel (stats, bars, loaded records) — the KPI
    # sibling of JENKINS_IGNORE, deliberately its own knob
    kpi_ignore: str = ""
    error_analysis_days: int = 14

    # --- Upgrade checker: outbound version lookups (Docker Hub / GitHub /
    # Artifact Hub). These are the ONLY outbound-internet calls QuestOps makes;
    # on hosts that reach the internet via a corporate proxy, set upgrades_proxy
    # — internal Jira/Jenkins/ES/LDAP calls never use it.
    upgrades_proxy: str = ""          # e.g. http://proxy.mycorp.local:8080
    upgrades_verify_ssl: bool = True  # false if the proxy re-signs TLS
    # latest-version sources (endoflife.date is NOT used — often unreachable):
    # Docker Hub tags, GitHub releases/tags, Artifact Hub package versions.
    # Point any at an internal mirror if the public host is blocked.
    dockerhub_api_base: str = "https://hub.docker.com/v2"
    github_api_base: str = "https://api.github.com"
    artifacthub_api_base: str = "https://artifacthub.io/api/v1"

    # --- Platform Postgres (extra platform tables, e.g. devops_projects) ---
    # A SEPARATE database from QuestOps' own DATABASE_URL. Used by the Access
    # page to cross-check the devops_projects table (project, company,
    # dev_team, qc_team, ops_team) against the cloned inventory. Blank = the
    # cross-check panel shows "not configured".
    platform_database_url: str = ""
    platform_projects_table: str = "devops_projects"
    # feature flag: whether the Access page offers WRITE actions on the table
    # (quick fixes, row editor, delete/dedupe). false = read-only cross-check;
    # the endpoints are disabled server-side too, not just hidden
    platform_db_actions: bool = True

    # --- Mail (Logging health email reports) ---
    # Leave smtp_host blank to disable sending (the report PREVIEW always
    # works). In demo mode a send with no host is simulated.
    # mail_transport picks HOW to talk to the server:
    #   ews  — Exchange Web Services via exchangelib (Credentials →
    #          Configuration(server=SMTP_HOST) → Account(primary_smtp_address=
    #          SMTP_FROM, autodiscover=False) → Message(HTMLBody).send()) —
    #          for Exchange servers that reject basic SMTP AUTH
    #          ("No suitable authentication method found")
    #   smtp — classic smtplib (port/starttls/ssl knobs apply)
    mail_transport: str = "ews"
    smtp_host: str = ""              # EWS server hostname, or the SMTP host
    smtp_port: int = 587             # smtp transport only
    smtp_user: str = ""              # login username (EWS credentials / SMTP auth)
    smtp_password: str = ""
    smtp_starttls: bool = True       # smtp transport only
    smtp_ssl: bool = False           # smtp transport only
    smtp_from: str = "questops@localhost"   # sender / EWS primary_smtp_address
    # bare recipient names (no @) get this domain appended, mirroring the
    # send_mail.py utility; blank = full addresses required
    mail_default_domain: str = ""
    mail_retries: int = 3            # send attempts before giving up
    mail_retry_wait: float = 3.0     # seconds between attempts
    # always looped into every logging report email (blank = disabled)
    admin_email: str = ""

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
    # individual users allowed into QuestOps WITHOUT the team LDAP group —
    # they see ONLY the pages in restricted_pages, and those pages are
    # visible to NOBODY else
    restricted_users: str = ""
    restricted_pages: str = ""
    # NOTE: [TEAM] group membership (Access Management) is NOT resolved via LDAP
    # here — it runs the cloned Engine repo's scripts/Tools/LDAP/getTeamMembersCN.sh
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

    # --- ADO -> Gitea migration ---
    # Gitea targets are DEFINED FROM THE UI (one instance per ADO collection,
    # stored in the database). These are just transport knobs.
    gitea_verify_ssl: bool = True    # false for self-signed Gitea TLS

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
    def restricted_user_set(self) -> set[str]:
        return {u.lower() for u in self._csv(self.restricted_users)}

    @property
    def restricted_page_list(self) -> list[str]:
        return [p.lower() for p in self._csv(self.restricted_pages)]

    @property
    def kpi_sync_marks(self) -> list[int]:
        return sorted(int(m) % 60 for m in self._csv(self.kpi_sync_minutes)) or [5, 35]

    @property
    def kpi_ignore_tokens(self) -> list[str]:
        return [t.lower() for t in self._csv(self.kpi_ignore)]

    @property
    def kpi_date_field_list(self) -> list[str]:
        return self._csv(self.kpi_date_fields) or ["builddate", "@timestamp"]

    @property
    def log_prd_env_list(self) -> list[str]:
        return [e.lower() for e in self._csv(self.log_prd_envs)] or ["prd"]

    @property
    def log_main_env_list(self) -> list[str]:
        return [e.lower() for e in self._csv(self.log_main_envs)]

    @property
    def log_extra_env_list(self) -> list[str]:
        return [e.lower() for e in self._csv(self.log_extra_envs)]

    @property
    def log_platform_prefix_map(self) -> dict:
        """deploy_platform (lower-cased) -> log index prefix. The four built-ins
        always apply; LOG_PLATFORM_PREFIXES only adds/overrides extra pairs."""
        out = {"ocp": "oc", "linuxvm": "vmlin", "windowsvm": "vmwin", "k8s": "k8s"}
        for pair in self._csv(self.log_platform_prefixes):
            if ":" in pair:
                k, v = pair.split(":", 1)
                if k.strip() and v.strip():
                    out[k.strip().lower()] = v.strip()
        return out

    @property
    def ado_access_exclude_list(self) -> set[str]:
        return {u.strip().lower() for u in self._csv(self.ado_access_exclude)}

    @property
    def ldap_servers(self) -> list[dict]:
        """The LDAP server(s) surfaced on the Access page's health check — just
        the primary (login) directory. [TEAM] group members are resolved out of
        band via the Engine repo's getTeamMembersCN.sh, not by binding here."""
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
