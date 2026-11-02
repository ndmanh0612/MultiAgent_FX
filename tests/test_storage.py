"""Unit tests for SQLite database persistence (Database Design)."""

from datetime import UTC, datetime

import pytest

from app.domain.models import (
    ForexPair,
    ForexSignal,
    MarketSnapshot,
    SignalAction,
    SignalStatus,
    TechnicalIndicators,
)
from app.notifications.telegram_service import DeliveryResult
from app.storage.database import CURRENT_SCHEMA_VERSION, DatabaseManager


@pytest.fixture
def temp_db(tmp_path):
    """Provide a temporary isolated SQLite database."""
    db_file = tmp_path / "test_forex.db"
    return DatabaseManager(db_file)


@pytest.mark.unit
def test_database_initialization(temp_db: DatabaseManager):
    with temp_db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT version FROM schema_version;")
        row = cur.fetchone()
        assert row is not None
        assert row["version"] == CURRENT_SCHEMA_VERSION


@pytest.mark.unit
def test_save_and_retrieve_market_snapshot(temp_db: DatabaseManager):
    now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    snapshot = MarketSnapshot(
        symbol=ForexPair.EUR_USD,
        timeframe="H1",
        candle_timestamp=now,
        open=1.0850,
        high=1.0890,
        low=1.0830,
        close=1.0870,
        volume=1500,
        indicators=TechnicalIndicators(ema20=1.084, ema50=1.081, rsi14=60.5, atr14=0.003),
        bid=1.0869,
        ask=1.0871,
        spread_pips=2.0,
        is_stale=False,
    )

    row_id = temp_db.save_market_snapshot(snapshot)
    assert row_id > 0

    with temp_db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM market_snapshots WHERE id = ?;", (row_id,))
        row = cur.fetchone()
        assert row["symbol"] == "EUR_USD"
        assert row["close"] == 1.0870
        assert row["rsi14"] == pytest.approx(60.5)


@pytest.mark.unit
def test_save_analysis_run_and_decisions(temp_db: DatabaseManager):
    now = datetime(2026, 11, 2, 11, 0, tzinfo=UTC)
    reports = {
        "market_report": "Bullish EMA crossover",
        "news_report": "Fed steady, ECB dovish",
        "investment_plan": "Buy recommendation",
        "trader_investment_plan": "Buy at 1.0850",
        "final_trade_decision": "Approved BUY order",
    }

    run_id = temp_db.save_analysis_run(
        symbol="EUR_USD",
        candle_timestamp=now,
        model_name="TradingAgents-Gemini",
        action="BUY",
        final_rating="Buy",
        execution_seconds=4.5,
        reports=reports,
    )
    assert run_id > 0

    with temp_db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM analysis_runs WHERE id = ?;", (run_id,))
        row = cur.fetchone()
        assert row["action"] == "BUY"

        cur.execute("SELECT * FROM agent_decisions WHERE run_id = ?;", (run_id,))
        dec_row = cur.fetchone()
        assert "Bullish EMA crossover" in dec_row["market_report"]


@pytest.mark.unit
def test_save_signal_and_telegram_delivery(temp_db: DatabaseManager):
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    signal = ForexSignal(
        symbol=ForexPair.GBP_USD,
        timeframe="H1",
        action=SignalAction.BUY,
        entry=1.2950,
        stop_loss=1.2910,
        take_profit=1.3030,
        risk_reward=2.0,
        reason="Breakout confirmed",
        candle_timestamp=now,
        model_name="test-model",
        status=SignalStatus.PENDING,
        sl_pips=40.0,
        tp_pips=80.0,
        spread_pips=1.8,
    )

    sig_id = temp_db.save_signal(signal)
    assert sig_id > 0

    delivery = DeliveryResult(
        success=True,
        message_id=98765,
        chat_id="test_channel",
        is_dry_run=True,
    )
    del_id = temp_db.save_telegram_delivery(delivery, signal_id=sig_id, symbol="GBP_USD", action="BUY")
    assert del_id > 0

    # Record evaluation result
    eval_id = temp_db.save_evaluation_result(
        signal_id=sig_id,
        symbol="GBP_USD",
        outcome="WIN_TP",
        entry_price=1.2950,
        exit_price=1.3030,
        profit_loss_pips=80.0,
        risk_reward_achieved=2.0,
    )
    assert eval_id > 0

    # Check signal status updated to WIN_TP
    recent = temp_db.get_recent_signals(limit=5)
    assert len(recent) == 1
    assert recent[0]["status"] == "WIN_TP"
