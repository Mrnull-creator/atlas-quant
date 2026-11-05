"""Parquet-backed implementations of market data storage interfaces."""

from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from pathlib import Path
from uuid import UUID

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.integrity import (
    ParquetIntegrityVerifier,
)
from atlas_quant.services.market_data.infrastructure.parquet.reader import (
    ParquetOHLCVReader,
    ParquetQuoteReader,
)
from atlas_quant.services.market_data.infrastructure.parquet.writer import (
    ParquetOHLCVWriter,
    ParquetQuoteWriter,
)


class ParquetOHLCVStorage:
    """Implements OHLCVStorage using immutable local Parquet files."""

    def __init__(
        self,
        root_directory: str | Path,
        batch_size: int = 10_000,
    ) -> None:
        self._writer = ParquetOHLCVWriter(
            root_directory,
            batch_size=batch_size,
        )
        self._reader = ParquetOHLCVReader(
            root_directory,
            batch_size=batch_size,
        )
        self._verifier = ParquetIntegrityVerifier(root_directory)

    async def write(
        self,
        dataset_version_id: UUID,
        bars: Iterable[OHLCVBar],
    ) -> StorageObjectMetadata:
        return await self._writer.write(dataset_version_id, bars)

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[OHLCVBar]:
        return self._reader.read(dataset_version_id, start, end)

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify the stored object before dataset publication."""

        await self._verifier.verify(
            dataset_version_id,
            "ohlcv",
            expected,
        )


class ParquetQuoteStorage:
    """Implements QuoteStorage using immutable local Parquet files."""

    def __init__(
        self,
        root_directory: str | Path,
        batch_size: int = 10_000,
    ) -> None:
        self._writer = ParquetQuoteWriter(
            root_directory,
            batch_size=batch_size,
        )
        self._reader = ParquetQuoteReader(
            root_directory,
            batch_size=batch_size,
        )
        self._verifier = ParquetIntegrityVerifier(root_directory)

    async def write(
        self,
        dataset_version_id: UUID,
        quotes: Iterable[BidAskQuote],
    ) -> StorageObjectMetadata:
        return await self._writer.write(dataset_version_id, quotes)

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[BidAskQuote]:
        return self._reader.read(dataset_version_id, start, end)

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify the stored object before dataset publication."""

        await self._verifier.verify(
            dataset_version_id,
            "bid_ask",
            expected,
        )