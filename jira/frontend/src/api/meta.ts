import { api } from './client';
import { IssueType, Status, Priority, Label } from '../types';

export async function getIssueTypes(project_id?: string): Promise<IssueType[]> {
  const res = await api.get<IssueType[]>('/meta/issue-types', {
    params: project_id ? { project_id } : {},
  });
  return res.data;
}

export async function getStatuses(project_id?: string): Promise<Status[]> {
  const res = await api.get<Status[]>('/meta/statuses', {
    params: project_id ? { project_id } : {},
  });
  return res.data;
}

export async function getPriorities(): Promise<Priority[]> {
  const res = await api.get<Priority[]>('/meta/priorities');
  return res.data;
}

export async function getLabels(q = ''): Promise<Label[]> {
  const res = await api.get<Label[]>('/meta/labels', { params: q ? { q } : {} });
  return res.data;
}
