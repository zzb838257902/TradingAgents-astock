"""Point-in-time validation helpers."""

from tradingagents.market_data.contracts import PITLevel


def require_pit_required(pit_level: str, dataset_name: str) -> None:
    if pit_level != PITLevel.PIT_REQUIRED:
        raise ValueError(
            f"dataset {dataset_name} has pit_level={pit_level}; "
            "historical backtest requires pit_required"
        )
