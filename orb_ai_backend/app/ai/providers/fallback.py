"""Deterministic rule-based provider — the always-available fallback."""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List

from app.ai.providers.base import AIProvider, AIResponse


class RuleBasedProvider(AIProvider):
    name = "rule_based"

    async def generate(
        self, prompt_name: str, prompt_version: str, payload: Dict[str, Any]
    ) -> AIResponse:
        handler = getattr(self, f"_p_{prompt_name}", self._p_default)
        content = handler(payload)
        return AIResponse(
            content=content,
            text=content.get("summary", ""),
            provider=self.name,
            model="rule_based_v1",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )

    # --- prompt handlers ---

    def _p_trade_review(self, p: Dict[str, Any]) -> Dict[str, Any]:
        t = p.get("trade", {})
        pnl = float(t.get("pnl") or 0.0)
        entry = float(t.get("entry_price") or 0.0)
        exit_ = float(t.get("exit_price") or 0.0)
        qty = float(t.get("quantity") or 0.0)
        side = str(t.get("side") or "buy").lower()
        stop_loss = t.get("stop_loss")
        take_profit = t.get("take_profit")

        # Compute a naive R multiple from stop distance if available.
        r_multiple = 0.0
        if entry and stop_loss:
            risk_per_unit = abs(entry - float(stop_loss)) or 1e-9
            move = (exit_ - entry) if side == "buy" else (entry - exit_)
            r_multiple = move / risk_per_unit

        entry_quality = _clip(60 + r_multiple * 8, 0, 100)
        exit_quality = _clip(70 if pnl >= 0 else 45, 0, 100)
        risk_score = _clip(85 if stop_loss else 45, 0, 100)
        compliance = 95 if (stop_loss and take_profit) else 60

        flags: List[str] = []
        if not stop_loss:
            flags.append("no_stop_loss")
        if pnl < 0 and abs(r_multiple) > 2:
            flags.append("outsized_loss")
        if pnl < 0 and qty and entry and abs(qty * entry) > 100_000:
            flags.append("oversized_position")

        overall = round(mean([entry_quality, exit_quality, risk_score, compliance]), 1)
        return {
            "trade_quality_score": overall,
            "entry_quality": entry_quality,
            "exit_quality": exit_quality,
            "risk_management_score": risk_score,
            "rule_compliance": compliance,
            "emotional_flags": flags,
            "improvements": _suggest(entry_quality, exit_quality, risk_score, bool(stop_loss)),
            "summary": f"Trade scored {overall}/100 (rule-based).",
        }

    def _p_recommendation(self, p: Dict[str, Any]) -> Dict[str, Any]:
        s = p.get("portfolio_stats", {})
        win_rate = float(s.get("win_rate", 0.5))
        dd = float(s.get("max_drawdown_pct", 0.0))
        pf = float(s.get("profit_factor", 0.0))
        recs: List[Dict[str, Any]] = []
        if dd > 15:
            recs.append({
                "type": "risk_reduction", "confidence": 0.9, "priority": "high",
                "action": "Reduce position size by 25% until drawdown recovers.",
                "rationale": f"Current max drawdown {dd:.1f}% exceeds the 15% threshold.",
            })
        if win_rate < 0.4:
            recs.append({
                "type": "strategy", "confidence": 0.7, "priority": "medium",
                "action": "Review entry criteria — win rate below 40%.",
                "rationale": f"Win rate {win_rate*100:.1f}% suggests filter issues.",
            })
        if pf and pf < 1.2:
            recs.append({
                "type": "risk_reduction", "confidence": 0.65, "priority": "medium",
                "action": "Tighten stop losses or scale down R per trade.",
                "rationale": f"Profit factor {pf:.2f} leaves little margin for slippage.",
            })
        recs.append({
            "type": "position_sizing", "confidence": 0.6, "priority": "low",
            "action": "Risk ~1% of equity per trade based on recent volatility.",
            "rationale": "Standard fixed-fractional guidance.",
        })
        return {"recommendations": recs, "summary": f"{len(recs)} recommendations generated."}

    def _p_portfolio_insight(self, p: Dict[str, Any]) -> Dict[str, Any]:
        s = p.get("stats", {})
        return {
            "highlights": [
                f"Win rate: {round(float(s.get('win_rate', 0)) * 100, 1)}%",
                f"Profit factor: {round(float(s.get('profit_factor', 0)), 2)}",
                f"Max drawdown: {round(float(s.get('max_drawdown_pct', 0)), 2)}%",
            ],
            "alerts": [],
            "summary": "Portfolio performance snapshot (rule-based).",
        }

    def _p_strategy_review(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"summary": "Strategy review requires the LLM provider.",
                "strengths": [], "weaknesses": [], "parameter_hints": []}

    def _p_risk_analysis(self, p: Dict[str, Any]) -> Dict[str, Any]:
        s = p.get("stats", {})
        dd = float(s.get("max_drawdown_pct", 0.0))
        grade = "A" if dd < 5 else "B" if dd < 10 else "C" if dd < 20 else "D"
        return {
            "risk_grade": grade,
            "key_risks": ["Concentration" if dd > 15 else "Within tolerance"],
            "mitigations": ["Diversify across strategies", "Cap per-trade risk at 1R"],
            "summary": f"Rule-based risk grade {grade} (drawdown {dd:.1f}%).",
        }

    def _p_backtest_interp(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"verdict": "acceptable", "key_findings": [], "risks": [],
                "next_steps": [], "summary": "Backtest interpretation requires the LLM provider."}

    def _p_default(self, p: Dict[str, Any]) -> Dict[str, Any]:
        return {"summary": "AI is currently unavailable — rule-based response returned."}


def _clip(v: float, lo: float, hi: float) -> float:
    return round(max(lo, min(hi, v)), 1)


def _suggest(entry: float, exit_: float, risk: float, has_stop: bool) -> List[str]:
    out: List[str] = []
    if entry < 60:
        out.append("Wait for confirmation candle before entry.")
    if exit_ < 60:
        out.append("Trail stop once price reaches 1R in profit.")
    if not has_stop:
        out.append("Always set a stop loss before entering a trade.")
    return out
