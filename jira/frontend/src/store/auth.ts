import { create } from 'zustand';
import { User } from '../types';
import { tokenStore } from '../api/client';
import * as authApi from '../api/auth';

interface AuthState {
  user: User | null;
  loading: boolean;
  initialized: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  loadMe: () => Promise<void>;
  setUser: (user: User) => void;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: false,
  initialized: false,

  async login(username, password) {
    set({ loading: true });
    try {
      const tokens = await authApi.login(username, password);
      tokenStore.set(tokens.access_token, tokens.refresh_token);
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },

  logout() {
    tokenStore.clear();
    set({ user: null });
  },

  async loadMe() {
    if (!tokenStore.access) {
      set({ initialized: true });
      return;
    }
    set({ loading: true });
    try {
      const user = await authApi.getMe();
      set({ user, loading: false, initialized: true });
    } catch {
      tokenStore.clear();
      set({ user: null, loading: false, initialized: true });
    }
  },

  setUser(user) {
    set({ user });
  },
}));
