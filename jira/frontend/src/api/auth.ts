import { api } from './client';
import { Tokens, User } from '../types';

export async function login(username: string, password: string): Promise<Tokens> {
  const body = new URLSearchParams();
  body.set('username', username);
  body.set('password', password);
  const res = await api.post<Tokens>('/auth/login', body, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  return res.data;
}

export async function register(payload: {
  username: string;
  email: string;
  display_name: string;
  password: string;
}): Promise<User> {
  const res = await api.post<User>('/auth/register', payload);
  return res.data;
}

export async function refresh(refresh_token: string): Promise<Tokens> {
  const res = await api.post<Tokens>('/auth/refresh', { refresh_token });
  return res.data;
}

export async function getMe(): Promise<User> {
  const res = await api.get<User>('/auth/me');
  return res.data;
}

export async function updateMe(payload: Partial<User>): Promise<User> {
  const res = await api.patch<User>('/auth/me', payload);
  return res.data;
}

// Stop impersonation — must be called WITH the active impersonation token.
// Returns fresh tokens for the real admin.
export async function stopImpersonation(): Promise<{ access_token: string; refresh_token: string }> {
  const res = await api.post<{ access_token: string; refresh_token: string }>(
    '/auth/stop-impersonation'
  );
  return res.data;
}
