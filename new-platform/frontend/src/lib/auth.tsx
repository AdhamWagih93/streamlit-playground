import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { apiGet, apiPost, ApiError } from "./api";

export type Me = {
  username: string;
  display_name: string;
  email: string;
  roles: string[];
  role: string;
  is_admin: boolean;
  teams: string[];
  visible_envs: string[];
  visible_event_types: string[];
  auth_mode: "none" | "entra" | "ldap";
  data_mode: "demo" | "live";
};

type AuthCtx = {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<void>;
  devSwitch: (roles: string[], teams: string[]) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      setMe(await apiGet<Me>("/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const devSwitch = async (roles: string[], teams: string[]) => {
    await apiPost("/auth/dev/switch", { roles, teams });
    await refresh();
    window.location.reload(); // every panel is scope-dependent — full refetch is correct
  };

  const logout = async () => {
    await apiPost("/auth/logout");
    setMe(null);
  };

  return <Ctx.Provider value={{ me, loading, refresh, devSwitch, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
