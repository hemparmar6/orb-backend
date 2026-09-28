import { useEffect, useState } from 'react';
import { api, apiFetch } from '../api';
import { Badge } from '../ui';

type Plan = {
  id: string; key: string; name: string; tier: string; price_cents: number;
  currency: string; interval: string; provider: string;
  features: Record<string, boolean> | null;
};

type MeSub = { subscription: any; features: Record<string, boolean> };

export default function SubscriptionsPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [me, setMe] = useState<MeSub | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    Promise.all([
      apiFetch<Plan[]>('/subscriptions/plans'),
      apiFetch<MeSub>('/subscriptions/me'),
    ]).then(([p, m]) => { setPlans(p); setMe(m); })
      .catch((e) => setErr(String(e?.message || e)));
  };
  useEffect(load, []);

  const select = async (plan_key: string) => {
    setBusy(plan_key);
    try {
      const r = await apiFetch<any>('/subscriptions/checkout', {
        method: 'POST', body: JSON.stringify({ plan_key }),
      });
      if (r.url) {
        window.open(r.url, '_blank');
      }
      load();
    } catch (e: any) {
      alert(`Checkout failed: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <h2>Your subscription</h2>
      {err ? <div className="empty">Error: {err}</div> : null}
      <div className="table-wrap" style={{ maxWidth: 640 }}>
        <table className="table">
          <tbody>
            <tr>
              <th style={{ width: 180 }}>Plan</th>
              <td>{me?.subscription?.plan_name || 'Free (default)'}</td>
            </tr>
            <tr>
              <th>Status</th>
              <td>
                <Badge kind={me?.subscription?.status === 'active' ? 'ok' : 'warn'}>
                  {me?.subscription?.status || 'active'}
                </Badge>
              </td>
            </tr>
            <tr>
              <th>Provider</th>
              <td>{me?.subscription?.provider || 'noop'}</td>
            </tr>
            <tr>
              <th>Cancels at period end?</th>
              <td>{me?.subscription?.cancel_at_period_end ? 'Yes' : 'No'}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 24 }}>Available plans</h2>
      <div className="card-row">
        {plans.map((p) => (
          <div key={p.key} className="card" data-testid={`plan-card-${p.key}`}>
            <div className="card-title">{p.name}</div>
            <div style={{ fontSize: 24, fontWeight: 700, margin: '8px 0' }}>
              {p.price_cents === 0 ? 'Free' : `$${(p.price_cents / 100).toFixed(0)}`}
              {p.price_cents > 0 && <span style={{ fontSize: 13, opacity: 0.6 }}> / {p.interval}</span>}
            </div>
            <div className="muted" style={{ fontSize: 12 }}>{p.tier.toUpperCase()}</div>
            <ul style={{ margin: '12px 0', paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
              {Object.entries(p.features || {})
                .filter(([, on]) => on)
                .slice(0, 8)
                .map(([k]) => (
                  <li key={k}>{k.replace(/_/g, ' ')}</li>
                ))}
            </ul>
            {me?.subscription?.plan_key === p.key ? (
              <Badge kind="ok">CURRENT</Badge>
            ) : (
              <button
                className="btn"
                data-testid={`select-${p.key}`}
                disabled={busy === p.key}
                onClick={() => select(p.key)}
              >
                {busy === p.key ? 'Working…' : 'Select'}
              </button>
            )}
          </div>
        ))}
      </div>

      <h2 style={{ marginTop: 24 }}>Your features</h2>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Feature</th><th>Enabled</th></tr></thead>
          <tbody>
            {me && Object.entries(me.features).map(([k, on]) => (
              <tr key={k}>
                <td className="mono">{k}</td>
                <td>{on ? <Badge kind="ok">yes</Badge> : <Badge kind="err">no</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
