"""ScreeningService tests (Stage 6A Task 3)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.config import PaperPaths
from tradingagents.paper.exceptions import ScreeningInputError
from tradingagents.paper.repository import PaperRepository
from tradingagents.paper.screening import (
    ScreeningService,
    build_frozen_screen_run,
    screen_content_hash,
)
from tradingagents.paper.contracts import TargetPortfolioMode
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.report import RunReport, ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType
from tradingagents.market_data.market_hours import post_close_signal_time

FIXTURE = Path("tests/fixtures/market_data/provider_mini.json")


@pytest.fixture
def screening_setup(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    market_paths = MarketDataPaths(home_dir=tmp_path / "market")
    market_repo = MarketDataRepository(
        market_paths.live_db_path,
        snapshot_dir=market_paths.snapshot_dir,
    )
    load_fixture_into_repository(market_repo, fixture)
    paper_repo = PaperRepository(PaperPaths(home_dir=tmp_path / "paper"))
    config = ScreenerConfig(home_dir=tmp_path).model_copy(
        update={
            "universe": ScreenerConfig().universe.model_copy(
                update={"min_listing_days": 1, "min_avg_amount_20d": 1_000_000}
            ),
        }
    )
    trade_date = date(2026, 1, 2)
    signal_time = post_close_signal_time(trade_date)
    request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal_time)
    yield {
        "fixture": fixture,
        "market_repo": market_repo,
        "paper_repo": paper_repo,
        "config": config,
        "trade_date": trade_date,
        "signal_time": signal_time,
        "request": request,
        "db_path": market_paths.live_db_path,
    }
    paper_repo.close()


def test_screen_content_hash_is_stable():
    signal_time = post_close_signal_time(date(2026, 1, 2))
    report = RunReport(
        run_id="run-1",
        status=ScreeningStatus.OK,
        signal_time=signal_time,
        data_as_of=signal_time,
        target_weights={"600001": 0.1},
        cash_weight=0.9,
    )
    first = screen_content_hash(report)
    second = screen_content_hash(report.model_copy(update={"target_weights": {"600001": 0.1}}))
    assert first == second


def test_screening_service_freezes_run_and_inputs(screening_setup):
    service = ScreeningService(screening_setup["paper_repo"])
    frozen = service.run(
        screening_setup["market_repo"],
        screening_setup["config"],
        screening_setup["request"],
        screening_setup["signal_time"],
        db_path=screening_setup["db_path"],
        run_id="screen-task3",
        fixture=screening_setup["fixture"],
    )
    assert frozen.screen_run_id == "screen-task3"
    assert frozen.screen_content_hash
    rows = screening_setup["paper_repo"].connection.execute(
        """
        SELECT COUNT(*)
        FROM paper_run_inputs
        WHERE run_id = ?
        """,
        ["screen-task3"],
    ).fetchone()
    assert rows is not None
    assert int(rows[0]) > 0


def test_screening_service_raises_on_invalid_calendar(screening_setup):
    service = ScreeningService(screening_setup["paper_repo"])
    with pytest.raises(ScreeningInputError):
        service.run(
            screening_setup["market_repo"],
            screening_setup["config"],
            screening_setup["request"],
            post_close_signal_time(date(2099, 1, 1)),
            db_path=screening_setup["db_path"],
            fixture=screening_setup["fixture"],
        )


def test_build_frozen_screen_run_preserves_target_mode():
    signal_time = post_close_signal_time(date(2026, 1, 2))
    report = RunReport(
        run_id="cash",
        status=ScreeningStatus.OK,
        signal_time=signal_time,
        data_as_of=signal_time,
        target_weights={},
        cash_weight=1.0,
    )
    frozen = build_frozen_screen_run(
        report,
        target_mode=TargetPortfolioMode.ALL_CASH,
    )
    assert frozen.target_portfolio_mode == TargetPortfolioMode.ALL_CASH
