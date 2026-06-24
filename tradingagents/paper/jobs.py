"""Opening and after-close orchestrators for Stage 6A paper operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import CorporateActionRecord, MarketOpenSnapshot, QuoteStatus
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tradingagents.paper.contracts import RunStatus, StepStatus
from tradingagents.paper.corporate_actions import CorporateActionProcessor
from tradingagents.paper.exceptions import PaperError
from tradingagents.paper.execution import PaperExecutionEngine
from tradingagents.paper.planner import RebalancePlanner
from tradingagents.paper.reporting import PaperReportWriter, build_report_run_from_rebalance
from tradingagents.paper.repository import PaperRepository, RunStepWriteSpec
from tradingagents.paper.screening import STRATEGY_VERSION
from tradingagents.paper.valuation import MarkToMarketService, ValuationStatus
from tradingagents.scheduler.jobs import config_hash, is_trading_day, run_after_close
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.universe_resolver import UniverseRequest

SHANGHAI = ZoneInfo("Asia/Shanghai")

OPEN_JOB_STEPS = [
    "calendar_gate",
    "apply_effective_corporate_actions",
    "sync_market_open_snapshots",
    "load_pending_orders",
    "market_open_quality_gate",
    "execute_pending_orders",
    "persist_execution_report",
]

AFTER_CLOSE_JOB_STEPS = [
    "calendar_gate",
    "sync_market_data",
    "quality_gate",
    "reconcile_corporate_actions",
    "mark_to_market",
    "freeze_dataset_versions",
    "run_screening",
    "persist_frozen_screen_run",
    "create_rebalance_plan",
    "generate_reports",
    "finalize_run",
]

MONEY_IMPACT_STEPS = frozenset(
    {
        "apply_effective_corporate_actions",
        "execute_pending_orders",
        "reconcile_corporate_actions",
        "mark_to_market",
        "create_rebalance_plan",
    }
)


class StepCrashSimulation(PaperError):
    """Simulated crash after a step committed business state."""


@dataclass
class StepExecutionResult:
    status: StepStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


@dataclass
class PaperJobResult:
    run_id: str
    job_type: str
    status: RunStatus
    step_names: list[str]
    steps: dict[str, StepStatus]
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_REJECTIONS}:
            return 0
        if self.status == RunStatus.BLOCKED:
            return 2
        return 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "step_names": self.step_names,
            "steps": {name: status.value for name, status in self.steps.items()},
            "errors": self.errors,
            "exit_code": self.exit_code,
        }


@dataclass
class JobContext:
    paper_repo: PaperRepository
    run_id: str
    job_type: str
    account_id: str
    trade_date: date
    config: ScreenerConfig
    universe_hash: str
    strategy_version: str
    owner_id: str
    market_repo: MarketDataRepository | None = None
    sync: MarketDataSync | None = None
    universe_request: UniverseRequest | None = None
    open_snapshots: dict[str, MarketOpenSnapshot] | None = None
    execution_time: datetime | None = None
    crash_after_steps: set[str] = field(default_factory=set)
    resume_from: str | None = None
    fencing_token: int | None = None
    rebalance_run_id: str | None = None
    screen_run_id: str | None = None
    after_close_report: dict[str, Any] | None = None
    skip_remaining: bool = False


StepHandler = Callable[[JobContext], StepExecutionResult]


def build_paper_run_id(
    job_type: str,
    account_id: str,
    trade_date: date,
    universe_hash: str,
    config: ScreenerConfig,
    *,
    strategy_version: str = STRATEGY_VERSION,
) -> str:
    cfg_hash = config_hash(config)
    return (
        f"{job_type}:{account_id}:{trade_date.isoformat()}:"
        f"{universe_hash[:12]}:{cfg_hash[:12]}:{strategy_version}"
    )


def _step_input_hash(ctx: JobContext, step_name: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "run_id": ctx.run_id,
            "step_name": step_name,
            "account_id": ctx.account_id,
            "trade_date": ctx.trade_date.isoformat(),
            "payload": payload,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stable_error(code: str, *, detail: str | None = None, retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code,
        "detail": detail,
        "retryable": retryable,
    }


def _rows_to_open_snapshots(rows: list[dict[str, Any]]) -> dict[str, MarketOpenSnapshot]:
    snapshots: dict[str, MarketOpenSnapshot] = {}
    for row in rows:
        snapshot = MarketOpenSnapshot.model_validate(row)
        existing = snapshots.get(snapshot.symbol)
        if existing is None or snapshot.observed_at >= existing.observed_at:
            snapshots[snapshot.symbol] = snapshot
    return snapshots


def _open_execution_time(ctx: JobContext) -> datetime:
    if ctx.execution_time is not None:
        return ctx.execution_time
    return datetime(
        ctx.trade_date.year,
        ctx.trade_date.month,
        ctx.trade_date.day,
        9,
        35,
        tzinfo=SHANGHAI,
    )


def _step_calendar_gate(ctx: JobContext) -> StepExecutionResult:
    if ctx.market_repo is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no market repository"},
        )
    if not is_trading_day(ctx.market_repo, ctx.trade_date):
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            error=_stable_error(
                "NON_TRADING_DAY",
                detail=f"{ctx.trade_date.isoformat()} is not a trading day",
            ),
        )
    return StepExecutionResult(status=StepStatus.SUCCESS, output={"trading_day": True})


def _corporate_action_from_row(row: dict[str, Any]) -> CorporateActionRecord:
    return CorporateActionRecord.model_validate(row)


def _apply_corporate_actions_for_date(
    ctx: JobContext,
    *,
    effective_on: date,
    pay_only: bool,
) -> StepExecutionResult:
    if ctx.market_repo is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"applied": [], "skipped": True},
        )
    snapshot = ctx.paper_repo.load_account_snapshot(ctx.account_id, as_of_date=effective_on)
    symbols = sorted(snapshot.positions)
    if not symbols:
        return StepExecutionResult(status=StepStatus.SUCCESS, output={"applied": []})

    observed_at = _open_execution_time(ctx)
    rows = ctx.market_repo.get_corporate_actions(
        symbols,
        end=effective_on,
        available_before=observed_at,
    )
    processor = CorporateActionProcessor(
        ctx.paper_repo,
        account_id=ctx.account_id,
        owner_id=ctx.owner_id,
    )
    applied: list[str] = []
    manual: list[str] = []
    for row in rows:
        action = _corporate_action_from_row(row)
        if pay_only:
            if action.pay_date != effective_on:
                continue
        elif action.ex_date == effective_on:
            pass
        elif action.pay_date == effective_on:
            pass
        else:
            continue
        result = processor.apply(action)
        applied.append(result.corporate_action_id)
        if result.status.value == "NEEDS_MANUAL_ACTION":
            manual.append(result.corporate_action_id)
    if manual:
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            output={"applied": applied, "manual": manual},
            error=_stable_error(
                "CORPORATE_ACTION_MANUAL",
                detail=f"manual actions required: {', '.join(manual)}",
            ),
        )
    return StepExecutionResult(status=StepStatus.SUCCESS, output={"applied": applied})


def _step_apply_effective_corporate_actions(ctx: JobContext) -> StepExecutionResult:
    return _apply_corporate_actions_for_date(ctx, effective_on=ctx.trade_date, pay_only=False)


def _step_sync_market_open_snapshots(ctx: JobContext) -> StepExecutionResult:
    if ctx.open_snapshots:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={
                "mode": "injected",
                "symbols": sorted(ctx.open_snapshots),
            },
        )
    rebalance_run_id = ctx.rebalance_run_id or ctx.paper_repo.find_active_rebalance_for_execution(
        ctx.account_id,
        ctx.trade_date,
    )
    ctx.rebalance_run_id = rebalance_run_id
    if ctx.sync is None:
        return StepExecutionResult(
            status=StepStatus.BLOCKED,
            error=_stable_error(
                "SYNC_UNAVAILABLE",
                detail="market sync is not configured",
                retryable=True,
            ),
        )
    if rebalance_run_id is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"symbols": [], "skipped": True, "reason": "no pending rebalance"},
        )
    orders = ctx.paper_repo.list_pending_orders_for_rebalance(rebalance_run_id)
    symbols = sorted({order.symbol for order in orders})
    if not symbols:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"symbols": [], "skipped": True},
        )
    result = ctx.sync.sync_market_open_snapshots(
        symbols,
        ctx.trade_date,
        _open_execution_time(ctx),
    )
    if result.status == SyncStatus.PUBLISHED:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={
                "dataset": result.dataset,
                "version_id": result.version_id,
                "symbols": symbols,
            },
        )
    if result.status == SyncStatus.BLOCKED:
        return StepExecutionResult(
            status=StepStatus.BLOCKED,
            error=_stable_error(
                "OPEN_SNAPSHOT_BLOCKED",
                detail="; ".join(result.errors or [result.status.value]),
                retryable=True,
            ),
        )
    return StepExecutionResult(
        status=StepStatus.FAILED,
        error=_stable_error(
            "OPEN_SNAPSHOT_SYNC_FAILED",
            detail="; ".join(result.errors or [result.status.value]),
        ),
    )


def _step_load_pending_orders(ctx: JobContext) -> StepExecutionResult:
    rebalance_run_id = ctx.paper_repo.find_active_rebalance_for_execution(
        ctx.account_id,
        ctx.trade_date,
    )
    ctx.rebalance_run_id = rebalance_run_id
    if rebalance_run_id is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"rebalance_run_id": None, "pending_orders": 0},
        )
    pending = ctx.paper_repo.list_pending_orders_for_rebalance(rebalance_run_id)
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={
            "rebalance_run_id": rebalance_run_id,
            "pending_orders": len(pending),
            "symbols": sorted({order.symbol for order in pending}),
        },
    )


def _resolve_open_snapshots(ctx: JobContext) -> dict[str, MarketOpenSnapshot]:
    if ctx.open_snapshots:
        return ctx.open_snapshots
    if ctx.market_repo is None or ctx.rebalance_run_id is None:
        return {}
    load_step = ctx.paper_repo.get_run_step(ctx.run_id, "load_pending_orders")
    symbols: list[str] = []
    if load_step and load_step.output_json:
        symbols = json.loads(load_step.output_json).get("symbols") or []
    if not symbols:
        return {}
    rows = ctx.market_repo.get_market_open_snapshots(
        symbols,
        ctx.trade_date,
        _open_execution_time(ctx),
    )
    return _rows_to_open_snapshots(rows)


def _step_market_open_quality_gate(ctx: JobContext) -> StepExecutionResult:
    if ctx.rebalance_run_id is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no pending rebalance"},
        )
    pending = ctx.paper_repo.list_pending_orders_for_rebalance(ctx.rebalance_run_id)
    if not pending:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no pending orders"},
        )
    snapshots = _resolve_open_snapshots(ctx)
    missing = sorted({order.symbol for order in pending if order.symbol not in snapshots})
    if missing:
        sync_step = ctx.paper_repo.get_run_step(ctx.run_id, "sync_market_open_snapshots")
        if sync_step and sync_step.status == StepStatus.BLOCKED:
            return StepExecutionResult(
                status=StepStatus.BLOCKED,
                error=_stable_error(
                    "MISSING_OPEN_SNAPSHOT",
                    detail=f"missing snapshots for {', '.join(missing)}",
                    retryable=True,
                ),
            )
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            error=_stable_error(
                "MISSING_OPEN_SNAPSHOT",
                detail=f"missing snapshots for {', '.join(missing)}",
            ),
        )
    invalid = [
        symbol
        for symbol, snap in snapshots.items()
        if snap.quote_status == QuoteStatus.UNKNOWN
    ]
    if invalid:
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            error=_stable_error(
                "INVALID_OPEN_SNAPSHOT",
                detail=f"unknown quote status for {', '.join(sorted(invalid))}",
            ),
        )
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={"symbols": sorted(snapshots), "validated_orders": len(pending)},
    )


def _step_execute_pending_orders(ctx: JobContext) -> StepExecutionResult:
    if ctx.rebalance_run_id is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no pending rebalance"},
        )
    pending = ctx.paper_repo.list_pending_orders_for_rebalance(ctx.rebalance_run_id)
    if not pending:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no pending orders"},
        )
    snapshots = _resolve_open_snapshots(ctx)
    if not snapshots:
        sync_step = ctx.paper_repo.get_run_step(ctx.run_id, "sync_market_open_snapshots")
        if sync_step and sync_step.status == StepStatus.BLOCKED:
            return StepExecutionResult(
                status=StepStatus.BLOCKED,
                error=_stable_error(
                    "EXECUTE_BLOCKED",
                    detail="open snapshots unavailable",
                    retryable=True,
                ),
            )
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            error=_stable_error(
                "MISSING_OPEN_SNAPSHOT",
                detail="cannot execute without open snapshots",
            ),
        )
    if ctx.fencing_token is None:
        lease = ctx.paper_repo.acquire_account_lease(
            ctx.account_id,
            owner_id=ctx.owner_id,
        )
        ctx.fencing_token = lease.token
    engine = PaperExecutionEngine()
    result = engine.execute_rebalance(
        ctx.paper_repo,
        rebalance_run_id=ctx.rebalance_run_id,
        execution_date=ctx.trade_date,
        execution_time=_open_execution_time(ctx),
        fencing_token=ctx.fencing_token,
        owner_id=ctx.owner_id,
        snapshots=snapshots,
    )
    rejections = ctx.paper_repo.list_orders_for_rebalance(ctx.rebalance_run_id)
    rejected_count = sum(1 for order in rejections if order.status.value == "REJECTED")
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={
            "rebalance_run_id": ctx.rebalance_run_id,
            "fill_ids": result.fill_ids,
            "fill_count": len(result.fill_ids),
            "rejected_count": rejected_count,
        },
    )


def _step_persist_execution_report(ctx: JobContext) -> StepExecutionResult:
    execute_step = ctx.paper_repo.get_run_step(ctx.run_id, "execute_pending_orders")
    payload = {"run_id": ctx.run_id, "trade_date": ctx.trade_date.isoformat()}
    if execute_step and execute_step.output_json:
        payload["execution"] = json.loads(execute_step.output_json)
    return StepExecutionResult(status=StepStatus.SUCCESS, output=payload)


OPEN_STEP_HANDLERS: dict[str, StepHandler] = {
    "calendar_gate": _step_calendar_gate,
    "apply_effective_corporate_actions": _step_apply_effective_corporate_actions,
    "sync_market_open_snapshots": _step_sync_market_open_snapshots,
    "load_pending_orders": _step_load_pending_orders,
    "market_open_quality_gate": _step_market_open_quality_gate,
    "execute_pending_orders": _step_execute_pending_orders,
    "persist_execution_report": _step_persist_execution_report,
}


def _step_sync_market_data(ctx: JobContext) -> StepExecutionResult:
    if ctx.after_close_report is not None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"sync_steps": ctx.after_close_report.get("sync_steps", {})},
        )
    return StepExecutionResult(
        status=StepStatus.BLOCKED,
        error=_stable_error("SYNC_NOT_RUN", detail="after-close sync missing", retryable=True),
    )


def _step_quality_gate(ctx: JobContext) -> StepExecutionResult:
    if ctx.after_close_report is None:
        return StepExecutionResult(
            status=StepStatus.BLOCKED,
            error=_stable_error("QUALITY_GATE_BLOCKED", retryable=True),
        )
    errors = ctx.after_close_report.get("errors") or []
    if errors and ctx.after_close_report.get("status") == "error":
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            error=_stable_error("SCREENING_DATA_ERROR", detail="; ".join(errors)),
        )
    return StepExecutionResult(status=StepStatus.SUCCESS, output={"status": "ok"})


def _step_reconcile_corporate_actions(ctx: JobContext) -> StepExecutionResult:
    return _apply_corporate_actions_for_date(
        ctx,
        effective_on=ctx.trade_date,
        pay_only=True,
    )


def _step_mark_to_market(ctx: JobContext) -> StepExecutionResult:
    if ctx.market_repo is None:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True},
        )
    if ctx.fencing_token is None:
        lease = ctx.paper_repo.acquire_account_lease(
            ctx.account_id,
            owner_id=ctx.owner_id,
        )
        ctx.fencing_token = lease.token
    service = MarkToMarketService(ctx.paper_repo, ctx.market_repo)
    result = service.value_account(
        ctx.account_id,
        valuation_date=ctx.trade_date,
        available_before=datetime(
            ctx.trade_date.year,
            ctx.trade_date.month,
            ctx.trade_date.day,
            16,
            0,
            tzinfo=SHANGHAI,
        ),
        run_id=ctx.run_id,
        fencing_token=ctx.fencing_token,
        owner_id=ctx.owner_id,
    )
    if result.status == ValuationStatus.DATA_ERROR:
        return StepExecutionResult(
            status=StepStatus.DATA_ERROR,
            error=_stable_error("VALUATION_DATA_ERROR"),
        )
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={
            "valuation_date": result.nav.valuation_date.isoformat(),
            "total_equity_cny": str(result.nav.total_equity_cny),
        },
    )


def _step_freeze_dataset_versions(ctx: JobContext) -> StepExecutionResult:
    if ctx.screen_run_id is None:
        return StepExecutionResult(status=StepStatus.SUCCESS, output={"skipped": True})
    frozen = ctx.paper_repo.get_frozen_screen_run(ctx.screen_run_id)
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={
            "screen_run_id": ctx.screen_run_id,
            "dataset_versions": json.loads(frozen.dataset_versions_json),
        },
    )


def _step_run_screening(ctx: JobContext) -> StepExecutionResult:
    if ctx.after_close_report is None:
        return StepExecutionResult(
            status=StepStatus.FAILED,
            error=_stable_error("SCREENING_NOT_RUN"),
        )
    report = ctx.after_close_report.get("report") or {}
    ctx.screen_run_id = report.get("run_id")
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={"screen_run_id": ctx.screen_run_id, "status": report.get("status")},
    )


def _step_persist_frozen_screen_run(ctx: JobContext) -> StepExecutionResult:
    if ctx.screen_run_id is None:
        return StepExecutionResult(status=StepStatus.SUCCESS, output={"skipped": True})
    frozen = ctx.paper_repo.get_frozen_screen_run(ctx.screen_run_id)
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={"screen_run_id": frozen.screen_run_id, "status": frozen.status},
    )


def _step_create_rebalance_plan(ctx: JobContext) -> StepExecutionResult:
    if ctx.screen_run_id is None:
        return StepExecutionResult(status=StepStatus.SUCCESS, output={"skipped": True})
    if ctx.market_repo is None:
        return StepExecutionResult(
            status=StepStatus.BLOCKED,
            error=_stable_error("PLANNER_BLOCKED", detail="market repository required", retryable=True),
        )
    planner = RebalancePlanner(ctx.paper_repo, market_repo=ctx.market_repo)
    plan = planner.plan(
        ctx.account_id,
        ctx.screen_run_id,
        config=ctx.config,
        universe_hash=ctx.universe_hash,
        owner_id=ctx.owner_id,
    )
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={
            "rebalance_run_id": plan.rebalance_run_id,
            "order_count": len(plan.orders),
            "execution_date": plan.execution_date.isoformat(),
        },
    )


def _step_generate_reports(ctx: JobContext) -> StepExecutionResult:
    plan_step = ctx.paper_repo.get_run_step(ctx.run_id, "create_rebalance_plan")
    if plan_step is None or not plan_step.output_json:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no rebalance plan"},
        )
    plan_output = json.loads(plan_step.output_json)
    rebalance_run_id = plan_output.get("rebalance_run_id")
    if not rebalance_run_id:
        return StepExecutionResult(
            status=StepStatus.SUCCESS,
            output={"skipped": True, "reason": "no rebalance_run_id"},
        )
    step_statuses = {
        step.step_name: step.status.value
        for step in ctx.paper_repo.list_run_steps(ctx.run_id)
    }
    degradation_notes: list[str] = []
    sync_step = ctx.paper_repo.get_run_step(ctx.run_id, "sync_market_data")
    if sync_step and sync_step.output_json:
        sync_output = json.loads(sync_step.output_json)
        for key, value in (sync_output.get("sync_steps") or {}).items():
            if "degraded" in key or value in {"error", "blocked"}:
                degradation_notes.append(f"{key}={value}")
    run = build_report_run_from_rebalance(
        ctx.paper_repo,
        rebalance_run_id,
        run_status=ctx.after_close_report.get("status") if ctx.after_close_report else None,
        step_statuses=step_statuses,
        degradation_notes=degradation_notes,
    )
    writer = PaperReportWriter(ctx.paper_repo.paths.home_dir)
    manifest_path = writer.write(run, paper_repo=ctx.paper_repo)
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={
            "manifest_path": str(manifest_path),
            "revision": run.revision,
        },
    )


def _step_finalize_run(ctx: JobContext) -> StepExecutionResult:
    return StepExecutionResult(
        status=StepStatus.SUCCESS,
        output={"run_id": ctx.run_id, "trade_date": ctx.trade_date.isoformat()},
    )


AFTER_CLOSE_STEP_HANDLERS: dict[str, StepHandler] = {
    "calendar_gate": _step_calendar_gate,
    "sync_market_data": _step_sync_market_data,
    "quality_gate": _step_quality_gate,
    "reconcile_corporate_actions": _step_reconcile_corporate_actions,
    "mark_to_market": _step_mark_to_market,
    "freeze_dataset_versions": _step_freeze_dataset_versions,
    "run_screening": _step_run_screening,
    "persist_frozen_screen_run": _step_persist_frozen_screen_run,
    "create_rebalance_plan": _step_create_rebalance_plan,
    "generate_reports": _step_generate_reports,
    "finalize_run": _step_finalize_run,
}


def _aggregate_run_status(step_results: dict[str, StepStatus], execute_output: dict | None) -> RunStatus:
    statuses = list(step_results.values())
    if any(status == StepStatus.DATA_ERROR for status in statuses):
        return RunStatus.DATA_ERROR
    if any(status == StepStatus.FAILED for status in statuses):
        return RunStatus.FAILED
    if any(status == StepStatus.BLOCKED for status in statuses):
        return RunStatus.BLOCKED
    rejected = int((execute_output or {}).get("rejected_count") or 0)
    if rejected > 0:
        return RunStatus.COMPLETED_WITH_REJECTIONS
    return RunStatus.COMPLETED


def _should_run_step(ctx: JobContext, step_name: str, step_order: list[str]) -> bool:
    if ctx.resume_from is None:
        return True
    resume_index = step_order.index(ctx.resume_from)
    return step_order.index(step_name) >= resume_index


def _execute_step(
    ctx: JobContext,
    step_name: str,
    handler: StepHandler,
    *,
    step_order: list[str],
) -> StepStatus:
    if not _should_run_step(ctx, step_name, step_order):
        existing = ctx.paper_repo.get_run_step(ctx.run_id, step_name)
        return existing.status if existing is not None else StepStatus.SUCCESS

    existing = ctx.paper_repo.get_run_step(ctx.run_id, step_name)
    if existing is not None and existing.status == StepStatus.SUCCESS:
        if step_name == "load_pending_orders" and existing.output_json:
            payload = json.loads(existing.output_json)
            ctx.rebalance_run_id = payload.get("rebalance_run_id")
        return StepStatus.SUCCESS

    input_payload = {
        "account_id": ctx.account_id,
        "trade_date": ctx.trade_date.isoformat(),
        "rebalance_run_id": ctx.rebalance_run_id,
        "screen_run_id": ctx.screen_run_id,
    }
    input_hash = _step_input_hash(ctx, step_name, input_payload)
    if existing is not None and existing.status == StepStatus.DATA_ERROR:
        if existing.input_hash == input_hash:
            return StepStatus.DATA_ERROR

    started_at = datetime.now(tz=SHANGHAI)
    ctx.paper_repo.save_run_step(
        RunStepWriteSpec(
            run_id=ctx.run_id,
            step_name=step_name,
            status=StepStatus.RUNNING,
            input_hash=input_hash,
            started_at=started_at,
        )
    )
    try:
        result = handler(ctx)
    except Exception as exc:
        ctx.paper_repo.save_run_step(
            RunStepWriteSpec(
                run_id=ctx.run_id,
                step_name=step_name,
                status=StepStatus.FAILED,
                input_hash=input_hash,
                error_json=json.dumps(
                    _stable_error("STEP_EXCEPTION", detail=str(exc)),
                    ensure_ascii=False,
                ),
                started_at=started_at,
            )
        )
        return StepStatus.FAILED

    if ctx.crash_after_steps and step_name in ctx.crash_after_steps:
        ctx.paper_repo.save_run_step(
            RunStepWriteSpec(
                run_id=ctx.run_id,
                step_name=step_name,
                status=StepStatus.RUNNING,
                input_hash=input_hash,
                output_json=json.dumps(result.output, default=str),
                started_at=started_at,
            )
        )
        raise StepCrashSimulation(step_name)

    ctx.paper_repo.save_run_step(
        RunStepWriteSpec(
            run_id=ctx.run_id,
            step_name=step_name,
            status=result.status,
            input_hash=input_hash,
            output_json=json.dumps(result.output, default=str) if result.output else None,
            error_json=json.dumps(result.error, ensure_ascii=False) if result.error else None,
            started_at=started_at,
        )
    )
    return result.status


def _run_job(
    ctx: JobContext,
    *,
    job_type: str,
    step_order: list[str],
    handlers: dict[str, StepHandler],
) -> PaperJobResult:
    step_results: dict[str, StepStatus] = {}
    errors: list[str] = []
    execute_output: dict[str, Any] | None = None

    for step_name in step_order:
        handler = handlers[step_name]
        try:
            status = _execute_step(ctx, step_name, handler, step_order=step_order)
        except StepCrashSimulation as exc:
            step_results[step_name] = StepStatus.RUNNING
            errors.append(f"simulated crash after {exc.args[0]}")
            break
        step_results[step_name] = status
        step_record = ctx.paper_repo.get_run_step(ctx.run_id, step_name)
        if step_record and step_record.error_json:
            payload = json.loads(step_record.error_json)
            if payload.get("detail"):
                errors.append(str(payload["detail"]))
        if step_name == "execute_pending_orders" and step_record and step_record.output_json:
            execute_output = json.loads(step_record.output_json)

    if StepStatus.RUNNING in step_results.values():
        overall = RunStatus.RUNNING
    else:
        overall = _aggregate_run_status(step_results, execute_output)
    return PaperJobResult(
        run_id=ctx.run_id,
        job_type=job_type,
        status=overall,
        step_names=step_order,
        steps=step_results,
        errors=errors,
    )


def run_open_job(
    paper_repo: PaperRepository,
    *,
    account_id: str,
    trade_date: date,
    config: ScreenerConfig,
    universe_hash: str,
    market_repo: MarketDataRepository | None = None,
    sync: MarketDataSync | None = None,
    open_snapshots: dict[str, MarketOpenSnapshot] | None = None,
    execution_time: datetime | None = None,
    owner_id: str = "open-job",
    strategy_version: str = STRATEGY_VERSION,
    crash_after_steps: set[str] | None = None,
    resume_from: str | None = None,
) -> PaperJobResult:
    run_id = build_paper_run_id(
        "open",
        account_id,
        trade_date,
        universe_hash,
        config,
        strategy_version=strategy_version,
    )
    ctx = JobContext(
        paper_repo=paper_repo,
        run_id=run_id,
        job_type="open",
        account_id=account_id,
        trade_date=trade_date,
        config=config,
        universe_hash=universe_hash,
        strategy_version=strategy_version,
        owner_id=owner_id,
        market_repo=market_repo,
        sync=sync,
        open_snapshots=open_snapshots,
        execution_time=execution_time,
        crash_after_steps=crash_after_steps or set(),
        resume_from=resume_from,
    )
    return _run_job(
        ctx,
        job_type="open",
        step_order=OPEN_JOB_STEPS,
        handlers=OPEN_STEP_HANDLERS,
    )


def run_paper_after_close_job(
    paper_repo: PaperRepository,
    *,
    account_id: str,
    trade_date: date,
    config: ScreenerConfig,
    universe_hash: str,
    market_repo: MarketDataRepository,
    sync: MarketDataSync,
    universe_request: UniverseRequest,
    fixture: dict | None = None,
    force: bool = False,
    owner_id: str = "after-close-job",
    strategy_version: str = STRATEGY_VERSION,
    resume_from: str | None = None,
    paths: Any | None = None,
) -> PaperJobResult:
    from tradingagents.market_data.config import MarketDataPaths

    run_id = build_paper_run_id(
        "after_close",
        account_id,
        trade_date,
        universe_hash,
        config,
        strategy_version=strategy_version,
    )
    data_paths = paths or MarketDataPaths(home_dir=config.home_dir)
    after_close = run_after_close(
        trade_date,
        config,
        data_paths,
        sync,
        universe_request=universe_request,
        fixture=fixture,
        force=force,
        paper_repo=paper_repo,
    )
    ctx = JobContext(
        paper_repo=paper_repo,
        run_id=run_id,
        job_type="after_close",
        account_id=account_id,
        trade_date=trade_date,
        config=config,
        universe_hash=universe_hash,
        strategy_version=strategy_version,
        owner_id=owner_id,
        market_repo=market_repo,
        sync=sync,
        universe_request=universe_request,
        resume_from=resume_from,
        after_close_report={
            "status": after_close.status,
            "errors": after_close.errors,
            "sync_steps": after_close.sync_steps,
            "report": after_close.report.to_output_dict() if after_close.report else None,
        },
    )
    return _run_job(
        ctx,
        job_type="after_close",
        step_order=AFTER_CLOSE_JOB_STEPS,
        handlers=AFTER_CLOSE_STEP_HANDLERS,
    )
