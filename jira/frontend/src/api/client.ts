import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

const ACCESS_KEY = 'trackly_access_token';
const REFRESH_KEY = 'trackly_refresh_token';

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

export const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStore.access;
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

// Single-flight refresh so concurrent 401s don't stampede.
let refreshing: Promise<string | null> | null = null;

async function doRefresh(): Promise<string | null> {
  const refresh = tokenStore.refresh;
  if (!refresh) return null;
  try {
    // Use a bare axios call to avoid interceptor recursion.
    const res = await axios.post('/api/auth/refresh', { refresh_token: refresh });
    const { access_token, refresh_token } = res.data;
    tokenStore.set(access_token, refresh_token ?? refresh);
    return access_token;
  } catch {
    tokenStore.clear();
    return null;
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;
    const status = error.response?.status;
    const url = original?.url ?? '';

    // Don't try to refresh the refresh/login endpoints themselves.
    const isAuthCall = url.includes('/auth/login') || url.includes('/auth/refresh');

    if (status === 401 && original && !original._retry && !isAuthCall) {
      original._retry = true;
      if (!refreshing) {
        refreshing = doRefresh().finally(() => {
          refreshing = null;
        });
      }
      const newToken = await refreshing;
      if (newToken) {
        original.headers.set('Authorization', `Bearer ${newToken}`);
        return api(original);
      }
      // Refresh failed — bounce to login.
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export function apiErrorMessage(err: unknown, fallback = 'Something went wrong'): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
    if (err.message) return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}
