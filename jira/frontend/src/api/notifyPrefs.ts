import { api } from './client';
import { NotifPreferences, NotifChannel } from '../types';

export async function getNotificationPreferences(): Promise<NotifPreferences> {
  const res = await api.get<NotifPreferences>('/notification-preferences');
  return res.data;
}

export async function updateNotificationPreferences(
  updates: { event: string; channel: NotifChannel; enabled: boolean }[]
): Promise<NotifPreferences> {
  const res = await api.put<NotifPreferences>('/notification-preferences', { updates });
  return res.data;
}
