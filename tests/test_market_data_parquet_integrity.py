"""Tests for Parquet storage integrity verification."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.integrity import (
    ParquetIntegrityError,
    ParquetIntegrityVerifier,
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


def test_verifies_ohlcv_object(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(i) for i in range(5)],
        )
    )

    asyncio.run(
        ParquetIntegrityVerifier(tmp_path).verify(
            dataset_version_id,
            "ohlcv",
            metadata,
        )
    )


def test_verifies_quote_object(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetQuoteWriter(tmp_path).write(
            dataset_version_id,
            [make_quote(i) for i in range(4)],
        )
    )

    asyncio.run(
        ParquetIntegrityVerifier(tmp_path).verify(
            dataset_version_id,
            "bid_ask",
            metadata,
        )
    )


def test_detects_checksum_mismatch(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(0)],
        )
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
            ParquetIntegrityVerifier(tmp_path).verify(
                dataset_version_id,
                "ohlcv",
                metadata,
            )
        )


def test_detects_size_mismatch(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(0)],
        )
    )

    incorrect_metadata = replace(
        metadata,
        size_bytes=metadata.size_bytes + 1,
    )

    with pytest.raises(
        ParquetIntegrityError,
        match="size",
    ):
        asyncio.run(
            ParquetIntegrityVerifier(tmp_path).verify(
                dataset_version_id,
                "ohlcv",
                incorrect_metadata,
            )
        )


def test_detects_record_count_mismatch(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(0), make_bar(1)],
        )
    )

    incorrect_metadata = replace(
        metadata,
        record_count=3,
    )

    with pytest.raises(
        ParquetIntegrityError,
        match="record count",
    ):
        asyncio.run(
            ParquetIntegrityVerifier(tmp_path).verify(
                dataset_version_id,
                "ohlcv",
                incorrect_metadata,
            )
        )


def test_detects_timestamp_boundary_mismatch(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(0), make_bar(1)],
        )
    )

    incorrect_metadata = replace(
        metadata,
        first_timestamp=BASE_TIMESTAMP + timedelta(minutes=1),
    )

    with pytest.raises(
        ParquetIntegrityError,
        match="boundaries",
    ):
        asyncio.run(
            ParquetIntegrityVerifier(tmp_path).verify(
                dataset_version_id,
                "ohlcv",
                incorrect_metadata,
            )
        )


def test_rejects_unexpected_storage_uri(tmp_path) -> None:
    dataset_version_id = uuid4()

    metadata = asyncio.run(
        ParquetOHLCVWriter(tmp_path).write(
            dataset_version_id,
            [make_bar(0)],
        )
    )

    incorrect_metadata = replace(
        metadata,
        storage_uri="file:///unexpected/location.parquet",
    )

    with pytest.raises(
        ParquetIntegrityError,
        match="Storage URI",
    ):
        asyncio.run(
            ParquetIntegrityVerifier(tmp_path).verify(
                dataset_version_id,
                "ohlcv",
                incorrect_metadata,
            )
        )


def test_missing_object_raises_file_not_found(tmp_path) -> None:
    metadata = StorageObjectMetadata(
        storage_uri=(
            tmp_path
            / "ohlcv"
            / f"{uuid4()}.parquet"
        ).as_uri(),
        storage_format="parquet",
        checksum_algorithm="sha256",
        checksum="0" * 64,
        size_bytes=1,
        record_count=1,
        first_timestamp=BASE_TIMESTAMP,
        last_timestamp=BASE_TIMESTAMP,
    )

    dataset_version_id = uuid4()

    # The URI must identify the requested version.
    metadata = replace(
        metadata,
        storage_uri=(
            tmp_path
            / "ohlcv"
            / f"{dataset_version_id}.parquet"
        ).as_uri(),
    )

    with pytest.raises(FileNotFoundError):
        asyncio.run(
            ParquetIntegrityVerifier(tmp_path).verify(
                dataset_version_id,
                "ohlcv",
                metadata,
            )
        )