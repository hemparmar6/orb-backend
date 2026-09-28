import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card, fmtDate, statusBadge } from '../ui';
import { useAdminSocket } from '../useAdminSocket';
import { LiveBadge } from '../LiveBadge';

export default function DashboardPage() {
  const { snapshot, state, lastUpdatedAt } = useAdminSocket(3);
  const [fallback, setFallback] = useState<any>(null);
  const [fallbackErr, setFallbackErr] = useState<string | null>(null);

  // REST fallback ONLY when the socket never opens — we don't want two
  // sources of truth once WS is streaming.
  useEffect(() => {
    let cancelled = false;
    if (state !== 'open' && !snapshot) {
      Promise.all([
        api.systemHealth().catch(() => null),
        api.listOrders({ page: 1, page_size: 10 }).catch(() => null),
        api.listSessions({ page: 1, page_size: 5, status: 'running' }).catch(() => null),
      ]).then(([h, o, s]) => {
        if (cancelled) return;
        if (!h) { setFallbackErr('Unable to load dashboard'); return; }
        setFallback({ health: h, recent_orders: o?.items || [], running_sessions: s?.items || [], daily_pnl: 0 });
      });
    }
    return () => { cancelled = true; };
  }, [state, snapshot]);

  // Prefer live snapshot; fall back to REST until the socket opens.
  const data = snapshot || fallback;
  const health = data?.health;
  const dailyPnl = data?.daily_pnl ?? 0;
  const pnlCls = dailyPnl >= 0 ? 'pos' : 'neg';
  const runningSessions = data?.running_sessions || [];
  const recentOrders = data?.recent_orders || [];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          Real-time snapshot · <code>WS /api/v1/ws/admin</code>
        </div>
        <LiveBadge state={state} lastUpdatedAt={lastUpdatedAt} testId="dashboard-live-badge" />
      </div>

      {fallbackErr && !snapshot && (
        <div className="card" data-testid="dashboard-error" style={{ borderColor: 'var(--red)', color: 'var(--red)', marginBottom: 12 }}>
          {fallbackErr}
        </div>
      )}

      <div className="grid grid-4" data-testid="overview-cards">
        <Card testId="stat-users-active" title="Active Users" value={health?.users_active ?? '—'} sub={`${health?.users_total ?? 0} total · ${health?.users_admins ?? 0} admins`} />
        <Card testId="stat-brokers" title="Connected Brokers" value={health?.broker_accounts_active ?? '—'} sub={`${health?.broker_accounts_total ?? 0} total accounts`} />
        <Card testId="stat-sessions-running" title="Running Sessions" value={health?.engine_sessions_running ?? '—'} sub={`${health?.engine_sessions_total ?? 0} total sessions`} />
        <Card testId="stat-daily-pnl" title="Daily P&L" value={<span className={pnlCls}>{Number(dailyPnl).toFixed(2)}</span>} sub="Sum of engine session day_pnl" />
      </div>

      <div className="section-title">System Health</div>
      <div className="grid grid-4">
        <Card testId="stat-status" title="Status" value={<><span className={`status-dot ${health?.status === 'ok' ? 'ok' : 'err'}`} />{health?.status ?? '—'}</>} sub={`API v${health?.api_version ?? '—'}`} />
        <Card testId="stat-db" title="Database" value={<><span className={`status-dot ${health?.database === 'ok' ? 'ok' : 'err'}`} />{health?.database ?? '—'}</>} />
        <Card testId="stat-redis" title="Redis / Pub-Sub" value={<><span className={`status-dot ${health?.redis === 'ok' ? 'ok' : health?.redis === 'disabled' ? 'off' : 'err'}`} />{health?.redis ?? '—'}</>} sub="WS + order broadcast backend" />
        <Card testId="stat-strategies" title="Registered Strategies" value={health?.registered_strategies?.length ?? '—'} sub={(health?.registered_strategies || []).join(', ') || '—'} />
      </div>

      <div className="section-title">Live Sessions</div>
      <div className="table-wrap" data-testid="live-sessions-table">
        <table className="table">
          <thead>
            <tr>
              <th>SESSION</th><th>USER</th><th>STRATEGY</th><th>MODE</th><th>SYMBOLS</th><th>DAY P&L</th><th>STARTED</th><th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {runningSessions.length ? runningSessions.map((s: any) => (
              <tr key={s.id}>
                <td className="mono">{String(s.id).slice(0, 8)}</td>
                <td className="mono">{String(s.user_id).slice(0, 8)}</td>
                <td>{s.strategy_name}</td>
                <td>{statusBadge(s.execution_mode)}</td>
                <td className="mono">{(s.symbols || []).join(', ') || '—'}</td>
                <td className={`mono ${Number(s.day_pnl || 0) >= 0 ? 'pos' : 'neg'}`}>{Number(s.day_pnl || 0).toFixed(2)}</td>
                <td className="mono">{fmtDate(s.started_at)}</td>
                <td>{statusBadge(s.status)}</td>
              </tr>
            )) : (
              <tr><td colSpan={8} className="table-empty">No running sessions.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="section-title">Recent Orders</div>
      <div className="table-wrap" data-testid="recent-orders-table">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th><th>USER</th><th>SESSION</th><th>SYMBOL</th><th>SIDE</th><th>TYPE</th><th>QTY</th><th>STATUS</th><th>CREATED</th>
            </tr>
          </thead>
          <tbody>
            {recentOrders.length ? recentOrders.map((o: any) => (
              <tr key={o.id}>
                <td className="mono">{String(o.id).slice(0, 8)}</td>
                <td className="mono">{String(o.user_id).slice(0, 8)}</td>
                <td className="mono">{String(o.engine_session_id || '').slice(0, 8) || '—'}</td>
                <td>{o.symbol}</td>
                <td>{statusBadge(o.side)}</td>
                <td className="mono">{o.order_type}</td>
                <td className="mono">{o.quantity}</td>
                <td>{statusBadge(o.status)}</td>
                <td className="mono">{fmtDate(o.created_at)}</td>
              </tr>
            )) : (
              <tr><td colSpan={9} className="table-empty">No orders yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
