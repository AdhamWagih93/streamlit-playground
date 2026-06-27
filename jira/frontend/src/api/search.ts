import { api } from './client';
import { Page, IssueListItem, SavedFilter, TqlSchema, TqlValue } from '../types';

export async function runSearch(tql: string, page = 1, page_size = 50): Promise<Page<IssueListItem>> {
  const res = await api.post<Page<IssueListItem>>('/search', { tql, page, page_size });
  return res.data;
}

// --- TQL autocomplete / help ------------------------------------------------

export async function getTqlSchema(): Promise<TqlSchema> {
  const res = await api.get<TqlSchema>('/search/tql-schema');
  return res.data;
}

export async function getTqlValues(field: string, q = ''): Promise<TqlValue[]> {
  const res = await api.get<TqlValue[]>('/search/values', { params: { field, q } });
  return res.data;
}

export async function validateTql(tql: string): Promise<{ valid: boolean; error?: string | null }> {
  const res = await api.get<{ valid: boolean; error?: string | null }>('/search/validate', {
    params: { tql },
  });
  return res.data;
}

export async function listFilters(): Promise<SavedFilter[]> {
  const res = await api.get<SavedFilter[]>('/search/filters');
  return res.data;
}

export async function createFilter(payload: {
  name: string;
  query: string;
  is_shared: boolean;
}): Promise<SavedFilter> {
  const res = await api.post<SavedFilter>('/search/filters', payload);
  return res.data;
}

export async function updateFilter(
  id: string,
  payload: Partial<{ name: string; query: string; is_shared: boolean }>
): Promise<SavedFilter> {
  const res = await api.patch<SavedFilter>(`/search/filters/${id}`, payload);
  return res.data;
}

export async function deleteFilter(id: string): Promise<void> {
  await api.delete(`/search/filters/${id}`);
}
