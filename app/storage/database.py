"""SQLite local persistence schema, migrations, and repository functions."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.models import ForexSignal, MarketSnapshot
from app.notifications.telegram_service import DeliveryResult

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path("data/forex_signals.db")


SCHEMA_V1 = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT 'H1',
    candle_timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    ema20 REAL,
    ema50 REAL,
    rsi14 REAL,
    atr14 REAL,
    bid REAL,
    ask REAL,
    spread_pips REAL,
    is_stale INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, candle_timestamp)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    candle_timestamp TEXT NOT NULL,
    model_name TEXT NOT NULL,
    action TEXT NOT NULL,
    final_rating TEXT NOT NULL,
    execution_seconds REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    market_report TEXT,
    macro_report TEXT,
    investment_plan TEXT,
    trader_plan TEXT,
    final_trade_decision TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES analysis_runs(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT 'H1',
    action TEXT NOT NULL,
    entry REAL,
    stop_loss REAL,
    take_profit REAL,
    risk_reward REAL,
    reason TEXT NOT NULL,
    candle_timestamp TEXT NOT NULL,
    model_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    sl_pips REAL,
    tp_pips REAL,
    spread_pips REAL,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, candle_timestamp, action)
);

CREATE TABLE IF NOT EXISTS telegram_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    message_id INTEGER,
    chat_id TEXT,
    success INTEGER NOT NULL,
    is_dry_run INTEGER NOT NULL,
    error TEXT,
    sent_timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    outcome TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    profit_loss_pips REAL,
    risk_reward_achieved REAL,
    evaluated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_sym_time ON market_snapshots(symbol, candle_timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_sym_time ON signals(symbol, candle_timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
"""


class DatabaseManager:
    """Manages SQLite database initialization, transactions, and entity persistence."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            env_path = os.getenv("FOREX_DB_PATH")
            self.db_path = Path(env_path) if env_path else DEFAULT_DB_PATH
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        """Provide a transactional database connection."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initialize schema and apply versioning."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_V1)

            # Check and record version
            cur = conn.cursor()
            cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;")
            row = cur.fetchone()
            if not row:
                now_str = datetime.now(UTC).isoformat()
                cur.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?);",
                    (CURRENT_SCHEMA_VERSION, now_str),
                )
                logger.info("Initialized SQLite database schema v%d at %s", CURRENT_SCHEMA_VERSION, self.db_path)

    def save_market_snapshot(self, snapshot: MarketSnapshot) -> int:
        """Insert or replace a verified market snapshot."""
        now_str = datetime.now(UTC).isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO market_snapshots (
                    symbol, timeframe, candle_timestamp, open, high, low, close, volume,
                    ema20, ema50, rsi14, atr14, bid, ask, spread_pips, is_stale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.symbol.value,
                    snapshot.timeframe,
                    snapshot.candle_timestamp.isoformat(),
                    snapshot.open,
                    snapshot.high,
                    snapshot.low,
                    snapshot.close,
                    snapshot.volume,
                    snapshot.indicators.ema20,
                    snapshot.indicators.ema50,
                    snapshot.indicators.rsi14,
                    snapshot.indicators.atr14,
                    snapshot.bid,
                    snapshot.ask,
                    snapshot.spread_pips,
                    1 if snapshot.is_stale else 0,
                    now_str,
                ),
            )
            return cur.lastrowid

    def save_analysis_run(
        self,
        symbol: str,
        candle_timestamp: datetime,
        model_name: str,
        action: str,
        final_rating: str,
        execution_seconds: float,
        reports: dict[str, Any] | None = None,
    ) -> int:
        """Record an analysis run and its internal agent reports."""
        now_str = datetime.now(UTC).isoformat()
        reports = reports or {}
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO analysis_runs (
                    symbol, candle_timestamp, model_name, action, final_rating,
                    execution_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    candle_timestamp.isoformat(),
                    model_name,
                    action,
                    final_rating,
                    execution_seconds,
                    now_str,
                ),
            )
            run_id = cur.lastrowid

            cur.execute(
                """
                INSERT INTO agent_decisions (
                    run_id, market_report, macro_report, investment_plan,
                    trader_plan, final_trade_decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    reports.get("market_report", ""),
                    reports.get("news_report", ""),
                    reports.get("investment_plan", ""),
                    reports.get("trader_investment_plan", ""),
                    reports.get("final_trade_decision", ""),
                    now_str,
                ),
            )
            return run_id

    def save_signal(self, signal: ForexSignal, run_id: int | None = None) -> int:
        """Insert or update a generated Forex signal."""
        now_str = datetime.now(UTC).isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO signals (
                    run_id, symbol, timeframe, action, entry, stop_loss, take_profit,
                    risk_reward, reason, candle_timestamp, model_name, status,
                    sl_pips, tp_pips, spread_pips, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    signal.symbol.value,
                    signal.timeframe,
                    signal.action.value,
                    signal.entry,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.risk_reward,
                    signal.reason,
                    signal.candle_timestamp.isoformat(),
                    signal.model_name,
                    signal.status.value,
                    signal.sl_pips,
                    signal.tp_pips,
                    signal.spread_pips,
                    now_str,
                ),
            )
            return cur.lastrowid

    def save_telegram_delivery(
        self,
        delivery: DeliveryResult,
        signal_id: int | None = None,
        symbol: str = "",
        action: str = "",
    ) -> int:
        """Record the outcome of a Telegram delivery attempt."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO telegram_deliveries (
                    signal_id, symbol, action, message_id, chat_id, success,
                    is_dry_run, error, sent_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    symbol,
                    action,
                    delivery.message_id,
                    delivery.chat_id,
                    1 if delivery.success else 0,
                    1 if delivery.is_dry_run else 0,
                    delivery.error,
                    delivery.timestamp.isoformat(),
                ),
            )
            return cur.lastrowid

    def save_evaluation_result(
        self,
        signal_id: int,
        symbol: str,
        outcome: str,
        entry_price: float,
        exit_price: float,
        profit_loss_pips: float,
        risk_reward_achieved: float,
    ) -> int:
        """Store the backtesting/evaluation outcome for a signal."""
        now_str = datetime.now(UTC).isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO evaluation_results (
                    signal_id, symbol, outcome, entry_price, exit_price,
                    profit_loss_pips, risk_reward_achieved, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    symbol,
                    outcome,
                    entry_price,
                    exit_price,
                    profit_loss_pips,
                    risk_reward_achieved,
                    now_str,
                ),
            )
            # Update signal status
            cur.execute("UPDATE signals SET status = ? WHERE id = ?;", (outcome, signal_id))
            return cur.lastrowid

    def get_recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent signals ordered by creation timestamp."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?;", (limit,))
            return [dict(row) for row in cur.fetchall()]
