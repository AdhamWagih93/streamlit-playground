import { api } from './client';
import {
  MailSettings,
  MailSettingsUpdate,
  TestResult,
  JiraConnection,
  JiraConnectionPayload,
  JiraRemoteProject,
  IdentityProvider,
  IdentityProviderPayload,
  GlobalPermission,
  HolderType,
  AuthSettings,
} from '../types';

// Mail -----------------------------------------------------------------------
export async function getMailSettings(): Promise<MailSettings> {
  const res = await api.get<MailSettings>('/admin/mail');
  return res.data;
}

export async function updateMailSettings(payload: MailSettingsUpdate): Promise<MailSettings> {
  const res = await api.put<MailSettings>('/admin/mail', payload);
  return res.data;
}

export async function testMail(to: string): Promise<TestResult> {
  const res = await api.post<TestResult>('/admin/mail/test', { to });
  return res.data;
}

// Authentication settings -----------------------------------------------------
export async function getAuthSettings(): Promise<AuthSettings> {
  const res = await api.get<AuthSettings>('/admin/auth-settings');
  return res.data;
}

export async function updateAuthSettings(payload: AuthSettings): Promise<AuthSettings> {
  const res = await api.put<AuthSettings>('/admin/auth-settings', payload);
  return res.data;
}

// Jira connections ------------------------------------------------------------
export async function listJiraConnections(): Promise<JiraConnection[]> {
  const res = await api.get<JiraConnection[]>('/admin/jira-connections');
  return res.data;
}

export async function createJiraConnection(payload: JiraConnectionPayload): Promise<JiraConnection> {
  const res = await api.post<JiraConnection>('/admin/jira-connections', payload);
  return res.data;
}

export async function updateJiraConnection(
  id: string,
  payload: Partial<JiraConnectionPayload>
): Promise<JiraConnection> {
  const res = await api.patch<JiraConnection>(`/admin/jira-connections/${id}`, payload);
  return res.data;
}

export async function deleteJiraConnection(id: string): Promise<void> {
  await api.delete(`/admin/jira-connections/${id}`);
}

export async function testJiraConnection(id: string): Promise<TestResult> {
  const res = await api.post<TestResult>(`/admin/jira-connections/${id}/test`);
  return res.data;
}

export async function listJiraConnectionProjects(id: string): Promise<JiraRemoteProject[]> {
  const res = await api.get<JiraRemoteProject[]>(`/admin/jira-connections/${id}/projects`);
  return res.data;
}

// Identity providers ----------------------------------------------------------
export async function listIdentityProviders(): Promise<IdentityProvider[]> {
  const res = await api.get<IdentityProvider[]>('/admin/identity-providers');
  return res.data;
}

export async function createIdentityProvider(payload: IdentityProviderPayload): Promise<IdentityProvider> {
  const res = await api.post<IdentityProvider>('/admin/identity-providers', payload);
  return res.data;
}

export async function updateIdentityProvider(
  id: string,
  payload: Partial<IdentityProviderPayload>
): Promise<IdentityProvider> {
  const res = await api.patch<IdentityProvider>(`/admin/identity-providers/${id}`, payload);
  return res.data;
}

export async function deleteIdentityProvider(id: string): Promise<void> {
  await api.delete(`/admin/identity-providers/${id}`);
}

export async function testIdentityProvider(id: string): Promise<TestResult> {
  const res = await api.post<TestResult>(`/admin/identity-providers/${id}/test`);
  return res.data;
}

// Global permissions ----------------------------------------------------------
export async function listGlobalPermissions(): Promise<GlobalPermission[]> {
  const res = await api.get<GlobalPermission[]>('/admin/global-permissions');
  return res.data;
}

export async function createGlobalPermission(payload: {
  permission: string;
  holder_type: HolderType;
  holder_value: string;
}): Promise<GlobalPermission> {
  const res = await api.post<GlobalPermission>('/admin/global-permissions', payload);
  return res.data;
}

export async function deleteGlobalPermission(id: string): Promise<void> {
  await api.delete(`/admin/global-permissions/${id}`);
}
