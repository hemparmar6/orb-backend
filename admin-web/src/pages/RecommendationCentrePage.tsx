import { useEffect, useState } from 'react';
import { api } from '../api';
import { Badge } from '../ui';

type Rec = {
  id: string; type: string; action: string; rationale: string | null;
  confidence: number; priority: 'low' | 'medium' | 'high';
  status: string; created_at: string;
};

const priorityKind = (p: string) =>
  p === 'high' ? 'err' : p === 'medium' ? 'warn' : 'info';

export default function RecommendationCentrePage() {
  const [recs, setRecs] = useState<Rec[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.aiListRecommendations()
      .then(setRecs)
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <h1>Recommendation centre</h1>
      <p style={{ color: '#888', fontSize: 13 }}>
        Advisory only — recommendations are surfaced to end users and never executed
        automatically. Users acknowledge or dismiss them from the mobile app.
      </p>
      {loading ? <div>Loading…</div> : (
        <div className="grid" data-testid="ai-recs-grid">
          {recs.map((r) => (
            <div key={r.id} className="card">
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Badge kind={priorityKind(r.priority) as any}>{r.priority.toUpperCase()}</Badge>
                <span style={{ color: '#888', fontSize: 12, textTransform: 'capitalize' }}>
                  {r.type.replace(/_/g, ' ')}
                </span>
              </div>
              <div style={{ marginTop: 8, fontWeight: 600 }}>{r.action}</div>
              {r.rationale && <div style={{ fontSize: 13, color: '#bbb', marginTop: 4 }}>{r.rationale}</div>}
              <div style={{ marginTop: 6, fontSize: 12, color: '#888' }}>
                {(r.confidence * 100).toFixed(0)}% confidence · {r.status}
              </div>
            </div>
          ))}
          {!recs.length && <div style={{ color: '#888' }}>No recommendations pending.</div>}
        </div>
      )}
    </div>
  );
}
