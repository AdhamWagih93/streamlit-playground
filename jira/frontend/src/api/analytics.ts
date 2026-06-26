import { api } from './client';
import { OverviewStats, ProjectStats } from '../types';

// Re-export attention/insight types so callers can import them alongside the
// analytics API surface.
export type {
  OverviewStats,
  ProjectStats,
  ProjectStatRow,
  AttentionIssue,
  AttentionItem,
  AttentionSeverity,
  SprintHealth,
} from '../types';

// Site-admin only — stats across every project.
export async function getOverview(): Promise<OverviewStats> {
  const res = await api.get<OverviewStats>('/analytics/overview');
  return res.data;
}

// Any user — stats limited to the projects they can access.
export async function getMyOverview(): Promise<OverviewStats> {
  const res = await api.get<OverviewStats>('/analytics/my');
  return res.data;
}

// Per-project stats — requires browse access (403 otherwise).
export async function getProjectStats(keyOrId: string): Promise<ProjectStats> {
  const res = await api.get<ProjectStats>(`/analytics/projects/${keyOrId}`);
  return res.data;
}
