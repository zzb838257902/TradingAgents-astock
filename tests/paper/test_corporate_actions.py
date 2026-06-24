"""Corporate action processor tests (Stage 6A Task 5)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import CorporateActionRecord
from tradingagents.paper.contracts import (
    CorporateActionApplicationStatus,
    PositionEntry,
    PositionSourceType,
)
from tradingagents.paper.corporate_actions import CorporateActionProcessor
from tradingagents.paper.repository import PaperRepository
from tests.paper.conftest import (
    append_position_with_lease,
    rebuild_projection_with_lease,
    seed_demo_account,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
RECORD_DATE = date(2026, 6, 22)
PAY_DATE = date(2026, 6, 25)
EX_DATE = date(2026, 6, 23)


def dividend(
    *,
    record_date: date | None = RECORD_DATE,
    pay_date: date | None = PAY_DATE,
    cash_div: float = 0.12,
    action_id: str = "ca-div-600000",
) -> CorporateActionRecord:
    return CorporateActionRecord(
        corporate_action_id=action_id,
        symbol="600000",
        ex_date=EX_DATE,
        action_type="cash_div",
        cash_div=cash_div,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="fixture",
        record_date=record_date,
        pay_date=pay_date,
    )


def seed_shares(
    repo: PaperRepository,
    *,
    quantity: int = 1000,
    acquired_date: date = date(2026, 6, 1),
    cost_cny: Decimal = Decimal("10000.00"),
) -> None:
    seed_demo_account(repo)
    append_position_with_lease(
        repo,
        PositionEntry(
            position_entry_id="pos-seed-600000",
            account_id="demo",
            symbol="600000",
            quantity_delta=quantity,
            cost_delta_cny=cost_cny,
            effective_date=acquired_date,
            source_type=PositionSourceType.ADJUSTMENT,
            source_id="seed",
            component="QUANTITY",
            business_key="demo:ADJUSTMENT:seed:QUANTITY",
        ),
    )
    rebuild_projection_with_lease(repo, as_of_date=RECORD_DATE)
    repo.expire_lease_for_test("demo")


def processor(repo: PaperRepository) -> CorporateActionProcessor:
    return CorporateActionProcessor(repo, owner_id="corp-test")


def test_dividend_uses_record_date_and_posts_on_pay_date(repo):
    seed_shares(repo)
    before = repo.cash_on("demo", PAY_DATE)
    application = processor(repo).apply(dividend())
    assert application.entitlement_quantity == 1000
    assert application.status == CorporateActionApplicationStatus.APPLIED
    assert repo.cash_on("demo", date(2026, 6, 24)) == before
    assert repo.cash_on("demo", PAY_DATE) == before + Decimal("120.00")


def test_dividend_without_pay_date_needs_manual_action(repo):
    seed_shares(repo)
    result = processor(repo).apply(dividend(pay_date=None))
    assert result.status == CorporateActionApplicationStatus.NEEDS_MANUAL_ACTION
    assert repo.cash_entries_for("demo", result.corporate_action_id) == []


def test_dividend_without_record_date_needs_manual_action(repo):
    seed_shares(repo)
    result = processor(repo).apply(dividend(record_date=None))
    assert result.status == CorporateActionApplicationStatus.NEEDS_MANUAL_ACTION
    assert repo.cash_entries_for("demo", result.corporate_action_id) == []


def test_stock_bonus_adjusts_quantity_on_ex_date(repo):
    seed_shares(repo, quantity=1000, cost_cny=Decimal("10000.00"))
    action = CorporateActionRecord(
        corporate_action_id="ca-bonus-600000",
        symbol="600000",
        ex_date=EX_DATE,
        action_type="stock_event",
        stock_div=0.3,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="fixture",
        record_date=RECORD_DATE,
        pay_date=None,
    )
    result = processor(repo).apply(action)
    assert result.status == CorporateActionApplicationStatus.APPLIED
    snapshot = repo.load_account_snapshot("demo", as_of_date=EX_DATE)
    assert snapshot.positions["600000"].quantity == 1300
    lot_row = repo.connection.execute(
        """
        SELECT remaining_quantity, remaining_cost_cny
        FROM paper_lots
        WHERE account_id = 'demo' AND symbol = '600000' AND closed_at IS NULL
        ORDER BY acquired_date, lot_id
        LIMIT 1
        """
    ).fetchone()
    assert lot_row[0] == 1300
    assert Decimal(str(lot_row[1])) == Decimal("10000.00")


def test_fractional_bonus_without_cash_in_lieu_needs_manual_action(repo):
    seed_shares(repo, quantity=1001)
    action = CorporateActionRecord(
        corporate_action_id="ca-frac-bonus",
        symbol="600000",
        ex_date=EX_DATE,
        action_type="stock_event",
        stock_div=0.1,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="fixture",
        record_date=RECORD_DATE,
    )
    result = processor(repo).apply(action)
    assert result.status == CorporateActionApplicationStatus.NEEDS_MANUAL_ACTION
    snapshot = repo.load_account_snapshot("demo", as_of_date=EX_DATE)
    assert snapshot.positions["600000"].quantity == 1001


def test_rights_ratio_needs_manual_action(repo):
    seed_shares(repo)
    action = CorporateActionRecord(
        corporate_action_id="ca-rights",
        symbol="600000",
        ex_date=EX_DATE,
        action_type="stock_event",
        rights_ratio=0.2,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="fixture",
        record_date=RECORD_DATE,
    )
    result = processor(repo).apply(action)
    assert result.status == CorporateActionApplicationStatus.NEEDS_MANUAL_ACTION


def test_apply_is_idempotent(repo):
    seed_shares(repo)
    proc = processor(repo)
    first = proc.apply(dividend())
    second = proc.apply(dividend())
    assert first.application_key == second.application_key
    assert len(repo.cash_entries_for("demo", first.corporate_action_id)) == 1


def test_split_preserves_total_cost(repo):
    seed_shares(repo, quantity=1000, cost_cny=Decimal("10000.00"))
    action = CorporateActionRecord(
        corporate_action_id="ca-split-600000",
        symbol="600000",
        ex_date=EX_DATE,
        action_type="split",
        split_ratio=2.0,
        available_at=datetime(2026, 6, 20, 15, 0, tzinfo=SHANGHAI),
        source="fixture",
        record_date=RECORD_DATE,
    )
    result = processor(repo).apply(action)
    assert result.status == CorporateActionApplicationStatus.APPLIED
    snapshot = repo.load_account_snapshot("demo", as_of_date=EX_DATE)
    assert snapshot.positions["600000"].quantity == 2000
    lot_row = repo.connection.execute(
        """
        SELECT remaining_quantity, remaining_cost_cny
        FROM paper_lots
        WHERE account_id = 'demo' AND symbol = '600000' AND closed_at IS NULL
        ORDER BY acquired_date, lot_id
        LIMIT 1
        """
    ).fetchone()
    assert lot_row[0] == 2000
    assert Decimal(str(lot_row[1])) == Decimal("10000.00")
