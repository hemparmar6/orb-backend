import { useEffect, useState } from 'react';
import { api, tokenStore } from '../api';
import { Badge, fmtDate } from '../ui';

const TYPES = [
  'daily', 'weekly', 'monthly', 'portfolio', 'trade_history',
  'risk', 'strategy_performance', 'broker_activity', 'pnl',
];

export default function ReportsPage() {
  const [history, setHistory] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    api.reportsHistory().then((r) => setHistory(r.items || [])).catch((e) => setErr(String(e?.message || e)));
  };
  useEffect(load, []);

  const generate = async (type: string, format: 'pdf' | 'csv') => {
    setBusy(`${type}-${format}`);
    try {
      const res = await fetch(api.reportGenerateUrl(type, format), {
        headers: { Authorization: `Bearer ${tokenStore.get()}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `orb_ai_${type}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      load();
    } catch (e: any) {
      alert(`Report generation failed: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <div className="card-row">
        {TYPES.map((t) => (
          <div key={t} className="card" data-testid={`report-card-${t}`}>
            <div className="card-title">{t.replace(/_/g, ' ').toUpperCase()}</div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button
                className="btn btn-sm"
                onClick={() => generate(t, 'pdf')}
                disabled={busy === `${t}-pdf`}
                data-testid={`gen-pdf-${t}`}
              >
                {busy === `${t}-pdf` ? '…' : 'PDF'}
              </button>
              <button
                className="btn btn-sm"
                onClick={() => generate(t, 'csv')}
                disabled={busy === `${t}-csv`}
                data-testid={`gen-csv-${t}`}
              >
                {busy === `${t}-csv` ? '…' : 'CSV'}
              </button>
            </div>
          </div>
        ))}
      </div>

      <h2 style={{ marginTop: 24 }}>Report History</h2>
      {err ? <div className="empty">Error: {err}</div> : null}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Generated</th><th>Type</th><th>Format</th><th>Status</th><th>Rows</th><th>Size</th><th>Filename</th></tr>
          </thead>
          <tbody>
            {history.length === 0 ? (
              <tr><td colSpan={7} className="empty">No reports generated yet</td></tr>
            ) : history.map((h) => (
              <tr key={h.id}>
                <td className="mono">{fmtDate(h.created_at)}</td>
                <td>{h.report_type}</td>
                <td>{h.report_format}</td>
                <td><Badge kind={h.status === 'generated' ? 'ok' : h.status === 'failed' ? 'err' : 'warn'}>{h.status}</Badge></td>
                <td>{h.row_count}</td>
                <td>{Math.round((h.byte_size || 0) / 1024)} KB</td>
                <td className="mono" style={{ fontSize: 12 }}>{h.filename}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
