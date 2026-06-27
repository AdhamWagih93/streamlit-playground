import { create } from 'zustand';
import { User } from '../types';
import { tokenStore } from '../api/client';
import * as authApi from '../api/auth';
import { impersonateUser as impersonateUserApi } from '../api/admin';
import { decodeJwt } from '../lib/jwt';

// Backup slots for the real admin's tokens while impersonating someone.
const ADMIN_ACCESS_KEY = 'trackly_admin_access';
const ADMIN_REFRESH_KEY = 'trackly_admin_refresh';

const adminBackup = {
  get access() {
    return localStorage.getItem(ADMIN_ACCESS_KEY);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ADMIN_ACCESS_KEY, access);
    localStorage.setItem(ADMIN_REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ADMIN_ACCESS_KEY);
    localStorage.removeItem(ADMIN_REFRESH_KEY);
  },
};

// True when the active access token is an impersonation token.
function isImpersonationToken(): boolean {
  return decodeJwt(tokenStore.access)?.imp === true;
}

interface AuthState {
  user: User | null;
  loading: boolean;
  initialized: boolean;
  impersonating: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  loadMe: () => Promise<void>;
  setUser: (user: User) => void;
  setTokens: (access: string, refresh: string) => Promise<void>;
  impersonate: (userId: number) => Promise<void>;
  stopImpersonation: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: false,
  initialized: false,
  impersonating: isImpersonationToken(),

  async login(username, password) {
    set({ loading: true });
    try {
      const tokens = await authApi.login(username, password);
      tokenStore.set(tokens.access_token, tokens.refresh_token);
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true, impersonating: isImpersonationToken() });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },

  logout() {
    tokenStore.clear();
    adminBackup.clear();
    set({ user: null, impersonating: false });
  },

  async loadMe() {
    if (!tokenStore.access) {
      set({ initialized: true, impersonating: false });
      return;
    }
    set({ loading: true });
    try {
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true, impersonating: isImpersonationToken() });
    } catch {
      tokenStore.clear();
      set({ user: null, loading: false, initialized: true, impersonating: false });
    }
  },

  setUser(user) {
    set({ user });
  },

  async setTokens(access, refresh) {
    tokenStore.set(access, refresh);
    set({ loading: true });
    try {
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true, impersonating: isImpersonationToken() });
    } catch (err) {
      tokenStore.clear();
      set({ user: null, loading: false, initialized: true, impersonating: false });
      throw err;
    }
  },

  async impersonate(userId) {
    // Back up the current (admin) tokens so we can return without a round-trip.
    const adminAccess = tokenStore.access;
    const adminRefresh = tokenStore.refresh;
    if (adminAccess && adminRefresh) {
      adminBackup.set(adminAccess, adminRefresh);
    }
    set({ loading: true });
    try {
      const tokens = await impersonateUserApi(userId);
      // Swap to the target user's tokens via the same store the interceptor reads.
      tokenStore.set(tokens.access_token, tokens.refresh_token);
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true, impersonating: true });
    } catch (err) {
      // Roll back: keep the admin signed in as themselves.
      if (adminAccess && adminRefresh) {
        tokenStore.set(adminAccess, adminRefresh);
      }
      adminBackup.clear();
      set({ loading: false });
      throw err;
    }
  },

  async stopImpersonation() {
    set({ loading: true });
    try {
      if (adminBackup.access) {
        // Fast path: restore the backed-up admin tokens.
        const access = localStorage.getItem(ADMIN_ACCESS_KEY)!;
        const refresh = localStorage.getItem(ADMIN_REFRESH_KEY)!;
        tokenStore.set(access, refresh);
        adminBackup.clear();
      } else {
        // No backup (e.g. reloaded mid-impersonation): ask the server, using
        // the current impersonation token, for fresh admin tokens.
        const tokens = await authApi.stopImpersonation();
        tokenStore.set(tokens.access_token, tokens.refresh_token);
      }
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true, impersonating: false });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },
}));
