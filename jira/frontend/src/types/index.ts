// Core domain types mirroring the Trackly REST API contract.

export interface User {
  id: string;
  username: string;
  display_name: string;
  email: string;
  avatar_url?: string | null;
  is_admin: boolean;
  is_active: boolean;
  timezone?: string | null;
}

export interface Tokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface IssueType {
  id: string;
  name: string;
  icon?: string | null;
  color?: string | null;
  is_subtask: boolean;
  project_id?: string | null;
}

export type StatusCategory = 'todo' | 'in_progress' | 'done';

export interface Status {
  id: string;
  name: string;
  category: StatusCategory;
  order: number;
}

export interface Priority {
  id: string;
  name: string;
  icon?: string | null;
  color?: string | null;
  rank: number;
}

export interface Label {
  id: string;
  name: string;
}

export type ProjectType = 'scrum' | 'kanban' | string;

export interface ProjectBrief {
  id: string;
  key: string;
  name: string;
  project_type: ProjectType;
  avatar_color?: string | null;
}

export interface Component {
  id: string;
  name: string;
  description?: string | null;
  lead?: User | null;
  lead_id?: string | null;
}

export interface Version {
  id: string;
  name: string;
  description?: string | null;
  released: boolean;
  release_date?: string | null;
  start_date?: string | null;
}

export interface ProjectMember {
  user: User;
  role: string;
}

export interface ProjectOut extends ProjectBrief {
  description?: string | null;
  lead?: User | null;
  components: Component[];
  versions: Version[];
  permission_scheme_id?: number | string | null;
  permission_scheme?: { id: number | string; name: string } | null;
}

export interface IssueRefType {
  id: string;
  name: string;
  icon?: string | null;
  color?: string | null;
  is_subtask?: boolean;
}

export interface IssueRefStatus {
  id: string;
  name: string;
  category: StatusCategory;
}

export interface IssueRefPriority {
  id: string;
  name: string;
  icon?: string | null;
  color?: string | null;
}

export interface IssueListItem {
  id: string;
  key: string;
  summary: string;
  type?: IssueRefType | null;
  status?: IssueRefStatus | null;
  priority?: IssueRefPriority | null;
  assignee?: User | null;
  reporter?: User | null;
  story_points?: number | null;
  parent_id?: string | null;
  epic_id?: string | null;
  sprint_id?: string | null;
  rank?: string | null;
  due_date?: string | null;
  updated_at?: string | null;
  labels: string[];
}

export interface Comment {
  id: string;
  body: string;
  author: User;
  created_at: string;
  updated_at?: string | null;
}

export interface Worklog {
  id: string;
  time_spent: string;
  time_spent_seconds?: number | null;
  comment?: string | null;
  author: User;
  started_at?: string | null;
  created_at: string;
}

export interface Attachment {
  id: string;
  filename: string;
  size?: number | null;
  content_type?: string | null;
  author?: User | null;
  created_at: string;
}

export interface IssueLink {
  id: string;
  link_type: string;
  target_key: string;
  target_summary?: string | null;
  target_status?: IssueRefStatus | null;
  direction?: 'inward' | 'outward' | string;
}

export interface HistoryEntry {
  id: string;
  field: string;
  old_value?: string | null;
  new_value?: string | null;
  author?: User | null;
  created_at: string;
}

