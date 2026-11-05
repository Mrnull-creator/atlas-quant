import threading
import asyncio
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.writer import (
    ParquetOHLCVWriter,
    ParquetQuoteWriter,
)


def make_bar(
    minute: int,
    close: str = "1.1050",
) -> OHLCVBar:
    return OHLCVBar(
        symbol="EURUSD",
        timeframe=Timeframe.M1,
        timestamp=datetime(
            2026,
            11,
            5,
            10,
            minute,
            tzinfo=timezone.utc,
        ),
        open=Decimal("1.1000"),
        high=Decimal("1.1100"),
        low=Decimal("1.0900"),
        close=Decimal(close),
        volume=Decimal("100.0000"),
    )


def make_quote(minute: int) -> BidAskQuote:
    return BidAskQuote(
        symbol="EURUSD",
        timestamp=datetime(
            2026,
            11,
            5,
            10,
            minute,
            tzinfo=timezone.utc,
        ),
        bid=Decimal("1.10500"),
        ask=Decimal("1.10510"),
        bid_size=Decimal("10.000"),
        ask_size=Decimal("12.500"),
    )


def test_ohlcv_writer_creates_verified_parquet_file(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    writer = ParquetOHLCVWriter(
        tmp_path,
        batch_size=2,
    )

    metadata = asyncio.run(
        writer.write(
            dataset_version_id,
            [
                make_bar(2),
                make_bar(0),
                make_bar(1),
            ],
        )
    )

    expected_path = (
        tmp_path
        / "ohlcv"
        / f"{dataset_version_id}.parquet"
    )

    assert expected_path.is_file()
    assert metadata.storage_uri == expected_path.resolve().as_uri()
    assert metadata.storage_format == "parquet"
    assert metadata.checksum_algorithm == "sha256"
    assert metadata.size_bytes == expected_path.stat().st_size
    assert metadata.record_count == 3

    # The boundaries represent the actual minimum and maximum timestamps,
    # not the order in which observations were supplied.
    assert metadata.first_timestamp == make_bar(0).timestamp
    assert metadata.last_timestamp == make_bar(2).timestamp

    table = pq.read_table(expected_path)

    assert table.num_rows == 3
    assert table.column("symbol").to_pylist() == [
        "EURUSD",
        "EURUSD",
        "EURUSD",
    ]
    assert table.column("close").to_pylist() == [
        "1.1050",
        "1.1050",
        "1.1050",
    ]

    digest = hashlib.sha256(expected_path.read_bytes()).hexdigest()
    assert metadata.checksum == digest


def test_ohlcv_writer_does_not_overwrite_existing_version(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    writer = ParquetOHLCVWriter(tmp_path)

    first_metadata = asyncio.run(
        writer.write(dataset_version_id, [make_bar(0)])
    )

    original_path = (
        tmp_path
        / "ohlcv"
        / f"{dataset_version_id}.parquet"
    )
    original_bytes = original_path.read_bytes()

    with pytest.raises(FileExistsError):
        asyncio.run(
            writer.write(
                dataset_version_id,
                [make_bar(1, close="1.1050")],
            )
        )

    assert original_path.read_bytes() == original_bytes
    assert first_metadata.checksum == hashlib.sha256(
        original_bytes
    ).hexdigest()


def test_ohlcv_writer_rejects_empty_dataset_version(
    tmp_path,
) -> None:
    writer = ParquetOHLCVWriter(tmp_path)

    with pytest.raises(ValueError, match="empty"):
        asyncio.run(
            writer.write(uuid4(), [])
        )

    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_ohlcv_writer_cleans_up_after_invalid_observation(
    tmp_path,
) -> None:
    writer = ParquetOHLCVWriter(
        tmp_path,
        batch_size=1,
    )

    valid_bar = make_bar(0)
    invalid_observation = object()

    with pytest.raises(TypeError):
        asyncio.run(
            writer.write(
                uuid4(),
                [valid_bar, invalid_observation],
            )
        )

    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_quote_writer_creates_verified_parquet_file(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    writer = ParquetQuoteWriter(
        tmp_path,
        batch_size=1,
    )

    metadata = asyncio.run(
        writer.write(
            dataset_version_id,
            [make_quote(0), make_quote(1)],
        )
    )

    expected_path = (
        tmp_path
        / "bid_ask"
        / f"{dataset_version_id}.parquet"
    )

    assert expected_path.is_file()
    assert metadata.storage_uri == expected_path.resolve().as_uri()
    assert metadata.record_count == 2
    assert metadata.first_timestamp == make_quote(0).timestamp
    assert metadata.last_timestamp == make_quote(1).timestamp

    table = pq.read_table(expected_path)

    assert table.num_rows == 2
    assert table.column("bid").to_pylist() == [
        "1.10500",
        "1.10500",
    ]
    assert table.column("ask").to_pylist() == [
        "1.10510",
        "1.10510",
    ]


def test_quote_writer_does_not_overwrite_existing_version(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    writer = ParquetQuoteWriter(tmp_path)

    asyncio.run(
        writer.write(dataset_version_id, [make_quote(0)])
    )

    original_path = (
        tmp_path
        / "bid_ask"
        / f"{dataset_version_id}.parquet"
    )
    original_bytes = original_path.read_bytes()

    with pytest.raises(FileExistsError):
        asyncio.run(
            writer.write(
                dataset_version_id,
                [make_quote(1)],
            )
        )

    assert original_path.read_bytes() == original_bytes


def test_writer_rejects_invalid_batch_size(tmp_path) -> None:
    with pytest.raises(ValueError):
        ParquetOHLCVWriter(tmp_path, batch_size=0)

    with pytest.raises(ValueError):
        ParquetOHLCVWriter(tmp_path, batch_size=-1)

    with pytest.raises(ValueError):
        ParquetOHLCVWriter(tmp_path, batch_size=True)
def test_writer_cancellation_waits_for_worker_and_removes_published_file(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    writer = ParquetOHLCVWriter(tmp_path)

    worker_started = threading.Event()
    release_worker = threading.Event()

    original_write_sync = writer._write_sync

    def blocking_write(*args):
        worker_started.set()

        if not release_worker.wait(timeout=10):
            raise TimeoutError("Test worker was not released.")

        return original_write_sync(*args)

    writer._write_sync = blocking_write

    async def scenario() -> None:
        task = asyncio.create_task(
            writer.write(dataset_version_id, [make_bar(0)])
        )

        assert await asyncio.to_thread(
            worker_started.wait,
            5,
        )

        task.cancel()
        await asyncio.sleep(0.05)

        # Cancellation must not let the caller finish while the worker
        # is still operating on the file.
        assert not task.done()

        release_worker.set()

        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
    finally:
        release_worker.set()

    final_path = (
        tmp_path
        / "ohlcv"
        / f"{dataset_version_id}.parquet"
    )

    assert not final_path.exists()
    assert not list(tmp_path.rglob("*.tmp"))