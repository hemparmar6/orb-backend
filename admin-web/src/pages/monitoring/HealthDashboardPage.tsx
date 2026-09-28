import { useEffect, useState } from 'react';
import { api } from '../../api';
import { Card } from '../../ui';

type Component = { status: string; latency_ms?: number | null; detail?: string; [k: string]: any };
type Health = { overall: string; app: string; version: string; environment: string; components: Record<string, Component> };

const dotFor = (s: string | undefined) => (s === 'ok' ? 'ok' : s === 'disabled' ? 'off' : s === 'degraded' ? 'warn' : 'err');
const labelFor = (s: string | undefined) => s || '—';

export default function HealthDashboardPage() {
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancel = false;
    api.monitoringHealth()
      .then((v: Health) => !cancel && setH(v))
      .catch(e => !cancel && setErr(e.message || 'Failed'));
    return () => { cancel = true; };
  }, [tick]);

  useEffect(() => {
    const t = setInterval(() => setTick(x => x + 1), 15_000);
    return () => clearInterval(t);
  }, []);

  const c = h?.components;

  return (
    <div data-testid="health-page">
      <div className="toolbar">
        <div className="dim">Live system status · auto-refreshed every 15s · <code>/api/v1/monitoring/health</code></div>
        <div className="spacer" />
        <button className="btn btn-sm" data-testid="health-refresh-button" onClick={() => setTick(t => t + 1)}>↻ Refresh</button>
      </div>

      {err && <div className="card" data-testid="health-error" style={{ color: 'var(--red)' }}>{err}</div>}

      <div className="grid grid-4" data-testid="health-overall">
        <Card testId="hp-overall" title="Overall" value={<><span className={`status-dot ${dotFor(h?.overall || '')}`} />{labelFor(h?.overall)}</>} sub={`${h?.app ?? '—'} v${h?.version ?? '—'} · ${h?.environment ?? '—'}`} />
        <Card testId="hp-api" title="API" value={<><span className={`status-dot ${dotFor(c?.api?.status || '')}`} />{labelFor(c?.api?.status)}</>} sub="FastAPI process" />
        <Card testId="hp-db" title="Database" value={<><span className={`status-dot ${dotFor(c?.database?.status || '')}`} />{labelFor(c?.database?.status)}</>} sub={`${c?.database?.latency_ms ?? '—'} ms`} />
        <Card testId="hp-redis" title="Redis" value={<><span className={`status-dot ${dotFor(c?.redis?.status || '')}`} />{labelFor(c?.redis?.status)}</>} sub={c?.redis?.detail || `${c?.redis?.latency_ms ?? '—'} ms`} />
      </div>

      <div className="section-title">AI · Brokers · Realtime · Scheduler</div>
      <div className="grid grid-4">
        <Card testId="hp-ai" title="AI Service" value={<><span className={`status-dot ${dotFor(c?.ai_service?.status || '')}`} />{labelFor(c?.ai_service?.status)}</>} sub={`${c?.ai_service?.provider ?? '—'} · ${c?.ai_service?.model ?? '—'}`} />
        <Card testId="hp-brokers" title="Broker Connectivity" value={<><span className={`status-dot ${dotFor(c?.brokers?.status || '')}`} />{labelFor(c?.brokers?.status)}</>} sub={`${c?.brokers?.accounts_active ?? 0} / ${c?.brokers?.accounts_total ?? 0} active`} />
        <Card testId="hp-ws" title="WebSockets" value={<><span className={`status-dot ${dotFor(c?.websockets?.status || '')}`} />{labelFor(c?.websockets?.status)}</>} sub={`quote:${String(c?.websockets?.quote_broadcaster)} · order:${String(c?.websockets?.order_broadcaster)}`} />
        <Card testId="hp-sched" title="Scheduler" value={<><span className={`status-dot ${dotFor(c?.scheduler?.status || '')}`} />{labelFor(c?.scheduler?.status)}</>} sub={c?.scheduler?.detail || 'APScheduler'} />
      </div>

      <div className="section-title">Component Detail</div>
      <div className="grid grid-2">
        {c && Object.entries(c).map(([name, comp]) => (
          <div className="card" key={name} data-testid={`hp-detail-${name}`}>
            <div className="card-title">
              <span className={`status-dot ${dotFor(comp.status)}`} /> {name}
            </div>
            <pre className="json" style={{ margin: 0 }}>{JSON.stringify(comp, null, 2)}</pre>
          </div>
        ))}
      </div>
    </div>
  );
}
