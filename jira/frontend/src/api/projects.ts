import { api } from './client';
import {
  ProjectBrief,
  ProjectOut,
  Component,
  Version,
  ProjectMember,
  CreateProjectPayload,
} from '../types';

export async function listProjects(): Promise<ProjectBrief[]> {
  const res = await api.get<ProjectBrief[]>('/projects');
  return res.data;
}

export async function createProject(payload: CreateProjectPayload): Promise<ProjectOut> {
  const res = await api.post<ProjectOut>('/projects', payload);
  return res.data;
}

export async function getProject(keyOrId: string): Promise<ProjectOut> {
  const res = await api.get<ProjectOut>(`/projects/${keyOrId}`);
  return res.data;
}

export async function updateProject(id: string, payload: Partial<CreateProjectPayload>): Promise<ProjectOut> {
  const res = await api.patch<ProjectOut>(`/projects/${id}`, payload);
  return res.data;
}

export async function deleteProject(id: string): Promise<void> {
  await api.delete(`/projects/${id}`);
}

// Members
export async function listMembers(id: string): Promise<ProjectMember[]> {
  const res = await api.get<ProjectMember[]>(`/projects/${id}/members`);
  return res.data;
}

export async function addMember(id: string, user_id: string, role: string): Promise<ProjectMember> {
  const res = await api.post<ProjectMember>(`/projects/${id}/members`, { user_id, role });
  return res.data;
}

export async function removeMember(id: string, user_id: string): Promise<void> {
  await api.delete(`/projects/${id}/members`, { data: { user_id } });
}

// Components
export async function listComponents(id: string): Promise<Component[]> {
  const res = await api.get<Component[]>(`/projects/${id}/components`);
  return res.data;
}

export async function createComponent(
  id: string,
  payload: { name: string; description?: string; lead_id?: string }
): Promise<Component> {
  const res = await api.post<Component>(`/projects/${id}/components`, payload);
  return res.data;
}

export async function updateComponent(
  id: string,
  componentId: string,
  payload: { name?: string; description?: string; lead_id?: string }
): Promise<Component> {
  const res = await api.patch<Component>(`/projects/${id}/components/${componentId}`, payload);
  return res.data;
}

export async function deleteComponent(id: string, componentId: string): Promise<void> {
  await api.delete(`/projects/${id}/components/${componentId}`);
}

// Versions
export async function listVersions(id: string): Promise<Version[]> {
  const res = await api.get<Version[]>(`/projects/${id}/versions`);
  return res.data;
}

export async function createVersion(
  id: string,
  payload: { name: string; description?: string; released?: boolean; release_date?: string | null }
): Promise<Version> {
  const res = await api.post<Version>(`/projects/${id}/versions`, payload);
  return res.data;
}

export async function updateVersion(
  id: string,
  versionId: string,
  payload: Partial<{ name: string; description: string; released: boolean; release_date: string | null }>
): Promise<Version> {
  const res = await api.patch<Version>(`/projects/${id}/versions/${versionId}`, payload);
  return res.data;
}

export async function deleteVersion(id: string, versionId: string): Promise<void> {
  await api.delete(`/projects/${id}/versions/${versionId}`);
}
