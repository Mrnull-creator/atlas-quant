"""Integration tests for Parquet storage adapters."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.integrity import (
    ParquetIntegrityError,
)
from atlas_quant.services.market_data.infrastructure.parquet.storage import (
    ParquetOHLCVStorage,
    ParquetQuoteStorage,
)


BASE_TIMESTAMP = datetime(
    2026, 1, 1, 10, 0, tzinfo=timezone.utc
)


def make_bar(minute: int) -> OHLCVBar:
    return OHLCVBar(
        symbol="EURUSD",
        timeframe=Timeframe.M1,
        timestamp=BASE_TIMESTAMP + timedelta(minutes=minute),
        open=Decimal("1.1000"),
        high=Decimal("1.1100"),
        low=Decimal("1.0900"),
        close=Decimal("1.1050"),
        volume=Decimal("100.0000"),
    )


def make_quote(minute: int) -> BidAskQuote:
    return BidAskQuote(
        symbol="EURUSD",
        timestamp=BASE_TIMESTAMP + timedelta(minutes=minute),
        bid=Decimal("1.10000"),
        ask=Decimal("1.10020"),
        bid_size=Decimal("100000.00"),
        ask_size=Decimal("120000.00"),
    )


async def collect(iterator):
    return [item async for item in iterator]


def test_ohlcv_storage_write_read_and_verify(tmp_path) -> None:
    dataset_version_id = uuid4()
    observations = [make_bar(i) for i in range(5)]

    storage = ParquetOHLCVStorage(tmp_path, batch_size=2)

    metadata = asyncio.run(
        storage.write(dataset_version_id, observations)
    )

    actual = asyncio.run(
        collect(storage.read(dataset_version_id))
    )

    assert actual == observations
    assert metadata.record_count == 5

    asyncio.run(
        storage.verify(dataset_version_id, metadata)
    )


def test_quote_storage_write_read_and_verify(tmp_path) -> None:
    dataset_version_id = uuid4()
    observations = [make_quote(i) for i in range(4)]

    storage = ParquetQuoteStorage(tmp_path, batch_size=2)

    metadata = asyncio.run(
        storage.write(dataset_version_id, observations)
    )

    actual = asyncio.run(
        collect(storage.read(dataset_version_id))
    )

    assert actual == observations
    assert metadata.record_count == 4

    asyncio.run(
        storage.verify(dataset_version_id, metadata)
    )


def test_ohlcv_storage_rejects_overwrite(tmp_path) -> None:
    dataset_version_id = uuid4()
    storage = ParquetOHLCVStorage(tmp_path)

    asyncio.run(
        storage.write(dataset_version_id, [make_bar(0)])
    )

    with pytest.raises(FileExistsError):
        asyncio.run(
            storage.write(dataset_version_id, [make_bar(1)])
        )


def test_quote_storage_rejects_overwrite(tmp_path) -> None:
    dataset_version_id = uuid4()
    storage = ParquetQuoteStorage(tmp_path)

    asyncio.run(
        storage.write(dataset_version_id, [make_quote(0)])
    )

    with pytest.raises(FileExistsError):
        asyncio.run(
            storage.write(dataset_version_id, [make_quote(1)])
        )


def test_ohlcv_storage_applies_time_range(tmp_path) -> None:
    dataset_version_id = uuid4()
    storage = ParquetOHLCVStorage(tmp_path)

    asyncio.run(
        storage.write(
            dataset_version_id,
            [make_bar(i) for i in range(5)],
        )
    )

    actual = asyncio.run(
        collect(
            storage.read(
                dataset_version_id,
                start=BASE_TIMESTAMP + timedelta(minutes=1),
                end=BASE_TIMESTAMP + timedelta(minutes=4),
            )
        )
    )

    assert [bar.timestamp for bar in actual] == [
        BASE_TIMESTAMP + timedelta(minutes=1),
        BASE_TIMESTAMP + timedelta(minutes=2),
        BASE_TIMESTAMP + timedelta(minutes=3),
    ]


def test_storage_verification_detects_corruption(tmp_path) -> None:
    dataset_version_id = uuid4()
    storage = ParquetOHLCVStorage(tmp_path)

    metadata = asyncio.run(
        storage.write(dataset_version_id, [make_bar(0)])
    )

    path = (
        tmp_path
        / "ohlcv"
        / f"{dataset_version_id}.parquet"
    )

    original = path.read_bytes()
    path.write_bytes(
        bytes([original[0] ^ 1]) + original[1:]
    )

    with pytest.raises(
        ParquetIntegrityError,
        match="checksum",
    ):
        asyncio.run(
            storage.verify(dataset_version_id, metadata)
        )