// Creators admin — review creator applications, approve/reject strategies,
// and associate EXISTING coupons with approved creator strategies.
// Coupon management itself stays on the existing coupon system; this page
// only links coupons by selection.
import { useEffect, useState } from 'react';
import { apiFetch } from '../api';
import { Badge } from '../ui';

type Creator = {
  id: string; user_id: string; user_email: string | null; display_name: string;
  bio: string | null; status: string; rejected_reason: string | null;
  redemption_count: number; created_at: string;
};

type CreatorStrategy = {
  id: string; creator_id: string; creator_display_name: string | null; name: string;
  market: string; timeframe: string; trading_style: string; status: string;
  assigned_coupon: { code: string; status: string } | null; created_at: string;
};

type Coupon = { id: string; code: string; status: string; redemptions_count: number };

function kindFor(status: string): 'ok' | 'warn' | 'err' | 'info' {
  if (status === 'approved' || status === 'active') return 'ok';
  if (status === 'pending') return 'warn';
  if (status === 'rejected' || status === 'suspended') return 'err';
  return 'info';
}

export default function CreatorsPage() {
  const [creators, setCreators] = useState<Creator[]>([]);
  const [strategies, setStrategies] = useState<CreatorStrategy[]>([]);
  const [coupons, setCoupons] = useState<Coupon[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [assignSel, setAssignSel] = useState<Record<string, string>>({});

  const load = () => {
    Promise.all([
      apiFetch<Creator[]>('/creators/admin/applications'),
      apiFetch<CreatorStrategy[]>('/creators/admin/strategies'),
      apiFetch<Coupon[]>('/coupons/admin'),
    ]).then(([c, s, cp]) => { setCreators(c); setStrategies(s); setCoupons(cp); })
      .catch((e) => setErr(String(e?.message || e)));
  };
  useEffect(load, []);

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setErr(null);
    try { await fn(); load(); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(null); }
  };

  const creatorAction = (id: string, action: 'approve' | 'reject' | 'suspend') =>
    act(`creator-${action}-${id}`, () =>
      apiFetch(`/creators/admin/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify(action === 'reject' ? { reason: 'Rejected by admin' } : {}),
      }),
    );

  const strategyAction = (id: string, action: 'approve' | 'reject') =>
    act(`strategy-${action}-${id}`, () =>
      apiFetch(`/creators/admin/strategies/${id}/${action}`, {
        method: 'POST',
        body: JSON.stringify(action === 'reject' ? { reason: 'Rejected by admin' } : {}),
      }),
    );

  const assign = (strategyId: string) => {
    const couponId = assignSel[strategyId];
    if (!couponId) { setErr('Select an existing coupon first'); return; }
    return act(`assign-${strategyId}`, () =>
      apiFetch(`/creators/admin/strategies/${strategyId}/assign-coupon`, {
        method: 'POST', body: JSON.stringify({ coupon_id: couponId }),
      }),
    );
  };

  return (
    <div data-testid="creators-page">
      <h2>Creator applications</h2>
      {err ? <div className="empty" data-testid="creators-error">Error: {err}</div> : null}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Creator</th><th>Email</th><th>Status</th><th>Redemptions</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {creators.length === 0 ? (
              <tr><td colSpan={5} className="muted">No creator applications yet.</td></tr>
            ) : creators.map((c) => (
              <tr key={c.id} data-testid={`creator-row-${c.id}`}>
                <td>{c.display_name}</td>
                <td className="mono">{c.user_email}</td>
                <td><Badge kind={kindFor(c.status)}>{c.status}</Badge></td>
                <td className="mono" data-testid={`creator-redemptions-${c.id}`}>{c.redemption_count}</td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button
                      data-testid={`approve-creator-${c.id}`}
                      disabled={busy !== null || c.status === 'approved'}
                      onClick={() => creatorAction(c.id, 'approve')}
                    >Approve</button>
                    <button
                      data-testid={`reject-creator-${c.id}`}
                      disabled={busy !== null || c.status === 'rejected'}
                      onClick={() => creatorAction(c.id, 'reject')}
                    >Reject</button>
                    <button
                      data-testid={`suspend-creator-${c.id}`}
                      disabled={busy !== null || c.status !== 'approved'}
                      onClick={() => creatorAction(c.id, 'suspend')}
                    >Suspend</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 24 }}>Creator strategies</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Strategy</th><th>Creator</th><th>Market</th><th>Status</th>
              <th>Assigned coupon</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {strategies.length === 0 ? (
              <tr><td colSpan={6} className="muted">No creator strategies yet.</td></tr>
            ) : strategies.map((st) => (
              <tr key={st.id} data-testid={`strategy-row-${st.id}`}>
                <td>{st.name}<div className="muted" style={{ fontSize: 12 }}>{st.timeframe}</div></td>
                <td>{st.creator_display_name}</td>
                <td>{st.market}</td>
                <td><Badge kind={kindFor(st.status)}>{st.status}</Badge></td>
                <td data-testid={`strategy-coupon-${st.id}`}>
                  {st.assigned_coupon ? (
                    <span className="mono">{st.assigned_coupon.code} ({st.assigned_coupon.status})</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <button
                      data-testid={`approve-strategy-${st.id}`}
                      disabled={busy !== null || st.status === 'approved'}
                      onClick={() => strategyAction(st.id, 'approve')}
                    >Approve</button>
                    <button
                      data-testid={`reject-strategy-${st.id}`}
                      disabled={busy !== null || st.status === 'rejected'}
                      onClick={() => strategyAction(st.id, 'reject')}
                    >Reject</button>
                    {st.status === 'approved' ? (
                      <>
                        <select
                          data-testid={`assign-select-${st.id}`}
                          value={assignSel[st.id] || ''}
                          onChange={(e) => setAssignSel({ ...assignSel, [st.id]: e.target.value })}
                        >
                          <option value="">Select existing coupon…</option>
                          {coupons.map((cp) => (
                            <option key={cp.id} value={cp.id}>
                              {cp.code} ({cp.status})
                            </option>
                          ))}
                        </select>
                        <button
                          data-testid={`assign-coupon-${st.id}`}
                          disabled={busy !== null || !assignSel[st.id]}
                          onClick={() => assign(st.id)}
                        >Assign</button>
                      </>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