export interface IssueDetail extends IssueListItem {
  project: ProjectBrief;
  description?: string | null;
  components: Component[];
  fix_versions: Version[];
  comments: Comment[];
  attachments: Attachment[];
  worklogs: Worklog[];
  subtasks: IssueListItem[];
  links: IssueLink[];
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Board {
  id: string;
  project_id: string;
  name: string;
  board_type: string;
}

export type SprintState = 'future' | 'active' | 'closed';

export interface Sprint {
  id: string;
  board_id: string;
  name: string;
  goal?: string | null;
  state: SprintState;
  start_date?: string | null;
  end_date?: string | null;
}

export interface BoardColumn {
  status_id: string;
  status_name: string;
  category: StatusCategory;
  issues: IssueListItem[];
}

export interface BoardData {
  board: Board;
  columns: BoardColumn[];
  active_sprint?: Sprint | null;
}

export interface BacklogData {
  board: Board;
  sprints: Sprint[];
  sprint_issues: Record<string, IssueListItem[]>;
  backlog: IssueListItem[];
}

export interface SavedFilter {
  id: string;
  name: string;
  query: string;
  is_shared: boolean;
  owner?: User | null;
}

export interface Notification {
  id: string;
  title?: string | null;
  body?: string | null;
  message?: string | null;
  is_read: boolean;
  read: boolean;
  issue_key?: string | null;
  created_at: string;
}

// Payloads ----------------------------------------------------------------

export interface CreateIssuePayload {
  project_id: string;
  type_id: string;
  summary: string;
  description?: string;
  status_id?: string;
  priority_id?: string;
  assignee_id?: string | null;
  parent_id?: string;
  epic_id?: string;
  sprint_id?: string | null;
  story_points?: number | null;
  due_date?: string | null;
  label_names?: string[];
  component_ids?: string[];
  fix_version_ids?: string[];
}

export type UpdateIssuePayload = Partial<CreateIssuePayload>;

export interface RankPayload {
  after_id?: string;
  before_id?: string;
  sprint_id?: string | null;
  status_id?: string;
}

export interface CreateProjectPayload {
  key: string;
  name: string;
  description?: string;
  project_type: string;
  avatar_color?: string;
  lead_id?: string;
}

// ---------------------------------------------------------------------------
// Admin: mail, jira connections, identity providers, global permissions
// ---------------------------------------------------------------------------

export interface UserBrief {
  id: string;
  username: string;
  display_name: string;
  email: string;
  avatar_url?: string | null;
}

export interface MailSettings {
  enabled: boolean;
  host: string;
  port: number;
  username: string;
  use_tls: boolean;
  use_ssl: boolean;
  from_address: string;
  from_name: string;
  password_set: boolean;
}

export interface MailSettingsUpdate {
  enabled: boolean;
  host: string;
  port: number;
  username: string;
  use_tls: boolean;
  use_ssl: boolean;
  from_address: string;
  from_name: string;
  password?: string;
}

export interface TestResult {
  ok: boolean;
  message: string;
  account?: string;
}

export type JiraAuthMode = 'cloud' | 'server';

export interface JiraConnection {
  id: string;
  name: string;
  base_url: string;
  auth_mode: JiraAuthMode;
  email: string;
  verify_ssl: boolean;
  enabled: boolean;
  is_default: boolean;
  last_checked_at?: string | null;
  last_check_ok?: boolean | null;
  token_set: boolean;
}

export interface JiraConnectionPayload {
  name: string;
  base_url: string;
  auth_mode: JiraAuthMode;
  email: string;
  api_token?: string;
  verify_ssl: boolean;
  enabled: boolean;
  is_default: boolean;
}

export interface JiraRemoteProject {
  key: string;
  name: string;
  id: string;
  lead?: string | null;
  exists_locally: boolean;
}

export type ProviderType = 'ldap' | 'entra';

export interface IdentityProvider {
  id: string;
  name: string;
  provider_type: ProviderType;
  enabled: boolean;
  auto_provision_users: boolean;
  sync_groups: boolean;
  order: number;
  // LDAP
  ldap_host?: string | null;
  ldap_port?: number | null;
  ldap_use_ssl?: boolean | null;
  ldap_bind_dn?: string | null;
  ldap_bind_password_set?: boolean;
  ldap_user_base_dn?: string | null;
  ldap_user_filter?: string | null;
  ldap_attr_username?: string | null;
  ldap_attr_email?: string | null;
  ldap_attr_display_name?: string | null;
  ldap_group_base_dn?: string | null;
  ldap_group_filter?: string | null;
  ldap_attr_group_name?: string | null;
  // Entra
  entra_tenant_id?: string | null;
  entra_client_id?: string | null;
  entra_redirect_uri?: string | null;
  entra_scopes?: string | null;
  entra_client_secret_set?: boolean;
}

export interface IdentityProviderPayload {
  name: string;
  provider_type: ProviderType;
  enabled: boolean;
  auto_provision_users: boolean;
  sync_groups: boolean;
  order: number;
  ldap_host?: string;
  ldap_port?: number;
  ldap_use_ssl?: boolean;
  ldap_bind_dn?: string;
  ldap_bind_password?: string;
  ldap_user_base_dn?: string;
  ldap_user_filter?: string;
  ldap_attr_username?: string;
  ldap_attr_email?: string;
  ldap_attr_display_name?: string;
  ldap_group_base_dn?: string;
  ldap_group_filter?: string;
  ldap_attr_group_name?: string;
  entra_tenant_id?: string;
  entra_client_id?: string;
  entra_client_secret?: string;
  entra_redirect_uri?: string;
  entra_scopes?: string;
}

export type HolderType = 'group' | 'user' | 'role' | 'special';

export interface GlobalPermission {
  id: string;
  permission: string;
  holder_type: HolderType;
  holder_value: string;
}

// ---------------------------------------------------------------------------
// Groups, roles, permission schemes
// ---------------------------------------------------------------------------

export interface Group {
  id: string;
  name: string;
  description?: string | null;
  directory_source?: string | null;
  is_system: boolean;
}

export interface GroupDetail extends Group {
  members: UserBrief[];
}

export interface Role {
  id: string;
  name: string;
  description?: string | null;
  is_default: boolean;
}

export interface ProjectActor {
  id: string;
  role_id: string;
  user?: UserBrief | null;
  group?: { id: string; name: string } | null;
}

export interface CatalogEntry {
  key: string;
  description: string;
}

export interface PermissionCatalog {
  global_permissions: CatalogEntry[];
  project_permissions: CatalogEntry[];
  holder_types: string[];
  special_holders: string[];
}

export interface PermissionScheme {
  id: string;
  name: string;
  description?: string | null;
  is_default: boolean;
}

export interface PermissionGrant {
  id: string;
  permission: string;
  holder_type: HolderType;
  holder_value: string;
}

export interface PermissionSchemeDetail extends PermissionScheme {
  grants: PermissionGrant[];
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

export type SyncStatus = 'idle' | 'running' | 'paused' | 'error' | 'completed';

export interface SyncRun {
  id: string;
  started_at?: string | null;
  finished_at?: string | null;
  status: string;
  trigger: string;
  processed: number;
  created: number;
  updated: number;
  errors: number;
  message?: string | null;
}

export interface SyncLink {
  id: string;
  project_id: string;
  connection_id: string;
  jira_project_key: string;
  jira_project_id?: string | null;
  enabled: boolean;
  status: SyncStatus;
  updated_watermark?: string | null;
  cursor_start_at?: string | null;
  total_issues: number;
  processed_issues: number;
  last_synced_at?: string | null;
  last_error?: string | null;
  sync_permissions: boolean;
  recent_runs: SyncRun[];
}

export interface SyncDiscover {
  found: boolean;
  jira_project_key?: string | null;
  name?: string | null;
  jira_project_id?: string | null;
  issue_count?: number | null;
  message?: string | null;
}

// ---------------------------------------------------------------------------
// Notification preferences
// ---------------------------------------------------------------------------

export type NotifChannel = 'in_app' | 'email';

export interface NotifPrefRow {
  event: string;
  label: string;
  in_app: boolean;
  email: boolean;
}

export interface NotifPreferences {
  email_available: boolean;
  rows: NotifPrefRow[];
}

// ---------------------------------------------------------------------------
// External auth providers (public)
// ---------------------------------------------------------------------------

export interface AuthProvider {
  id: string;
  name: string;
  type: ProviderType;
  enabled: boolean;
}

export interface AuthPolicy {
  allow_local_login: boolean;
  allow_self_registration: boolean;
}

// ---------------------------------------------------------------------------
// Admin: authentication settings
// ---------------------------------------------------------------------------

export interface AuthSettings {
  allow_local_login: boolean;
  allow_self_registration: boolean;
  access_token_minutes: number | null;
  refresh_token_minutes: number | null;
  registration_allowed_domains: string | null;
}

// ---------------------------------------------------------------------------
// Analytics / Insights
// ---------------------------------------------------------------------------

export interface CountItem {
  label: string;
  count: number;
  category?: StatusCategory | null;
  color?: string | null;
}

// Resolved time window that scopes the descriptive analytics stats.
export interface Window {
  period: string;
  start: string | null;
  end: string | null;
}

export type AttentionSeverity = 'high' | 'medium' | 'low';

// A single issue surfaced inside an attention bucket / rollup.
export interface AttentionIssue {
  key: string;
  summary: string;
  priority?: string | null;
  priority_color?: string | null;
  assignee?: string | null;
  status?: string | null;
  due_date?: string | null;
  days_overdue?: number | null;
  updated_at?: string | null;
}

// A bucket of issues that need attention (overdue, unassigned, blocked, …).
export interface AttentionItem {
  key: string;
  label: string;
  description: string;
  count: number;
  severity: AttentionSeverity;
  tql?: string | null;
  samples: AttentionIssue[];
}

// Active-sprint health summary for a project.
export interface SprintHealth {
  sprint_id: number;
  name: string;
  goal?: string | null;
  end_date?: string | null;
  days_remaining?: number | null;
  total_points: number;
  completed_points: number;
  percent_complete: number; // 0..1
  incomplete_issues: number;
  at_risk: boolean;
  risk_reason?: string | null;
}

export interface ProjectStatRow {
  project_id: string;
  project_key: string;
  project_name: string;
  avatar_color?: string | null;
  total_issues: number;
  open_issues: number;
  closed_issues: number;
  resolution_rate: number;
  avg_velocity_points: number;
  // Attention signals (rows already sorted most-urgent first).
  attention_score: number;
  overdue: number;
  high_priority_open: number;
  unassigned_open: number;
  blocked: number;
  at_risk_sprint: boolean;
  needs_attention: boolean;
  top_reasons: string[];
}

export interface OverviewStats {
  scope: 'all' | 'mine';
  total_projects: number;
  total_issues: number;
  open_issues: number;
  closed_issues: number;
  resolution_rate: number;
  by_status: CountItem[];
  by_type: CountItem[];
  projects: ProjectStatRow[];
  // Cross-project attention rollup.
  total_overdue: number;
  total_unassigned_open: number;
  total_high_priority_open: number;
  total_blocked: number;
  projects_at_risk: number;
  projects_needing_attention: number;
  top_attention: AttentionIssue[];
  // Resolved time window scoping the descriptive stats above.
  window: Window;
}

export interface VelocityPoint {
  sprint_id: string;
  sprint_name: string;
  committed_points: number;
  completed_points: number;
  completed_issues: number;
}

export interface ProjectStats {
  project_id: string;
  project_key: string;
  project_name: string;
  total_issues: number;
  open_issues: number;
  in_progress_issues: number;
  closed_issues: number;
  resolution_rate: number;
  by_status: CountItem[];
  by_type: CountItem[];
  by_priority: CountItem[];
  velocity: VelocityPoint[];
  avg_velocity_points: number;
  avg_velocity_issues: number;
  // Action-first attention data.
  attention: AttentionItem[];
  attention_score: number;
  sprint_health: SprintHealth | null;
  // Resolved time window scoping the descriptive stats above.
  window: Window;
}
