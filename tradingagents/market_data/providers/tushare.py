"""Tushare Pro provider adapter (offline-testable via injected client)."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Sequence

import pandas as pd

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
from tradingagents.market_data.financials import financial_available_at
from tradingagents.market_data.market_hours import SHANGHAI, ensure_aware_shanghai
from tradingagents.market_data.providers.free_astock_sources import normalize_tushare_daily_indicator_row

_RATE_LIMIT_PATTERN = re.compile(r"每分钟最多访问")
_PERMISSION_PATTERN = re.compile(r"权限|积分|没有访问|接口不存在")
_NETWORK_PATTERN = re.compile(r"timeout|timed out|connection|网络", re.IGNORECASE)

_PROBE_SPECS: list[tuple[str, str, str, PITLevel]] = [
    ("security_master", "stock_basic", "stock_basic", PITLevel.PIT_REQUIRED),
    ("trade_calendar", "trade_cal", "trade_cal", PITLevel.PIT_REQUIRED),
    ("daily_bars", "daily", "daily", PITLevel.PIT_REQUIRED),
    ("daily_indicators", "daily_basic", "daily_basic", PITLevel.PIT_REQUIRED),
    ("financials", "fina_indicator", "fina_indicator", PITLevel.PIT_REQUIRED),
    ("industry_members", "index_member_all", "index_member_all", PITLevel.PIT_REQUIRED),
    ("concept_members", "dc_member", "dc_member", PITLevel.PIT_REQUIRED),
    ("index_members", "index_weight", "index_weight", PITLevel.PIT_REQUIRED),
]


def _parse_tushare_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "-" in text:
        return date.fromisoformat(text[:10])
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def normalize_ts_code(symbol: str) -> str:
    if "." in symbol:
        return symbol
    if symbol.startswith(("5", "6", "9")):
        return f"{symbol}.SH"
    return f"{symbol}.SZ"


def denormalize_ts_code(ts_code: str) -> str:
    return ts_code.split(".", 1)[0]


def count_stock_basic_target(frame: pd.DataFrame, as_of: date) -> int:
    """Count securities that should exist in master on ``as_of`` (raw API rows)."""
    seen: set[str] = set()
    total = 0
    for row in frame.to_dict(orient="records"):
        ts_code = str(row.get("ts_code", "")).strip()
        if not ts_code or ts_code in seen:
            continue
        list_date = _parse_tushare_date(row.get("list_date"))
        if list_date is None or list_date > as_of:
            continue
        delist_date = _parse_tushare_date(row.get("delist_date"))
        if delist_date is not None and as_of >= delist_date:
            continue
        seen.add(ts_code)
        total += 1
    return total


def industry_member_query_params(board_code: str) -> dict[str, str]:
    """Build ``index_member_all`` params per Tushare SW industry contract."""
    params: dict[str, str] = {"is_sw": "1"}
    if board_code.endswith(".SI"):
        params["l2_code"] = board_code.removesuffix(".SI")
    else:
        params["l3_code"] = board_code
    return params


def map_stock_basic_frame(frame: pd.DataFrame, source: str) -> list[SecurityRecord]:
    records: list[SecurityRecord] = []
    for row in frame.to_dict(orient="records"):
        list_date = _parse_tushare_date(row.get("list_date"))
        if list_date is None:
            continue
        symbol = denormalize_ts_code(str(row["ts_code"]))
        name = str(row.get("name", symbol))
        list_status = str(row.get("list_status", "L"))
        delist_date = _parse_tushare_date(row.get("delist_date"))
        available_at = datetime.combine(list_date, time(9, 0), tzinfo=SHANGHAI)
        if list_status == "D" and delist_date is not None:
            valid_to = delist_date
            available_at = max(
                available_at,
                datetime.combine(delist_date, time(15, 0), tzinfo=SHANGHAI),
            )
        else:
            valid_to = None
            delist_date = None
        records.append(SecurityRecord(
            symbol=symbol,
            name=name,
            board=str(row.get("market", "main")),
            valid_from=list_date,
            valid_to=valid_to,
            list_date=list_date,
            delist_date=delist_date,
            status=list_status,
            st_flag=False,
            available_at=available_at,
            source=source,
        ))
    return records


def dedupe_securities_for_as_of(
    records: list[SecurityRecord], as_of: date
) -> list[SecurityRecord]:
    priority = {"L": 0, "P": 1, "D": 2}
    by_symbol: dict[str, SecurityRecord] = {}
    for record in records:
        if not record.was_effective_on(as_of):
            continue
        existing = by_symbol.get(record.symbol)
        if existing is None or priority.get(record.status, 9) < priority.get(
            existing.status, 9
        ):
            by_symbol[record.symbol] = record
    return list(by_symbol.values())


def map_trade_calendar_frame(frame: pd.DataFrame, source: str) -> list[TradingDay]:
    rows: list[TradingDay] = []
    for row in frame.to_dict(orient="records"):
        trade_date = _parse_tushare_date(row.get("cal_date") or row.get("trade_date"))
        if trade_date is None:
            continue
        exchange = str(row.get("exchange", "SSE"))
        is_open = bool(int(row.get("is_open", 0)))
        rows.append(TradingDay(
            exchange=exchange,
            trade_date=trade_date,
            is_open=is_open,
            available_at=datetime.combine(trade_date, time(9, 0), tzinfo=SHANGHAI),
            source=source,
        ))
    return rows


def map_daily_bars_frame(frame: pd.DataFrame, source: str) -> list[dict]:
    rows: list[dict] = []
    for row in frame.to_dict(orient="records"):
        trade_date = _parse_tushare_date(row.get("trade_date"))
        if trade_date is None:
            continue
        symbol = denormalize_ts_code(str(row["ts_code"]))
        volume = float(row.get("vol", row.get("volume", 0.0)))
        amount = float(row.get("amount", 0.0))
        if amount <= 0:
            amount = float(row.get("close", 0.0)) * volume
        rows.append({
            "symbol": symbol,
            "trade_date": trade_date,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": volume,
            "amount": amount,
            "pre_close": float(row.get("pre_close", row.get("close", 0.0)) or 0.0),
            "available_at": datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI),
            "source": source,
        })
    return rows


def map_financial_frame(
    frame: pd.DataFrame,
    source: str,
    *,
    open_dates: list[date] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for row in frame.to_dict(orient="records"):
        ann_date = _parse_tushare_date(row.get("ann_date"))
        if ann_date is None:
            continue
        report_period = str(row.get("end_date", ""))
        rows.append({
            "symbol": denormalize_ts_code(str(row["ts_code"])),
            "report_period": report_period,
            "roe": float(row.get("roe") or 0.0),
            "operating_cashflow": float(row.get("ocfps") or 0.0),
            "net_profit": float(row.get("netprofit_yoy") or 0.0),
            "debt_ratio": float(row.get("debt_to_assets") or 0.0),
            "announcement_date": ann_date,
            "actual_announcement_time": None,
            "available_at": financial_available_at(ann_date, open_dates=open_dates),
            "update_flag": str(row.get("update_flag")) if row.get("update_flag") is not None else None,
            "source_version": report_period,
            "record_type": "indicator",
            "source": source,
        })
    return rows


def map_industry_members_frame(
    frame: pd.DataFrame, board_code: str, source: str
) -> list[Membership]:
    rows: list[Membership] = []
    for row in frame.to_dict(orient="records"):
        in_date = _parse_tushare_date(row.get("in_date"))
        if in_date is None:
            continue
        ts_code = row.get("ts_code") or row.get("con_code")
        if ts_code is None:
            continue
        out_date = _parse_tushare_date(row.get("out_date"))
        rows.append(Membership(
            board_type="industry",
            board_code=board_code,
            symbol=denormalize_ts_code(str(ts_code)),
            membership_mode=MembershipMode.EFFECTIVE_INTERVAL,
            effective_from=in_date,
            effective_to=out_date,
            available_at=datetime.combine(in_date, time(9, 0), tzinfo=SHANGHAI),
            source=source,
        ))
    return rows


def map_index_members_frame(
    frame: pd.DataFrame, board_code: str, source: str
) -> list[Membership]:
    rows: list[Membership] = []
    for row in frame.to_dict(orient="records"):
        trade_date = _parse_tushare_date(row.get("trade_date"))
        if trade_date is None:
            continue
        rows.append(Membership(
            board_type="index",
            board_code=board_code,
            symbol=denormalize_ts_code(str(row["con_code"])),
            membership_mode=MembershipMode.EFFECTIVE_INTERVAL,
            effective_from=trade_date,
            effective_to=trade_date + timedelta(days=1),
            available_at=datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI),
            source=source,
        ))
    return rows


def map_concept_members_frame(
    frame: pd.DataFrame, board_code: str, source: str
) -> list[Membership]:
    rows: list[Membership] = []
    for row in frame.to_dict(orient="records"):
        trade_date = _parse_tushare_date(row.get("trade_date"))
        if trade_date is None:
            continue
        rows.append(Membership(
            board_type="concept",
            board_code=board_code,
            symbol=denormalize_ts_code(str(row["con_code"])),
            membership_mode=MembershipMode.DATED_SNAPSHOT,
            effective_from=trade_date,
            effective_to=trade_date + timedelta(days=1),
            snapshot_date=trade_date,
            available_at=datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI),
            source=source,
        ))
    return rows


def classify_tushare_error(exc: Exception) -> DataStatus:
    message = str(exc)
    if _RATE_LIMIT_PATTERN.search(message):
        return DataStatus.RATE_LIMITED
    if _PERMISSION_PATTERN.search(message):
        return DataStatus.PERMISSION_DENIED
    if _NETWORK_PATTERN.search(message):
        return DataStatus.NETWORK_ERROR
    return DataStatus.ERROR


class TushareProvider:
    name = "tushare"

    def __init__(self, token: str | None = None, client: Any | None = None):
        self._token = token or os.environ.get("TUSHARE_TOKEN")
        self._client = client

    def _require_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._token:
            raise PermissionError("TUSHARE_TOKEN is not set")
        import tushare as ts

        self._client = ts.pro_api(self._token)
        return self._client

    def _query(self, api_name: str, **params: Any) -> pd.DataFrame:
        client = self._require_client()
        frame = client.query(api_name, **params)
        if frame is None:
            return pd.DataFrame()
        return frame

    def _wrap_call(
        self,
        call: Callable[[], Any],
        pit_level: PITLevel = PITLevel.PIT_REQUIRED,
    ) -> DataResult[Any]:
        run_time = datetime.now(tz=SHANGHAI)
        try:
            payload = call()
        except PermissionError as exc:
            return DataResult(
                data=None,
                status=DataStatus.PERMISSION_DENIED,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                ingested_at=run_time,
                run_time=run_time,
                pit_level=pit_level,
                errors=[str(exc)],
            )
        except Exception as exc:
            return DataResult(
                data=None,
                status=classify_tushare_error(exc),
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                ingested_at=run_time,
                run_time=run_time,
                pit_level=pit_level,
                errors=[str(exc)],
            )
        if payload is None:
            payload = []
        if isinstance(payload, pd.DataFrame):
            rows = payload.to_dict(orient="records")
        else:
            rows = payload
        status = DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY
        return DataResult(
            data=payload,
            status=status,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=pit_level,
            errors=[],
        )

    def _fetch_stock_basic_frame(self) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        fields = "ts_code,name,list_date,delist_date,market,list_status"
        for list_status in ("L", "D", "P"):
            frame = self._query(
                "stock_basic",
                exchange="",
                list_status=list_status,
                fields=fields,
            )
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def count_listed_securities_target(self, as_of: date) -> int:
        def call() -> int:
            return count_stock_basic_target(self._fetch_stock_basic_frame(), as_of)

        result = self._wrap_call(call)
        if result.data is None:
            return 0
        return int(result.data)

    def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
        def call() -> list[SecurityRecord]:
            frame = self._fetch_stock_basic_frame()
            records = map_stock_basic_frame(frame, self.name)
            return dedupe_securities_for_as_of(records, as_of)

        result = self._wrap_call(call)
        if result.data is None:
            return result
        records = result.data
        return result.model_copy(update={
            "data": records,
            "status": DataStatus.OK if records else DataStatus.SUCCESS_EMPTY,
        })

    def get_trade_calendar(self, start: date, end: date) -> DataResult[list[TradingDay]]:
        def call() -> list[TradingDay]:
            frame = self._query(
                "trade_cal",
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            return map_trade_calendar_frame(frame, self.name)

        result = self._wrap_call(call)
        if result.data is None:
            return result
        return result.model_copy(update={"data": result.data})

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        def call() -> list[dict]:
            rows: list[dict] = []
            for symbol in symbols:
                frame = self._query(
                    "daily",
                    ts_code=normalize_ts_code(symbol),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                rows.extend(map_daily_bars_frame(frame, self.name))
            return rows

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def get_daily_by_trade_date(self, trade_date: date) -> DataResult[list[dict]]:
        def call() -> list[dict]:
            frame = self._query("daily", trade_date=trade_date.strftime("%Y%m%d"))
            return map_daily_bars_frame(frame, self.name)

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def get_daily_indicators(self, trade_date: date) -> DataResult[list[dict]]:
        def call() -> list[dict]:
            frame = self._query(
                "daily_basic",
                trade_date=trade_date.strftime("%Y%m%d"),
            )
            rows: list[dict] = []
            for row in frame.to_dict(orient="records"):
                rows.append(
                    normalize_tushare_daily_indicator_row(
                        row,
                        trade_date,
                        source=self.name,
                    )
                )
            return rows

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def get_financials(
        self, symbols: Sequence[str], announced_before: datetime
    ) -> DataResult[list[dict]]:
        cutoff = ensure_aware_shanghai(announced_before)

        def call() -> list[dict]:
            rows: list[dict] = []
            for symbol in symbols:
                frame = self._query(
                    "fina_indicator",
                    ts_code=normalize_ts_code(symbol),
                )
                rows.extend(map_financial_frame(frame, self.name))
            visible = [
                row for row in rows
                if ensure_aware_shanghai(row["available_at"]) <= cutoff
            ]
            return visible

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def get_industry_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        as_of = ensure_aware_shanghai(as_of)

        def call() -> list[Membership]:
            frame = self._query("index_member_all", **industry_member_query_params(code))
            return map_industry_members_frame(frame, code, self.name)

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "as_of": as_of,
            "available_at": as_of,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def get_concept_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        as_of = ensure_aware_shanghai(as_of)

        def call() -> list[Membership]:
            frame = self._query(
                "dc_member",
                ts_code=code,
                trade_date=as_of.strftime("%Y%m%d"),
            )
            return map_concept_members_frame(frame, code, self.name)

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "as_of": as_of,
            "available_at": as_of,
            "pit_level": PITLevel.PIT_REQUIRED,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def get_index_members(self, code: str, as_of: datetime) -> DataResult[list[Membership]]:
        as_of = ensure_aware_shanghai(as_of)

        def call() -> list[Membership]:
            frame = self._query(
                "index_weight",
                index_code=code,
                trade_date=as_of.strftime("%Y%m%d"),
            )
            return map_index_members_frame(frame, code, self.name)

        result = self._wrap_call(call)
        if result.data is None:
            return result
        rows = result.data
        return result.model_copy(update={
            "data": rows,
            "as_of": as_of,
            "available_at": as_of,
            "status": DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
        })

    def probe_capabilities(self) -> DataResult[list[ProviderCapability]]:
        run_time = datetime.now(tz=SHANGHAI)
        if not self._token and self._client is None:
            return DataResult(
                data=None,
                status=DataStatus.PERMISSION_DENIED,
                source=self.name,
                as_of=run_time,
                available_at=run_time,
                ingested_at=run_time,
                run_time=run_time,
                pit_level=PITLevel.PIT_REQUIRED,
                errors=["TUSHARE_TOKEN is not set"],
            )

        capabilities: list[ProviderCapability] = []
        for dataset, endpoint, api_name, pit_level in _PROBE_SPECS:
            try:
                frame = self._query(api_name, limit=1)
                permitted = frame is not None
                error = None
            except Exception as exc:
                permitted = False
                error = str(exc)
            capabilities.append(ProviderCapability(
                dataset=dataset,
                endpoint=endpoint,
                permitted=permitted,
                pit_level=pit_level,
                probed_at=run_time,
                error=error,
            ))

        return DataResult(
            data=capabilities,
            status=DataStatus.OK,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )
