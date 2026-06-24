from datetime import date, datetime, timezone

from tradingagents.market_data.contracts import (
    DataResult,
    DataStatus,
    PITLevel,
    SecurityRecord,
)


def test_error_result_is_not_usable():
    result = DataResult[list[int]](
        data=None,
        status=DataStatus.ERROR,
        source="test",
        as_of=datetime(2026, 6, 19, tzinfo=timezone.utc),
        available_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        pit_level=PITLevel.PIT_REQUIRED,
        errors=["timeout"],
    )
    assert not result.is_usable


def test_current_only_result_rejected_for_historical_mode():
    result = DataResult[list[int]](
        data=[1],
        status=DataStatus.OK,
        source="test",
        as_of=datetime(2026, 6, 19, tzinfo=timezone.utc),
        available_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        pit_level=PITLevel.CURRENT_ONLY,
    )
    assert not result.usable_in_historical_mode


def test_security_record_uses_effective_dates():
    record = SecurityRecord(
        symbol="600001",
        name="示例股份",
        board="main",
        valid_from=date(2020, 1, 1),
        valid_to=date(2024, 5, 1),
        list_date=date(2000, 1, 1),
        delist_date=date(2024, 5, 1),
        status="listed",
        st_flag=False,
        available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        source="fixture",
    )
    assert record.was_effective_on(date(2023, 1, 3))
    assert not record.was_effective_on(date(2025, 1, 3))
