"""Corporate action processing for paper portfolio (Stage 6A)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal

from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import CorporateActionRecord
from tradingagents.paper.contracts import (
    CashEntry,
    CashEntryType,
    CorporateActionApplicationStatus,
    PositionEntry,
    PositionSourceType,
    money,
)
from tradingagents.paper.repository import CorporateActionApplicationSpec, PaperRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")

CorporateActionStatus = CorporateActionApplicationStatus


@dataclass(frozen=True)
class CorporateActionApplicationResult:
    corporate_action_id: str
    application_key: str
    status: CorporateActionApplicationStatus
    entitlement_quantity: int
    revision: int = 1


def entitlement_source_hash(
    account_id: str,
    action: CorporateActionRecord,
    entitlement_quantity: int,
) -> str:
    payload = {
        "account_id": account_id,
        "corporate_action_id": action.corporate_action_id,
        "symbol": action.symbol,
        "record_date": action.record_date.isoformat() if action.record_date else None,
        "entitlement_quantity": entitlement_quantity,
        "source_version": action.source_version,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CorporateActionProcessor:
    def __init__(
        self,
        paper_repo: PaperRepository,
        *,
        account_id: str = "demo",
        owner_id: str = "corporate-actions",
    ) -> None:
        self.paper_repo = paper_repo
        self.account_id = account_id
        self.owner_id = owner_id

    def apply(
        self,
        action: CorporateActionRecord,
        *,
        revision: int = 1,
    ) -> CorporateActionApplicationResult:
        existing = self.paper_repo.get_active_corporate_action_application(
            self.account_id,
            action.corporate_action_id,
        )
        if existing is not None and existing["status"] == CorporateActionApplicationStatus.APPLIED.value:
            return CorporateActionApplicationResult(
                corporate_action_id=action.corporate_action_id,
                application_key=f"{self.account_id}:{action.corporate_action_id}:{existing['revision']}",
                status=CorporateActionApplicationStatus.APPLIED,
                entitlement_quantity=int(existing["entitlement_quantity"]),
                revision=int(existing["revision"]),
            )

        if action.record_date is None:
            return self._record_manual(action, entitlement_quantity=0, revision=revision)

        entitlement = self.paper_repo.position_quantity_on(
            self.account_id,
            action.symbol,
            action.record_date,
        )
        if entitlement <= 0:
            return self._record_manual(action, entitlement_quantity=0, revision=revision)

        if action.action_type == "cash_div":
            return self._apply_cash_dividend(action, entitlement, revision=revision)
        if action.action_type == "stock_event":
            return self._apply_stock_event(action, entitlement, revision=revision)
        if action.action_type == "split":
            return self._apply_split(action, entitlement, revision=revision)
        return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)

    def _apply_cash_dividend(
        self,
        action: CorporateActionRecord,
        entitlement: int,
        *,
        revision: int,
    ) -> CorporateActionApplicationResult:
        if action.pay_date is None or action.cash_div is None:
            return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)

        dividend_amount = money(Decimal(str(action.cash_div)) * Decimal(entitlement))
        occurred_at = datetime.combine(action.pay_date, time(9, 0), tzinfo=SHANGHAI)
        cash_entry = CashEntry(
            cash_entry_id=f"cash-{action.corporate_action_id}",
            account_id=self.account_id,
            entry_type=CashEntryType.DIVIDEND,
            amount_cny=dividend_amount,
            source_type="CORPORATE_ACTION",
            source_id=action.corporate_action_id,
            component="CASH_DIVIDEND",
            occurred_at=occurred_at,
        )
        lease = self.paper_repo.acquire_account_lease(self.account_id, owner_id=self.owner_id)
        application_key = self.paper_repo.apply_corporate_action(
            CorporateActionApplicationSpec(
                account_id=self.account_id,
                corporate_action_id=action.corporate_action_id,
                revision=revision,
                entitlement_quantity=entitlement,
                entitlement_source_hash=entitlement_source_hash(
                    self.account_id, action, entitlement
                ),
                status=CorporateActionApplicationStatus.APPLIED,
            ),
            fencing_token=lease.token,
            owner_id=lease.owner_id,
            cash_entry=cash_entry,
            effective_date=action.pay_date,
        )
        return CorporateActionApplicationResult(
            corporate_action_id=action.corporate_action_id,
            application_key=application_key,
            status=CorporateActionApplicationStatus.APPLIED,
            entitlement_quantity=entitlement,
            revision=revision,
        )

    def _apply_stock_event(
        self,
        action: CorporateActionRecord,
        entitlement: int,
        *,
        revision: int,
    ) -> CorporateActionApplicationResult:
        if action.rights_ratio:
            return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)
        if not action.stock_div or action.stock_div <= 0:
            return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)

        bonus_exact = Decimal(str(action.stock_div)) * Decimal(entitlement)
        bonus_shares = int(bonus_exact)
        if bonus_exact != Decimal(bonus_shares):
            return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)

        target_total = entitlement + bonus_shares
        multiplier = Decimal(target_total) / Decimal(entitlement)
        return self._apply_quantity_adjustment(
            action,
            entitlement=entitlement,
            target_total=target_total,
            multiplier=multiplier,
            revision=revision,
        )

    def _apply_split(
        self,
        action: CorporateActionRecord,
        entitlement: int,
        *,
        revision: int,
    ) -> CorporateActionApplicationResult:
        if not action.split_ratio or action.split_ratio <= 0:
            return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)

        multiplier = Decimal(str(action.split_ratio))
        target_exact = multiplier * Decimal(entitlement)
        target_total = int(target_exact)
        if target_exact != Decimal(target_total):
            return self._record_manual(action, entitlement_quantity=entitlement, revision=revision)

        return self._apply_quantity_adjustment(
            action,
            entitlement=entitlement,
            target_total=target_total,
            multiplier=multiplier,
            revision=revision,
        )

    def _apply_quantity_adjustment(
        self,
        action: CorporateActionRecord,
        *,
        entitlement: int,
        target_total: int,
        multiplier: Decimal,
        revision: int,
    ) -> CorporateActionApplicationResult:
        quantity_delta = target_total - entitlement
        position_entry = PositionEntry(
            position_entry_id=f"pos-{action.corporate_action_id}",
            account_id=self.account_id,
            symbol=action.symbol,
            quantity_delta=quantity_delta,
            cost_delta_cny=money(0),
            effective_date=action.ex_date,
            source_type=PositionSourceType.CORPORATE_ACTION,
            source_id=action.corporate_action_id,
            component="QUANTITY",
            business_key=(
                f"{self.account_id}:CORPORATE_ACTION:{action.corporate_action_id}:QUANTITY"
            ),
        )
        lease = self.paper_repo.acquire_account_lease(self.account_id, owner_id=self.owner_id)
        application_key = self.paper_repo.apply_corporate_action(
            CorporateActionApplicationSpec(
                account_id=self.account_id,
                corporate_action_id=action.corporate_action_id,
                revision=revision,
                entitlement_quantity=entitlement,
                entitlement_source_hash=entitlement_source_hash(
                    self.account_id, action, entitlement
                ),
                status=CorporateActionApplicationStatus.APPLIED,
            ),
            fencing_token=lease.token,
            owner_id=lease.owner_id,
            position_entry=position_entry,
            lot_multiplier=multiplier,
            lot_target_total=target_total,
            effective_date=action.ex_date,
        )
        return CorporateActionApplicationResult(
            corporate_action_id=action.corporate_action_id,
            application_key=application_key,
            status=CorporateActionApplicationStatus.APPLIED,
            entitlement_quantity=entitlement,
            revision=revision,
        )

    def _record_manual(
        self,
        action: CorporateActionRecord,
        *,
        entitlement_quantity: int,
        revision: int,
    ) -> CorporateActionApplicationResult:
        lease = self.paper_repo.acquire_account_lease(self.account_id, owner_id=self.owner_id)
        application_key = self.paper_repo.apply_corporate_action(
            CorporateActionApplicationSpec(
                account_id=self.account_id,
                corporate_action_id=action.corporate_action_id,
                revision=revision,
                entitlement_quantity=entitlement_quantity,
                entitlement_source_hash=entitlement_source_hash(
                    self.account_id, action, entitlement_quantity
                ),
                status=CorporateActionApplicationStatus.NEEDS_MANUAL_ACTION,
            ),
            fencing_token=lease.token,
            owner_id=lease.owner_id,
        )
        return CorporateActionApplicationResult(
            corporate_action_id=action.corporate_action_id,
            application_key=application_key,
            status=CorporateActionApplicationStatus.NEEDS_MANUAL_ACTION,
            entitlement_quantity=entitlement_quantity,
            revision=revision,
        )
