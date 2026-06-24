"""Paper scheduler recovery tests (Stage 6A Task 6)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import RunStatus, StepStatus
from tradingagents.paper.jobs import run_open_job
from tradingagents.paper.recovery import recover_paper_run
from tradingagents.paper.repository import RunStepWriteSpec
from tradingagents.screener.config import ScreenerConfig
from tests.paper.conftest import TRADE_DATE, seed_execution_orders

SHANGHAI = ZoneInfo("Asia/Shanghai")


def open_snapshot(*, symbol: str = "600000") -> MarketOpenSnapshot:
    observed_at = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)
    return MarketOpenSnapshot(
        symbol=symbol,
        trade_date=TRADE_DATE,
        observed_at=observed_at,
        open_cny=10.0,
        prev_close_cny=10.0,
        last_cny=10.0,
        cumulative_volume_shares=1_000_000,
        quote_status=QuoteStatus.TRADING,
        upper_limit_cny=11.0,
        lower_limit_cny=9.0,
        source="fixture",
        available_at=observed_at,
    )


def test_blocked_step_can_be_retried(repo):
    seed_execution_orders(repo)
    config = ScreenerConfig()
    blocked = run_open_job(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
    )
    assert blocked.status == RunStatus.BLOCKED

    recovered = recover_paper_run(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
        open_snapshots={"600000": open_snapshot()},
    )
    assert recovered.status == RunStatus.COMPLETED
    assert repo.count_fills("demo") == 1


def test_data_error_step_is_terminal(repo):
    seed_execution_orders(repo)
    config = ScreenerConfig()
    result = run_open_job(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
        open_snapshots={"600000": open_snapshot()},
    )
    repo.save_run_step(
        RunStepWriteSpec(
            run_id=result.run_id,
            step_name="market_open_quality_gate",
            status=StepStatus.DATA_ERROR,
            input_hash=repo.get_run_step(
                result.run_id,
                "market_open_quality_gate",
            ).input_hash,
            error_json='{"code":"MISSING_OPEN_SNAPSHOT"}',
        )
    )
    recovery = recover_paper_run(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
    )
    assert recovery.status == RunStatus.DATA_ERROR
    assert recovery.exit_code == 1
