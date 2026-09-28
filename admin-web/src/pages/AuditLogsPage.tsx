import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Modal, Pagination, Toast, fmtDate, statusBadge } from '../ui';

export default function AuditLogsPage() {
  const [actorUserId, setActor] = useState('');
  const [action, setAction] = useState('');
  const [targetType, setTargetType] = useState('');
  const [targetId, setTargetId] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [inspect, setInspect] = useState<any>(null);
  const [toast, setToast] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const p: any = { page, page_size: pageSize };
      if (actorUserId) p.actor_user_id = actorUserId;
      if (action) p.action = action;
      if (targetType) p.target_type = targetType;
      if (targetId) p.target_id = targetId;
      setData(await api.listAuditLogs(p));
    } catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }, [actorUserId, action, targetType, targetId, page]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="toolbar" data-testid="audit-toolbar">
        <input data-testid="audit-actor-filter" placeholder="actor user_id" value={actorUserId} onChange={(e) => { setPage(1); setActor(e.target.value); }} />
        <input data-testid="audit-action-filter" placeholder="action (e.g. user.update)" value={action} onChange={(e) => { setPage(1); setAction(e.target.value); }} />
        <input data-testid="audit-target-type-filter" placeholder="target_type" value={targetType} onChange={(e) => { setPage(1); setTargetType(e.target.value); }} />
        <input data-testid="audit-target-id-filter" placeholder="target_id" value={targetId} onChange={(e) => { setPage(1); setTargetId(e.target.value); }} />
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="audit-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="audit-table">
        <table className="table">
          <thead><tr>
            <th>TIME</th><th>ACTOR</th><th>ACTION</th><th>TARGET</th><th>IP</th><th></th>
          </tr></thead>
          <tbody>
            {data.items.length === 0 ? (
              <tr><td colSpan={6} className="table-empty">No audit entries yet.</td></tr>
            ) : data.items.map((a: any) => (
              <tr key={a.id}>
                <td className="mono">{fmtDate(a.created_at)}</td>
                <td className="mono">{(a.actor_user_id || '').slice(0, 8) || '—'}</td>
                <td>{statusBadge(a.action)}</td>
                <td className="mono">{a.target_type}:{(a.target_id || '').slice(0, 8) || '—'}</td>
                <td className="mono muted">{a.ip_address || '—'}</td>
                <td><button className="btn btn-sm" onClick={() => setInspect(a)} data-testid={`audit-inspect-${a.id}`}>Details</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="audit-pagination" />

      {inspect && (
        <Modal title={`Audit · ${inspect.action}`} onClose={() => setInspect(null)} testId="audit-modal">
          <div className="kv">
            <div className="k">ID</div><div className="v">{inspect.id}</div>
            <div className="k">When</div><div className="v">{fmtDate(inspect.created_at)}</div>
            <div className="k">Actor</div><div className="v">{inspect.actor_user_id || '—'}</div>
            <div className="k">Target</div><div className="v">{inspect.target_type} · {inspect.target_id || '—'}</div>
            <div className="k">IP</div><div className="v">{inspect.ip_address || '—'}</div>
          </div>
          <div className="section-title">Details</div>
          <pre className="json">{JSON.stringify(inspect.details || {}, null, 2)}</pre>
        </Modal>
      )}
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
