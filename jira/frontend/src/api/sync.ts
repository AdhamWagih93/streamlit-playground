import { api } from './client';
import { SyncLink, SyncDiscover, SyncRun } from '../types';

export interface SyncConnection {
  id: number;
  name: string;
  base_url: string;
  is_default: boolean;
}

// Non-secret connection list, readable by project admins (not just site admins).
export async function listSyncConnections(): Promise<SyncConnection[]> {
  const res = await api.get<SyncConnection[]>('/sync/connections');
  return res.data;
}

export async function getSync(projectId: string): Promise<SyncLink | null> {
  try {
    const res = await api.get<SyncLink>(`/sync/projects/${projectId}`);
    return res.data;
  } catch (err: unknown) {
    // 404 = project not linked yet
    if (isNotFound(err)) return null;
    throw err;
  }
}

function isNotFound(err: unknown): boolean {
  return (
    typeof err === 'object' &&
    err !== null &&
    'response' in err &&
    (err as { response?: { status?: number } }).response?.status === 404
  );
}

export async function discoverSync(
  projectId: string,
  connectionId?: string
): Promise<SyncDiscover> {
  const res = await api.get<SyncDiscover>(`/sync/projects/${projectId}/discover`, {
    params: connectionId ? { connection_id: connectionId } : {},
  });
  return res.data;
}

export async function linkSync(
  projectId: string,
  payload: { connection_id?: string; jira_project_key?: string; sync_permissions: boolean }
): Promise<SyncLink> {
  const res = await api.post<SyncLink>(`/sync/projects/${projectId}/link`, payload);
  return res.data;
}

export async function startSync(projectId: string): Promise<{ status: string; message: string; link: SyncLink }> {
  const res = await api.post(`/sync/projects/${projectId}/start`);
  return res.data;
}

export async function pauseSync(projectId: string): Promise<{ status: string; message: string; link: SyncLink }> {
  const res = await api.post(`/sync/projects/${projectId}/pause`);
  return res.data;
}

export async function resumeSync(projectId: string): Promise<{ status: string; message: string; link: SyncLink }> {
  const res = await api.post(`/sync/projects/${projectId}/resume`);
  return res.data;
}

export async function unlinkSync(projectId: string): Promise<void> {
  await api.delete(`/sync/projects/${projectId}/link`);
}

export async function listSyncRuns(projectId: string): Promise<SyncRun[]> {
  const res = await api.get<SyncRun[]>(`/sync/projects/${projectId}/runs`);
  return res.data;
}
