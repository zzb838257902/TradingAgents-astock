"""Low-level free A-share data fetchers (mockable for offline tests)."""

from __future__ import annotations

import hashlib
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

    def fetch_sina_bulletin_rows(self, symbol: str, page: int = 1) -> list[dict[str, Any]]: ...

    def fetch_eastmoney_news_rows(self, symbol: str) -> list[dict[str, Any]]: ...

    def fetch_eastmoney_fund_flow_row(
        self, symbol: str, trade_date: date
    ) -> dict[str, Any] | None: ...

    def fetch_ths_hot_topic_rows(self, trade_date: date) -> list[dict[str, Any]]: ...


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

    def fetch_sina_bulletin_rows(self, symbol: str, page: int = 1) -> list[dict[str, Any]]:
        import requests

        code = _normalize_event_symbol(symbol)
        url = (
            "https://vip.stock.finance.sina.com.cn/corp/view/"
            f"vCB_AllBulletin.php?stockid={code}&Page={page}"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn/",
        }
        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise ProviderFetchError("network_error", str(exc)) from exc
        if response.status_code == 429:
            raise ProviderFetchError("rate_limited", f"HTTP 429 for {code}")
        if response.status_code >= 400:
            raise ProviderFetchError("http_error", f"HTTP {response.status_code} for {code}")
        response.encoding = response.apparent_encoding or "gb2312"
        html = response.text
        try:
            rows = parse_sina_bulletin_html(html, code)
            validate_sina_bulletin_parse(html, rows, symbol=code)
            return rows
        except ProviderFetchError:
            raise
        except Exception as exc:
            raise ProviderFetchError("parse_error", str(exc)) from exc

    def fetch_eastmoney_news_rows(self, symbol: str) -> list[dict[str, Any]]:
        from tradingagents.dataflows.a_stock import _fetch_news_eastmoney

        code = _normalize_event_symbol(symbol)
        rows: list[dict[str, Any]] = []
        try:
            for item in _fetch_news_eastmoney(code):
                time_text = str(item.get("time") or "").strip()
                published_at = datetime.now(tz=SHANGHAI)
                if time_text:
                    published_at = datetime.fromisoformat(time_text.replace(" ", "T"))
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=SHANGHAI)
                rows.append({
                    "symbol": code,
                    "title": str(item.get("title") or "").strip(),
                    "published_at": published_at,
                    "source_url": str(item.get("url") or ""),
                    "source_record_id": str(item.get("url") or item.get("title") or code),
                })
        except Exception as exc:
            raise ProviderFetchError("network_error", str(exc)) from exc
        return rows

    def fetch_eastmoney_fund_flow_row(
        self, symbol: str, trade_date: date
    ) -> dict[str, Any] | None:
        from tradingagents.dataflows.a_stock import _em_get

        code = _normalize_event_symbol(symbol)
        secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
        try:
            response = _em_get(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                params={"secid": secid, "lmt": 20, "klt": 101},
                timeout=15,
            )
            payload = response.json().get("data") or {}
            klines = payload.get("klines") or []
        except Exception as exc:
            raise ProviderFetchError("network_error", str(exc)) from exc
        target = trade_date.isoformat()
        for line in reversed(klines):
            parts = str(line).split(",")
            if len(parts) < 2:
                continue
            day = parts[0]
            if day != target:
                continue
            main_net = float(parts[1])
            sentiment = "positive" if main_net > 0 else "negative" if main_net < 0 else "neutral"
            return {
                "symbol": code,
                "title": f"主力净流入 {main_net/1e4:.0f} 万元",
                "published_at": _post_close_available_at(trade_date),
                "source_url": "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                "source_record_id": f"{code}:{target}",
                "sentiment": sentiment,
            }
        return None

    def fetch_ths_hot_topic_rows(self, trade_date: date) -> list[dict[str, Any]]:
        import requests

        snapshot_error = None
        if trade_date != shanghai_today():
            from tradingagents.market_data.sync_policy import live_snapshot_date_error
            snapshot_error = live_snapshot_date_error(trade_date, dataset="event_hot_topics")
        if snapshot_error:
            raise ProviderFetchError("error", snapshot_error)
        url = (
            "http://zx.10jqka.com.cn/event/api/getharden/"
            f"date/{trade_date.isoformat()}/orderby/date/orderway/desc/charset/GBK/"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Chrome/117.0.0.0 Safari/537.36"
            )
        }
        try:
            response = requests.get(url, headers=headers, timeout=10)
            payload = response.json()
        except Exception as exc:
            raise ProviderFetchError("network_error", str(exc)) from exc
        if payload.get("errocode", 0) != 0:
            raise ProviderFetchError("parse_error", str(payload.get("errormsg", "unknown")))
        rows: list[dict[str, Any]] = []
        for item in payload.get("data") or []:
            reason = str(item.get("reason") or "").strip()
            rows.append({
                "symbol": "",
                "title": reason or str(item.get("name") or "热点题材"),
                "published_at": datetime.combine(trade_date, time(9, 35), tzinfo=SHANGHAI),
                "source_url": url,
                "source_record_id": f"{trade_date.isoformat()}:{item.get('code', '')}:{reason}",
            })
        return rows


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


class ProviderFetchError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


