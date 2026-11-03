
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from atlas_quant.services.market_data.contracts.market_data import (
    BidAskQuoteContract,
    OHLCVBarContract,
)
from atlas_quant.services.market_data.domain.enums import Timeframe


def create_bar(**overrides):
    values = {
        "symbol": "EURUSD",
        "timeframe": Timeframe.M1,
        "timestamp": datetime(
            2026, 1, 1, 12, 0, tzinfo=timezone.utc
        ),
        "open": Decimal("1.1000"),
        "high": Decimal("1.1050"),
        "low": Decimal("1.0950"),
        "close": Decimal("1.1020"),
        "volume": Decimal("100"),
    }

    values.update(overrides)
    return OHLCVBarContract(**values)


def create_quote(**overrides):
    values = {
        "symbol": "EURUSD",
        "timestamp": datetime(
            2026, 1, 1, 12, 0, tzinfo=timezone.utc
        ),
        "bid": Decimal("1.1000"),
        "ask": Decimal("1.1002"),
        "bid_size": Decimal("10"),
        "ask_size": Decimal("12"),
    }

    values.update(overrides)
    return BidAskQuoteContract(**values)


# OHLCV bar contract tests


def test_create_ohlcv_bar_contract():
    bar = create_bar()

    assert bar.symbol == "EURUSD"
    assert bar.timeframe == Timeframe.M1
    assert bar.open == Decimal("1.1000")
    assert bar.volume == Decimal("100")


def test_bar_normalizes_symbol():
    bar = create_bar(symbol=" eurusd ")

    assert bar.symbol == "EURUSD"


def test_bar_rejects_empty_symbol():
    with pytest.raises(ValidationError):
        create_bar(symbol=" ")


def test_bar_rejects_invalid_timeframe():
    with pytest.raises(ValidationError):
        create_bar(timeframe="invalid")


def test_bar_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        create_bar(
            timestamp=datetime(2026, 1, 1, 12, 0)
        )


def test_bar_normalizes_timestamp_to_utc():
    timestamp = datetime(
        2026, 1, 1, 15, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    bar = create_bar(timestamp=timestamp)

    assert bar.timestamp.hour == 12
    assert bar.timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "field",
    ["open", "high", "low", "close"],
)
@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_bar_rejects_invalid_prices(field, value):
    with pytest.raises(ValidationError):
        create_bar(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "open": Decimal("1.1100"),
            "high": Decimal("1.1050"),
        },
        {
            "close": Decimal("1.1100"),
            "high": Decimal("1.1050"),
        },
        {
            "low": Decimal("1.1100"),
        },
    ],
)
def test_bar_rejects_invalid_ohlc_relationships(overrides):
    with pytest.raises(ValidationError):
        create_bar(**overrides)


def test_bar_accepts_missing_volume():
    bar = create_bar(volume=None)

    assert bar.volume is None


def test_bar_accepts_zero_volume():
    bar = create_bar(volume=Decimal("0"))

    assert bar.volume == Decimal("0")


@pytest.mark.parametrize(
    "value",
    [
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_bar_rejects_invalid_volume(value):
    with pytest.raises(ValidationError):
        create_bar(volume=value)


def test_bar_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        create_bar(unexpected_field="value")


def test_bar_is_immutable():
    bar = create_bar()

    with pytest.raises(ValidationError):
        bar.symbol = "GBPUSD"


# Bid/ask quote contract tests


def test_create_bid_ask_quote_contract():
    quote = create_quote()

    assert quote.symbol == "EURUSD"
    assert quote.bid == Decimal("1.1000")
    assert quote.ask == Decimal("1.1002")


def test_quote_normalizes_symbol():
    quote = create_quote(symbol=" eurusd ")

    assert quote.symbol == "EURUSD"


def test_quote_rejects_empty_symbol():
    with pytest.raises(ValidationError):
        create_quote(symbol=" ")


def test_quote_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        create_quote(
            timestamp=datetime(2026, 1, 1, 12, 0)
        )


def test_quote_normalizes_timestamp_to_utc():
    timestamp = datetime(
        2026, 1, 1, 15, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    quote = create_quote(timestamp=timestamp)

    assert quote.timestamp.hour == 12
    assert quote.timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "field",
    ["bid", "ask"],
)
@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_quote_rejects_invalid_prices(field, value):
    with pytest.raises(ValidationError):
        create_quote(**{field: value})


def test_quote_preserves_crossed_market():
    quote = create_quote(
        bid=Decimal("1.1010"),
        ask=Decimal("1.1000"),
    )

    assert quote.bid > quote.ask


def test_quote_accepts_missing_sizes():
    quote = create_quote(
        bid_size=None,
        ask_size=None,
    )

    assert quote.bid_size is None
    assert quote.ask_size is None


def test_quote_accepts_zero_sizes():
    quote = create_quote(
        bid_size=Decimal("0"),
        ask_size=Decimal("0"),
    )

    assert quote.bid_size == Decimal("0")
    assert quote.ask_size == Decimal("0")


@pytest.mark.parametrize(
    "field",
    ["bid_size", "ask_size"],
)
@pytest.mark.parametrize(
    "value",
    [
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_quote_rejects_invalid_sizes(field, value):
    with pytest.raises(ValidationError):
        create_quote(**{field: value})


def test_quote_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        create_quote(unexpected_field="value")


def test_quote_is_immutable():
    quote = create_quote()

    with pytest.raises(ValidationError):
        quote.symbol = "GBPUSD"