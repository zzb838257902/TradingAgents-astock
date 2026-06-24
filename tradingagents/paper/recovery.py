"""Crash recovery for Stage 6A paper scheduler runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import MarketOpenSnapshot
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.paper.contracts import RunStatus, StepStatus
from tradingagents.paper.jobs import (
    AFTER_CLOSE_JOB_STEPS,
    MONEY_IMPACT_STEPS,
    OPEN_JOB_STEPS,
    PaperJobResult,
    build_paper_run_id,
    run_open_job,
    run_paper_after_close_job,
)
from tradingagents.paper.repository import PaperRepository, RunStepWriteSpec
from tradingagents.paper.screening import STRATEGY_VERSION
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.universe_resolver import UniverseRequest

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class RecoveryResult:
    run_id: str
    job_type: str
    status: RunStatus
    resumed_from: str | None = None
    reconciled_steps: list[str] | None = None
    job_result: PaperJobResult | None = None

    @property
    def exit_code(self) -> int:
        if self.status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_REJECTIONS}:
            return 0
        if self.status == RunStatus.BLOCKED:
            return 2
        return 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "resumed_from": self.resumed_from,
            "reconciled_steps": self.reconciled_steps or [],
            "exit_code": self.exit_code,
        }
        if self.job_result is not None:
            payload["job_result"] = self.job_result.to_dict()
        return payload


def _step_order(job_type: str) -> list[str]:
    if job_type == "open":
        return OPEN_JOB_STEPS
    return AFTER_CLOSE_JOB_STEPS


def reconcile_running_step(
    paper_repo: PaperRepository,
    *,
    run_id: str,
    step_name: str,
    account_id: str,
) -> bool:
    step = paper_repo.get_run_step(run_id, step_name)
    if step is None or step.status != StepStatus.RUNNING:
        return False

    if step_name == "execute_pending_orders":
        load_step = paper_repo.get_run_step(run_id, "load_pending_orders")
        if load_step is None or not load_step.output_json:
            return False
        payload = json.loads(load_step.output_json)
        rebalance_run_id = payload.get("rebalance_run_id")
        if not rebalance_run_id:
            return False
        if not paper_repo.rebalance_has_fills(rebalance_run_id):
            return False
        execute_output = step.output_json
        if not execute_output:
            execute_output = json.dumps(
                {
                    "rebalance_run_id": rebalance_run_id,
                    "fill_count": paper_repo.count_fills(account_id),
                    "reconciled": True,
                }
            )
        paper_repo.save_run_step(
            RunStepWriteSpec(
                run_id=run_id,
                step_name=step_name,
                status=StepStatus.SUCCESS,
                input_hash=step.input_hash,
                output_json=execute_output,
                started_at=step.started_at,
                finished_at=datetime.now(tz=SHANGHAI),
            )
        )
        return True
    return False


def _resume_point(
    paper_repo: PaperRepository,
    *,
    run_id: str,
    job_type: str,
    account_id: str,
) -> tuple[str | None, list[str], RunStatus | None]:
    reconciled: list[str] = []
    for step_name in _step_order(job_type):
        step = paper_repo.get_run_step(run_id, step_name)
        if step is None:
            return step_name, reconciled, None
        if step.status == StepStatus.SUCCESS:
            continue
        if step.status == StepStatus.RUNNING:
            if reconcile_running_step(
                paper_repo,
                run_id=run_id,
                step_name=step_name,
                account_id=account_id,
            ):
                reconciled.append(step_name)
                continue
            return step_name, reconciled, RunStatus.RUNNING
        if step.status == StepStatus.DATA_ERROR:
            return None, reconciled, RunStatus.DATA_ERROR
        if step.status == StepStatus.BLOCKED:
            return step_name, reconciled, None
        if step.status == StepStatus.FAILED:
            if step_name in MONEY_IMPACT_STEPS and step_name != "execute_pending_orders":
                return None, reconciled, RunStatus.FAILED
            return step_name, reconciled, None
    return None, reconciled, RunStatus.COMPLETED


def recover_paper_run(
    paper_repo: PaperRepository,
    *,
    account_id: str,
    trade_date: date,
    config: ScreenerConfig,
    universe_hash: str,
    job_type: str = "open",
    market_repo: MarketDataRepository | None = None,
    sync: MarketDataSync | None = None,
    open_snapshots: dict[str, MarketOpenSnapshot] | None = None,
    execution_time: datetime | None = None,
    universe_request: UniverseRequest | None = None,
    fixture: dict | None = None,
    force: bool = False,
    owner_id: str = "recover",
    strategy_version: str = STRATEGY_VERSION,
    paths: Any | None = None,
) -> RecoveryResult:
    run_id = build_paper_run_id(
        job_type,
        account_id,
        trade_date,
        universe_hash,
        config,
        strategy_version=strategy_version,
    )
    resume_from, reconciled, terminal_status = _resume_point(
        paper_repo,
        run_id=run_id,
        job_type=job_type,
        account_id=account_id,
    )
    if terminal_status == RunStatus.COMPLETED:
        return RecoveryResult(
            run_id=run_id,
            job_type=job_type,
            status=RunStatus.COMPLETED,
            reconciled_steps=reconciled,
        )
    if terminal_status in {RunStatus.DATA_ERROR, RunStatus.FAILED, RunStatus.RUNNING}:
        return RecoveryResult(
            run_id=run_id,
            job_type=job_type,
            status=terminal_status,
            reconciled_steps=reconciled,
        )
    if resume_from is None:
        return RecoveryResult(
            run_id=run_id,
            job_type=job_type,
            status=RunStatus.COMPLETED,
            reconciled_steps=reconciled,
        )

    if job_type == "open":
        job_result = run_open_job(
            paper_repo,
            account_id=account_id,
            trade_date=trade_date,
            config=config,
            universe_hash=universe_hash,
            market_repo=market_repo,
            sync=sync,
            open_snapshots=open_snapshots,
            execution_time=execution_time,
            owner_id=owner_id,
            strategy_version=strategy_version,
            resume_from=resume_from,
        )
    else:
        if market_repo is None or sync is None or universe_request is None:
            return RecoveryResult(
                run_id=run_id,
                job_type=job_type,
                status=RunStatus.FAILED,
                reconciled_steps=reconciled,
            )
        job_result = run_paper_after_close_job(
            paper_repo,
            account_id=account_id,
            trade_date=trade_date,
            config=config,
            universe_hash=universe_hash,
            market_repo=market_repo,
            sync=sync,
            universe_request=universe_request,
            fixture=fixture,
            force=force,
            owner_id=owner_id,
            strategy_version=strategy_version,
            resume_from=resume_from,
            paths=paths,
        )

    return RecoveryResult(
        run_id=run_id,
        job_type=job_type,
        status=job_result.status,
        resumed_from=resume_from,
        reconciled_steps=reconciled,
        job_result=job_result,
    )
