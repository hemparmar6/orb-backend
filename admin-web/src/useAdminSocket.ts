import { useEffect, useRef, useState } from 'react';
import { tokenStore } from './api';

/** Payload shape delivered by `WS /api/v1/ws/admin`. */
export type AdminSnapshot = {
  health: {
    status: string;
    database: string;
    redis: string;
    engine_sessions_running: number;
    engine_sessions_total: number;
    users_total: number;
    users_active: number;
    users_admins: number;
    broker_accounts_total: number;
    broker_accounts_active: number;
    backtests_total: number;
    backtests_completed: number;
    market_data_providers: string[];
    historical_providers: string[];
    registered_strategies: string[];
    api_version: string;
    now: string;
  };
  running_sessions: any[];
  recent_orders: any[];
  order_counters: {
    orders_pending: number;
    orders_filled: number;
    orders_partial: number;
    orders_cancelled: number;
    orders_rejected: number;
  };
  daily_pnl: number;
  generated_at: string;
};

export type ConnState = 'connecting' | 'open' | 'closed' | 'error';

/** Compute the ws:// URL that terminates at our FastAPI WS mount. */
function wsUrl(intervalSec: number): string | null {
  const token = tokenStore.get();
  if (!token) return null;
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({
    token,
    interval: String(Math.max(1, Math.min(15, intervalSec))),
  });
  return `${proto}//${window.location.host}/api/v1/ws/admin?${params.toString()}`;
}

export function useAdminSocket(intervalSec: number = 3): {
  snapshot: AdminSnapshot | null;
  state: ConnState;
  lastUpdatedAt: number | null;
  error: string | null;
} {
  const [snapshot, setSnapshot] = useState<AdminSnapshot | null>(null);
  const [state, setState] = useState<ConnState>('connecting');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const backoffRef = useRef<number>(500);
  const closedByUsRef = useRef<boolean>(false);
  const pingTimerRef = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    closedByUsRef.current = false;

    function connect(): void {
      const url = wsUrl(intervalSec);
      if (!url) { setState('error'); setError('No auth token'); return; }
      setState('connecting');
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setState('open');
        setError(null);
        backoffRef.current = 500;
        // Heartbeat keeps some proxies from culling the socket.
        pingTimerRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            try { ws.send(JSON.stringify({ action: 'ping' })); } catch { /* noop */ }
          }
        }, 25_000);
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'snapshot' && msg.data) {
            setSnapshot(msg.data as AdminSnapshot);
            setLastUpdatedAt(Date.now());
          } else if (msg.type === 'error' && msg.data) {
            setError(String(msg.data.message || msg.data.code || 'ws error'));
          }
        } catch (e) {
          // Ignore malformed frames — the server should never send them.
        }
      };

      ws.onerror = () => {
        setState('error');
      };

      ws.onclose = () => {
        if (pingTimerRef.current) {
          clearInterval(pingTimerRef.current);
          pingTimerRef.current = null;
        }
        if (closedByUsRef.current) { setState('closed'); return; }
        setState('closed');
        // Exponential backoff (capped) — never spam the server if auth is bad.
        const delay = backoffRef.current;
        backoffRef.current = Math.min(delay * 2, 8000);
        window.setTimeout(() => {
          if (!closedByUsRef.current) connect();
        }, delay);
      };
    }

    connect();

    return () => {
      closedByUsRef.current = true;
      if (pingTimerRef.current) { clearInterval(pingTimerRef.current); pingTimerRef.current = null; }
      if (wsRef.current) { try { wsRef.current.close(); } catch { /* noop */ } wsRef.current = null; }
    };
  }, [intervalSec]);

  return { snapshot, state, lastUpdatedAt, error };
}
