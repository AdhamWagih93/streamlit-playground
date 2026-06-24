import { api } from './client';
import {
  Group,
  GroupDetail,
  Role,
  ProjectActor,
  PermissionCatalog,
  PermissionScheme,
  PermissionSchemeDetail,
  PermissionGrant,
  HolderType,
} from '../types';

// Groups ---------------------------------------------------------------------
export async function listGroups(q = ''): Promise<Group[]> {
  const res = await api.get<Group[]>('/groups', { params: q ? { q } : {} });
  return res.data;
}

export async function getGroup(id: string): Promise<GroupDetail> {
  const res = await api.get<GroupDetail>(`/groups/${id}`);
  return res.data;
}

export async function createGroup(payload: { name: string; description?: string }): Promise<Group> {
  const res = await api.post<Group>('/groups', payload);
  return res.data;
}

export async function updateGroup(
  id: string,
  payload: { name?: string; description?: string }
): Promise<Group> {
  const res = await api.patch<Group>(`/groups/${id}`, payload);
  return res.data;
}

export async function deleteGroup(id: string): Promise<void> {
  await api.delete(`/groups/${id}`);
}

export async function addGroupMember(id: string, user_id: string): Promise<void> {
  await api.post(`/groups/${id}/members`, { user_id });
}

export async function removeGroupMember(id: string, user_id: string): Promise<void> {
  await api.delete(`/groups/${id}/members/${user_id}`);
}

// Roles ----------------------------------------------------------------------
export async function listRoles(): Promise<Role[]> {
  const res = await api.get<Role[]>('/roles');
  return res.data;
}

export async function createRole(payload: { name: string; description?: string }): Promise<Role> {
  const res = await api.post<Role>('/roles', payload);
  return res.data;
}

export async function listProjectActors(projectId: string): Promise<ProjectActor[]> {
  const res = await api.get<ProjectActor[]>(`/roles/projects/${projectId}/actors`);
  return res.data;
}

export async function addProjectActor(
  projectId: string,
  payload: { role_id: string; user_id?: string; group_id?: string }
): Promise<ProjectActor> {
  const res = await api.post<ProjectActor>(`/roles/projects/${projectId}/actors`, payload);
  return res.data;
}

export async function removeProjectActor(projectId: string, actorId: string): Promise<void> {
  await api.delete(`/roles/projects/${projectId}/actors/${actorId}`);
}

// Permission schemes ----------------------------------------------------------
export async function getPermissionCatalog(): Promise<PermissionCatalog> {
  const res = await api.get<PermissionCatalog>('/permission-schemes/catalog');
  return res.data;
}

export async function listPermissionSchemes(): Promise<PermissionScheme[]> {
  const res = await api.get<PermissionScheme[]>('/permission-schemes');
  return res.data;
}

export async function createPermissionScheme(payload: {
  name: string;
  description?: string;
}): Promise<PermissionScheme> {
  const res = await api.post<PermissionScheme>('/permission-schemes', payload);
  return res.data;
}

export async function getPermissionScheme(id: string): Promise<PermissionSchemeDetail> {
  const res = await api.get<PermissionSchemeDetail>(`/permission-schemes/${id}`);
  return res.data;
}

export async function updatePermissionScheme(
  id: string,
  payload: { name?: string; description?: string }
): Promise<PermissionScheme> {
  const res = await api.patch<PermissionScheme>(`/permission-schemes/${id}`, payload);
  return res.data;
}

export async function deletePermissionScheme(id: string): Promise<void> {
  await api.delete(`/permission-schemes/${id}`);
}

export async function addSchemeGrant(
  id: string,
  payload: { permission: string; holder_type: HolderType; holder_value: string }
): Promise<PermissionGrant> {
  const res = await api.post<PermissionGrant>(`/permission-schemes/${id}/grants`, payload);
  return res.data;
}

export async function deleteSchemeGrant(id: string, grantId: string): Promise<void> {
  await api.delete(`/permission-schemes/${id}/grants/${grantId}`);
}

export async function setProjectScheme(projectId: string, scheme_id: string | null): Promise<void> {
  await api.put(`/permission-schemes/projects/${projectId}`, { scheme_id });
}
