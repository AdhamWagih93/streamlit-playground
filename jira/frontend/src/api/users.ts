import { api } from './client';
import { User } from '../types';

export async function searchUsers(q = ''): Promise<User[]> {
  const res = await api.get<User[]>('/users', { params: q ? { q } : {} });
  return res.data;
}
