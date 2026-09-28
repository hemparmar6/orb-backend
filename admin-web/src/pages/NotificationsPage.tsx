import { useEffect, useState } from 'react';
import { api } from '../api';
import { Badge, Pagination, fmtDate } from '../ui';

export default function NotificationsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(25);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .listNotifications({ page, page_size: pageSize })
      .then((r) => {
        setItems(r.items || []);
        setTotal(r.total || 0);
      })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [page]);

  const sevKind = (s: string) =>
    s === 'error' || s === 'critical' ? 'err' :
    s === 'warning' ? 'warn' : 'info';

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div className="muted">{total} notification(s) in total</div>
        <button className="btn btn-sm" disabled={loading} onClick={load} data-testid="reload-notifications">
          {loading ? 'Loading…' : 'Reload'}
        </button>
      </div>
      {err ? <div className="empty">Error: {err}</div> : null}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Created</th><th>Event</th><th>Severity</th><th>Title</th>
              <th>Channels</th><th>Attempts</th><th>Read?</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={7} className="empty">No notifications</td></tr>
            ) : items.map((n) => (
              <tr key={n.id}>
                <td className="mono">{fmtDate(n.created_at)}</td>
                <td>{n.event}</td>
                <td><Badge kind={sevKind(n.severity) as any}>{n.severity}</Badge></td>
                <td>{n.title}<div className="muted" style={{fontSize: 12}}>{n.body}</div></td>
                <td>
                  {n.channel_status && Object.entries(n.channel_status).map(([k, v]: any) => (
                    <div key={k} className="muted" style={{ fontSize: 12 }}>
                      <b>{k}:</b> {String(v).slice(0, 40)}
                    </div>
                  ))}
                </td>
                <td>{n.delivery_attempts}</td>
                <td>{n.read_at ? <Badge kind="ok">yes</Badge> : <Badge kind="warn">no</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={total} onPage={setPage} />
    </div>
  );
}
