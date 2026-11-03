
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe


def create_bar(**overrides):
    values = {
        "symbol": "EURUSD",
        "timeframe": Timeframe.M1,
        "timestamp": datetime(
            2026, 1, 1, 10, 0, tzinfo=timezone.utc
        ),
        "open": Decimal("1.1000"),
        "high": Decimal("1.1050"),
        "low": Decimal("1.0990"),
        "close": Decimal("1.1030"),
        "volume": Decimal("100"),
    }

    values.update(overrides)
    return OHLCVBar(**values)


def test_create_valid_ohlcv_bar():
    bar = create_bar()

    assert bar.symbol == "EURUSD"
    assert bar.timeframe == Timeframe.M1
    assert bar.open == Decimal("1.1000")
    assert bar.high == Decimal("1.1050")
    assert bar.low == Decimal("1.0990")
    assert bar.close == Decimal("1.1030")
    assert bar.volume == Decimal("100")


def test_normalizes_symbol():
    bar = create_bar(symbol=" eurusd ")

    assert bar.symbol == "EURUSD"


def test_rejects_empty_symbol():
    with pytest.raises(ValueError, match="cannot be empty"):
        create_bar(symbol=" ")


def test_rejects_invalid_timeframe():
    with pytest.raises(TypeError, match="Timeframe"):
        create_bar(timeframe="1m")


def test_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        create_bar(timestamp=datetime(2026, 1, 1, 10, 0))


def test_normalizes_timestamp_to_utc():
    timestamp = datetime(
        2026, 1, 1, 13, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    bar = create_bar(timestamp=timestamp)

    assert bar.timestamp.hour == 10
    assert bar.timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "field",
    ["open", "high", "low", "close"],
)
def test_rejects_non_decimal_prices(field):
    with pytest.raises(TypeError, match="Decimal"):
        create_bar(**{field: 1.10})


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
    ],
)
def test_rejects_non_positive_prices(value):
    with pytest.raises(ValueError, match="greater than zero"):
        create_bar(open=value)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_rejects_non_finite_prices(value):
    with pytest.raises(ValueError, match="finite"):
        create_bar(open=value)


def test_rejects_high_below_open():
    with pytest.raises(ValueError, match="High"):
        create_bar(high=Decimal("1.0990"))


def test_rejects_low_above_close():
    with pytest.raises(ValueError, match="Low"):
        create_bar(low=Decimal("1.1040"))


def test_accepts_zero_volume():
    bar = create_bar(volume=Decimal("0"))

    assert bar.volume == Decimal("0")


def test_rejects_negative_volume():
    with pytest.raises(ValueError, match="Volume"):
        create_bar(volume=Decimal("-1"))


def test_rejects_non_decimal_volume():
    with pytest.raises(TypeError, match="Decimal"):
        create_bar(volume=100.0)


def test_accepts_missing_volume():
    bar = create_bar(volume=None)

    assert bar.volume is None


def test_bar_is_immutable():
    bar = create_bar()

    with pytest.raises(FrozenInstanceError):
        bar.symbol = "GBPUSD"