_BULLETIN_ROW_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})</td>\s*<td>\s*"
    r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>([^<]+)</a>",
    re.IGNORECASE,
)
_BULLETIN_DATELIST_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*(?:&nbsp;|\u00a0|\s)+"
    r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>([^<]+)</a>",
    re.IGNORECASE,
)
_BULLETIN_ID_RE = re.compile(r"[?&]id=(\d+)", re.IGNORECASE)
_BULLETIN_DETAIL_LINK_RE = re.compile(r"vCB_AllBulletinDetail\.php", re.IGNORECASE)
_BULLETIN_DATELIST_CONTAINER_RE = re.compile(
    r"""class=['"]datelist['"]""",
    re.IGNORECASE,
)
_BULLETIN_DATELIST_ENTRY_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*(?:&nbsp;|\u00a0|\s)+<a[^>]+href=['\"][^'\"]+['\"]",
    re.IGNORECASE,
)
_SINA_BULLETIN_PAGE_RE = re.compile(
    r"vCB_AllBulletin(?:\.php|/)|AllBulletin/stockid/",
    re.IGNORECASE,
)
_BLOCKED_PAGE_MARKERS = (
    "access denied",
    "captcha",
    "verify you are human",
    "验证码",
    "请完成验证",
    "人机验证",
    "waf_block",
    "security check",
)
_SUPPLIER_EMPTY_MARKERS = (
    "暂时没有数据",
    "暂无数据",
    "暂无公告",
    "没有相关公告",
    "没有找到相关信息",
)
_EMPTY_DATELIST_RE = re.compile(
    r"""class=['"]datelist['"][^>]*>\s*<ul>\s*</ul>""",
    re.IGNORECASE,
)


def count_sina_bulletin_detail_links(html: str) -> int:
    return len(_BULLETIN_DETAIL_LINK_RE.findall(html))


def sina_bulletin_page_is_blocked_or_malformed(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _BLOCKED_PAGE_MARKERS)


def sina_bulletin_page_is_target_page(html: str, symbol: str) -> bool:
    code = _normalize_event_symbol(symbol)
    if not _SINA_BULLETIN_PAGE_RE.search(html):
        return False
    if f"stockid={code}" not in html and f"stockid/{code}" not in html:
        return False
    return "公司公告" in html


def sina_bulletin_page_has_explicit_empty_marker(html: str) -> bool:
    if any(marker in html for marker in _SUPPLIER_EMPTY_MARKERS):
        return True
    return _EMPTY_DATELIST_RE.search(html) is not None


def sina_bulletin_page_is_supplier_empty(html: str, symbol: str) -> bool:
    if count_sina_bulletin_detail_links(html) > 0:
        return False
    if _BULLETIN_DATELIST_ENTRY_RE.search(html):
        return False
    if _BULLETIN_ROW_RE.search(html):
        return False
    if not sina_bulletin_page_is_target_page(html, symbol):
        return False
    if sina_bulletin_page_is_blocked_or_malformed(html):
        return False
    if _BULLETIN_DATELIST_CONTAINER_RE.search(html):
        return sina_bulletin_page_has_explicit_empty_marker(html)
    return sina_bulletin_page_has_explicit_empty_marker(html)


def _build_sina_bulletin_row(
    date_str: str,
    href: str,
    title: str,
    symbol: str,
) -> dict[str, Any]:
    record_id = ""
    match = _BULLETIN_ID_RE.search(href)
    if match:
        record_id = match.group(1)
    if not record_id:
        record_id = hashlib.sha256(f"{href}|{title}".encode()).hexdigest()[:16]
    if href.startswith("/"):
        source_url = f"https://vip.stock.finance.sina.com.cn{href}"
    else:
        source_url = href
    return {
        "symbol": symbol,
        "title": title.strip(),
        "published_date": date.fromisoformat(date_str),
        "source_record_id": record_id,
        "source_url": source_url,
        "source_version": "v1",
    }


def parse_sina_bulletin_html(html: str, symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for pattern in (_BULLETIN_ROW_RE, _BULLETIN_DATELIST_RE):
        for date_str, href, title in pattern.findall(html):
            key = (date_str, href, title.strip())
            if key in seen:
                continue
            seen.add(key)
            rows.append(_build_sina_bulletin_row(date_str, href, title, symbol))
    return rows


def validate_sina_bulletin_parse(
    html: str,
    rows: list[dict[str, Any]],
    *,
    symbol: str,
) -> None:
    if rows:
        if sina_bulletin_page_is_blocked_or_malformed(html):
            raise ProviderFetchError(
                "parse_error",
                "bulletin response looks like captcha/access denied page",
            )
        return
    if sina_bulletin_page_is_blocked_or_malformed(html):
        raise ProviderFetchError(
            "parse_error",
            "bulletin response looks like captcha/access denied page",
        )
    if not sina_bulletin_page_is_target_page(html, symbol):
        raise ProviderFetchError(
            "parse_error",
            f"response is not the company bulletin page for {symbol}",
        )
    detail_links = count_sina_bulletin_detail_links(html)
    if detail_links > 0:
        raise ProviderFetchError(
            "parse_error",
            f"bulletin page contains {detail_links} detail links but parser returned 0 rows",
        )
    if sina_bulletin_page_is_supplier_empty(html, symbol):
        return
    if _BULLETIN_DATELIST_ENTRY_RE.search(html) or _BULLETIN_ROW_RE.search(html):
        raise ProviderFetchError(
            "parse_error",
            "bulletin page contains announcement markers but parser returned 0 rows",
        )
    raise ProviderFetchError(
        "parse_error",
        "bulletin page lacks explicit supplier-empty markers",
    )


def _normalize_event_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    if len(text) == 6 and text.isdigit():
        return text
    raise ValueError(f"invalid A-share symbol: {symbol!r}")

