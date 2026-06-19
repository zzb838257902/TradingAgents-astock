"""Local after-close scheduler jobs (no external queue)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync, SyncStatus
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.live import build_fixture_from_repository
from tradingagents.screener.pipeline import run_screen
from tradingagents.screener.report import RunReport, ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseResolver, UniverseType
from tradingagents.scheduler.state import JobKey, JobStateStore


@dataclass
class AfterCloseResult:
    job_key: JobKey
    status: str
    skipped: bool = False
    report: RunReport | None = None
    errors: list[str] = field(default_factory=list)
    sync_steps: dict[str, str] = field(default_factory=dict)


def config_hash(config: ScreenerConfig) -> str:
    return hashlib.sha256(config.model_dump_json().encode()).hexdigest()


def is_trading_day(repo: MarketDataRepository, trade_date: date) -> bool:
    open_dates = repo.list_open_trade_dates()
    if open_dates:
        return trade_date in open_dates
    return trade_date.weekday() < 5


def _recent_trading_dates(repo: MarketDataRepository, trade_date: date, count: int = 30) -> list[date]:
    dates = [day for day in repo.list_open_trade_dates() if day <= trade_date]
    if not dates:
        return [trade_date]
    return dates[-count:]


def run_after_close(
    trade_date: date,
    config: ScreenerConfig,
    paths: MarketDataPaths,
    sync: MarketDataSync,
    *,
    universe_request: UniverseRequest | None = None,
    fixture: dict | None = None,
    force: bool = False,
) -> AfterCloseResult:
    key = JobKey("after_close", trade_date, config_hash(config))
    store = JobStateStore(paths.home_dir / "scheduler")
    if not force and store.latest_success(key) is not None:
        cached = store.load_report(key)
        if cached:
            payload = {key_name: value for key_name, value in cached.items() if key_name != "sync_steps"}
            report = RunReport.model_validate(payload)
        else:
            report = None
        return AfterCloseResult(job_key=key, status="success", skipped=True, report=report)

    attempt_id = store.begin_attempt(key)
    repo = sync.repository
    signal_time = post_close_signal_time(trade_date)
    request = universe_request or UniverseRequest(
        universe_type=UniverseType.ALL,
        as_of=signal_time,
    )
    sync_steps: dict[str, str] = {}
    errors: list[str] = []

    try:
        if not is_trading_day(repo, trade_date):
            report = RunReport(
                run_id=key.storage_id(),
                status=ScreeningStatus.DATA_ERROR,
                signal_time=signal_time,
                data_as_of=signal_time,
                universe_type=request.universe_type.value,
                errors=[f"{trade_date.isoformat()} is not a trading day"],
            )
            path = store.save_report(key, report.to_output_dict())
            store.finish_attempt(key, attempt_id, "skipped", report_path=str(path))
            return AfterCloseResult(
                job_key=key,
                status="skipped",
                skipped=True,
                report=report,
                errors=report.errors,
            )

        probe = sync.probe_capabilities()
        sync_steps["capability_probe"] = probe.status.value
        if probe.status != SyncStatus.PUBLISHED:
            errors.extend(probe.errors or ["capability probe failed"])
            raise RuntimeError("; ".join(errors))

        security = sync.sync_security_master(trade_date)
        sync_steps["security_master"] = security.status.value
        if security.status != SyncStatus.PUBLISHED:
            errors.extend(security.errors or ["security_master sync failed"])
            raise RuntimeError("; ".join(errors))

        daily = sync.sync_daily(trade_date)
        sync_steps["daily_bars"] = daily.status.value
        if daily.status != SyncStatus.PUBLISHED:
            errors.extend(daily.errors or ["daily_bars sync failed"])
            raise RuntimeError("; ".join(errors))

        financials = sync.sync_financials(signal_time)
        sync_steps["financials"] = financials.status.value

        if fixture is None:
            resolved = UniverseResolver(repo).resolve(
                request.model_copy(update={"as_of": signal_time})
            )
            if not resolved.is_ok:
                report = RunReport(
                    run_id=key.storage_id(),
                    status=ScreeningStatus.DATA_ERROR,
                    signal_time=signal_time,
                    data_as_of=signal_time,
                    universe_type=request.universe_type.value,
                    universe_code=request.universe_code,
                    errors=resolved.errors,
                )
                path = store.save_report(key, report.to_output_dict())
                store.finish_attempt(
                    key, attempt_id, "error",
                    report_path=str(path),
                    errors=resolved.errors,
                )
                return AfterCloseResult(
                    job_key=key,
                    status="error",
                    report=report,
                    errors=resolved.errors,
                    sync_steps=sync_steps,
                )
            symbols = resolved.symbols
            trading_dates = _recent_trading_dates(repo, trade_date)
            fixture = build_fixture_from_repository(repo, symbols, trading_dates, signal_time)

        report = run_screen(
            fixture,
            config,
            paths.live_db_path,
            reload=False,
            universe_request=request,
            run_id=key.storage_id(),
        )
        payload = report.to_output_dict()
        payload["sync_steps"] = sync_steps
        path = store.save_report(key, payload)
        final_status = "success" if report.status != ScreeningStatus.DATA_ERROR else "error"
        store.finish_attempt(
            key,
            attempt_id,
            final_status,
            report_path=str(path),
            errors=report.errors or None,
        )
        return AfterCloseResult(
            job_key=key,
            status=final_status,
            report=report,
            errors=report.errors,
            sync_steps=sync_steps,
        )
    except Exception as exc:
        store.finish_attempt(key, attempt_id, "error", errors=[str(exc)])
        return AfterCloseResult(
            job_key=key,
            status="error",
            errors=[str(exc)],
            sync_steps=sync_steps,
        )


def load_fixture_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
