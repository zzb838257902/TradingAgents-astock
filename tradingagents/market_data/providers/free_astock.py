"""Default free A-share market data provider (mootdx + public HTTP)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Sequence

from tradingagents.market_data.contracts import (
    DataResult,
    DataStatus,
    Membership,
    MembershipMode,
    PITLevel,
    ProviderCapability,
    SecurityRecord,
    TradingDay,
)
from tradingagents.market_data.market_hours import SHANGHAI, ensure_aware_shanghai, post_close_signal_time
from tradingagents.market_data.adjustments import (
    baseline_factor_row,
    build_pit_rows_from_xdxr,
    ensure_factor_baseline,
    resolve_prev_close_from_bars,
)
from tradingagents.market_data.providers.existing_astock import ExistingAStockProvider
from tradingagents.market_data.providers.free_astock_sources import (
    FreeAStockSourceBackend,
    LiveFreeAStockSourceBackend,
)
from tradingagents.market_data.sync_policy import live_snapshot_date_error

_PROBE_SPECS: list[tuple[str, str, PITLevel, str]] = [
    ("security_master", "mootdx.stocks", PITLevel.PIT_REQUIRED, "public metadata"),
    ("trade_calendar", "sina.index_kline", PITLevel.PIT_REQUIRED, "SSE trading days"),
    ("daily_bars", "mootdx+sina", PITLevel.PIT_REQUIRED, "public quotes"),
    ("financials", "sina+mootdx", PITLevel.PIT_REQUIRED, "announcement_date required"),
    ("industry_members", "eastmoney.board", PITLevel.CURRENT_ONLY, "live snapshot only"),
    ("concept_members", "eastmoney.board", PITLevel.CURRENT_ONLY, "live snapshot only"),
    ("index_members", "eastmoney.board", PITLevel.CURRENT_ONLY, "live snapshot only"),
    ("adjustment_factors", "mootdx.xdxr", PITLevel.PIT_REQUIRED, "ex_date PIT via corporate actions"),
]


def _result(
    data,
    *,
    status: DataStatus,
    source: str,
    as_of: datetime,
    available_at: datetime,
    pit_level: PITLevel,
    errors: list[str] | None = None,
) -> DataResult:
    now = datetime.now(tz=SHANGHAI)
    return DataResult(
        data=data,
        status=status,
        source=source,
        as_of=as_of,
        available_at=available_at,
        ingested_at=now,
        run_time=now,
        pit_level=pit_level,
        errors=errors or [],
    )


def _earliest_bar_trade_date(bars: list[dict]) -> date | None:
    dates: list[date] = []
    for bar in bars:
        trade_date = bar.get("trade_date")
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        if isinstance(trade_date, date):
            dates.append(trade_date)
    return min(dates) if dates else None


class FreeAStockProvider:
    name = "free_astock"

    def __init__(self, backend: FreeAStockSourceBackend | None = None):
        self._backend = backend or LiveFreeAStockSourceBackend()
        self._daily_adapter = ExistingAStockProvider()

    def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
        run_time = datetime.now(tz=SHANGHAI)
        date_error = live_snapshot_date_error(as_of, dataset="security_master")
        if date_error:
            return _result(
                None,
                status=DataStatus.DATA_QUALITY_FAILED,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=[date_error],
            )
        try:
            raw_rows = self._backend.list_mootdx_stocks()
        except Exception as exc:
            return _result(
                None,
                status=DataStatus.NETWORK_ERROR,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=[str(exc)],
            )
        records: list[SecurityRecord] = []
        for row in raw_rows:
            list_date = row.get("list_date")
            if list_date is None:
                available_at = run_time
                valid_from = as_of
            else:
                available_at = datetime.combine(list_date, time(9, 0), tzinfo=SHANGHAI)
                valid_from = list_date
            record = SecurityRecord(
                symbol=row["symbol"],
                name=row["name"],
                board=row.get("board", "main"),
                valid_from=valid_from,
                valid_to=None,
                list_date=list_date or valid_from,
                delist_date=None,
                status="L",
                st_flag=False,
                available_at=available_at,
                source=self.name,
            )
            if not record.was_effective_on(as_of):
                continue
            records.append(record)
        status = DataStatus.OK if records else DataStatus.SUCCESS_EMPTY
        return _result(
            records,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def count_listed_securities_target(self, as_of: date) -> int:
        date_error = live_snapshot_date_error(as_of, dataset="security_master")
        if date_error:
            return 0
        try:
            return len(self._backend.list_mootdx_stocks())
        except Exception:
            return 0

    def get_trade_calendar(self, start: date, end: date) -> DataResult[list[TradingDay]]:
        run_time = datetime.now(tz=SHANGHAI)
        try:
            trade_dates = self._backend.fetch_sse_trade_dates(start, end)
        except Exception as exc:
            return _result(
                None,
                status=DataStatus.NETWORK_ERROR,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=[str(exc)],
            )
        rows = [
            TradingDay(
                exchange="SSE",
                trade_date=trade_date,
                is_open=True,
                available_at=datetime.combine(trade_date, time(9, 0), tzinfo=SHANGHAI),
                source=self.name,
            )
            for trade_date in trade_dates
        ]
        status = DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY
        return _result(
            rows,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        return self._daily_adapter.get_daily_bars(symbols, start, end)

    def get_daily_by_trade_date(self, trade_date: date) -> DataResult[list[dict]]:
        run_time = datetime.now(tz=SHANGHAI)
        date_error = live_snapshot_date_error(trade_date, dataset="daily_bars")
        if date_error:
            return _result(
                None,
                status=DataStatus.DATA_QUALITY_FAILED,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=[date_error],
            )
        try:
            rows = self._backend.fetch_eastmoney_daily_snapshot(trade_date)
        except Exception as exc:
            return _result(
                None,
                status=DataStatus.NETWORK_ERROR,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=[str(exc)],
            )
        if run_time.date() == trade_date:
            for row in rows:
                row["available_at"] = max(row["available_at"], run_time)
        status = DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY
        return _result(
            rows,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_daily_indicators(self, trade_date: date) -> DataResult[list[dict]]:
        run_time = datetime.now(tz=SHANGHAI)
        return _result(
            [],
            status=DataStatus.NOT_AVAILABLE_YET,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.BEST_EFFORT,
            errors=["daily_indicators not implemented for free_astock"],
        )

    def get_financials(
        self, symbols: Sequence[str], announced_before: datetime
    ) -> DataResult[list[dict]]:
        import os
        import time

        run_time = datetime.now(tz=SHANGHAI)
        announced_before = ensure_aware_shanghai(announced_before)
        interval = float(os.environ.get("FINANCIAL_SYNC_INTERVAL", "0.15"))
        rows: list[dict] = []
        errors: list[str] = []
        for index, symbol in enumerate(symbols):
            if index > 0 and interval > 0:
                time.sleep(interval)
            try:
                rows.extend(
                    self._backend.fetch_sina_financial_rows(symbol, announced_before)
                )
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        if errors and not rows:
            return _result(
                None,
                status=DataStatus.ERROR,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=errors,
            )
        status = DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY
        return _result(
            rows,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
            errors=errors,
        )

    def _current_only_members(
        self,
        board_type: str,
        board_code: str,
        as_of: datetime,
    ) -> DataResult[list[Membership]]:
        as_of = ensure_aware_shanghai(as_of)
        run_time = datetime.now(tz=SHANGHAI)
        snapshot_date = as_of.date()
        try:
            symbols = self._backend.fetch_eastmoney_board_members(board_code)
        except Exception as exc:
            return _result(
                None,
                status=DataStatus.NETWORK_ERROR,
                source=self.name,
                as_of=as_of,
                available_at=as_of,
                pit_level=PITLevel.CURRENT_ONLY,
                errors=[str(exc)],
            )
        rows = [
            Membership(
                board_type=board_type,
                board_code=board_code,
                symbol=symbol,
                membership_mode=MembershipMode.CURRENT_ONLY,
                snapshot_date=snapshot_date,
                available_at=as_of,
                source=self.name,
            )
            for symbol in symbols
        ]
        status = DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY
        return _result(
            rows,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=as_of,
            pit_level=PITLevel.CURRENT_ONLY,
        )

    def get_industry_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        return self._current_only_members("industry", code, as_of)

    def get_concept_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        return self._current_only_members("concept", code, as_of)

    def get_index_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        return self._current_only_members("index", code, as_of)

    def fetch_adjustment_factor_rows(
        self,
        symbols: Sequence[str],
        *,
        as_of: date | None = None,
    ) -> DataResult[tuple[list[dict], list[dict]]]:
        run_time = datetime.now(tz=SHANGHAI)
        factor_date = as_of or run_time.date()
        available_at = post_close_signal_time(factor_date)
        if run_time.date() == factor_date:
            available_at = max(available_at, run_time)
        factor_rows: list[dict] = []
        action_rows: list[dict] = []
        errors: list[str] = []
        for symbol in symbols:
            try:
                xdxr_rows = self._backend.fetch_xdxr_frame(symbol)
                daily_result = self.get_daily_bars(
                    [symbol],
                    date(1990, 1, 1),
                    run_time.date(),
                )
                daily_bars = daily_result.data or []
                anchor_date = _earliest_bar_trade_date(daily_bars) or factor_date
                anchor_available = post_close_signal_time(anchor_date)

                def prev_close_resolver(ex_date: date, bars: list[dict] = daily_bars) -> float | None:
                    return resolve_prev_close_from_bars(bars, ex_date)

                factors, actions = build_pit_rows_from_xdxr(
                    symbol,
                    xdxr_rows,
                    source=self.name,
                    prev_close_resolver=prev_close_resolver,
                )
                factors = ensure_factor_baseline(
                    factors,
                    symbol,
                    anchor_date,
                    available_at=anchor_available,
                    source=self.name,
                )
                factor_rows.extend(factors)
                action_rows.extend(actions)
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
                anchor_date = factor_date
                factor_rows.append(
                    baseline_factor_row(
                        symbol,
                        anchor_date,
                        available_at=post_close_signal_time(anchor_date),
                        source=self.name,
                    )
                )
        if not factor_rows:
            return _result(
                None,
                status=DataStatus.ERROR,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=errors,
            )
        status = DataStatus.OK if factor_rows else DataStatus.SUCCESS_EMPTY
        return _result(
            (factor_rows, action_rows),
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
            errors=errors,
        )

    def probe_capabilities(self) -> DataResult[list[ProviderCapability]]:
        run_time = datetime.now(tz=SHANGHAI)
        capabilities: list[ProviderCapability] = []
        errors: list[str] = []
        sample_symbols: list[str] = []
        try:
            sample_symbols = [
                row["symbol"]
                for row in self._backend.list_mootdx_stocks()[:1]
            ]
        except Exception as exc:
            errors.append(f"security_master: {exc}")

        for dataset, endpoint, pit_level, license_note in _PROBE_SPECS:
            permitted = False
            error: str | None = None
            try:
                if dataset == "security_master":
                    permitted = bool(self._backend.list_mootdx_stocks())
                elif dataset == "trade_calendar":
                    window_start = run_time.date() - timedelta(days=14)
                    permitted = bool(
                        self._backend.fetch_sse_trade_dates(window_start, run_time.date())
                    )
                elif dataset == "daily_bars":
                    today = run_time.date()
                    error = None
                    try:
                        permitted = bool(self._backend.fetch_eastmoney_daily_snapshot(today))
                    except Exception as exc:
                        error = str(exc)
                        permitted = False
                    if not permitted and sample_symbols:
                        backfill = self.get_daily_bars(
                            sample_symbols[:1],
                            today - timedelta(days=7),
                            today,
                        )
                        if backfill.is_usable_for_screening or backfill.allows_empty_universe:
                            permitted = True
                            error = None
                elif dataset == "financials":
                    if sample_symbols:
                        self._backend.fetch_sina_financial_rows(
                            sample_symbols[0],
                            run_time,
                        )
                        permitted = True
                    else:
                        permitted = False
                elif dataset == "adjustment_factors":
                    if sample_symbols:
                        frame = self._backend.fetch_xdxr_frame(sample_symbols[0])
                        permitted = frame is not None
                    else:
                        permitted = False
                elif dataset in {"industry_members", "concept_members", "index_members"}:
                    permitted = bool(self._backend.fetch_eastmoney_board_members("BK0475"))
            except Exception as exc:
                permitted = False
                error = str(exc)
            if error:
                errors.append(f"{dataset}: {error}")
            capabilities.append(ProviderCapability(
                dataset=dataset,
                endpoint=endpoint,
                permitted=permitted,
                pit_level=pit_level,
                license_note=license_note,
                probed_at=run_time,
                error=error,
            ))
        core = {item.dataset for item in capabilities if item.permitted}
        required = {
            "security_master",
            "trade_calendar",
            "daily_bars",
            "financials",
            "adjustment_factors",
        }
        status = DataStatus.OK if required.issubset(core) else DataStatus.PARTIAL
        return _result(
            capabilities,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
            errors=errors,
        )


__all__ = ["FreeAStockProvider"]
