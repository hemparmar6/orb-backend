import { useState } from 'react';
import { api } from '../api';
import { Card } from '../ui';

type MI = {
  symbol: string; timeframe: string; regime: string; trend_strength: number;
  volatility_regime: string; liquidity: string; gap_behaviour: string | null;
  generated_at: string;
};

export default function MarketIntelligencePage() {
  const [symbol, setSymbol] = useState('SPY');
  const [data, setData] = useState<MI | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setErr(null);
    setBusy(true);
    try {
      setData(await api.aiMarketIntel(symbol));
    } catch (e: any) {
      setData(null);
      setErr(`No snapshot for ${symbol}.`);
    } finally {
      setBusy(false);
    }
  };

  const capture = async () => {
    setErr(null);
    setBusy(true);
    try {
      setData(await api.aiSnapshotMarketIntel(symbol));
    } catch (e: any) {
      setErr(`Cannot compute snapshot: ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <h1>Market intelligence</h1>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          className="input"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          data-testid="mi-symbol-input"
        />
        <button className="btn" onClick={load} disabled={busy}>Load</button>
        <button className="btn" onClick={capture} disabled={busy}>Snapshot now</button>
      </div>
      {err && <div style={{ color: 'crimson' }}>{err}</div>}
      {data && (
        <div className="grid" data-testid="mi-grid">
          <Card title="Regime" value={data.regime} />
          <Card title="Trend strength" value={data.trend_strength.toFixed(1)} />
          <Card title="Volatility" value={data.volatility_regime} />
          <Card title="Liquidity" value={data.liquidity} />
          <Card title="Gap" value={data.gap_behaviour ?? '—'} />
          <Card title="Timeframe" value={data.timeframe} />
        </div>
      )}
    </div>
  );
}
