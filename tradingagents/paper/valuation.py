"""Daily mark-to-market valuation for paper portfolio (Stage 6A)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.contracts import NavSnapshot, money
from tradingagents.paper.exceptions import PaperError
from tradingagents.paper.invariants import assert_account_invariants
from tradingagents.paper.repository import PaperRepository, RunInputCapture, ValuationWriteSpec

SHANGHAI = ZoneInfo("Asia/Shanghai")
RETURN_QUANTUM = Decimal("0.0000000001")


class ValuationStatus(StrEnum):
    OK = "OK"
    DATA_ERROR = "DATA_ERROR"


@dataclass(frozen=True)
class ValuationSourceRow:
    symbol: str
    quantity: int
    price_cny: Decimal
    price_status: str
    source_row_key: str
    row_content_hash: str
    available_at: datetime
    dataset_version_id: str | None = None


@dataclass(frozen=True)
class ValuationResult:
    status: ValuationStatus
    nav: NavSnapshot
    sources: list[ValuationSourceRow]


class ValuationDataError(PaperError):
    """Required valuation inputs are missing or incomplete."""


def _return_ratio(current: Decimal, previous: Decimal) -> Decimal:
    if previous == 0:
        return Decimal("0")
    return (current - previous) / previous


def _quantize_return(value: Decimal) -> Decimal:
    return value.quantize(RETURN_QUANTUM, rounding=ROUND_HALF_UP)


def _valuation_price_capture(
    *,
    run_id: str,
    symbol: str,
    trade_date: date,
    row: dict[str, object],
) -> RunInputCapture:
    row_json = json.dumps(row, sort_keys=True, default=str)
    return RunInputCapture(
        run_id=run_id,
        input_type="VALUATION_PRICE",
        scope_key=f"{symbol}:{trade_date.isoformat()}",
        row_content_hash=hashlib.sha256(row_json.encode("utf-8")).hexdigest(),
        row_json=row_json,
        source_dataset_version_id=row.get("dataset_version_id"),  # type: ignore[arg-type]
        source_available_at=row.get("available_at"),  # type: ignore[arg-type]
    )


class MarkToMarketService:
    def __init__(
        self,
        paper_repo: PaperRepository,
        market_repo: MarketDataRepository,
        *,
        owner_id: str = "mark-to-market",
    ) -> None:
        self.paper_repo = paper_repo
        self.market_repo = market_repo
        self.owner_id = owner_id

    def value_account(
        self,
        account_id: str,
        *,
        valuation_date: date,
        available_before: datetime,
        run_id: str,
        fencing_token: int,
        owner_id: str,
    ) -> ValuationResult:
        snapshot = self.paper_repo.load_account_snapshot(account_id, as_of_date=valuation_date)
        if not snapshot.positions:
            nav = self._write_flat_nav(
                account_id=account_id,
                valuation_date=valuation_date,
                cash_cny=snapshot.cash_cny,
                fencing_token=fencing_token,
                owner_id=owner_id,
            )
            return ValuationResult(status=ValuationStatus.OK, nav=nav, sources=[])

        symbols = sorted(snapshot.positions)
        same_day_bars = {
            row["symbol"]: row
            for row in self.market_repo.get_daily_bars(
                symbols,
                valuation_date,
                available_before,
                start=valuation_date,
            )
        }
        history_bars = self.market_repo.get_daily_bars(
            symbols,
            valuation_date,
            available_before,
        )
        latest_by_symbol: dict[str, dict] = {}
        for row in history_bars:
            latest_by_symbol[row["symbol"]] = row

        sources: list[ValuationSourceRow] = []
        captures: list[RunInputCapture] = []
        positions_value = Decimal("0")

        for symbol in symbols:
            position = snapshot.positions[symbol]
            if symbol in same_day_bars:
                bar = same_day_bars[symbol]
                price = money(Decimal(str(bar["close"])))
                price_status = "OK"
                available_at = bar["available_at"]
                source_key = f"{symbol}:{valuation_date.isoformat()}:close"
            elif self.market_repo.is_suspended_on(symbol, valuation_date, available_before):
                stale = latest_by_symbol.get(symbol)
                if stale is None or stale["trade_date"] >= valuation_date:
                    raise ValuationDataError(
                        f"DATA_ERROR: suspended holding {symbol} lacks stale close before {valuation_date}"
                    )
                price = money(Decimal(str(stale["close"])))
                price_status = "STALE_SUSPENDED_PRICE"
                available_at = stale["available_at"]
                source_key = f"{symbol}:{stale['trade_date'].isoformat()}:stale_close"
            else:
                raise ValuationDataError(
                    f"DATA_ERROR: missing close price for {symbol} on {valuation_date}"
                )

            row_payload = {
                "symbol": symbol,
                "trade_date": valuation_date.isoformat(),
                "price_cny": str(price),
                "price_status": price_status,
                "source_row_key": source_key,
                "available_at": available_at,
            }
            captures.append(
                _valuation_price_capture(
                    run_id=run_id,
                    symbol=symbol,
                    trade_date=valuation_date,
                    row=row_payload,
                )
            )
            sources.append(
                ValuationSourceRow(
                    symbol=symbol,
                    quantity=position.quantity,
                    price_cny=price,
                    price_status=price_status,
                    source_row_key=source_key,
                    row_content_hash=captures[-1].row_content_hash,
                    available_at=available_at,
                )
            )
            positions_value += money(price * Decimal(position.quantity))

        positions_value = money(positions_value)
        for capture in captures:
            self.paper_repo.capture_run_inputs(capture)

        history = self.paper_repo.get_nav_history_context(account_id, valuation_date)
        total_equity = money(snapshot.cash_cny + positions_value)
        if history.latest is None:
            daily_return = None
            cumulative_return = Decimal("0")
            drawdown = Decimal("0")
        else:
            prior_equity = money(history.latest.total_equity_cny)
            daily_return = _quantize_return(_return_ratio(total_equity, prior_equity))
            cumulative_return = _quantize_return(
                _return_ratio(total_equity, history.initial_equity_cny)
            )
            peak_equity = max(history.peak_equity_cny, total_equity)
            drawdown = _quantize_return(_return_ratio(total_equity, peak_equity))

        manifest_payload = {
            "account_id": account_id,
            "valuation_date": valuation_date.isoformat(),
            "sources": [source.source_row_key for source in sources],
        }
        manifest_hash = hashlib.sha256(
            json.dumps(manifest_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

        nav = self.paper_repo.write_valuation(
            ValuationWriteSpec(
                account_id=account_id,
                valuation_date=valuation_date,
                cash_cny=snapshot.cash_cny,
                positions_value_cny=positions_value,
                total_equity_cny=total_equity,
                sources=[
                    {
                        "symbol": source.symbol,
                        "quantity": source.quantity,
                        "price_cny": source.price_cny,
                        "price_status": source.price_status,
                        "source_row_key": source.source_row_key,
                        "dataset_version_id": source.dataset_version_id,
                        "row_content_hash": source.row_content_hash,
                        "available_at": source.available_at,
                    }
                    for source in sources
                ],
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                drawdown=drawdown,
                valuation_manifest_hash=manifest_hash,
            ),
            fencing_token=fencing_token,
            owner_id=owner_id,
        )
        self.paper_repo.update_position_marks(
            account_id,
            valuation_date=valuation_date,
            marks={source.symbol: source.price_cny for source in sources},
            fencing_token=fencing_token,
            owner_id=owner_id,
        )
        self.paper_repo.rebuild_account_projection(
            account_id,
            as_of_date=valuation_date,
            fencing_token=fencing_token,
            owner_id=owner_id,
        )
        assert_account_invariants(
            self.paper_repo.connection,
            account_id,
            as_of_date=valuation_date,
        )
        return ValuationResult(status=ValuationStatus.OK, nav=nav, sources=sources)

    def _write_flat_nav(
        self,
        *,
        account_id: str,
        valuation_date: date,
        cash_cny: Decimal,
        fencing_token: int,
        owner_id: str,
    ) -> NavSnapshot:
        history = self.paper_repo.get_nav_history_context(account_id, valuation_date)
        total_equity = money(cash_cny)
        if history.latest is None:
            daily_return = None
            cumulative_return = Decimal("0")
            drawdown = Decimal("0")
        else:
            prior_equity = money(history.latest.total_equity_cny)
            daily_return = _quantize_return(_return_ratio(total_equity, prior_equity))
            cumulative_return = _quantize_return(
                _return_ratio(total_equity, history.initial_equity_cny)
            )
            peak_equity = max(history.peak_equity_cny, total_equity)
            drawdown = _quantize_return(_return_ratio(total_equity, peak_equity))
        return self.paper_repo.write_valuation(
            ValuationWriteSpec(
                account_id=account_id,
                valuation_date=valuation_date,
                cash_cny=cash_cny,
                positions_value_cny=money(0),
                total_equity_cny=total_equity,
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                drawdown=drawdown,
            ),
            fencing_token=fencing_token,
            owner_id=owner_id,
        )
