import { api } from './client';
import { Board, BoardData, BacklogData, Sprint } from '../types';

export async function listBoards(project_id: string): Promise<Board[]> {
  const res = await api.get<Board[]>('/agile/boards', { params: { project_id } });
  return res.data;
}

export async function getBoard(boardId: string, sprint_id?: string): Promise<BoardData> {
  const res = await api.get<BoardData>(`/agile/boards/${boardId}/board`, {
    params: sprint_id ? { sprint_id } : {},
  });
  return res.data;
}

export async function getBacklog(boardId: string): Promise<BacklogData> {
  const res = await api.get<BacklogData>(`/agile/boards/${boardId}/backlog`);
  return res.data;
}

export async function createSprint(
  boardId: string,
  payload: { name: string; goal?: string }
): Promise<Sprint> {
  const res = await api.post<Sprint>(`/agile/boards/${boardId}/sprints`, payload);
  return res.data;
}

export async function updateSprint(
  sprintId: string,
  payload: Partial<{ name: string; goal: string; start_date: string; end_date: string }>
): Promise<Sprint> {
  const res = await api.patch<Sprint>(`/agile/sprints/${sprintId}`, payload);
  return res.data;
}

export async function startSprint(sprintId: string): Promise<Sprint> {
  const res = await api.post<Sprint>(`/agile/sprints/${sprintId}/start`);
  return res.data;
}

export async function completeSprint(sprintId: string): Promise<Sprint> {
  const res = await api.post<Sprint>(`/agile/sprints/${sprintId}/complete`);
  return res.data;
}

export async function deleteSprint(sprintId: string): Promise<void> {
  await api.delete(`/agile/sprints/${sprintId}`);
}
