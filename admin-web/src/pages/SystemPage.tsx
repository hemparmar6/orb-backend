import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card } from '../ui';

export default function SystemPage() {
  const [health, setHealth] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api.systemHealth()
      .then((h) => !cancelled && setHealth(h))
      .catch((e) => !cancelled && setErr(e.message || 'Failed'));
    return () => { cancelled = true; };
  }, [tick]);

  const isOk = (s?: string) => s === 'ok';

  return (
    <div>
      <div className="toolbar">
        <div className="dim">Live system status · auto-fetched from <code>/api/v1/admin/system/health</code></div>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={() => setTick(t => t + 1)} data-testid="system-refresh-button">↻ Refresh</button>
      </div>

      {err && <div className="card" data-testid="system-error" style={{ color: 'var(--red)' }}>{err}</div>}

      <div className="grid grid-4" data-testid="system-cards">
        <Card testId="sys-status" title="Overall Status" value={<><span className={`status-dot ${isOk(health?.status) ? 'ok' : 'err'}`} />{health?.status ?? '—'}</>} sub={`API v${health?.api_version ?? '—'}`} />
        <Card testId="sys-database" title="API / DB" value={<><span className={`status-dot ${isOk(health?.database) ? 'ok' : 'err'}`} />{health?.database ?? '—'}</>} sub="Async SQLAlchemy" />
        <Card testId="sys-redis" title="Redis (Pub-Sub / WS)" value={<><span className={`status-dot ${isOk(health?.redis) ? 'ok' : health?.redis === 'disabled' ? 'off' : 'err'}`} />{health?.redis ?? '—'}</>} sub="Order broadcaster backend" />
        <Card testId="sys-workers" title="Background Workers" value={health ? String(health.engine_sessions_running) + ' active' : '—'} sub={`${health?.engine_sessions_total ?? 0} total engine sessions`} />
      </div>

      <div className="section-title">Users · Broker Accounts · Runs</div>
      <div className="grid grid-3">
        <Card testId="sys-users" title="Users" value={health?.users_total ?? '—'} sub={`${health?.users_active ?? 0} active · ${health?.users_admins ?? 0} admins`} />
        <Card testId="sys-brokers" title="Broker Accounts" value={health?.broker_accounts_total ?? '—'} sub={`${health?.broker_accounts_active ?? 0} active`} />
        <Card testId="sys-backtests" title="Backtests" value={health?.backtests_total ?? '—'} sub={`${health?.backtests_completed ?? 0} completed`} />
      </div>

      <div className="section-title">Providers &amp; Strategies</div>
      <div className="grid grid-3" data-testid="sys-providers">
        <div className="card">
          <div className="card-title">Market Data Providers</div>
          <div className="mono" style={{ marginTop: 6 }}>{(health?.market_data_providers || []).join(', ') || '—'}</div>
        </div>
        <div className="card">
          <div className="card-title">Historical Providers</div>
          <div className="mono" style={{ marginTop: 6 }}>{(health?.historical_providers || []).join(', ') || '—'}</div>
        </div>
        <div className="card">
          <div className="card-title">Registered Strategies</div>
          <div className="mono" style={{ marginTop: 6 }}>{(health?.registered_strategies || []).join(', ') || '—'}</div>
        </div>
      </div>

      <div className="section-title">Raw payload</div>
      <pre className="json" data-testid="system-raw">{JSON.stringify(health, null, 2)}</pre>
    </div>
  );
}
