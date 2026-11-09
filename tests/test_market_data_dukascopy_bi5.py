from __future__ import annotations

import lzma
import math
import struct
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.dukascopy_bi5 import (
    BI5DecodeError,
    decode_bi5,
)


RECORD = struct.Struct(">IIIff")

PERIOD_START = datetime(2025, 1, 15, tzinfo=timezone.utc)


def make_payload(
    records: list[tuple[int, int, int, float, float]],
) -> bytes:
    raw = b"".join(RECORD.pack(*record) for record in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


def test_decodes_bi5_records_into_bid_ask_quotes() -> None:
    payload = make_payload(
        [
            (0, 108456, 108455, 1.25, 2.50),
            (1234, 108457, 108456, 1.50, 2.75),
        ]
    )

    result = decode_bi5(
        payload,
        symbol="EURUSD",
        period_start=PERIOD_START,
        period_duration=timedelta(hours=1),
        price_divisor=100000,
    )

    assert len(result) == 2
    assert all(isinstance(item, BidAskQuote) for item in result)

    assert result[0].symbol == "EURUSD"
    assert result[0].timestamp == PERIOD_START
    assert result[0].bid == Decimal("1.08455")
    assert result[0].ask == Decimal("1.08456")
    assert result[0].bid_size == Decimal("2.5")
    assert result[0].ask_size == Decimal("1.25")

    assert result[1].timestamp == (
        PERIOD_START + timedelta(milliseconds=1234)
    )


def test_supports_daily_periods() -> None:
    payload = make_payload(
        [(86_399_999, 108456, 108455, 1.0, 1.0)]
    )

    result = decode_bi5(
        payload,
        symbol="EURUSD",
        period_start=PERIOD_START,
        period_duration=timedelta(days=1),
        price_divisor=100000,
    )

    assert len(result) == 1
    assert result[0].timestamp == (
        PERIOD_START + timedelta(milliseconds=86_399_999)
    )


def test_empty_payload_returns_empty_tuple() -> None:
    result = decode_bi5(
        b"",
        symbol="EURUSD",
        period_start=PERIOD_START,
        period_duration=timedelta(hours=1),
        price_divisor=100000,
    )

    assert result == ()


def test_rejects_invalid_compressed_payload() -> None:
    with pytest.raises(BI5DecodeError, match="LZMA"):
        decode_bi5(
            b"not a valid BI5 payload",
            symbol="EURUSD",
            period_start=PERIOD_START,
            period_duration=timedelta(hours=1),
            price_divisor=100000,
        )


def test_rejects_incomplete_record() -> None:
    payload = lzma.compress(
        b"\x00" * 19,
        format=lzma.FORMAT_ALONE,
    )

    with pytest.raises(BI5DecodeError, match="incomplete record"):
        decode_bi5(
            payload,
            symbol="EURUSD",
            period_start=PERIOD_START,
            period_duration=timedelta(hours=1),
            price_divisor=100000,
        )


def test_rejects_timestamp_outside_period() -> None:
    payload = make_payload(
        [(3_600_000, 108456, 108455, 1.0, 1.0)]
    )

    with pytest.raises(BI5DecodeError, match="outside its period"):
        decode_bi5(
            payload,
            symbol="EURUSD",
            period_start=PERIOD_START,
            period_duration=timedelta(hours=1),
            price_divisor=100000,
        )


@pytest.mark.parametrize("volume", [math.nan, math.inf, -math.inf, -1.0])
def test_rejects_invalid_volume(volume: float) -> None:
    payload = make_payload(
        [(0, 108456, 108455, volume, 1.0)]
    )

    with pytest.raises(BI5DecodeError):
        decode_bi5(
            payload,
            symbol="EURUSD",
            period_start=PERIOD_START,
            period_duration=timedelta(hours=1),
            price_divisor=100000,
        )


def test_rejects_zero_price() -> None:
    payload = make_payload(
        [(0, 108456, 0, 1.0, 1.0)]
    )

    with pytest.raises(BI5DecodeError, match="Invalid market observation"):
        decode_bi5(
            payload,
            symbol="EURUSD",
            period_start=PERIOD_START,
            period_duration=timedelta(hours=1),
            price_divisor=100000,
        )


@pytest.mark.parametrize("divisor", [0, -1, True, 1.5])
def test_rejects_invalid_price_divisor(divisor: int) -> None:
    with pytest.raises(BI5DecodeError, match="price_divisor"):
        decode_bi5(
            b"",
            symbol="EURUSD",
            period_start=PERIOD_START,
            period_duration=timedelta(hours=1),
            price_divisor=divisor,
        )


def test_rejects_naive_period_start() -> None:
    with pytest.raises(BI5DecodeError, match="timezone-aware"):
        decode_bi5(
            b"",
            symbol="EURUSD",
            period_start=datetime(2025, 1, 15),
            period_duration=timedelta(hours=1),
            price_divisor=100000,
        )
