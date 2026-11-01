"""Mock data generators and sample payloads for offline testing of OANDA integration."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta


def generate_mock_h1_candles(
    symbol: str = "EUR_USD",
    end_time: datetime | None = None,
    count: int = 100,
    include_incomplete: bool = False,
    base_price: float | None = None,
) -> list[dict]:
    """Generate realistic synthetic H1 candles matching OANDA v3 API format.

    OANDA JSON schema:
    {
      "complete": true,
      "volume": 1234,
      "time": "2026-11-01T21:00:00.000000000Z",
      "mid": {
        "o": "1.08500",
        "h": "1.08700",
        "l": "1.08450",
        "c": "1.08620"
      }
    }
    """
    if end_time is None:
        # Align to top of hour
        now = datetime.now(UTC)
        end_time = now.replace(minute=0, second=0, microsecond=0)

    if base_price is None:
        if "JPY" in symbol:
            base_price = 153.50
        elif "GBP" in symbol:
            base_price = 1.2950
        else:
            base_price = 1.0850

    candles = []
    # Generate backwards from end_time so end_time is the latest candle
    start_time = end_time - timedelta(hours=count - 1)

    curr_price = base_price
    for i in range(count):
        candle_time = start_time + timedelta(hours=i)

        # Gentle sine wave with subtle noise
        wave = math.sin(i * 0.15) * (0.0020 if "JPY" not in symbol else 0.35)
        step_o = curr_price + wave
        step_h = step_o + (0.0015 if "JPY" not in symbol else 0.25)
        step_l = step_o - (0.0012 if "JPY" not in symbol else 0.20)
        step_c = step_o + (0.0005 if "JPY" not in symbol else 0.08)

        is_last = (i == count - 1)
        is_complete = not (is_last and include_incomplete)

        time_str = candle_time.strftime("%Y-%m-%dT%H:00:00.000000000Z")
        candles.append({
            "complete": is_complete,
            "volume": 1500 + (i % 20) * 50,
            "time": time_str,
            "mid": {
                "o": f"{step_o:.5f}" if "JPY" not in symbol else f"{step_o:.3f}",
                "h": f"{step_h:.5f}" if "JPY" not in symbol else f"{step_h:.3f}",
                "l": f"{step_l:.5f}" if "JPY" not in symbol else f"{step_l:.3f}",
                "c": f"{step_c:.5f}" if "JPY" not in symbol else f"{step_c:.3f}",
            },
        })
        curr_price = step_c

    return candles


def get_mock_oanda_candles_response(
    symbol: str = "EUR_USD",
    count: int = 100,
    include_incomplete: bool = False,
    end_time: datetime | None = None,
) -> dict:
    """Return an OANDA v3 candles endpoint mock response dictionary."""
    candles = generate_mock_h1_candles(
        symbol=symbol,
        count=count,
        include_incomplete=include_incomplete,
        end_time=end_time,
    )
    return {
        "instrument": symbol,
        "granularity": "H1",
        "candles": candles,
    }


def get_mock_oanda_pricing_response(symbol: str = "EUR_USD") -> dict:
    """Return an OANDA v3 pricing endpoint mock response dictionary."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    if "JPY" in symbol:
        bid = 153.485
        ask = 153.500
    elif "GBP" in symbol:
        bid = 1.29490
        ask = 1.29505
    else:
        bid = 1.08492
        ask = 1.08504

    return {
        "time": now_str,
        "prices": [
            {
                "type": "PRICE",
                "instrument": symbol,
                "time": now_str,
                "status": "tradeable",
                "tradeable": True,
                "bids": [{"price": f"{bid:.5f}", "liquidity": 10000000}],
                "asks": [{"price": f"{ask:.5f}", "liquidity": 10000000}],
                "closeoutBid": f"{bid:.5f}",
                "closeoutAsk": f"{ask:.5f}",
            }
        ],
    }
