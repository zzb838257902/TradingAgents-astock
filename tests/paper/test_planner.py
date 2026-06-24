"""Rebalance planner tests (Stage 6A Task 3)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import (
    OrderSide,
    PositionEntry,
    PositionSourceType,
    TargetPortfolioMode,
)
from tradingagents.paper.exceptions import InvalidScreenRun, RevisionConflict
from tradingagents.paper.planner import RebalancePlanner, stable_order_id
from tradingagents.paper.repository import (
    ExecutionBatch,
    FillSpec,
    PaperRepository,
    RunInputCapture,
)
from tradingagents.paper.screening import build_frozen_screen_run
from tradingagents.scheduler.jobs import universe_hash
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.report import RunReport, ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType
from tradingagents.market_data.market_hours import post_close_signal_time
from tests.paper.conftest import append_position_with_lease

FIXTURE = Path("tests/fixtures/market_data/provider_mini.json")
SIGNAL_DATE = date(2026, 1, 2)
SIGNAL_TIME = post_close_signal_time(SIGNAL_DATE)


@pytest.fixture
def planner_setup(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    market_paths = MarketDataPaths(home_dir=tmp_path / "market")
    market_repo = MarketDataRepository(
        market_paths.live_db_path,
        snapshot_dir=market_paths.snapshot_dir,
    )
    load_fixture_into_repository(market_repo, fixture)
    paper_repo = PaperRepository(PaperPaths(home_dir=tmp_path / "paper"))
    paper_repo.create_account("demo", Decimal("1000000.00"))
    config = ScreenerConfig(home_dir=tmp_path).model_copy(
        update={
            "universe": ScreenerConfig().universe.model_copy(
                update={"min_listing_days": 1, "min_avg_amount_20d": 1_000_000}
            ),
        }
    )
    request = UniverseRequest(universe_type=UniverseType.ALL, as_of=SIGNAL_TIME)
    yield {
        "market_repo": market_repo,
        "paper_repo": paper_repo,
        "config": config,
        "request": request,
        "universe_hash": universe_hash(request),
    }
    paper_repo.close()


def _capture_signal_close(
    paper_repo: PaperRepository,
    *,
    screen_run_id: str,
    symbol: str,
    close_cny: str,
) -> None:
    row = {
        "symbol": symbol,
        "trade_date": SIGNAL_DATE.isoformat(),
        "open": close_cny,
        "high": close_cny,
        "low": close_cny,
        "close": close_cny,
        "volume": 1_000_000,
    }
    paper_repo.capture_run_inputs(
        RunInputCapture(
            run_id=screen_run_id,
            input_type="DAILY_BAR",
            scope_key=f"{symbol}:{SIGNAL_DATE.isoformat()}",
            row_content_hash="hash-close",
            row_json=json.dumps(row),
        )
    )


def _freeze_report(
    paper_repo: PaperRepository,
    report: RunReport,
    *,
    target_mode: TargetPortfolioMode,
    captured_inputs: list[RunInputCapture] | None = None,
):
    frozen = build_frozen_screen_run(report, target_mode=target_mode)
    return paper_repo.freeze_screen_run(frozen, captured_inputs=captured_inputs or [])


def test_data_error_with_empty_weights_never_creates_liquidation_orders(planner_setup):
    paper_repo = planner_setup["paper_repo"]
    report = RunReport(
        run_id="bad",
        status=ScreeningStatus.DATA_ERROR,
        signal_time=SIGNAL_TIME,
        data_as_of=SIGNAL_TIME,
        target_weights={},
        cash_weight=1.0,
    )
    _freeze_report(
        paper_repo,
        report,
        target_mode=TargetPortfolioMode.WEIGHTS,
    )
    planner = RebalancePlanner(
        paper_repo,
        market_repo=planner_setup["market_repo"],
    )
    with pytest.raises(InvalidScreenRun):
        planner.plan(
            "demo",
            "bad",
            config=planner_setup["config"],
            universe_hash=planner_setup["universe_hash"],
        )
    assert paper_repo.list_orders("demo") == []


def test_all_cash_requires_explicit_mode(planner_setup):
    paper_repo = planner_setup["paper_repo"]
    append_position_with_lease(
        paper_repo,
        PositionEntry(
            position_entry_id="pos-all-cash",
            account_id="demo",
            symbol="600001",
            quantity_delta=1000,
            cost_delta_cny=Decimal("10000.00"),
            effective_date=SIGNAL_DATE,
            source_type=PositionSourceType.ADJUSTMENT,
            source_id="seed",
            component="QUANTITY",
            business_key="demo:ADJUSTMENT:seed:QUANTITY",
        ),
    )
    paper_repo.expire_lease_for_test("demo")
    report = RunReport(
        run_id="cash",
        status=ScreeningStatus.OK,
        signal_time=SIGNAL_TIME,
        data_as_of=SIGNAL_TIME,
        target_weights={},
        cash_weight=1.0,
    )
    _capture_signal_close(paper_repo, screen_run_id="cash", symbol="600001", close_cny="10.00")
    _freeze_report(
        paper_repo,
        report,
        target_mode=TargetPortfolioMode.ALL_CASH,
    )
    plan = RebalancePlanner(
        paper_repo,
        market_repo=planner_setup["market_repo"],
    ).plan(
        "demo",
        "cash",
        config=planner_setup["config"],
        universe_hash=planner_setup["universe_hash"],
    )
    assert plan.orders
    assert all(order.side == OrderSide.SELL for order in plan.orders)
    assert plan.orders[0].order_id == stable_order_id(
        side=OrderSide.SELL,
        symbol="600001",
        rebalance_run_id=plan.rebalance_run_id,
    )


def test_same_input_reuses_active_revision(planner_setup):
    paper_repo = planner_setup["paper_repo"]
    report = RunReport(
        run_id="screen-ok",
        status=ScreeningStatus.OK,
        signal_time=SIGNAL_TIME,
        data_as_of=SIGNAL_TIME,
        target_weights={"600001": 0.1},
        cash_weight=0.9,
    )
    _capture_signal_close(paper_repo, screen_run_id="screen-ok", symbol="600001", close_cny="10.00")
    _freeze_report(paper_repo, report, target_mode=TargetPortfolioMode.WEIGHTS)
    planner = RebalancePlanner(
        paper_repo,
        market_repo=planner_setup["market_repo"],
    )
    first = planner.plan(
        "demo",
        "screen-ok",
        config=planner_setup["config"],
        universe_hash=planner_setup["universe_hash"],
    )
    second = planner.plan(
        "demo",
        "screen-ok",
        config=planner_setup["config"],
        universe_hash=planner_setup["universe_hash"],
    )
    assert second.rebalance_run_id == first.rebalance_run_id
    assert second.revision == first.revision


def test_changed_screen_content_hash_requires_force_revision(planner_setup):
    paper_repo = planner_setup["paper_repo"]
    base_report = RunReport(
        run_id="screen-a",
        status=ScreeningStatus.OK,
        signal_time=SIGNAL_TIME,
        data_as_of=SIGNAL_TIME,
        target_weights={"600001": 0.1},
        cash_weight=0.9,
    )
    _capture_signal_close(paper_repo, screen_run_id="screen-a", symbol="600001", close_cny="10.00")
    _freeze_report(paper_repo, base_report, target_mode=TargetPortfolioMode.WEIGHTS)
    planner = RebalancePlanner(
        paper_repo,
        market_repo=planner_setup["market_repo"],
    )
    planner.plan(
        "demo",
        "screen-a",
        config=planner_setup["config"],
        universe_hash=planner_setup["universe_hash"],
    )

    changed_report = base_report.model_copy(
        update={
            "run_id": "screen-b",
            "target_weights": {"600001": 0.2},
            "cash_weight": 0.8,
        }
    )
    _capture_signal_close(
        paper_repo,
        screen_run_id="screen-b",
        symbol="600001",
        close_cny="10.00",
    )
    _freeze_report(
        paper_repo,
        changed_report,
        target_mode=TargetPortfolioMode.WEIGHTS,
    )

    with pytest.raises(RevisionConflict):
        planner.plan(
            "demo",
            "screen-b",
            config=planner_setup["config"],
            universe_hash=planner_setup["universe_hash"],
        )


def test_revision_with_fills_cannot_be_replanned(planner_setup):
    paper_repo = planner_setup["paper_repo"]
    execution_date = date(2026, 1, 3)
    execution_time = post_close_signal_time(execution_date).replace(hour=9, minute=35)
    report = RunReport(
        run_id="screen-exec",
        status=ScreeningStatus.OK,
        signal_time=SIGNAL_TIME,
        data_as_of=SIGNAL_TIME,
        target_weights={"600001": 0.1},
        cash_weight=0.9,
    )
    _capture_signal_close(
        paper_repo,
        screen_run_id="screen-exec",
        symbol="600001",
        close_cny="10.00",
    )
    _freeze_report(paper_repo, report, target_mode=TargetPortfolioMode.WEIGHTS)
    planner = RebalancePlanner(
        paper_repo,
        market_repo=planner_setup["market_repo"],
    )
    plan = planner.plan(
        "demo",
        "screen-exec",
        config=planner_setup["config"],
        universe_hash=planner_setup["universe_hash"],
    )
    buy_order = next(order for order in plan.orders if order.side == OrderSide.BUY)
    lease = paper_repo.acquire_account_lease("demo", owner_id="executor")
    paper_repo.apply_execution_batch(
        ExecutionBatch(
            account_id="demo",
            rebalance_run_id=plan.rebalance_run_id,
            execution_date=execution_date,
            execution_time=execution_time,
            owner_id="executor",
            fills=[
                FillSpec(
                    fill_id="fill-exec-1",
                    order_id=buy_order.order_id,
                    account_id="demo",
                    symbol="600001",
                    quantity=buy_order.planned_quantity,
                    price_cny=buy_order.reference_price_cny,
                    commission_cny=Decimal("5.00"),
                )
            ],
        ),
        fencing_token=lease.token,
    )
    paper_repo.expire_lease_for_test("demo")

    with pytest.raises(RevisionConflict):
        planner.plan(
            "demo",
            "screen-exec",
            config=planner_setup["config"],
            universe_hash=planner_setup["universe_hash"],
            force_revision=True,
        )
