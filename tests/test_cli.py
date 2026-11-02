"""Unit tests for Command Line Interface (CLI - Phase 6)."""

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Forex AI Multi-Agent Telegram Signal Bot CLI" in result.output


def test_cli_test_telegram_dry_run():
    result = runner.invoke(app, ["test-telegram", "--dry-run"])
    assert result.exit_code == 0
    assert "Telegram test SUCCESSFUL [DRY-RUN MODE]" in result.output


def test_cli_analyze_command_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREX_DB_PATH", str(tmp_path / "cli_test.db"))
    result = runner.invoke(app, ["analyze", "--symbol", "EUR_USD", "--mock", "--dry-run"])
    assert result.exit_code == 0
    assert "Starting Analysis: EUR_USD" in result.output
    assert "Completed EUR_USD" in result.output


def test_cli_run_once_command_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREX_DB_PATH", str(tmp_path / "cli_run_once.db"))
    result = runner.invoke(app, ["run-once", "--mock", "--dry-run"])
    assert result.exit_code == 0
    assert "Initiating single sweep across all 3 major pairs" in result.output
    assert "Finished full sweep." in result.output
