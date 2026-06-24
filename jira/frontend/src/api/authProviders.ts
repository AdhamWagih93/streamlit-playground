import axios from 'axios';
import { api } from './client';
import { AuthProvider, AuthPolicy } from '../types';

// Public endpoint — works without auth, but using the shared client is fine
// since the request interceptor only adds a header when a token exists.
export async function listAuthProviders(): Promise<AuthProvider[]> {
  const res = await api.get<AuthProvider[]>('/auth/providers');
  return res.data;
}

// Public endpoint — instance auth policy, no auth required.
export async function getAuthPolicy(): Promise<AuthPolicy> {
  const res = await api.get<AuthPolicy>('/auth/policy');
  return res.data;
}

export async function getEntraAuthorizeUrl(id: string): Promise<string> {
  const res = await axios.get<{ authorization_url: string }>(`/api/auth/entra/${id}/authorize`);
  return res.data.authorization_url;
}
