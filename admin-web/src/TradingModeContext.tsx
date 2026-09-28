/**
 * Global Trading Mode context — one authoritative snapshot of the
 * server-side PAPER/LIVE state, shared across every screen so the top-nav
 * badge and any embedded read-out stay in sync after a mode change.
 *
 * SAFETY: On any load error the context refuses to report LIVE.  The badge
 * stays on PAPER (or the previously-known state) so a transient API
 * failure can never falsely present real-money execution as enabled.
 */
import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { api } from './api';

export type TradingModeSnapshot = {
  mode: 'paper' | 'live';
  changed_at?: string | null;
  changed_by?: string | null;
  armed_at?: string | null;
  cooldown_active?: boolean;
  cooldown_expires_at?: string | null;
  cooldown_seconds?: number;
  active_live_sessions?: number;
  open_live_positions?: number;
  open_live_orders?: number;
  kill_switch_active?: boolean;
  live_gate?: string;
  auto_revert?: {
    enabled: boolean;
    at_time?: string | null;
    timezone?: string | null;
    last_run_date?: string | null;
    last_run_result?: string | null;
    last_run_at?: string | null;
  };
};

type Ctx = {
  snapshot: TradingModeSnapshot | null;
  loading: boolean;
  errored: boolean;
  refresh: () => Promise<void>;
};

const TradingModeCtx = createContext<Ctx>({
  snapshot: null,
  loading: true,
  errored: false,
  refresh: async () => {},
});

/**
 * Provider mounted once at the app shell.  Only fetches when the caller
 * signals ``authenticated=true`` so unauthenticated screens (LoginPage)
 * don't fire an admin-only request.
 */
export function TradingModeProvider({
  authenticated,
  children,
}: {
  authenticated: boolean;
  children: ReactNode;
}) {
  const [snapshot, setSnapshot] = useState<TradingModeSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);
  const inFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (!authenticated) {
      setLoading(false);
      return;
    }
    if (inFlight.current) return inFlight.current;
    const p = (async () => {
      setLoading(true);
      try {
        const snap = await api.tradingModeGet();
        // Only accept explicit paper/live from the server; treat anything
        // unexpected as an error so we never falsely display LIVE.
        if (snap && (snap.mode === 'paper' || snap.mode === 'live')) {
          setSnapshot(snap as TradingModeSnapshot);
          setErrored(false);
        } else {
          setErrored(true);
        }
      } catch {
        setErrored(true);
        // Never fall back to LIVE.  Keep previous snapshot if we had one,
        // otherwise remain null (badge renders PAPER-safe placeholder).
      } finally {
        setLoading(false);
        inFlight.current = null;
      }
    })();
    inFlight.current = p;
    return p;
  }, [authenticated]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ snapshot, loading, errored, refresh }),
    [snapshot, loading, errored, refresh],
  );

  return <TradingModeCtx.Provider value={value}>{children}</TradingModeCtx.Provider>;
}

export function useTradingMode(): Ctx {
  return useContext(TradingModeCtx);
}
