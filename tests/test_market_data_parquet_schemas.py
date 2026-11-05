from datetime import datetime, timezone
from decimal import Decimal

import pyarrow as pa

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.schemas import (
    BID_ASK_PARQUET_SCHEMA,
    OHLCV_PARQUET_SCHEMA,
    PARQUET_SCHEMA_VERSION,
    bid_ask_quote_from_record,
    bid_ask_quote_to_record,
    ohlcv_bar_from_record,
    ohlcv_bar_to_record,
)


def test_parquet_schemas_have_expected_versioned_metadata() -> None:
    assert (
        OHLCV_PARQUET_SCHEMA.metadata[b"atlas_quant.schema_name"]
        == b"ohlcv"
    )
    assert (
        BID_ASK_PARQUET_SCHEMA.metadata[b"atlas_quant.schema_name"]
        == b"bid_ask"
    )

    assert (
        OHLCV_PARQUET_SCHEMA.metadata[b"atlas_quant.schema_version"]
        == PARQUET_SCHEMA_VERSION.encode()
    )
    assert (
        BID_ASK_PARQUET_SCHEMA.metadata[b"atlas_quant.schema_version"]
        == PARQUET_SCHEMA_VERSION.encode()
    )


def test_ohlcv_schema_uses_utc_timestamp_and_string_decimals() -> None:
    assert (
        OHLCV_PARQUET_SCHEMA.field("timestamp").type
        == pa.timestamp("us", tz="UTC")
    )

    for field_name in ("open", "high", "low", "close", "volume"):
        assert (
            OHLCV_PARQUET_SCHEMA.field(field_name).type
            == pa.string()
        )

    assert OHLCV_PARQUET_SCHEMA.field("volume").nullable is True


def test_bid_ask_schema_uses_utc_timestamp_and_string_decimals() -> None:
    assert (
        BID_ASK_PARQUET_SCHEMA.field("timestamp").type
        == pa.timestamp("us", tz="UTC")
    )

    for field_name in ("bid", "ask", "bid_size", "ask_size"):
        assert (
            BID_ASK_PARQUET_SCHEMA.field(field_name).type
            == pa.string()
        )

    assert BID_ASK_PARQUET_SCHEMA.field("bid_size").nullable is True
    assert BID_ASK_PARQUET_SCHEMA.field("ask_size").nullable is True


def test_ohlcv_bar_round_trip_preserves_decimal_values() -> None:
    original = OHLCVBar(
        symbol=" eurusd ",
        timeframe=Timeframe.M1,
        timestamp=datetime(
            2026,
            11,
            5,
            10,
            30,
            tzinfo=timezone.utc,
        ),
        open=Decimal("1.0834500"),
        high=Decimal("1.0840000"),
        low=Decimal("1.0829000"),
        close=Decimal("1.0837500"),
        volume=Decimal("12345.670000"),
    )

    record = ohlcv_bar_to_record(original)

    table = pa.Table.from_pylist(
        [record],
        schema=OHLCV_PARQUET_SCHEMA,
    )
    restored_record = table.to_pylist()[0]
    restored = ohlcv_bar_from_record(restored_record)

    assert restored == original
    assert str(restored.open) == "1.0834500"
    assert str(restored.high) == "1.0840000"
    assert str(restored.low) == "1.0829000"
    assert str(restored.close) == "1.0837500"
    assert str(restored.volume) == "12345.670000"
    assert restored.timestamp.tzinfo is not None
    assert restored.timestamp.utcoffset().total_seconds() == 0


def test_ohlcv_round_trip_preserves_missing_volume() -> None:
    original = OHLCVBar(
        symbol="EURUSD",
        timeframe=Timeframe.H1,
        timestamp=datetime(
            2026,
            11,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
        open=Decimal("1.1"),
        high=Decimal("1.2"),
        low=Decimal("1.0"),
        close=Decimal("1.15"),
        volume=None,
    )

    table = pa.Table.from_pylist(
        [ohlcv_bar_to_record(original)],
        schema=OHLCV_PARQUET_SCHEMA,
    )
    restored = ohlcv_bar_from_record(table.to_pylist()[0])

    assert restored == original
    assert restored.volume is None


def test_bid_ask_round_trip_preserves_decimal_values_and_crossed_quote() -> None:
    original = BidAskQuote(
        symbol=" eurusd ",
        timestamp=datetime(
            2026,
            11,
            5,
            10,
            30,
            0,
            123456,
            tzinfo=timezone.utc,
        ),
        bid=Decimal("1.08450000"),
        ask=Decimal("1.08440000"),
        bid_size=Decimal("10.2500"),
        ask_size=Decimal("12.7500"),
    )

    record = bid_ask_quote_to_record(original)

    table = pa.Table.from_pylist(
        [record],
        schema=BID_ASK_PARQUET_SCHEMA,
    )
    restored = bid_ask_quote_from_record(table.to_pylist()[0])

    assert restored == original
    assert str(restored.bid) == "1.08450000"
    assert str(restored.ask) == "1.08440000"
    assert str(restored.bid_size) == "10.2500"
    assert str(restored.ask_size) == "12.7500"
    assert restored.bid > restored.ask


def test_bid_ask_round_trip_preserves_missing_sizes() -> None:
    original = BidAskQuote(
        symbol="EURUSD",
        timestamp=datetime(
            2026,
            11,
            5,
            10,
            30,
            tzinfo=timezone.utc,
        ),
        bid=Decimal("1.0845"),
        ask=Decimal("1.0846"),
    )

    table = pa.Table.from_pylist(
        [bid_ask_quote_to_record(original)],
        schema=BID_ASK_PARQUET_SCHEMA,
    )
    restored = bid_ask_quote_from_record(table.to_pylist()[0])

    assert restored == original
    assert restored.bid_size is None
    assert restored.ask_size is None