"""Immutable Parquet writers for normalized market data."""

import hashlib
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.async_io import (
    run_blocking_safely,
)
from atlas_quant.services.market_data.infrastructure.parquet.schemas import (
    BID_ASK_PARQUET_SCHEMA,
    OHLCV_PARQUET_SCHEMA,
    bid_ask_quote_to_record,
    ohlcv_bar_to_record,
)


class _ParquetWriter:
    """Shared implementation for immutable local Parquet objects."""

    def __init__(
        self,
        root_directory: str | Path,
        batch_size: int = 10_000,
    ) -> None:
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer.")

        self._root_directory = Path(root_directory).expanduser().resolve()
        self._batch_size = batch_size

    async def write(
        self,
        dataset_version_id: UUID,
        observations: Iterable[Any],
        data_kind: str,
        schema: pa.Schema,
        converter: Callable[[Any], dict[str, Any]],
    ) -> StorageObjectMetadata:
        """Write observations without blocking the event loop."""

        final_path = (
            self._root_directory
            / data_kind
            / f"{dataset_version_id}.parquet"
        )

        def cleanup_published_file(
            _: StorageObjectMetadata,
        ) -> None:
            """Remove an object whose caller was cancelled."""

            self._unlink_quietly(final_path)

        return await run_blocking_safely(
            self._write_sync,
            dataset_version_id,
            observations,
            data_kind,
            schema,
            converter,
            cancel_cleanup=cleanup_published_file,
        )

    def _write_sync(
        self,
        dataset_version_id: UUID,
        observations: Iterable[Any],
        data_kind: str,
        schema: pa.Schema,
        converter: Callable[[Any], dict[str, Any]],
    ) -> StorageObjectMetadata:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("dataset_version_id must be a UUID.")

        target_directory = self._root_directory / data_kind
        target_directory.mkdir(parents=True, exist_ok=True)

        final_path = target_directory / f"{dataset_version_id}.parquet"

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{dataset_version_id}.",
            suffix=".tmp",
            dir=target_directory,
        )
        os.close(file_descriptor)

        temporary_path = Path(temporary_name)
        parquet_writer: pq.ParquetWriter | None = None
        parquet_file: pq.ParquetFile | None = None
        published = False

        record_count = 0
        first_timestamp = None
        last_timestamp = None

        try:
            iterator = iter(observations)

            while True:
                records: list[dict[str, Any]] = []

                for _ in range(self._batch_size):
                    try:
                        observation = next(iterator)
                    except StopIteration:
                        break

                    record = converter(observation)
                    records.append(record)

                    timestamp = record["timestamp"]

                    if (
                        first_timestamp is None
                        or timestamp < first_timestamp
                    ):
                        first_timestamp = timestamp

                    if (
                        last_timestamp is None
                        or timestamp > last_timestamp
                    ):
                        last_timestamp = timestamp

                if not records:
                    break

                table = pa.Table.from_pylist(
                    records,
                    schema=schema,
                )

                if parquet_writer is None:
                    parquet_writer = pq.ParquetWriter(
                        temporary_path,
                        schema,
                        compression="zstd",
                    )

                parquet_writer.write_table(table)
                record_count += len(records)

            if record_count == 0:
                raise ValueError(
                    "Cannot write an empty normalized dataset version."
                )

            parquet_writer.close()
            parquet_writer = None

            # Verify the completed Parquet file before publication.
            parquet_file = pq.ParquetFile(temporary_path)

            if parquet_file.metadata.num_rows != record_count:
                raise RuntimeError(
                    "Parquet verification failed: record count mismatch."
                )

            if not parquet_file.schema_arrow.equals(
                schema,
                check_metadata=True,
            ):
                raise RuntimeError(
                    "Parquet verification failed: schema mismatch."
                )

            # Close the reader before publication. This is particularly
            # important on Windows, where open handles prevent deletion.
            parquet_file.close()
            parquet_file = None

            # Flush the completed file to the operating system.
            with temporary_path.open("rb+") as file:
                file.flush()
                os.fsync(file.fileno())

            checksum = self._calculate_sha256(temporary_path)
            size_bytes = temporary_path.stat().st_size

            # Atomically publish without overwriting an existing version.
            # Both paths are on the same volume.
            os.link(temporary_path, final_path)
            published = True

            # The final file is already published. Failure to remove the
            # temporary hard link must not turn success into a write error.
            self._unlink_quietly(temporary_path)

            return StorageObjectMetadata(
                storage_uri=final_path.as_uri(),
                storage_format="parquet",
                checksum_algorithm="sha256",
                checksum=checksum,
                size_bytes=size_bytes,
                record_count=record_count,
                first_timestamp=first_timestamp,
                last_timestamp=last_timestamp,
            )

        except BaseException:
            if parquet_file is not None:
                try:
                    parquet_file.close()
                except Exception:
                    pass

            if parquet_writer is not None:
                try:
                    parquet_writer.close()
                except Exception:
                    pass

            self._unlink_quietly(temporary_path)

            # If an error occurred after publication, roll back the file
            # created by this operation.
            if published:
                self._unlink_quietly(final_path)

            raise

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        """Attempt file cleanup without masking the original outcome."""

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _calculate_sha256(path: Path) -> str:
        """Calculate a file's SHA-256 checksum incrementally."""

        digest = hashlib.sha256()

        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)

        return digest.hexdigest()


class ParquetOHLCVWriter(_ParquetWriter):
    """Writes immutable OHLCV dataset-version files."""

    async def write(
        self,
        dataset_version_id: UUID,
        bars: Iterable[OHLCVBar],
    ) -> StorageObjectMetadata:
        return await super().write(
            dataset_version_id=dataset_version_id,
            observations=bars,
            data_kind="ohlcv",
            schema=OHLCV_PARQUET_SCHEMA,
            converter=ohlcv_bar_to_record,
        )


class ParquetQuoteWriter(_ParquetWriter):
    """Writes immutable bid/ask dataset-version files."""

    async def write(
        self,
        dataset_version_id: UUID,
        quotes: Iterable[BidAskQuote],
    ) -> StorageObjectMetadata:
        return await super().write(
            dataset_version_id=dataset_version_id,
            observations=quotes,
            data_kind="bid_ask",
            schema=BID_ASK_PARQUET_SCHEMA,
            converter=bid_ask_quote_to_record,
        )