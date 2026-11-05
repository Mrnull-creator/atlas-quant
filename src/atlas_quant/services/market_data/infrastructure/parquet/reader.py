"""Incremental Parquet readers for normalized market data."""

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.async_io import (
    run_blocking_safely,
)
from atlas_quant.services.market_data.infrastructure.parquet.schemas import (
    BID_ASK_PARQUET_SCHEMA,
    OHLCV_PARQUET_SCHEMA,
    bid_ask_quote_from_record,
    ohlcv_bar_from_record,
)


class _ParquetBatchReader:
    """Reads Parquet record batches while keeping memory bounded."""

    def __init__(
        self,
        path: Path,
        schema: pa.Schema,
        batch_size: int,
    ) -> None:
        self._file = pq.ParquetFile(path)

        if not self._file.schema_arrow.equals(
            schema,
            check_metadata=True,
        ):
            self._file.close()
            raise ValueError(
                f"Parquet schema is incompatible: {path}"
            )

        self._batches = iter(
            self._file.iter_batches(
                batch_size=batch_size,
            )
        )

    def next_batch(self) -> tuple[bool, list[dict[str, Any]]]:
        """Return the next batch, or indicate that reading is complete."""

        try:
            batch = next(self._batches)
        except StopIteration:
            return False, []

        return True, batch.to_pylist()

    def close(self) -> None:
        """Release the underlying Parquet file handle."""

        self._file.close()


class _ParquetReader:
    """Shared asynchronous implementation for normalized Parquet data."""

    def __init__(
        self,
        root_directory: str | Path,
        data_kind: str,
        schema: pa.Schema,
        converter: Callable[[dict[str, Any]], Any],
        batch_size: int = 10_000,
    ) -> None:
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer.")

        self._root_directory = (
            Path(root_directory).expanduser().resolve()
        )
        self._data_kind = data_kind
        self._schema = schema
        self._converter = converter
        self._batch_size = batch_size

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[Any]:
        """Return an asynchronous iterator over a time range."""

        if not isinstance(dataset_version_id, UUID):
            raise TypeError("dataset_version_id must be a UUID.")

        start_utc = self._normalize_boundary(start, "start")
        end_utc = self._normalize_boundary(end, "end")

        if (
            start_utc is not None
            and end_utc is not None
            and start_utc >= end_utc
        ):
            raise ValueError("start must be earlier than end.")

        return self._read(
            dataset_version_id,
            start_utc,
            end_utc,
        )

    async def _read(
        self,
        dataset_version_id: UUID,
        start: datetime | None,
        end: datetime | None,
    ) -> AsyncIterator[Any]:
        """Read batches without blocking the event loop."""

        path = (
            self._root_directory
            / self._data_kind
            / f"{dataset_version_id}.parquet"
        )

        # If cancellation arrives during file opening, wait until the
        # constructor finishes and close the resulting reader.
        reader = await run_blocking_safely(
            _ParquetBatchReader,
            path,
            self._schema,
            self._batch_size,
            cancel_cleanup=lambda opened_reader: opened_reader.close(),
        )

        try:
            while True:
                # A cancelled batch read must finish before the finally
                # block closes the underlying file.
                has_batch, records = await run_blocking_safely(
                    reader.next_batch
                )

                if not has_batch:
                    break

                for record in records:
                    timestamp = record["timestamp"]

                    if start is not None and timestamp < start:
                        continue

                    if end is not None and timestamp >= end:
                        continue

                    yield self._converter(record)

        finally:
            # Also wait for close to finish if cancellation arrives here.
            await run_blocking_safely(reader.close)

    @staticmethod
    def _normalize_boundary(
        value: datetime | None,
        field_name: str,
    ) -> datetime | None:
        """Validate a time boundary and normalize it to UTC."""

        if value is None:
            return None

        if not isinstance(value, datetime):
            raise TypeError(
                f"{field_name} must be a datetime value or None."
            )

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{field_name} must include timezone information."
            )

        return value.astimezone(timezone.utc)


class ParquetOHLCVReader(_ParquetReader):
    """Reads immutable OHLCV dataset-version files."""

    def __init__(
        self,
        root_directory: str | Path,
        batch_size: int = 10_000,
    ) -> None:
        super().__init__(
            root_directory=root_directory,
            data_kind="ohlcv",
            schema=OHLCV_PARQUET_SCHEMA,
            converter=ohlcv_bar_from_record,
            batch_size=batch_size,
        )

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[OHLCVBar]:
        return super().read(dataset_version_id, start, end)


class ParquetQuoteReader(_ParquetReader):
    """Reads immutable bid/ask dataset-version files."""

    def __init__(
        self,
        root_directory: str | Path,
        batch_size: int = 10_000,
    ) -> None:
        super().__init__(
            root_directory=root_directory,
            data_kind="bid_ask",
            schema=BID_ASK_PARQUET_SCHEMA,
            converter=bid_ask_quote_from_record,
            batch_size=batch_size,
        )

    def read(
        self,
        dataset_version_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AsyncIterator[BidAskQuote]:
        return super().read(dataset_version_id, start, end)