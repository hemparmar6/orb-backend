import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card, Toast, fmtDate } from '../ui';
import { useTradingMode } from '../TradingModeContext';

const CONFIRMATION = 'I UNDERSTAND THIS CAN PLACE REAL-MONEY ORDERS.';

type AutoRevertCfg = {
  enabled: boolean;
  at_time: string;
  timezone: string;
};

export default function TradingModePage() {
  const { snapshot: shared, refresh: refreshShared } = useTradingMode();
  const [state, setState] = useState<any>(shared);
  const [reason, setReason] = useState('');
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);
  const [ack, setAck] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [toast, setToast] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRevert, setAutoRevert] = useState<AutoRevertCfg>({
    enabled: false, at_time: '15:30', timezone: 'Asia/Kolkata',
  });
  const [autoBusy, setAutoBusy] = useState(false);

  async function load() {
    try {
      const [snap, ar] = await Promise.all([
        api.tradingModeGet(),
        api.tradingModeAutoRevertGet(),
      ]);
      setState(snap);
      setAutoRevert({
        enabled: !!ar.enabled,
        at_time: ar.at_time || '15:30',
        timezone: ar.timezone || 'Asia/Kolkata',
      });
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }
  useEffect(() => { void load(); }, []);
  // Keep the local card in sync with the shared context so a mode change
  // triggered elsewhere is reflected here immediately.
  useEffect(() => { if (shared) setState((s: any) => s ?? shared); }, [shared]);

  async function change(mode: 'paper' | 'live') {
    try {
      const next = await api.tradingModeSet({
        mode,
        reason: reason.trim() || undefined,
        confirmation: mode === 'live' ? phrase : undefined,
      });
      setState(next); setShowLiveConfirm(false); setAck(false); setPhrase('');
      setToast({ msg: `${mode.toUpperCase()} trading enabled`, kind: 'ok' });
      await refreshShared();
    } catch (e: any) {
      const code: string | undefined = e?.code;
      const detail = e?.detail?.detail?.details || e?.detail?.details;
      let msg = e?.message || 'Trading mode change failed';
      if (code === 'live_confirmation_required') {
        msg = 'LIVE requires the exact confirmation phrase.';
      } else if (code === 'live_cooldown_active') {
        msg = 'LIVE is armed but still in cooldown — real-money orders are blocked until it expires.';
      } else if (code === 'live_exposure_blocks_paper_mode') {
        const counts = detail || {};
        msg = `Cannot switch to PAPER while LIVE exposure remains — sessions: ${counts.active_live_sessions ?? '?'}, positions: ${counts.open_live_positions ?? '?'}, orders: ${counts.open_live_orders ?? '?'}.`;
      } else if (code === 'invalid_trading_mode') {
        msg = 'Invalid trading mode value.';
      } else if (e?.status === 401 || e?.status === 403) {
        msg = 'Admin authorization required to change trading mode.';
      }
      setToast({ msg, kind: 'err' });
      await load();
      await refreshShared();
    }
  }

  async function saveAutoRevert(next: AutoRevertCfg) {
    setAutoBusy(true);
    try {
      const saved = await api.tradingModeAutoRevertSet(next);
      setAutoRevert({
        enabled: !!saved.enabled,
        at_time: saved.at_time || next.at_time,
        timezone: saved.timezone || next.timezone,
      });
      setToast({ msg: 'Auto-revert saved', kind: 'ok' });
    } catch (e: any) {
      const code = e?.code;
      const msg =
        code === 'invalid_auto_revert_config'
          ? 'Auto-revert config is invalid — check time (HH:MM) and timezone.'
          : e?.message || 'Save failed';
      setToast({ msg, kind: 'err' });
    } finally {
      setAutoBusy(false);
    }
  }

  if (error) return <div className="empty" data-testid="trading-mode-error">Error: {error}</div>;
  if (!state) return <div className="empty" data-testid="trading-mode-loading">Loading…</div>;

  const isLive = state.mode === 'live';
  const cooldownActive = !!state.cooldown_active;
  const liveReady = state.live_gate !== 'blocked';

  return (
    <div data-testid="trading-mode-page">
      <div className={`card ${isLive ? 'neg' : 'pos'}`} data-testid="trading-mode-status">
        <div className="card-title" data-testid="trading-mode-status-label">TRADING MODE · SERVER ENFORCED</div>
        <div className="card-value" data-testid="trading-mode-current">
          {isLive
            ? cooldownActive
              ? 'LIVE TRADING · COOLDOWN — Real-money execution blocked until cooldown expires'
              : 'LIVE TRADING — Real-money execution enabled'
            : 'PAPER TRADING — No real-money orders'}
        </div>
        <div className="card-sub" data-testid="trading-mode-last-change">
          Last changed {fmtDate(state.changed_at)} · operator {state.changed_by || 'system default'}
          {state.armed_at ? ` · armed ${fmtDate(state.armed_at)}` : ''}
          {cooldownActive ? ` · cooldown expires ${fmtDate(state.cooldown_expires_at)}` : ''}
        </div>
      </div>

      <div className="card-row" data-testid="trading-mode-exposure-summary">
        <Card title="Active LIVE sessions" value={state.active_live_sessions} testId="trading-mode-live-sessions" />
        <Card title="Open LIVE positions" value={state.open_live_positions} testId="trading-mode-live-positions" />
        <Card title="Open LIVE orders" value={state.open_live_orders} testId="trading-mode-live-orders" />
        <Card title="Kill switch" value={state.kill_switch_active ? 'ACTIVE' : 'off'} testId="trading-mode-kill-switch" />
      </div>

      <h2 data-testid="trading-mode-controls-heading">Operator control</h2>
      <p className="dim" data-testid="trading-mode-safety-note">
        LIVE is an additional gate. Broker authentication, real market data, risk controls, reconciliation, OCO protection, the kill switch and the post-arm cooldown still apply.
      </p>
      <div className="toolbar" data-testid="trading-mode-controls">
        <input data-testid="trading-mode-reason" placeholder="Reason (audited)" value={reason} onChange={e => setReason(e.target.value)} />
        <button data-testid="trading-mode-paper-button" disabled={!isLive} onClick={() => change('paper')}>Switch to PAPER</button>
        <button data-testid="trading-mode-live-button" className="btn-danger" disabled={isLive || !liveReady} onClick={() => setShowLiveConfirm(true)}>Arm LIVE</button>
      </div>

      {showLiveConfirm && (
        <div className="modal-back" data-testid="trading-mode-live-confirmation">
          <div className="modal-card">
            <h3 data-testid="trading-mode-live-confirmation-heading">Switch to LIVE TRADING</h3>
            <p data-testid="trading-mode-live-warning">This can place real-money orders once the cooldown expires. Existing live safety gates remain mandatory.</p>
            <label data-testid="trading-mode-live-ack-label">
              <input data-testid="trading-mode-live-ack" type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />
              I understand this can place real-money orders.
            </label>
            <input data-testid="trading-mode-live-phrase" placeholder={CONFIRMATION} value={phrase} onChange={e => setPhrase(e.target.value)} />
            <button data-testid="trading-mode-live-confirm-button" disabled={!ack || phrase.trim().toUpperCase() !== CONFIRMATION} onClick={() => change('live')}>Confirm LIVE</button>
            <button data-testid="trading-mode-live-cancel-button" onClick={() => setShowLiveConfirm(false)}>Cancel</button>
          </div>
        </div>
      )}

      <h2 data-testid="auto-revert-heading">Automatic nightly PAPER revert</h2>
      <p className="dim" data-testid="auto-revert-note">
        Optional. When enabled, the scheduler attempts to flip LIVE → PAPER at the configured local time. It is refused if any LIVE session / position / order still exists.
      </p>
      <div className="toolbar" data-testid="auto-revert-controls">
        <label>
          <input
            type="checkbox"
            data-testid="auto-revert-enabled"
            checked={autoRevert.enabled}
            onChange={e => setAutoRevert({ ...autoRevert, enabled: e.target.checked })}
          />
          Enable auto-revert
        </label>
        <input
          data-testid="auto-revert-time"
          placeholder="HH:MM"
          value={autoRevert.at_time}
          onChange={e => setAutoRevert({ ...autoRevert, at_time: e.target.value })}
        />
        <input
          data-testid="auto-revert-tz"
          placeholder="Asia/Kolkata"
          value={autoRevert.timezone}
          onChange={e => setAutoRevert({ ...autoRevert, timezone: e.target.value })}
        />
        <button
          data-testid="auto-revert-save"
          disabled={autoBusy}
          onClick={() => saveAutoRevert(autoRevert)}
        >
          Save
        </button>
      </div>
      {state.auto_revert?.last_run_at && (
        <p className="dim" data-testid="auto-revert-last-run">
          Last tick: {fmtDate(state.auto_revert.last_run_at)} · result {state.auto_revert.last_run_result}
        </p>
      )}

      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
