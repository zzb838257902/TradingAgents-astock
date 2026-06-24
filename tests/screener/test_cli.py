from typer.testing import CliRunner

from tradingagents.screener.cli import app

runner = CliRunner()


def test_data_health_reports_capability_levels():
    result = runner.invoke(app, ["data-health"])
    assert result.exit_code == 0
    assert "daily_bars" in result.stdout
    assert "pit_required" in result.stdout


def test_backtest_fixture_is_reproducible(tmp_path):
    args = [
        "backtest-fixture", "--fixture", "tests/fixtures/screener/mvp_market.json",
        "--home-dir", str(tmp_path),
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
