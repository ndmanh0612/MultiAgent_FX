"""Command Line Interface for Forex AI Multi-Agent Telegram Signal Bot."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import typer

from app.agents.forex_graph import ForexTradingAgentsGraph
from app.data.oanda_client import OandaClient, OandaConfig
from app.domain.models import ForexPair, SignalAction
from app.engine.signal_engine import SignalEngine
from app.notifications.telegram_service import TelegramConfig, TelegramService
from app.storage.database import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ForexSignalBot")

app = typer.Typer(
    name="forex-bot",
    help="Forex AI Multi-Agent Telegram Signal Bot CLI",
    add_completion=False,
)


def _process_symbol(
    pair: ForexPair,
    mock_mode: bool = False,
    dry_run: bool = False,
    save_db: bool = True,
) -> None:
    """End-to-end execution pipeline for a single currency pair."""
    t0 = time.time()
    header = f"========== Starting Analysis: {pair.value} =========="
    logger.info(header)
    typer.echo(header)

    # 1. Initialize Clients
    oanda_cfg = OandaConfig.from_env()
    if mock_mode:
        oanda_cfg.mock_mode = True

    oanda_client = OandaClient(oanda_cfg)
    tg_cfg = TelegramConfig.from_env()
    if dry_run:
        tg_cfg.dry_run = True

    telegram_service = TelegramService(tg_cfg)
    signal_engine = SignalEngine()
    db_mgr = DatabaseManager() if save_db else None

    # 2. Fetch H1 Completed Candle Market Snapshot
    try:
        snapshot = oanda_client.get_market_snapshot(pair, count=60)
        logger.info(
            "Snapshot fetched: Close=%.5f, ATR=%.5f, Spread=%.1f pips, Stale=%s",
            snapshot.close,
            snapshot.indicators.atr14 or 0.0,
            snapshot.spread_pips or 0.0,
            snapshot.is_stale,
        )
        if db_mgr:
            db_mgr.save_market_snapshot(snapshot)
    except Exception as exc:
        logger.error("Failed to fetch market data for %s: %s", pair.value, exc)
        typer.secho(f"Failed to fetch market data: {exc}", fg=typer.colors.RED)
        return

    # 3. Execute Multi-Agent Graph
    agent_graph = ForexTradingAgentsGraph(mock_mode=mock_mode)
    analysis_result = agent_graph.analyze(snapshot)
    exec_time = time.time() - t0

    run_id = None
    if db_mgr:
        run_id = db_mgr.save_analysis_run(
            symbol=pair.value,
            candle_timestamp=snapshot.candle_timestamp,
            model_name=analysis_result.get("model_name", "TradingAgents-Gemini"),
            action=analysis_result.get("action", "NO_TRADE"),
            final_rating=analysis_result.get("final_rating", "Hold"),
            execution_seconds=round(exec_time, 2),
            reports=analysis_result,
        )

    # 4. Generate Deterministic Signal
    signal = signal_engine.generate_signal(
        analysis_result=analysis_result,
        snapshot=snapshot,
        model_name="TradingAgents-Gemini",
    )

    if db_mgr:
        db_mgr.save_signal(signal, run_id=run_id)

    # 5. Broadcast Signal to Telegram
    delivery = telegram_service.send_signal(signal)
    if db_mgr and signal.action != SignalAction.NO_TRADE:
        db_mgr.save_telegram_delivery(delivery, symbol=pair.value, action=signal.action.value)

    # 6. Display Result Summary
    outcome_str = f"Signal Outcome: {signal.action.value} {pair.value} | Status: {signal.status.value}"
    logger.info(outcome_str)
    typer.echo(outcome_str)
    if signal.action in (SignalAction.BUY, SignalAction.SELL):
        levels_str = (
            f"Levels: Entry={signal.entry:.5f} | SL={signal.stop_loss:.5f} ({signal.sl_pips} pips) | "
            f"TP={signal.take_profit:.5f} ({signal.tp_pips} pips) | R:R={signal.risk_reward:.1f}"
        )
        logger.info(levels_str)
        typer.echo(levels_str)
    logger.info("Reason: %s", signal.reason)
    completed_str = f"========== Completed {pair.value} in {exec_time:.2f}s ==========\n"
    logger.info(completed_str)
    typer.echo(completed_str)


@app.command("analyze")
def analyze_command(
    symbol: str = typer.Option("EUR_USD", "--symbol", "-s", help="Currency pair (EUR_USD, GBP_USD, USD_JPY)"),
    mock: bool = typer.Option(False, "--mock", help="Force offline mock mode"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Telegram dry-run mode"),
    save_db: bool = typer.Option(True, "--save-db/--no-save-db", help="Persist to SQLite"),
) -> None:
    """Analyze a single Forex instrument on the latest completed H1 candle."""
    try:
        pair = ForexPair.from_string(symbol)
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from e

    _process_symbol(pair, mock_mode=mock, dry_run=dry_run, save_db=save_db)


@app.command("run-once")
def run_once_command(
    mock: bool = typer.Option(False, "--mock", help="Force offline mock mode"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Telegram dry-run mode"),
    save_db: bool = typer.Option(True, "--save-db/--no-save-db", help="Persist to SQLite"),
) -> None:
    """Analyze all supported instruments (EUR_USD, GBP_USD, USD_JPY) sequentially."""
    start_msg = "Initiating single sweep across all 3 major pairs..."
    logger.info(start_msg)
    typer.echo(start_msg)
    for pair in (ForexPair.EUR_USD, ForexPair.GBP_USD, ForexPair.USD_JPY):
        _process_symbol(pair, mock_mode=mock, dry_run=dry_run, save_db=save_db)
    end_msg = "Finished full sweep."
    logger.info(end_msg)
    typer.echo(end_msg)


@app.command("scheduler")
def scheduler_command(
    minute: int = typer.Option(2, "--minute", "-m", help="Minute past the hour to execute (0-59)"),
    mock: bool = typer.Option(False, "--mock", help="Force offline mock mode"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Telegram dry-run mode"),
) -> None:
    """Start local APScheduler running automatically after each completed H1 candle."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    sched = BlockingScheduler()
    logger.info("Starting local APScheduler on Windows. Hourly trigger at minute :%02d UTC.", minute)

    def job():
        logger.info("Scheduler triggered at %s UTC", datetime.now(UTC))
        for pair in (ForexPair.EUR_USD, ForexPair.GBP_USD, ForexPair.USD_JPY):
            try:
                _process_symbol(pair, mock_mode=mock, dry_run=dry_run, save_db=True)
            except Exception as e:
                logger.error("Job error for %s: %s", pair.value, e)

    sched.add_job(job, "cron", minute=minute, timezone="UTC", id="forex_h1_job")

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped cleanly.")


@app.command("test-telegram")
def test_telegram_command(
    text: str | None = typer.Option(None, "--text", "-t", help="Optional custom message"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate message without sending"),
) -> None:
    """Send a diagnostic ping message to verify Telegram bot credentials and connectivity."""
    tg_cfg = TelegramConfig.from_env()
    if dry_run:
        tg_cfg.dry_run = True

    service = TelegramService(tg_cfg)
    result = service.send_test_message(custom_text=text)

    if result.success:
        if result.is_dry_run:
            typer.secho("✅ Telegram test SUCCESSFUL [DRY-RUN MODE] (No live request sent).", fg=typer.colors.YELLOW)
        else:
            typer.secho(f"✅ Telegram test SUCCESSFUL! Delivered to Chat ID {result.chat_id} (Message ID: {result.message_id}).", fg=typer.colors.GREEN)
    else:
        typer.secho(f"❌ Telegram test FAILED: {result.error}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
