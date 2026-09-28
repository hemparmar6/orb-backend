import { ReactNode, useEffect, useState } from 'react';

export function Card({ title, value, sub, testId }: { title: string; value: ReactNode; sub?: ReactNode; testId?: string }) {
  return (
    <div className="card" data-testid={testId}>
      <div className="card-title">{title}</div>
      <div className="card-value">{value}</div>
      {sub && <div className="card-sub">{sub}</div>}
    </div>
  );
}

export function Badge({ kind = 'info', children, testId }: { kind?: 'ok' | 'err' | 'warn' | 'info' | 'purple'; children: ReactNode; testId?: string }) {
  return <span className={`badge ${kind}`} data-testid={testId}>{children}</span>;
}

export function statusBadge(status?: string) {
  if (!status) return <Badge>–</Badge>;
  const s = String(status).toLowerCase();
  if (['ok', 'running', 'complete', 'filled', 'active', 'true', 'live'].includes(s)) return <Badge kind="ok">{status}</Badge>;
  if (['error', 'failed', 'rejected', 'cancelled', 'canceled', 'stopped', 'false', 'inactive'].includes(s)) return <Badge kind="err">{status}</Badge>;
  if (['pending', 'queued', 'submitted', 'partially_filled', 'partial', 'starting'].includes(s)) return <Badge kind="warn">{status}</Badge>;
  if (['paper'].includes(s)) return <Badge kind="purple">{status}</Badge>;
  return <Badge>{status}</Badge>;
}

export function Pagination({ page, pageSize, total, onPage, testId }: { page: number; pageSize: number; total: number; onPage: (p: number) => void; testId?: string }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pagination" data-testid={testId}>
      <span className="info">{total.toLocaleString()} rows · page {page}/{pages}</span>
      <button className="btn btn-sm" disabled={page <= 1} onClick={() => onPage(1)} data-testid="page-first">« first</button>
      <button className="btn btn-sm" disabled={page <= 1} onClick={() => onPage(page - 1)} data-testid="page-prev">‹ prev</button>
      <button className="btn btn-sm" disabled={page >= pages} onClick={() => onPage(page + 1)} data-testid="page-next">next ›</button>
      <button className="btn btn-sm" disabled={page >= pages} onClick={() => onPage(pages)} data-testid="page-last">last »</button>
    </div>
  );
}

export function fmtDate(d?: string | null): string {
  if (!d) return '—';
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return String(d);
  return dt.toISOString().replace('T', ' ').slice(0, 19);
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function Toast({ msg, kind }: { msg: string; kind?: 'ok' | 'err' }) {
  const [show, setShow] = useState(true);
  useEffect(() => { const t = setTimeout(() => setShow(false), 3500); return () => clearTimeout(t); }, []);
  if (!show) return null;
  return <div className={`toast ${kind || ''}`} data-testid="toast">{msg}</div>;
}

export function Modal({ title, onClose, children, testId }: { title: string; onClose: () => void; children: ReactNode; testId?: string }) {
  return (
    <div className="modal-back" onClick={onClose} data-testid={testId}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {children}
        <div style={{ marginTop: 14, textAlign: 'right' }}>
          <button className="btn" onClick={onClose} data-testid="modal-close">Close</button>
        </div>
      </div>
    </div>
  );
}
