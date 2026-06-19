from datetime import date
from datetime import datetime, time, timezone
from typing import Sequence

from tradingagents.dataflows.a_stock import get_stock_data
from tradingagents.market_data.contracts import DataResult, DataStatus, PITLevel

_ERROR_PREFIXES = ("K线数据获取失败", "Error", "No data found")


def _post_close_available_at(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(15, 30), tzinfo=timezone.utc)


def _is_error_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return any(stripped.startswith(prefix) for prefix in _ERROR_PREFIXES)


def _parse_csv_body(text: str, symbol: str) -> list[dict]:
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 2:
        return []
    rows: list[dict] = []
    for line_no, line in enumerate(lines[1:], start=2):
        parts = line.split(",")
        if len(parts) < 6:
            raise ValueError(f"{symbol} row {line_no}: expected 6 columns")
        try:
            trade_date = date.fromisoformat(parts[0].strip())
            open_ = float(parts[1])
            high = float(parts[2])
            low = float(parts[3])
            close = float(parts[4])
            volume = float(parts[5])
        except ValueError as exc:
            raise ValueError(f"{symbol} row {line_no}: {exc}") from exc
        amount = close * volume
        rows.append({
            "symbol": symbol,
            "trade_date": trade_date,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "available_at": _post_close_available_at(trade_date),
            "source": "existing_astock",
        })
    return rows


class ExistingAStockProvider:
    name = "existing_astock"

    def list_securities(self, as_of: date) -> DataResult[list]:
        raise NotImplementedError("list_securities not implemented in MVP adapter")

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        now = datetime.now(timezone.utc)
        all_rows: list[dict] = []
        for symbol in symbols:
            raw = get_stock_data(symbol, start.isoformat(), end.isoformat())
            if _is_error_text(raw):
                return DataResult(
                    data=None,
                    status=DataStatus.ERROR,
                    source=self.name,
                    as_of=now,
                    available_at=now,
                    pit_level=PITLevel.PIT_REQUIRED,
                    errors=[raw.strip()],
                )
            try:
                rows = _parse_csv_body(raw, symbol)
            except ValueError as exc:
                return DataResult(
                    data=None,
                    status=DataStatus.ERROR,
                    source=self.name,
                    as_of=now,
                    available_at=now,
                    pit_level=PITLevel.PIT_REQUIRED,
                    errors=[str(exc)],
                )
            all_rows.extend(rows)
        return DataResult(
            data=all_rows,
            status=DataStatus.OK if all_rows else DataStatus.EMPTY,
            source=self.name,
            as_of=now,
            available_at=now,
            pit_level=PITLevel.PIT_REQUIRED,
            errors=[],
        )

    def get_financials(
        self, symbols: Sequence[str], available_before: date
    ) -> DataResult[list[dict]]:
        raise NotImplementedError("get_financials not implemented in MVP adapter")
