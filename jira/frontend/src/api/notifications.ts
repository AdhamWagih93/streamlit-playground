import { api } from './client';
import { Notification } from '../types';

export async function listNotifications(unread_only = false): Promise<Notification[]> {
  const res = await api.get<Notification[]>('/notifications', {
    params: unread_only ? { unread_only: true } : {},
  });
  return res.data;
}

export async function unreadCount(): Promise<number> {
  const res = await api.get<{ count: number }>('/notifications/unread-count');
  return res.data.count;
}

export async function markRead(id: string): Promise<void> {
  await api.post(`/notifications/${id}/read`);
}

export async function markAllRead(): Promise<void> {
  await api.post('/notifications/read-all');
}
