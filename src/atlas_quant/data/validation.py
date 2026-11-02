
import pandas as pd


REQUIRED_COLUMNS = ["open", "high", "low", "close"]


def validate_ohlc(data: pd.DataFrame) -> pd.DataFrame:
    """Validate chronological OHLC market data."""

    if data.empty:
        raise ValueError("Market data is empty")

    missing = set(REQUIRED_COLUMNS) - set(data.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    result = data.copy()

    if not isinstance(result.index, pd.DatetimeIndex):
        raise ValueError(
            "Index must be a DatetimeIndex"
        )

    if result.index.hasnans:
        raise ValueError("Timestamps contain missing values")

    if result.index.tz is None:
        raise ValueError(
            "Timestamps must include a timezone"
        )

    result = result.sort_index()

    if result.index.has_duplicates:
        raise ValueError("Duplicate timestamps found")

    for column in REQUIRED_COLUMNS:
        result[column] = pd.to_numeric(
            result[column],
            errors="raise",
        )

    if not result[REQUIRED_COLUMNS].notna().all().all():
        raise ValueError("OHLC values contain missing data")

    if not result[REQUIRED_COLUMNS].map(
        lambda value: value != float("inf")
        and value != float("-inf")
    ).all().all():
        raise ValueError("OHLC data contains infinite values")

    if (result[REQUIRED_COLUMNS] <= 0).any().any():
        raise ValueError("Prices must be positive")

    invalid_candles = (
        (result["high"] < result["low"])
        | (result["high"] < result["open"])
        | (result["high"] < result["close"])
        | (result["low"] > result["open"])
        | (result["low"] > result["close"])
    )

    if invalid_candles.any():
        raise ValueError("Invalid OHLC candle detected")

    return result