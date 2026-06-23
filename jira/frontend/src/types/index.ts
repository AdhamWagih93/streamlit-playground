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
