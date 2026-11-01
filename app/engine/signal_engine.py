"""Deterministic Signal Engine for Forex H1 trade signal generation and validation.

Translates multi-agent recommendations and verified market data into fully validated,
strictly ordered BUY, SELL, or NO_TRADE signals with reference Entry, SL, TP, and R:R.
Enforces non-hallucinated prices, pip precision, transaction cost safety, and deduplication.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import (
    ForexPair,
    ForexSignal,
    MarketSnapshot,
    SignalAction,
    SignalStatus,
)

logger = logging.getLogger(__name__)


class SignalEngineConfig(BaseModel):
    """Configuration parameters for the Signal Engine."""

    min_risk_reward: float = Field(default=1.5, ge=1.0, description="Minimum acceptable Risk/Reward ratio")
    atr_sl_multiplier: float = Field(default=1.5, gt=0, description="Multiplier on ATR14 for Stop Loss distance")
    atr_tp_multiplier: float = Field(default=3.0, gt=0, description="Multiplier on ATR14 for Take Profit distance")
    min_sl_pips: float = Field(default=8.0, gt=0, description="Minimum allowable Stop Loss distance in pips")
    max_sl_pips: float = Field(default=80.0, gt=0, description="Maximum allowable Stop Loss distance in pips")
    max_spread_to_sl_ratio: float = Field(
        default=0.20,
        gt=0,
        le=0.5,
        description="Maximum ratio of spread pips to Stop Loss pips",
    )
    max_allowed_spread_pips: dict[str, float] = Field(
        default_factory=lambda: {
            "EUR_USD": 3.0,
            "GBP_USD": 3.5,
            "USD_JPY": 4.0,
        }
    )


class SignalEngine:
    """Processes multi-agent decisions and verified market data into deterministic Forex signals."""

    def __init__(self, config: SignalEngineConfig | None = None):
        self.config = config or SignalEngineConfig()
        self._processed_candles: set[tuple[str, str]] = set()

    def _create_no_trade_signal(
        self,
        pair: ForexPair,
        snapshot: MarketSnapshot,
        reason: str,
        model_name: str = "TradingAgents-Gemini",
        status: SignalStatus = SignalStatus.REJECTED,
    ) -> ForexSignal:
        """Create and record a structured NO_TRADE signal."""
        logger.info("SignalEngine: NO_TRADE for %s at %s. Reason: %s", pair.value, snapshot.candle_timestamp, reason)
        return ForexSignal(
            symbol=pair,
            timeframe=snapshot.timeframe,
            action=SignalAction.NO_TRADE,
            entry=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            reason=reason,
            candle_timestamp=snapshot.candle_timestamp,
            model_name=model_name,
            status=status,
            sl_pips=None,
            tp_pips=None,
            spread_pips=snapshot.spread_pips,
        )

    def generate_signal(
        self,
        analysis_result: dict[str, Any],
        snapshot: MarketSnapshot,
        model_name: str = "TradingAgents-Gemini",
        allow_duplicate: bool = False,
    ) -> ForexSignal:
        """Evaluate multi-agent reasoning and generate a verified, deterministic Forex signal."""
        pair = snapshot.symbol
        candle_iso = snapshot.candle_timestamp.isoformat()
        dedup_key = (pair.value, candle_iso)

        # 1. Deduplication Check
        if not allow_duplicate and dedup_key in self._processed_candles:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason=f"Duplicate signal rejected: candle {candle_iso} already processed.",
                model_name=model_name,
            )

        # Mark candle as evaluated
        self._processed_candles.add(dedup_key)

        # 2. Check for Market Staleness or Closed Session
        if snapshot.is_stale:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason=f"Market data stale or closed: {snapshot.stale_reason or 'Stale candle'}",
                model_name=model_name,
            )

        # 3. Check Multi-Agent Recommendation
        action_str = str(analysis_result.get("action", "NO_TRADE")).upper()
        if action_str not in ("BUY", "SELL"):
            reason = analysis_result.get("reason") or analysis_result.get("final_trade_decision") or "Neutral / Hold decision by multi-agent consensus."
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason=f"Multi-agent consensus was NO_TRADE ({reason[:150]})",
                model_name=model_name,
            )

        # 4. Indicator Validation
        atr = snapshot.indicators.atr14
        if atr is None or atr <= 0:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason="Rejected: ATR14 indicator is missing or non-positive.",
                model_name=model_name,
            )

        # 5. Spread & Transaction Cost Validation
        spread_pips = snapshot.spread_pips or 0.0
        max_spread = self.config.max_allowed_spread_pips.get(pair.value, 4.0)
        if spread_pips > max_spread:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason=f"Rejected: Spread {spread_pips:.1f} pips exceeds maximum threshold {max_spread:.1f} pips.",
                model_name=model_name,
            )

        # 6. Calculate Entry Price
        # Ground Entry strictly in verified market data
        entry = pair.round_price(snapshot.close)

        # 7. Calculate Reference Stop Loss & Take Profit using ATR
        raw_sl_pips = pair.round_pips(pair.price_to_pips(atr * self.config.atr_sl_multiplier))
        # Clamp SL distance within risk limits
        sl_pips = max(self.config.min_sl_pips, min(self.config.max_sl_pips, raw_sl_pips))
        sl_distance = sl_pips * pair.pip_size

        # Check transaction cost impact (spread vs SL)
        if sl_pips > 0 and (spread_pips / sl_pips) > self.config.max_spread_to_sl_ratio:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason=(
                    f"Rejected: Spread {spread_pips:.1f} pips is {spread_pips / sl_pips:.1%} of Stop Loss ({sl_pips:.1f} pips), "
                    f"exceeding {self.config.max_spread_to_sl_ratio:.0%} limit."
                ),
                model_name=model_name,
            )

        # Calculate TP distance to ensure at least min_risk_reward
        target_rr = max(self.config.min_risk_reward, self.config.atr_tp_multiplier / self.config.atr_sl_multiplier)
        tp_pips = pair.round_pips(sl_pips * target_rr)
        tp_distance = tp_pips * pair.pip_size

        # 8. Compute Absolute Levels according to Direction
        if action_str == "BUY":
            action = SignalAction.BUY
            stop_loss = pair.round_price(entry - sl_distance)
            take_profit = pair.round_price(entry + tp_distance)
        else:  # SELL
            action = SignalAction.SELL
            stop_loss = pair.round_price(entry + sl_distance)
            take_profit = pair.round_price(entry - tp_distance)

        # 9. Verify SL/TP Ordering & Minimum R:R
        actual_sl_dist = abs(entry - stop_loss)
        actual_tp_dist = abs(take_profit - entry)

        if actual_sl_dist <= 0:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason="Rejected: Calculated Stop Loss is identical to Entry.",
                model_name=model_name,
            )

        computed_rr = round(actual_tp_dist / actual_sl_dist, 2)
        if computed_rr < self.config.min_risk_reward:
            return self._create_no_trade_signal(
                pair=pair,
                snapshot=snapshot,
                reason=f"Rejected: Computed R:R ({computed_rr}) is below required minimum ({self.config.min_risk_reward}).",
                model_name=model_name,
            )

        # 10. Extract Concise Reason from Agent Synthesis
        decision_text = analysis_result.get("final_trade_decision") or analysis_result.get("trader_investment_plan") or ""
        reason_summary = f"{action.value} signal confirmed by multi-agent consensus. "
        prefixes = ("**Executive Summary**:", "Executive Summary:", "**Investment Thesis**:", "Investment Thesis:")
        for line in decision_text.splitlines():
            line_str = line.strip()
            if any(line_str.startswith(p) for p in prefixes):
                reason_summary += line_str.split(":", 1)[1].strip() + " "
                break

        if len(reason_summary) <= 35:
            reason_summary += f"R:R={computed_rr:.1f}, SL={sl_pips:.1f}pips, TP={tp_pips:.1f}pips. Grounded in H1 ATR14."

        reason_summary = reason_summary.strip()

        signal = ForexSignal(
            symbol=pair,
            timeframe=snapshot.timeframe,
            action=action,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=computed_rr,
            reason=reason_summary,
            candle_timestamp=snapshot.candle_timestamp,
            model_name=model_name,
            status=SignalStatus.PENDING,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            spread_pips=spread_pips,
        )

        logger.info(
            "SignalEngine: APPROVED %s for %s at %s. Entry=%.5f, SL=%.5f, TP=%.5f, R:R=%.1f",
            signal.action.value,
            pair.value,
            candle_iso,
            signal.entry,
            signal.stop_loss,
            signal.take_profit,
            signal.risk_reward,
        )
        return signal
