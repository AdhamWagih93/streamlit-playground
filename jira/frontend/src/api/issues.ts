import { api } from './client';
import {
  IssueDetail,
  IssueListItem,
  Page,
  CreateIssuePayload,
  UpdateIssuePayload,
  RankPayload,
  Comment,
  Worklog,
  HistoryEntry,
  IssueLink,
  Attachment,
} from '../types';

export interface IssueQuery {
  project?: string;
  status_id?: string;
  assignee_id?: string;
  type_id?: string;
  sprint_id?: string;
  page?: number;
  page_size?: number;
}

export async function listIssues(query: IssueQuery = {}): Promise<Page<IssueListItem>> {
  const res = await api.get<Page<IssueListItem>>('/issues', { params: query });
  return res.data;
}

export async function createIssue(payload: CreateIssuePayload): Promise<IssueDetail> {
  const res = await api.post<IssueDetail>('/issues', payload);
  return res.data;
}

export async function getIssue(keyOrId: string): Promise<IssueDetail> {
  const res = await api.get<IssueDetail>(`/issues/${keyOrId}`);
  return res.data;
}

export async function updateIssue(keyOrId: string, payload: UpdateIssuePayload): Promise<IssueDetail> {
  const res = await api.patch<IssueDetail>(`/issues/${keyOrId}`, payload);
  return res.data;
}

export async function deleteIssue(keyOrId: string): Promise<void> {
  await api.delete(`/issues/${keyOrId}`);
}

export async function rankIssue(key: string, payload: RankPayload): Promise<void> {
  await api.put(`/issues/${key}/rank`, payload);
}

// Comments
export async function listComments(key: string): Promise<Comment[]> {
  const res = await api.get<Comment[]>(`/issues/${key}/comments`);
  return res.data;
}

export async function addComment(key: string, body: string): Promise<Comment> {
  const res = await api.post<Comment>(`/issues/${key}/comments`, { body });
  return res.data;
}

export async function updateComment(key: string, cid: string, body: string): Promise<Comment> {
  const res = await api.patch<Comment>(`/issues/${key}/comments/${cid}`, { body });
  return res.data;
}

export async function deleteComment(key: string, cid: string): Promise<void> {
  await api.delete(`/issues/${key}/comments/${cid}`);
}

// Worklogs
export async function listWorklogs(key: string): Promise<Worklog[]> {
  const res = await api.get<Worklog[]>(`/issues/${key}/worklogs`);
  return res.data;
}

export async function addWorklog(
  key: string,
  payload: { time_spent: string; comment?: string; started_at?: string }
): Promise<Worklog> {
  const res = await api.post<Worklog>(`/issues/${key}/worklogs`, payload);
  return res.data;
}

export async function deleteWorklog(key: string, id: string): Promise<void> {
  await api.delete(`/issues/${key}/worklogs/${id}`);
}

// History
export async function getHistory(key: string): Promise<HistoryEntry[]> {
  const res = await api.get<HistoryEntry[]>(`/issues/${key}/history`);
  return res.data;
}

// Links
export async function addLink(
  key: string,
  payload: { link_type: string; target_key: string }
): Promise<IssueLink> {
  const res = await api.post<IssueLink>(`/issues/${key}/links`, payload);
  return res.data;
}

export async function deleteLink(key: string, id: string): Promise<void> {
  await api.delete(`/issues/${key}/links/${id}`);
}

// Attachments
export async function listAttachments(key: string): Promise<Attachment[]> {
  const res = await api.get<Attachment[]>(`/issues/${key}/attachments`);
  return res.data;
}

export async function uploadAttachment(key: string, file: File): Promise<Attachment> {
  const form = new FormData();
  form.append('file', file);
  const res = await api.post<Attachment>(`/issues/${key}/attachments`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

export function attachmentDownloadUrl(id: string): string {
  return `/api/issues/attachments/${id}/download`;
}

export async function deleteAttachment(id: string): Promise<void> {
  await api.delete(`/issues/attachments/${id}`);
}
