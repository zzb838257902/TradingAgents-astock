"""Map fixture JSON into provider contract responses."""

from __future__ import annotations

from datetime import date, datetime, time
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
from tradingagents.market_data.market_hours import SHANGHAI, ensure_aware_shanghai
from tradingagents.market_data.pit import require_pit_required


def _parse_date(value: date | str) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _parse_available_at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return ensure_aware_shanghai(value)
    return ensure_aware_shanghai(datetime.fromisoformat(value))


class FixtureProvider:
    name = "fixture"

    def __init__(self, fixture: dict):
        self._fixture = fixture

    def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
        run_time = datetime.now(tz=SHANGHAI)
        trading_dates = sorted(_parse_date(key) for key in self._fixture["bars"])
        records: list[SecurityRecord] = []
        for item in self._fixture["symbols"]:
            symbol = item["symbol"]
            list_date = (
                _parse_date(item["list_date"]) if item.get("list_date") else trading_dates[0]
            )
            delist_date = None
            valid_to = None
            if item.get("delist_after"):
                delist_key = next(iter(self._fixture.get("delistings", {})), None)
                if delist_key:
                    delist_date = _parse_date(delist_key)
                    valid_to = delist_date
            available_at = datetime.combine(list_date, time(9, 0), tzinfo=SHANGHAI)
            records.append(SecurityRecord(
                symbol=symbol,
                name=symbol,
                board=item.get("board", "main"),
                valid_from=list_date,
                valid_to=valid_to,
                list_date=list_date,
                delist_date=delist_date,
                status="listed",
                st_flag=item.get("st_flag", False),
                available_at=available_at,
                source=self.name,
            ))
        effective = [record for record in records if record.was_effective_on(as_of)]
        return DataResult(
            data=effective,
            status=DataStatus.OK if effective else DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_trade_calendar(self, start: date, end: date) -> DataResult[list[TradingDay]]:
        run_time = datetime.now(tz=SHANGHAI)
        rows: list[TradingDay] = []
        for item in self._fixture.get("trade_calendar", []):
            trade_date = _parse_date(item["trade_date"])
            if start <= trade_date <= end:
                rows.append(TradingDay(
                    exchange=item.get("exchange", "SSE"),
                    trade_date=trade_date,
                    is_open=bool(item.get("is_open", True)),
                    available_at=datetime.combine(trade_date, time(9, 0), tzinfo=SHANGHAI),
                    source=self.name,
                ))
        return DataResult(
            data=rows,
            status=DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        run_time = datetime.now(tz=SHANGHAI)
        require_pit_required(
            self._fixture.get("datasets", {}).get("daily_bars", "pit_required"),
            "daily_bars",
        )
        rows: list[dict] = []
        for trade_date_str, day_bars in self._fixture["bars"].items():
            trade_date = _parse_date(trade_date_str)
            if trade_date < start or trade_date > end:
                continue
            for symbol in symbols:
                bar = day_bars.get(symbol)
                if bar is None:
                    continue
                available_at = datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI)
                rows.append({
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                    "amount": bar.get("amount", bar["close"] * bar["volume"]),
                    "available_at": available_at,
                    "source": self.name,
                })
        return DataResult(
            data=rows,
            status=DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_daily_by_trade_date(self, trade_date: date) -> DataResult[list[dict]]:
        return self.get_daily_bars(
            [item["symbol"] for item in self._fixture["symbols"]],
            trade_date,
            trade_date,
        )

    def get_daily_indicators(self, trade_date: date) -> DataResult[list[dict]]:
        run_time = datetime.now(tz=SHANGHAI)
        return DataResult(
            data=[],
            status=DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def get_financials(
        self, symbols: Sequence[str], announced_before: datetime
    ) -> DataResult[list[dict]]:
        run_time = datetime.now(tz=SHANGHAI)
        require_pit_required(
            self._fixture.get("datasets", {}).get("financials", "pit_required"),
            "financials",
        )
        cutoff = ensure_aware_shanghai(announced_before)
        rows: list[dict] = []
        for item in self._fixture.get("financials", []):
            if item["symbol"] not in symbols:
                continue
            available_at = _parse_available_at(item["available_at"])
            if available_at > cutoff:
                continue
            rows.append({
                "symbol": item["symbol"],
                "report_period": item["report_period"],
                "roe": item["roe"],
                "operating_cashflow": item["operating_cashflow"],
                "net_profit": item["net_profit"],
                "debt_ratio": item["debt_ratio"],
                "available_at": available_at,
                "source": self.name,
            })
        return DataResult(
            data=rows,
            status=DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def _memberships_for(
        self,
        board_type: str,
        code: str,
        as_of: datetime,
    ) -> list[Membership]:
        cutoff = ensure_aware_shanghai(as_of)
        as_of_date = cutoff.date()
        rows: list[Membership] = []
        for item in self._fixture.get("board_memberships", []):
            if item["board_type"] != board_type or item["board_code"] != code:
                continue
            available_at = _parse_available_at(item["available_at"])
            if available_at > cutoff:
                continue
            membership = Membership(
                board_type=item["board_type"],
                board_code=item["board_code"],
                symbol=item["symbol"],
                membership_mode=MembershipMode(item["membership_mode"]),
                effective_from=(
                    _parse_date(item["effective_from"]) if item.get("effective_from") else None
                ),
                effective_to=(
                    _parse_date(item["effective_to"]) if item.get("effective_to") else None
                ),
                snapshot_date=(
                    _parse_date(item["snapshot_date"]) if item.get("snapshot_date") else None
                ),
                available_at=available_at,
                source=self.name,
            )
            if membership.pit_member_on(as_of_date):
                rows.append(membership)
        return rows

    def get_industry_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        return self._membership_result("industry", code, as_of)

    def get_concept_members(
        self, code: str, as_of: datetime
    ) -> DataResult[list[Membership]]:
        return self._membership_result("concept", code, as_of)

    def get_index_members(self, code: str, as_of: datetime) -> DataResult[list[Membership]]:
        return self._membership_result("index", code, as_of)

    def _membership_result(
        self,
        board_type: str,
        code: str,
        as_of: datetime,
    ) -> DataResult[list[Membership]]:
        run_time = datetime.now(tz=SHANGHAI)
        rows = self._memberships_for(board_type, code, as_of)
        return DataResult(
            data=rows,
            status=DataStatus.OK if rows else DataStatus.SUCCESS_EMPTY,
            source=self.name,
            as_of=run_time,
            available_at=run_time,
            ingested_at=run_time,
            run_time=run_time,
            pit_level=PITLevel.PIT_REQUIRED,
        )

    def probe_capabilities(self) -> DataResult[list[ProviderCapability]]:
        run_time = datetime.now(tz=SHANGHAI)
        datasets = self._fixture.get("datasets", {})
        capabilities: list[ProviderCapability] = []
        mapping = {
            "daily_bars": ("daily_bars", "daily", PITLevel.PIT_REQUIRED),
            "financials": ("financials", "fina_indicator", PITLevel.PIT_REQUIRED),
            "security_master": ("security_master", "stock_basic", PITLevel.PIT_REQUIRED),
            "trade_calendar": ("trade_calendar", "trade_cal", PITLevel.PIT_REQUIRED),
        }
        for dataset_key, (dataset, endpoint, pit_level) in mapping.items():
            if dataset_key in datasets or dataset_key == "trade_calendar":
                capabilities.append(ProviderCapability(
                    dataset=dataset,
                    endpoint=endpoint,
                    permitted=True,
                    pit_level=pit_level,
                    probed_at=run_time,
                ))
        if self._fixture.get("trade_calendar"):
            if not any(item.dataset == "trade_calendar" for item in capabilities):
                capabilities.append(ProviderCapability(
                    dataset="trade_calendar",
                    endpoint="trade_cal",
                    permitted=True,
                    pit_level=PITLevel.PIT_REQUIRED,
                    probed_at=run_time,
                ))
        board_types = {item.get("board_type") for item in self._fixture.get("board_memberships", [])}
        board_probe = {
            "industry": ("industry_members", "index_member_all"),
            "concept": ("concept_members", "dc_member"),
            "index": ("index_members", "index_weight"),
        }
        for board_type, (dataset, endpoint) in board_probe.items():
            if board_type in board_types:
                capabilities.append(ProviderCapability(
                    dataset=dataset,
                    endpoint=endpoint,
                    permitted=True,
                    pit_level=PITLevel.PIT_REQUIRED,
                    probed_at=run_time,
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
