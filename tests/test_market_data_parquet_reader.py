"""Tests for incremental Parquet readers."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import threading

from atlas_quant.services.market_data.infrastructure.parquet.reader import (
    _ParquetBatchReader,
)
import pytest

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.reader import (
    ParquetOHLCVReader,
    ParquetQuoteReader,
)
from atlas_quant.services.market_data.infrastructure.parquet.writer import (
    ParquetOHLCVWriter,
    ParquetQuoteWriter,
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


def test_ohlcv_reader_round_trip(tmp_path) -> None:
    dataset_version_id = uuid4()
    observations = [make_bar(i) for i in range(5)]

    asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            observations,
        )
    )

    reader = ParquetOHLCVReader(tmp_path, batch_size=2)

    actual = asyncio.run(
        collect(reader.read(dataset_version_id))
    )

    assert actual == observations
    assert actual[0].open == Decimal("1.1000")
    assert actual[0].volume == Decimal("100.0000")


def test_ohlcv_reader_applies_inclusive_start_and_exclusive_end(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()

    asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(i) for i in range(5)],
        )
    )

    reader = ParquetOHLCVReader(tmp_path, batch_size=2)

    actual = asyncio.run(
        collect(
            reader.read(
                dataset_version_id,
                start=BASE_TIMESTAMP + timedelta(minutes=1),
                end=BASE_TIMESTAMP + timedelta(minutes=3),
            )
        )
    )

    assert [bar.timestamp for bar in actual] == [
        BASE_TIMESTAMP + timedelta(minutes=1),
        BASE_TIMESTAMP + timedelta(minutes=2),
    ]


def test_quote_reader_round_trip(tmp_path) -> None:
    dataset_version_id = uuid4()
    observations = [make_quote(i) for i in range(4)]

    asyncio.run(
        ParquetQuoteWriter(tmp_path).write(
            dataset_version_id,
            observations,
        )
    )

    reader = ParquetQuoteReader(tmp_path, batch_size=2)

    actual = asyncio.run(
        collect(reader.read(dataset_version_id))
    )

    assert actual == observations
    assert actual[0].bid == Decimal("1.10000")
    assert actual[0].ask_size == Decimal("120000.00")


def test_reader_rejects_naive_time_boundary(tmp_path) -> None:
    reader = ParquetOHLCVReader(tmp_path)

    with pytest.raises(ValueError, match="timezone"):
        reader.read(
            uuid4(),
            start=datetime(2026, 1, 1, 10, 0),
        )


def test_reader_rejects_invalid_time_range(tmp_path) -> None:
    reader = ParquetOHLCVReader(tmp_path)

    with pytest.raises(ValueError, match="earlier"):
        reader.read(
            uuid4(),
            start=BASE_TIMESTAMP + timedelta(minutes=2),
            end=BASE_TIMESTAMP + timedelta(minutes=1),
        )


def test_reader_raises_for_missing_dataset_version(tmp_path) -> None:
    reader = ParquetOHLCVReader(tmp_path)

    with pytest.raises(FileNotFoundError):
        asyncio.run(
            collect(reader.read(uuid4()))
        )


def test_reader_closes_file_when_iteration_is_stopped(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()

    asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(i) for i in range(5)],
        )
    )

    reader = ParquetOHLCVReader(tmp_path, batch_size=1)

    async def read_one_and_close():
        iterator = reader.read(dataset_version_id)

        first = await anext(iterator)
        await iterator.aclose()

        return first

    actual = asyncio.run(read_one_and_close())

    assert actual == make_bar(0)

    # On Windows, this also verifies that the reader released its handle.
    file_path = (
        tmp_path
        / "ohlcv"
        / f"{dataset_version_id}.parquet"
    )
    file_path.unlink()

    assert not file_path.exists()
def test_reader_cancellation_waits_for_batch_before_closing_file(
    tmp_path,
    monkeypatch,
) -> None:
    dataset_version_id = uuid4()

    asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(i) for i in range(5)],
        )
    )

    file_path = (
        tmp_path
        / "ohlcv"
        / f"{dataset_version_id}.parquet"
    )

    reader = ParquetOHLCVReader(tmp_path, batch_size=1)

    worker_started = threading.Event()
    release_worker = threading.Event()

    original_next_batch = _ParquetBatchReader.next_batch

    def blocking_next_batch(instance):
        worker_started.set()

        if not release_worker.wait(timeout=10):
            raise TimeoutError("Test reader was not released.")

        return original_next_batch(instance)

    monkeypatch.setattr(
        _ParquetBatchReader,
        "next_batch",
        blocking_next_batch,
    )

    async def scenario() -> None:
        task = asyncio.create_task(
            collect(reader.read(dataset_version_id))
        )

        assert await asyncio.to_thread(
            worker_started.wait,
            5,
        )

        task.cancel()
        await asyncio.sleep(0.05)

        # The reader must remain alive until its background operation ends.
        assert not task.done()

        release_worker.set()

        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
    finally:
        release_worker.set()

    # On Windows, this also confirms the file handle was released.
    file_path.unlink()
    assert not file_path.exists()