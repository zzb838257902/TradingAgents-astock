"""Paper scheduler job orchestration tests (Stage 6A Task 6)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.paper.contracts import RunStatus, StepStatus
from tradingagents.paper.jobs import OPEN_JOB_STEPS, run_open_job
from tradingagents.paper.recovery import recover_paper_run
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


def test_open_job_steps_are_complete_and_ordered(repo):
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
    assert result.step_names == OPEN_JOB_STEPS
    assert list(result.steps) == OPEN_JOB_STEPS
    assert result.status == RunStatus.COMPLETED
    assert result.exit_code == 0
    assert repo.count_fills("demo") == 1


def test_open_job_blocked_sync_still_runs_local_steps(repo):
    seed_execution_orders(repo)
    config = ScreenerConfig()
    result = run_open_job(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
    )
    assert result.steps["sync_market_open_snapshots"] == StepStatus.BLOCKED
    assert result.steps["load_pending_orders"] == StepStatus.SUCCESS
    assert result.steps["market_open_quality_gate"] == StepStatus.BLOCKED
    assert result.status == RunStatus.BLOCKED
    assert result.exit_code == 2


def test_commit_success_before_step_success_does_not_refill(repo):
    seed_execution_orders(repo)
    config = ScreenerConfig()
    first = run_open_job(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
        open_snapshots={"600000": open_snapshot()},
        crash_after_steps={"execute_pending_orders"},
    )
    assert first.status == RunStatus.RUNNING
    assert repo.count_fills("demo") == 1
    execute_step = repo.get_run_step(first.run_id, "execute_pending_orders")
    assert execute_step is not None
    assert execute_step.status == StepStatus.RUNNING

    recovery = recover_paper_run(
        repo,
        account_id="demo",
        trade_date=TRADE_DATE,
        config=config,
        universe_hash="uni-test",
        open_snapshots={"600000": open_snapshot()},
    )
    assert repo.count_fills("demo") == 1
    assert recovery.status == RunStatus.COMPLETED
    assert recovery.exit_code == 0
    execute_step = repo.get_run_step(recovery.run_id, "execute_pending_orders")
    assert execute_step is not None
    assert execute_step.status == StepStatus.SUCCESS
