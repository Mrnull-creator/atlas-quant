
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.domain.quotes import BidAskQuote


def create_quote(**overrides):
    values = {
        "symbol": "EURUSD",
        "timestamp": datetime(
            2026, 1, 1, 10, 0, tzinfo=timezone.utc
        ),
        "bid": Decimal("1.10000"),
        "ask": Decimal("1.10020"),
        "bid_size": Decimal("100"),
        "ask_size": Decimal("150"),
    }

    values.update(overrides)
    return BidAskQuote(**values)


def test_create_valid_quote():
    quote = create_quote()

    assert quote.symbol == "EURUSD"
    assert quote.bid == Decimal("1.10000")
    assert quote.ask == Decimal("1.10020")
    assert quote.bid_size == Decimal("100")
    assert quote.ask_size == Decimal("150")


def test_normalizes_symbol():
    quote = create_quote(symbol=" eurusd ")

    assert quote.symbol == "EURUSD"


def test_rejects_empty_symbol():
    with pytest.raises(ValueError, match="cannot be empty"):
        create_quote(symbol=" ")


def test_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        create_quote(timestamp=datetime(2026, 1, 1, 10, 0))


def test_normalizes_timestamp_to_utc():
    timestamp = datetime(
        2026, 1, 1, 13, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    quote = create_quote(timestamp=timestamp)

    assert quote.timestamp.hour == 10
    assert quote.timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize("field", ["bid", "ask"])
def test_rejects_non_decimal_prices(field):
    with pytest.raises(TypeError, match="Decimal"):
        create_quote(**{field: 1.10})


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
@pytest.mark.parametrize("field", ["bid", "ask"])
def test_rejects_invalid_prices(field, value):
    with pytest.raises(ValueError):
        create_quote(**{field: value})


@pytest.mark.parametrize(
    "field",
    ["bid_size", "ask_size"],
)
def test_accepts_zero_quantity(field):
    quote = create_quote(**{field: Decimal("0")})

    assert getattr(quote, field) == Decimal("0")


@pytest.mark.parametrize(
    "field",
    ["bid_size", "ask_size"],
)
def test_rejects_negative_quantity(field):
    with pytest.raises(ValueError, match="nonnegative"):
        create_quote(**{field: Decimal("-1")})


@pytest.mark.parametrize(
    "field",
    ["bid_size", "ask_size"],
)
def test_rejects_non_decimal_quantity(field):
    with pytest.raises(TypeError, match="Decimal"):
        create_quote(**{field: 100.0})


@pytest.mark.parametrize(
    "field",
    ["bid_size", "ask_size"],
)
def test_rejects_non_finite_quantity(field):
    with pytest.raises(ValueError, match="nonnegative"):
        create_quote(**{field: Decimal("NaN")})


def test_accepts_missing_quantities():
    quote = create_quote(
        bid_size=None,
        ask_size=None,
    )

    assert quote.bid_size is None
    assert quote.ask_size is None


def test_preserves_crossed_quotes():
    quote = create_quote(
        bid=Decimal("1.10100"),
        ask=Decimal("1.10000"),
    )

    assert quote.bid > quote.ask


def test_quote_is_immutable():
    quote = create_quote()

    with pytest.raises(FrozenInstanceError):
        quote.symbol = "GBPUSD"