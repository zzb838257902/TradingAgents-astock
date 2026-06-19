from datetime import date, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PITLevel(StrEnum):
    PIT_REQUIRED = "pit_required"
    CURRENT_ONLY = "current_only"
    BEST_EFFORT = "best_effort"


class DataStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    STALE = "stale"
    ERROR = "error"


class DataResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    data: T | None
    status: DataStatus
    source: str
    as_of: datetime
    available_at: datetime
    pit_level: PITLevel
    errors: list[str] = Field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return self.status in {DataStatus.OK, DataStatus.EMPTY} and not self.errors

    @property
    def usable_in_historical_mode(self) -> bool:
        return self.is_usable and self.pit_level == PITLevel.PIT_REQUIRED


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
