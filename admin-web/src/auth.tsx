import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { api, tokenStore, CurrentUser } from './api';

type AuthCtx = {
  user: CurrentUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(tokenStore.getUser());
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    const tok = tokenStore.get();
    if (!tok) { setLoading(false); return; }
    api.me()
      .then((u) => { setUser(u); tokenStore.setUser(u); })
      .catch(() => { tokenStore.clear(); setUser(null); })
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string) {
    const t = await api.login(email, password);
    tokenStore.set(t.access_token, t.refresh_token);
    const u = await api.me();
    if (u.role !== 'admin') {
      tokenStore.clear();
      throw new Error('This account does not have admin privileges.');
    }
    tokenStore.setUser(u);
    setUser(u);
  }

  async function logout() {
    try { await api.logout(); } catch { /* ignore */ }
    tokenStore.clear();
    setUser(null);
  }

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth() { return useContext(Ctx); }
