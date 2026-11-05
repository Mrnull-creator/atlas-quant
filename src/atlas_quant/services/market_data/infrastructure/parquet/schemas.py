from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pyarrow as pa

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


PARQUET_SCHEMA_VERSION = "1"

OHLCV_PARQUET_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field(
            "timestamp",
            pa.timestamp("us", tz="UTC"),
            nullable=False,
        ),
        pa.field("open", pa.string(), nullable=False),
        pa.field("high", pa.string(), nullable=False),
        pa.field("low", pa.string(), nullable=False),
        pa.field("close", pa.string(), nullable=False),
        pa.field("volume", pa.string(), nullable=True),
    ],
    metadata={
        b"atlas_quant.schema_name": b"ohlcv",
        b"atlas_quant.schema_version": PARQUET_SCHEMA_VERSION.encode(),
    },
)

BID_ASK_PARQUET_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field(
            "timestamp",
            pa.timestamp("us", tz="UTC"),
            nullable=False,
        ),
        pa.field("bid", pa.string(), nullable=False),
        pa.field("ask", pa.string(), nullable=False),
        pa.field("bid_size", pa.string(), nullable=True),
        pa.field("ask_size", pa.string(), nullable=True),
    ],
    metadata={
        b"atlas_quant.schema_name": b"bid_ask",
        b"atlas_quant.schema_version": PARQUET_SCHEMA_VERSION.encode(),
    },
)


def ohlcv_bar_to_record(bar: OHLCVBar) -> dict[str, Any]:
    """Convert a validated OHLCV domain object to a Parquet-ready record."""

    if not isinstance(bar, OHLCVBar):
        raise TypeError("bar must be an OHLCVBar instance.")

    return {
        "symbol": bar.symbol,
        "timeframe": bar.timeframe.value,
        "timestamp": bar.timestamp,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": (
            str(bar.volume)
            if bar.volume is not None
            else None
        ),
    }


def ohlcv_bar_from_record(
    record: Mapping[str, Any],
) -> OHLCVBar:
    """Reconstruct a validated OHLCV domain object from a Parquet record."""

    return OHLCVBar(
        symbol=record["symbol"],
        timeframe=Timeframe(record["timeframe"]),
        timestamp=record["timestamp"],
        open=Decimal(record["open"]),
        high=Decimal(record["high"]),
        low=Decimal(record["low"]),
        close=Decimal(record["close"]),
        volume=(
            Decimal(record["volume"])
            if record["volume"] is not None
            else None
        ),
    )


def bid_ask_quote_to_record(
    quote: BidAskQuote,
) -> dict[str, Any]:
    """Convert a validated bid/ask domain object to a Parquet-ready record."""

    if not isinstance(quote, BidAskQuote):
        raise TypeError("quote must be a BidAskQuote instance.")

    return {
        "symbol": quote.symbol,
        "timestamp": quote.timestamp,
        "bid": str(quote.bid),
        "ask": str(quote.ask),
        "bid_size": (
            str(quote.bid_size)
            if quote.bid_size is not None
            else None
        ),
        "ask_size": (
            str(quote.ask_size)
            if quote.ask_size is not None
            else None
        ),
    }


def bid_ask_quote_from_record(
    record: Mapping[str, Any],
) -> BidAskQuote:
    """Reconstruct a validated bid/ask domain object from a Parquet record."""

    return BidAskQuote(
        symbol=record["symbol"],
        timestamp=record["timestamp"],
        bid=Decimal(record["bid"]),
        ask=Decimal(record["ask"]),
        bid_size=(
            Decimal(record["bid_size"])
            if record["bid_size"] is not None
            else None
        ),
        ask_size=(
            Decimal(record["ask_size"])
            if record["ask_size"] is not None
            else None
        ),
    )