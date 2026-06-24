"""Frozen screening adapter for Stage 6A paper operations."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from tradingagents.market_data.market_hours import ensure_aware_shanghai, post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.contracts import FrozenScreenRun, TargetPortfolioMode, money
from tradingagents.paper.exceptions import ScreeningInputError
from tradingagents.paper.repository import PaperRepository, RunInputCapture
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.live import (
    bar_history_trading_dates,
    resolve_signal_trade_date,
    run_repository_screen,
)
from tradingagents.screener.pipeline import run_screen
from tradingagents.screener.report import RunReport, ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseResolver

STRATEGY_VERSION = "v1"


def screen_content_hash(report: RunReport) -> str:
    payload = report.model_dump(mode="json")
    payload["status"] = report.status.value
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_frozen_screen_run(
    report: RunReport,
    *,
    target_mode: TargetPortfolioMode,
) -> FrozenScreenRun:
    return FrozenScreenRun(
        screen_run_id=report.run_id,
        screen_content_hash=screen_content_hash(report),
        status=report.status.value,
        signal_time=report.signal_time,
        target_portfolio_mode=target_mode,
        target_weights_json=json.dumps(report.target_weights, sort_keys=True),
        cash_weight=money(report.cash_weight),
        dataset_versions_json=json.dumps(report.dataset_versions, sort_keys=True, default=str),
        event_dataset_versions_json=json.dumps(
            report.event_dataset_versions,
            sort_keys=True,
            default=str,
        ),
        run_report_json=report.model_dump_json(),
    )


def _row_content_hash(row: dict[str, Any]) -> str:
    encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _capture_row(
    *,
    run_id: str,
    input_type: str,
    scope_key: str,
    row: dict[str, Any],
    source_dataset_version_id: str | None = None,
    source_available_at: datetime | None = None,
) -> RunInputCapture:
    return RunInputCapture(
        run_id=run_id,
        input_type=input_type,
        scope_key=scope_key,
        row_content_hash=_row_content_hash(row),
        row_json=json.dumps(row, sort_keys=True, ensure_ascii=False, default=str),
        source_dataset_version_id=source_dataset_version_id,
        source_available_at=source_available_at,
    )


def _parse_available_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_aware_shanghai(value)
    text = str(value)
    if not text:
        return None
    return ensure_aware_shanghai(datetime.fromisoformat(text))


def capture_screening_inputs(
    market_repo: MarketDataRepository,
    *,
    run_id: str,
    symbols: list[str],
    trading_dates: list[date],
    signal_time: datetime,
) -> list[RunInputCapture]:
    if not symbols:
        return []

    captures: list[RunInputCapture] = []
    signal_time = ensure_aware_shanghai(signal_time)
    securities = {
        record.symbol: record
        for record in market_repo.get_effective_securities(signal_time.date(), signal_time)
        if record.symbol in set(symbols)
    }
    for symbol, record in sorted(securities.items()):
        row = {
            "symbol": record.symbol,
            "list_date": record.list_date.isoformat(),
            "board": record.board,
        }
        captures.append(
            _capture_row(
                run_id=run_id,
                input_type="SECURITY",
                scope_key=symbol,
                row=row,
            )
        )

    for trade_date in trading_dates:
        if post_close_signal_time(trade_date) > signal_time:
            continue
        for bar in market_repo.get_daily_bars(
            symbols,
            start=trade_date,
            end=trade_date,
            available_before=signal_time,
        ):
            trade_key = (
                bar["trade_date"].isoformat()
                if hasattr(bar["trade_date"], "isoformat")
                else str(bar["trade_date"])
            )
            row = {
                "symbol": bar["symbol"],
                "trade_date": trade_key,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
                "amount": bar.get("amount"),
            }
            captures.append(
                _capture_row(
                    run_id=run_id,
                    input_type="DAILY_BAR",
                    scope_key=f"{bar['symbol']}:{trade_key}",
                    row=row,
                    source_available_at=_parse_available_at(bar.get("available_at")),
                )
            )

    for fin in market_repo.get_financials(symbols, available_before=signal_time):
        report_period = (
            fin["report_period"].isoformat()
            if hasattr(fin["report_period"], "isoformat")
            else str(fin["report_period"])
        )
        row = {
            "symbol": fin["symbol"],
            "report_period": report_period,
            "roe": fin.get("roe"),
            "operating_cashflow": fin.get("operating_cashflow"),
            "net_profit": fin.get("net_profit"),
            "debt_ratio": fin.get("debt_ratio"),
            "announcement_date": (
                fin["announcement_date"].isoformat()
                if hasattr(fin.get("announcement_date"), "isoformat")
                else fin.get("announcement_date")
            ),
        }
        captures.append(
            _capture_row(
                run_id=run_id,
                input_type="FINANCIAL",
                scope_key=f"{fin['symbol']}:{report_period}",
                row=row,
                source_available_at=_parse_available_at(fin.get("available_at")),
            )
        )

    signal_trade_dates = [
        trade_date
        for trade_date in trading_dates
        if post_close_signal_time(trade_date) <= signal_time
    ]
    indicator_trade_date = (
        signal_trade_dates[-1] if signal_trade_dates else trading_dates[-1]
    )
    for indicator in market_repo.get_daily_indicators(
        symbols,
        indicator_trade_date,
        signal_time,
    ):
        trade_key = (
            indicator["trade_date"].isoformat()
            if hasattr(indicator["trade_date"], "isoformat")
            else str(indicator["trade_date"])
        )
        row = {
            "symbol": indicator["symbol"],
            "trade_date": trade_key,
            "pe_ttm": indicator.get("pe_ttm"),
            "pb": indicator.get("pb"),
            "turnover_pct": indicator.get("turnover_pct"),
            "total_market_cap_cny": indicator.get("total_market_cap_cny"),
            "float_market_cap_cny": indicator.get("float_market_cap_cny"),
            "source": indicator.get("source"),
        }
        captures.append(
            _capture_row(
                run_id=run_id,
                input_type="DAILY_INDICATOR",
                scope_key=f"{indicator['symbol']}:{trade_key}",
                row=row,
                source_available_at=_parse_available_at(indicator.get("available_at")),
            )
        )

    return captures


class ScreeningService:
    """Adapter that freezes screening inputs without changing ``run_screen()``."""

    def __init__(self, paper_repo: PaperRepository) -> None:
        self.paper_repo = paper_repo

    def run(
        self,
        market_repo: MarketDataRepository,
        config: ScreenerConfig,
        request: UniverseRequest,
        signal_time: datetime,
        *,
        db_path,
        run_id: str | None = None,
        target_mode: TargetPortfolioMode = TargetPortfolioMode.WEIGHTS,
        fixture: dict | None = None,
    ) -> FrozenScreenRun:
        as_of = signal_time.isoformat()
        trade_date, resolved_signal_time, errors = resolve_signal_trade_date(
            market_repo,
            as_of=as_of,
        )
        if errors:
            raise ScreeningInputError("; ".join(errors))
        assert trade_date is not None
        assert resolved_signal_time is not None

        request = request.model_copy(update={"as_of": resolved_signal_time})
        if fixture is not None:
            report = run_screen(
                fixture,
                config,
                db_path,
                reload=False,
                universe_request=request,
                run_id=run_id,
            )
        else:
            report = run_repository_screen(
                market_repo,
                config,
                db_path,
                request,
                trade_date=trade_date,
                signal_time=resolved_signal_time,
            )
            if run_id is not None:
                report = report.model_copy(update={"run_id": run_id})

        symbols: list[str] = []
        if report.status == ScreeningStatus.OK:
            resolved = UniverseResolver(market_repo).resolve(request)
            if resolved.is_ok:
                symbols = list(resolved.symbols)
        trading_dates = bar_history_trading_dates(market_repo, trade_date, config)
        captured_inputs = capture_screening_inputs(
            market_repo,
            run_id=report.run_id,
            symbols=symbols,
            trading_dates=trading_dates,
            signal_time=resolved_signal_time,
        )
        frozen = build_frozen_screen_run(report, target_mode=target_mode)
        return self.paper_repo.freeze_screen_run(
            frozen,
            captured_inputs=captured_inputs,
        )
