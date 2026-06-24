"""Repository-backed screener CLI tests (remediation Task 4)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.cli import app
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.live import resolve_signal_trade_date, run_repository_screen
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

from tests.remediation.test_baseline import FROZEN_FIXTURE_SHA256, SCREEN_HASH_KEYS

SHANGHAI = ZoneInfo("Asia/Shanghai")
FIXTURE = Path("tests/fixtures/screener/mvp_market.json")
MINI = Path("tests/fixtures/market_data/provider_mini.json")
runner = CliRunner()


def _relaxed_config(home_dir: Path) -> ScreenerConfig:
    base = ScreenerConfig(home_dir=home_dir)
    return base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 1,
            "min_avg_amount_20d": 1_000_000,
        }),
    })


def _write_relaxed_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "screener.yaml"
    config_path.write_text(
        "\n".join([
            f"home_dir: {tmp_path}",
            "universe:",
            "  min_listing_days: 1",
            "  min_avg_amount_20d: 1000000",
            "strategy:",
            "  momentum_weight: 0.5",
            "  quality_weight: 0.5",
            "portfolio:",
            "  portfolio_value: 1000000",
            "  max_positions: 10",
            "  max_stock_weight: 0.10",
            "  max_industry_weight: 0.25",
            "  cash_buffer: 0.10",
            "event_enrichment:",
            "  enabled: false",
        ]) + "\n",
        encoding="utf-8",
    )
    return config_path


def _screen_hash(payload: dict) -> str:
    body = {key: payload[key] for key in SCREEN_HASH_KEYS if key in payload}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_live_repo(tmp_path: Path) -> MarketDataRepository:
    fixture = json.loads(MINI.read_text(encoding="utf-8"))
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, fixture)
    return repo


def test_fixture_screen_cli_output_is_unchanged(tmp_path):
    args = [
        "screen",
        "--fixture",
        str(FIXTURE),
        "--home-dir",
        str(tmp_path),
        "--config",
        "config/screener.example.yaml",
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == second.exit_code == 0
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert _screen_hash(first_payload) == _screen_hash(second_payload)
    assert first_payload["fixture_sha256"] == FROZEN_FIXTURE_SHA256
    assert first_payload["status"] in {"ok", "empty_universe"}


def test_repository_screen_all_market(tmp_path):
    repo = _load_live_repo(tmp_path)
    config = _relaxed_config(tmp_path)
    paths = MarketDataPaths(home_dir=tmp_path)
    trade_date, signal_time, errors = resolve_signal_trade_date(
        repo,
        as_of=None,
        today=date(2026, 1, 3),
    )
    assert errors == []
    assert trade_date == date(2026, 1, 3)
    request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal_time)
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        request,
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status in {ScreeningStatus.OK, ScreeningStatus.EMPTY_UNIVERSE}


def test_repository_screen_custom_symbols(tmp_path):
    repo = _load_live_repo(tmp_path)
    config = _relaxed_config(tmp_path)
    paths = MarketDataPaths(home_dir=tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-03T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    request = UniverseRequest(
        universe_type=UniverseType.CUSTOM,
        symbols=("600001",),
        as_of=signal_time,
    )
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        request,
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status == ScreeningStatus.OK
    assert "600001" in report.ranking or report.top_symbol == "600001"


def test_repository_screen_industry_and_concept(tmp_path):
    repo = _load_live_repo(tmp_path)
    config = _relaxed_config(tmp_path)
    paths = MarketDataPaths(home_dir=tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-03T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    industry = UniverseRequest(
        universe_type=UniverseType.INDUSTRY,
        universe_code="801080.SI",
        as_of=signal_time,
    )
    industry_report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        industry,
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert industry_report.status == ScreeningStatus.OK
    assert industry_report.ranking == ["600001"]

    concept_trade_date, concept_signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-02T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    concept = UniverseRequest(
        universe_type=UniverseType.CONCEPT,
        universe_code="BK1184.DC",
        as_of=concept_signal_time,
    )
    concept_report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        concept,
        trade_date=concept_trade_date,
        signal_time=concept_signal_time,
    )
    assert concept_report.status == ScreeningStatus.OK
    assert concept_report.ranking == ["600001"]


def test_repository_screen_index(tmp_path):
    fixture = json.loads(MINI.read_text(encoding="utf-8"))
    fixture["board_definitions"] = list(fixture.get("board_definitions", [])) + [
        {
            "board_type": "index",
            "board_code": "000300.SH",
            "name": "沪深300",
            "pit_level": "pit_required",
        },
    ]
    fixture["board_memberships"] = list(fixture.get("board_memberships", [])) + [
        {
            "board_type": "index",
            "board_code": "000300.SH",
            "symbol": "600001",
            "membership_mode": "effective_interval",
            "effective_from": "2025-01-01",
            "available_at": "2025-01-01T09:00:00+08:00",
        },
    ]
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, fixture)
    config = _relaxed_config(tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-03T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        UniverseRequest(
            universe_type=UniverseType.INDEX,
            universe_code="000300.SH",
            as_of=signal_time,
        ),
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status == ScreeningStatus.OK
    assert report.ranking == ["600001"]


def test_missing_signal_day_quotes_returns_data_error(tmp_path):
    repo = _load_live_repo(tmp_path)
    repo.connection.execute(
        "DELETE FROM daily_bars WHERE trade_date = ?",
        [date(2026, 1, 3)],
    )
    config = _relaxed_config(tmp_path)
    paths = MarketDataPaths(home_dir=tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-03T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        UniverseRequest(
            universe_type=UniverseType.CUSTOM,
            symbols=("600001",),
            as_of=signal_time,
        ),
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert report.ranking == []
    assert "signal date" in report.errors[0].lower()
    assert "600001" in report.errors[0]


def test_repository_screen_cli_without_fixture(tmp_path):
    _load_live_repo(tmp_path)
    config_path = _write_relaxed_config(tmp_path)
    result = runner.invoke(app, [
        "screen",
        "--home-dir",
        str(tmp_path),
        "--config",
        str(config_path),
        "--as-of",
        "2026-01-03T15:30:00+08:00",
        "--universe",
        "custom",
        "--symbols",
        "600001",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source"] == "repository"
    assert payload["status"] == "ok"


def test_empty_repository_returns_data_error(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    result = runner.invoke(app, [
        "screen",
        "--home-dir",
        str(tmp_path),
        "--config",
        "config/screener.example.yaml",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "data_error"


def test_insufficient_trading_days_returns_data_error(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, json.loads(MINI.read_text(encoding="utf-8")))
    repo.connection.execute(
        "DELETE FROM trade_calendar WHERE trade_date != ?",
        [date(2026, 1, 2)],
    )
    config = _relaxed_config(tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-02T15:30:00+08:00",
        today=date(2026, 1, 2),
    )
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        UniverseRequest(universe_type=UniverseType.ALL, as_of=signal_time),
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert "two trading dates" in report.errors[0].lower()


def test_empty_universe_returns_data_error(tmp_path):
    repo = _load_live_repo(tmp_path)
    config = _relaxed_config(tmp_path)
    paths = MarketDataPaths(home_dir=tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-03T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        UniverseRequest(
            universe_type=UniverseType.CUSTOM,
            symbols=("999999",),
            as_of=signal_time,
        ),
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert "universe is empty" in report.errors[0].lower()


def test_missing_quotes_returns_data_error(tmp_path):
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, json.loads(MINI.read_text(encoding="utf-8")))
    repo.connection.execute("DELETE FROM daily_bars")
    config = _relaxed_config(tmp_path)
    trade_date, signal_time, _ = resolve_signal_trade_date(
        repo,
        as_of="2026-01-03T15:30:00+08:00",
        today=date(2026, 1, 3),
    )
    report = run_repository_screen(
        repo,
        config,
        paths.live_db_path,
        UniverseRequest(universe_type=UniverseType.ALL, as_of=signal_time),
        trade_date=trade_date,
        signal_time=signal_time,
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert "bar history" in report.errors[0].lower()


def test_explicit_as_of_does_not_fall_back_to_today(tmp_path):
    repo = _load_live_repo(tmp_path)
    trade_date, signal_time, errors = resolve_signal_trade_date(
        repo,
        as_of="2026-01-02T15:30:00+08:00",
        today=date(2026, 6, 21),
    )
    assert errors == []
    assert trade_date == date(2026, 1, 2)
    assert signal_time == post_close_signal_time(date(2026, 1, 2))


def test_default_as_of_uses_latest_open_date_not_after_today(tmp_path):
    repo = _load_live_repo(tmp_path)
    trade_date, signal_time, errors = resolve_signal_trade_date(
        repo,
        as_of=None,
        today=date(2026, 6, 21),
    )
    assert errors == []
    assert trade_date == date(2026, 1, 3)
    assert signal_time == post_close_signal_time(date(2026, 1, 3))
