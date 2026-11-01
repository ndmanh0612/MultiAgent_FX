"""Technical indicator calculation engine for Forex H1 candles.

Calculates EMA20, EMA50, RSI14, and ATR14 deterministically using pandas/numpy.
Strictly point-in-time: indicators are computed using only past and current candle data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.models import Candle, TechnicalIndicators


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    """Convert a sequence of validated Candle objects to a pandas DataFrame."""
    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    data = [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]
    df = pd.DataFrame(data)
    df.sort_values("timestamp", ascending=True, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def calculate_indicators_dataframe(candles: list[Candle]) -> pd.DataFrame:
    """Calculate EMA20, EMA50, RSI14, ATR14 and append them to the candle DataFrame.

    Uses Wilder's smoothing for RSI14 and ATR14, standard exponential weighting for EMA.
    """
    df = candles_to_dataframe(candles)
    if len(df) == 0:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # 1. EMA 20 & EMA 50
    if len(df) >= 20:
        df["ema20"] = close.ewm(span=20, adjust=False).mean()
    else:
        df["ema20"] = np.nan

    if len(df) >= 50:
        df["ema50"] = close.ewm(span=50, adjust=False).mean()
    else:
        df["ema50"] = np.nan

    # 2. RSI 14 (Wilder's smoothing: alpha = 1 / 14)
    if len(df) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        # First average via SMA over first 14 periods, followed by Wilder's RMA
        avg_gain = gain.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Handle zero loss condition (perfect uptrend -> 100, no change -> 50)
        rsi = rsi.where(avg_loss != 0, 100.0)
        rsi = rsi.where((avg_gain != 0) | (avg_loss != 0), 50.0)
        df["rsi14"] = rsi
    else:
        df["rsi14"] = np.nan

    # 3. ATR 14 (Wilder's smoothing over True Range)
    if len(df) >= 2:
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        if len(df) >= 15:
            df["atr14"] = tr.ewm(alpha=1.0 / 14.0, min_periods=14, adjust=False).mean()
        else:
            df["atr14"] = np.nan
    else:
        df["atr14"] = np.nan

    return df


def calculate_latest_indicators(candles: list[Candle]) -> TechnicalIndicators:
    """Calculate and extract TechnicalIndicators for the most recent completed candle."""
    if not candles:
        return TechnicalIndicators()

    df = calculate_indicators_dataframe(candles)
    last_row = df.iloc[-1]

    def _val(col: str) -> float | None:
        if col in last_row and pd.notna(last_row[col]):
            return float(last_row[col])
        return None

    return TechnicalIndicators(
        ema20=_val("ema20"),
        ema50=_val("ema50"),
        rsi14=_val("rsi14"),
        atr14=_val("atr14"),
    )
