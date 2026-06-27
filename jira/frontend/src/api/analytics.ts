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
  Window,
} from '../types';

// Time-window params accepted by every analytics endpoint. `period` is a
// shortcut (`all` or `7d`/`30d`/`90d`/`1y`); an explicit from/to range overrides it.
export interface AnalyticsParams {
  period?: string;
  from?: string;
  to?: string;
}

// Site-admin only — stats across every project.
export async function getOverview(params?: AnalyticsParams): Promise<OverviewStats> {
  const res = await api.get<OverviewStats>('/analytics/overview', { params });
  return res.data;
}

// Any user — stats limited to the projects they can access.
export async function getMyOverview(params?: AnalyticsParams): Promise<OverviewStats> {
  const res = await api.get<OverviewStats>('/analytics/my', { params });
  return res.data;
}

// Per-project stats — requires browse access (403 otherwise).
export async function getProjectStats(keyOrId: string, params?: AnalyticsParams): Promise<ProjectStats> {
  const res = await api.get<ProjectStats>(`/analytics/projects/${keyOrId}`, { params });
  return res.data;
}
