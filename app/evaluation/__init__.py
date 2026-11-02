"""Evaluation and backtesting package."""

from .backtester import (
    BacktestMetrics,
    TradeOutcome,
    compute_metrics,
    evaluate_ema_baseline,
    evaluate_signal_outcome,
)

__all__ = [
    "evaluate_signal_outcome",
    "compute_metrics",
    "evaluate_ema_baseline",
    "TradeOutcome",
    "BacktestMetrics",
]
