from datetime import date, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PITLevel(StrEnum):
    PIT_REQUIRED = "pit_required"
    CURRENT_ONLY = "current_only"
    BEST_EFFORT = "best_effort"


class PriceBasis(StrEnum):
    FORWARD_ADJUSTED = "forward_adjusted"
    RAW = "raw"


class DataStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    SUCCESS_EMPTY = "success_empty"
    STALE = "stale"
    PARTIAL = "partial"
    ERROR = "error"
    NOT_AVAILABLE_YET = "not_available_yet"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    DATA_QUALITY_FAILED = "data_quality_failed"


class MembershipMode(StrEnum):
    EFFECTIVE_INTERVAL = "effective_interval"
    DATED_SNAPSHOT = "dated_snapshot"
    CURRENT_ONLY = "current_only"


_EMPTY_UNIVERSE_STATUSES = {DataStatus.EMPTY, DataStatus.SUCCESS_EMPTY}
_SCREENING_USABLE_STATUSES = {DataStatus.OK, DataStatus.EMPTY}


class DataResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    data: T | None
    status: DataStatus
    source: str
    as_of: datetime
    available_at: datetime
    ingested_at: datetime | None = None
    run_time: datetime | None = None
    pit_level: PITLevel
    errors: list[str] = Field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """Backward-compatible usability check for phase 0-3 callers."""
        return self.status in _SCREENING_USABLE_STATUSES and not self.errors

    @property
    def is_usable_for_screening(self) -> bool:
        return self.status == DataStatus.OK and not self.errors

    @property
    def allows_empty_universe(self) -> bool:
        return self.status in _EMPTY_UNIVERSE_STATUSES and not self.errors

    @property
    def usable_in_historical_mode(self) -> bool:
        return self.is_usable_for_screening and self.pit_level == PITLevel.PIT_REQUIRED


class SecurityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    name: str
    board: str
    valid_from: date
    valid_to: date | None = None
    list_date: date
    delist_date: date | None = None
    status: str
    st_flag: bool
    available_at: datetime
    source: str

    def was_effective_on(self, value: date) -> bool:
        return self.valid_from <= value and (
            self.valid_to is None or value < self.valid_to
        )


class TradingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchange: str
    trade_date: date
    is_open: bool
    available_at: datetime
    source: str


class Membership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    board_type: str
    board_code: str
    symbol: str
    membership_mode: MembershipMode
    effective_from: date | None = None
    effective_to: date | None = None
    snapshot_date: date | None = None
    available_at: datetime
    source: str

    def was_member_on(self, value: date) -> bool:
        if self.membership_mode == MembershipMode.CURRENT_ONLY:
            return self.snapshot_date == value if self.snapshot_date else False
        if self.membership_mode == MembershipMode.DATED_SNAPSHOT:
            if self.snapshot_date is None:
                return False
            return value >= self.snapshot_date
        if self.effective_from is None:
            return False
        if value < self.effective_from:
            return False
        if self.effective_to is not None and value >= self.effective_to:
            return False
        return True

    def pit_member_on(self, value: date) -> bool:
        """Strict point-in-time membership for screening (dc_member daily snapshots)."""
        if self.membership_mode == MembershipMode.DATED_SNAPSHOT:
            return self.snapshot_date == value if self.snapshot_date else False
        return self.was_member_on(value)


class ProviderCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str
    endpoint: str
    permitted: bool
    history_start: date | None = None
    max_rows_per_call: int | None = None
    rate_limit_per_minute: int | None = None
    pit_level: PITLevel
    license_note: str = ""
    probed_at: datetime
    error: str | None = None
