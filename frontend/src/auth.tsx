import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "./api";

interface AuthState { authenticated: boolean; setupRequired: boolean; refresh: () => Promise<void>; }
const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuth] = useState(false);
  const [setupRequired, setSetup] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const refresh = async () => {
    try {
      const s = await api.status();
      setAuth(s.authenticated); setSetup(s.setup_required);
    } catch {
      setAuth(false); setSetup(false);
    } finally {
      setLoaded(true);
    }
  };
  useEffect(() => { void refresh(); }, []);
  if (!loaded) return <p>Loading…</p>;
  return <Ctx.Provider value={{ authenticated, setupRequired, refresh }}>{children}</Ctx.Provider>;
}
