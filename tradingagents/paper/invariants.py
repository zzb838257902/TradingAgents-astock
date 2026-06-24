"""Account ledger invariant checks for paper portfolio."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import duckdb

from tradingagents.paper.contracts import money
from tradingagents.paper.exceptions import AccountNotFound, PaperError


class InvariantViolation(PaperError):
    """An account ledger invariant failed validation."""


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def assert_account_invariants(
    connection: duckdb.DuckDBPyConnection,
    account_id: str,
    *,
    as_of_date: date | None = None,
) -> None:
    account_row = connection.execute(
        """
        SELECT initial_cash_cny
        FROM paper_accounts
        WHERE account_id = ?
        """,
        [account_id],
    ).fetchone()
    if account_row is None:
        raise AccountNotFound(f"account {account_id} not found")

    initial_cash = money(_decimal(account_row[0]))
    cash_rows = connection.execute(
        """
        SELECT component, amount_cny
        FROM paper_cash_ledger
        WHERE account_id = ?
        ORDER BY occurred_at, cash_entry_id
        """,
        [account_id],
    ).fetchall()

    total_cash = money(sum(_decimal(amount) for _, amount in cash_rows))
    non_initial_cash = money(
        sum(
            _decimal(amount)
            for component, amount in cash_rows
            if component != "INITIAL_CASH"
        )
    )
    expected_cash = money(initial_cash + non_initial_cash)
    if total_cash != expected_cash:
        raise InvariantViolation(
            f"cash invariant failed for {account_id}: "
            f"ledger sum {total_cash} != initial {initial_cash} + activity {non_initial_cash}"
        )

    initial_rows = [
        _decimal(amount) for component, amount in cash_rows if component == "INITIAL_CASH"
    ]
    if initial_rows and money(sum(initial_rows)) != initial_cash:
        raise InvariantViolation(
            f"INITIAL_CASH entries {money(sum(initial_rows))} != initial_cash_cny {initial_cash}"
        )
    if total_cash < Decimal("0"):
        raise InvariantViolation(f"negative cash balance {total_cash} for {account_id}")

    ledger_positions = connection.execute(
        """
        SELECT symbol, COALESCE(SUM(quantity_delta), 0)
        FROM paper_position_ledger
        WHERE account_id = ?
        GROUP BY symbol
        """,
        [account_id],
    ).fetchall()
    ledger_qty = {symbol: int(qty) for symbol, qty in ledger_positions}

    lot_rows = connection.execute(
        """
        SELECT symbol, COALESCE(SUM(remaining_quantity), 0),
               COALESCE(SUM(remaining_cost_cny), 0)
        FROM paper_lots
        WHERE account_id = ? AND closed_at IS NULL
        GROUP BY symbol
        """,
        [account_id],
    ).fetchall()
    lot_qty = {symbol: int(qty) for symbol, qty, _ in lot_rows}
    lot_cost = {symbol: money(_decimal(cost)) for symbol, _, cost in lot_rows}

    for symbol, qty in ledger_qty.items():
        if qty < 0:
            raise InvariantViolation(f"negative position quantity {qty} for {symbol}")
        lot_total = lot_qty.get(symbol, 0)
        if lot_total != qty:
            raise InvariantViolation(
                f"lot quantity {lot_total} != ledger quantity {qty} for {symbol}"
            )

    projection_rows = connection.execute(
        """
        SELECT symbol, quantity, available_quantity, average_cost_cny
        FROM paper_positions
        WHERE account_id = ?
        """,
        [account_id],
    ).fetchall()
    for symbol, quantity, available_quantity, average_cost_cny in projection_rows:
        quantity = int(quantity)
        available_quantity = int(available_quantity)
        if quantity < 0 or available_quantity < 0:
            raise InvariantViolation(f"negative projected position for {symbol}")
        if available_quantity > quantity:
            raise InvariantViolation(
                f"available_quantity {available_quantity} > quantity {quantity} for {symbol}"
            )
        ledger_quantity = ledger_qty.get(symbol, 0)
        if quantity != ledger_quantity:
            raise InvariantViolation(
                f"projection quantity {quantity} != ledger quantity {ledger_quantity} for {symbol}"
            )
        if quantity > 0:
            expected_cost = money(lot_cost.get(symbol, Decimal("0")))
            projected_cost = money(_decimal(average_cost_cny))
            if projected_cost != expected_cost and quantity == lot_qty.get(symbol, 0):
                avg_from_lots = money(expected_cost / Decimal(quantity))
                if projected_cost != avg_from_lots:
                    raise InvariantViolation(
                        f"average_cost {projected_cost} != lot average {avg_from_lots} for {symbol}"
                    )

    if as_of_date is not None:
        available_rows = connection.execute(
            """
            SELECT symbol, COALESCE(SUM(remaining_quantity), 0)
            FROM paper_lots
            WHERE account_id = ?
              AND closed_at IS NULL
              AND acquired_date < ?
            GROUP BY symbol
            """,
            [account_id, as_of_date],
        ).fetchall()
        available_by_symbol = {symbol: int(qty) for symbol, qty in available_rows}
        for symbol, _, available_quantity, _ in projection_rows:
            expected_available = available_by_symbol.get(symbol, 0)
            if int(available_quantity) != expected_available:
                raise InvariantViolation(
                    f"T+1 available quantity {available_quantity} != expected {expected_available} "
                    f"for {symbol} as of {as_of_date}"
                )

    nav_row = connection.execute(
        """
        SELECT valuation_date, cash_cny, positions_value_cny, total_equity_cny
        FROM paper_nav_snapshots
        WHERE account_id = ?
        ORDER BY valuation_date DESC
        LIMIT 1
        """,
        [account_id],
    ).fetchone()
    if nav_row is not None:
        nav_date, nav_cash, positions_value, total_equity = nav_row
        nav_cash = money(_decimal(nav_cash))
        positions_value = money(_decimal(positions_value))
        total_equity = money(_decimal(total_equity))
        if total_equity != money(nav_cash + positions_value):
            raise InvariantViolation(
                f"NAV invariant failed: {total_equity} != {nav_cash} + {positions_value}"
            )
        if as_of_date is None or nav_date >= as_of_date:
            cash_at_nav = money(
                _decimal(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(amount_cny), 0)
                        FROM paper_cash_ledger
                        WHERE account_id = ? AND CAST(occurred_at AS DATE) <= ?
                        """,
                        [account_id, nav_date],
                    ).fetchone()[0]
                )
            )
            if nav_cash != cash_at_nav:
                raise InvariantViolation(
                    f"NAV cash {nav_cash} != ledger cash {cash_at_nav} for {account_id}"
                )
