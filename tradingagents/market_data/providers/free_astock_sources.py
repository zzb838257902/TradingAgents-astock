"""Low-level free A-share data fetchers (mockable for offline tests)."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Protocol

import pandas as pd

from tradingagents.market_data.financials import (
    DEFAULT_RECORD_TYPE,
    _EQUITY_KEYS,
    _NET_PROFIT_KEYS,
    _ROE_DIRECT_KEYS,
    derive_roe,
    normalize_reported_roe,
)
from tradingagents.market_data.market_hours import SHANGHAI
from tradingagents.market_data.sync_policy import shanghai_today

_A_SHARE_CODE = re.compile(r"^[036]\d{5}$")

_FALLBACK_MOOTDX_SERVERS: tuple[tuple[str, int], ...] = (
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("110.41.147.114", 7709),
    ("124.70.176.52", 7709),
)


def _mootdx_quotes_client():
    """Connect to mootdx HQ, falling back when bestip scan is blocked."""
    import os

    from mootdx.quotes import Quotes

    skip_bestip = os.environ.get("MOOTDX_SKIP_BESTIP", "").lower() in {"1", "true", "yes"}
    if not skip_bestip:
        try:
            return Quotes.factory(market="std", bestip=True, timeout=10)
        except OSError:
            pass
    servers: list[tuple[str, int]] = list(_FALLBACK_MOOTDX_SERVERS)
    try:
        from mootdx.consts import HQ_HOSTS
        from tdxpy.constants import hq_hosts

        for host in hq_hosts[:12] + HQ_HOSTS[:8]:
            servers.append((host[1], int(host[2])))
    except Exception:
        pass
    seen: set[tuple[str, int]] = set()
    last_error: Exception | None = None
    for server in servers:
        if server in seen:
            continue
        seen.add(server)
        try:
            return Quotes.factory(market="std", server=server, timeout=10)
        except Exception as exc:
            last_error = exc
    raise OSError(f"unable to connect to mootdx HQ server: {last_error}")


def _parse_yyyymmdd(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "-" in text:
        return date.fromisoformat(text[:10])
    if len(text) >= 8 and text[:8].isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return None


def _post_close_available_at(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI)


class FreeAStockSourceBackend(Protocol):
    def list_mootdx_stocks(self) -> list[dict[str, Any]]: ...

    def fetch_sse_trade_dates(self, start: date, end: date) -> list[date]: ...

    def fetch_eastmoney_daily_snapshot(self, trade_date: date) -> list[dict[str, Any]]: ...

    def fetch_eastmoney_board_members(self, board_code: str) -> list[str]: ...

    def fetch_sina_financial_rows(
        self,
        symbol: str,
        announced_before: datetime,
    ) -> list[dict[str, Any]]: ...

    def fetch_xdxr_frame(self, symbol: str) -> list[dict[str, Any]]: ...


class LiveFreeAStockSourceBackend:
    """Production backend delegating to existing a_stock / mootdx integrations."""

    def list_mootdx_stocks(self) -> list[dict[str, Any]]:
        client = _mootdx_quotes_client()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for market in (0, 1):
            frame = client.stocks(market=market)
            if frame is None or frame.empty:
                continue
            for record in frame.to_dict(orient="records"):
                code = str(record.get("code", "")).strip()
                if not _A_SHARE_CODE.match(code) or code in seen:
                    continue
                seen.add(code)
                name = str(record.get("name", code)).strip()
                board = "main"
                if code.startswith("688"):
                    board = "star"
                elif code.startswith("3"):
                    board = "chinext"
                rows.append({
                    "symbol": code,
                    "name": name,
                    "board": board,
                    "list_date": None,
                })
        return rows

    def fetch_sse_trade_dates(self, start: date, end: date) -> list[date]:
        from tradingagents.dataflows.a_stock import _sina_kline_fallback

        frame = _sina_kline_fallback("000001", start.isoformat(), end.isoformat())
        if frame.empty:
            return []
        dates = pd.to_datetime(frame["Date"]).dt.date.tolist()
        return sorted(day for day in dates if start <= day <= end)

    def fetch_eastmoney_daily_snapshot(self, trade_date: date) -> list[dict[str, Any]]:
        today = shanghai_today()
        if trade_date != today:
            raise ValueError(
                f"eastmoney daily snapshot is current-session only; "
                f"requested {trade_date.isoformat()}, today is {today.isoformat()}"
            )
        from tradingagents.dataflows.a_stock import _em_get

        rows: list[dict[str, Any]] = []
        page = 1
        page_size = 100
        while True:
            response = _em_get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": str(page),
                    "pz": str(page_size),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": "f2,f5,f6,f12,f15,f16,f17,f18",
                },
                timeout=15,
            )
            payload = response.json().get("data") or {}
            items = payload.get("diff") or []
            if not items:
                break
            for item in items:
                symbol = str(item.get("f12", "")).strip()
                if not _A_SHARE_CODE.match(symbol):
                    continue
                close = float(item.get("f2") or 0.0)
                open_ = float(item.get("f17") or close)
                high = float(item.get("f15") or close)
                low = float(item.get("f16") or close)
                pre_close = float(item.get("f18") or close)
                volume = float(item.get("f5") or 0.0)
                amount = float(item.get("f6") or 0.0)
                if amount <= 0:
                    amount = close * volume
                rows.append({
                    "symbol": symbol,
                    "trade_date": today,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": amount,
                    "pre_close": pre_close,
                    "available_at": _post_close_available_at(today),
                    "source": "free_astock",
                })
            total = int(payload.get("total") or 0)
            if page * page_size >= total:
                break
            page += 1
        return rows

    def fetch_eastmoney_board_members(self, board_code: str) -> list[str]:
        from tradingagents.dataflows.a_stock import _em_get

        code = board_code.upper()
        if not code.startswith("BK"):
            code = f"BK{code.removeprefix('BK')}"
        symbols: list[str] = []
        page = 1
        while True:
            response = _em_get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": str(page),
                    "pz": "200",
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": f"b:{code}",
                    "fields": "f12",
                },
                timeout=15,
            )
            payload = response.json().get("data") or {}
            items = payload.get("diff") or []
            if not items:
                break
            for item in items:
                symbol = str(item.get("f12", "")).strip()
                if _A_SHARE_CODE.match(symbol):
                    symbols.append(symbol)
            total = int(payload.get("total") or 0)
            if page * 200 >= total:
                break
            page += 1
        return sorted(set(symbols))

    def fetch_sina_financial_rows(
        self,
        symbol: str,
        announced_before: datetime,
    ) -> list[dict[str, Any]]:
        import os
        import time

        from tradingagents.dataflows.a_stock import (
            _get_financial_report_sina,
            _normalize_ticker,
        )

        code = _normalize_ticker(symbol)
        request_interval = float(os.environ.get("FINANCIAL_REQUEST_INTERVAL", "0.05"))
        reports = ("利润表", "现金流量表", "资产负债表")
        frames: dict[str, pd.DataFrame] = {}
        for index, report_type in enumerate(reports):
            if index > 0 and request_interval > 0:
                time.sleep(request_interval)
            frames[report_type] = _get_financial_report_sina(code, report_type, "quarterly")
        income = frames["利润表"]
        cashflow = frames["现金流量表"]
        balance = frames["资产负债表"]
        if income.empty:
            return []
        cutoff_date = announced_before.date()
        rows: list[dict[str, Any]] = []
        for _, income_row in income.iterrows():
            report_period = _report_period_from_row(income_row)
            if report_period is None:
                continue
            announcement_date = _announcement_date_from_row(income_row)
            if announcement_date is None:
                continue
            if announcement_date > cutoff_date:
                continue
            ann_source = _announcement_date_source_from_row(income_row)
            cash_row = _match_report_row(cashflow, report_period)
            balance_row = _match_report_row(balance, report_period)
            net_profit = _float_field(income_row, _NET_PROFIT_KEYS)
            direct_roe = _float_field(income_row, _ROE_DIRECT_KEYS)
            rows.append({
                "symbol": code,
                "report_period": report_period,
                "roe": derive_roe(
                    direct_roe=direct_roe,
                    net_profit=net_profit,
                    equity=_float_field(balance_row, _EQUITY_KEYS),
                    report_period=report_period,
                ),
                "operating_cashflow": _float_field(
                    cash_row,
                    (
                        "经营活动产生的现金流量净额",
                        "经营活动现金流量净额",
                        "MANANETR",
                    ),
                ),
                "net_profit": net_profit,
                "debt_ratio": _debt_ratio(balance_row),
                "announcement_date": announcement_date,
                "announcement_date_source": ann_source,
                "source": "free_astock",
                "record_type": (
                    DEFAULT_RECORD_TYPE
                    if normalize_reported_roe(direct_roe) != 0.0
                    else "derived_indicator"
                ),
            })
        return rows

    def fetch_xdxr_frame(self, symbol: str) -> list[dict[str, Any]]:
        client = _mootdx_quotes_client()
        frame = client.xdxr(symbol=symbol)
        if frame is None or frame.empty:
            return []
        return frame.to_dict(orient="records")


def _report_period_from_row(row: pd.Series) -> str | None:
    for key in ("报告日", "报告期"):
        if key in row.index:
            parsed = _parse_yyyymmdd(row.get(key))
            if parsed is not None:
                return parsed.isoformat().replace("-", "")
    return None


def _announcement_date_from_row(row: pd.Series) -> date | None:
    for key in ("公告日期", "公告日", "披露日期"):
        if key in row.index:
            parsed = _parse_yyyymmdd(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _announcement_date_source_from_row(row: pd.Series) -> str:
    if "announcement_date_source" in row.index:
        value = row.get("announcement_date_source")
        if value in {"reported", "regulatory_deadline"}:
            return str(value)
    return "reported"


def _match_report_row(frame: pd.DataFrame, report_period: str) -> pd.Series | None:
    if frame.empty:
        return None
    for _, row in frame.iterrows():
        period = _report_period_from_row(row)
        if period == report_period:
            return row
    return None


def _float_field(row: pd.Series | None, keys: tuple[str, ...]) -> float:
    if row is None:
        return 0.0
    for key in keys:
        if key in row.index:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _debt_ratio(balance_row: pd.Series | None) -> float:
    if balance_row is None:
        return 0.0
    liabilities = _float_field(balance_row, ("负债合计", "TOTLIAB"))
    assets = _float_field(balance_row, ("资产总计", "资产合计", "TOTASSET"))
    if assets <= 0:
        return 0.0
    return liabilities / assets
