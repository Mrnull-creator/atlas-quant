"""Integrity verification for immutable Parquet dataset-version files."""

import hashlib
from pathlib import Path
from typing import Literal
from uuid import UUID

import pyarrow.parquet as pq

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.infrastructure.parquet.async_io import (
    run_blocking_safely,
)
from atlas_quant.services.market_data.infrastructure.parquet.schemas import (
    BID_ASK_PARQUET_SCHEMA,
    OHLCV_PARQUET_SCHEMA,
)


class ParquetIntegrityError(RuntimeError):
    """Raised when a Parquet object fails integrity verification."""


class ParquetIntegrityVerifier:
    """Verifies stored Parquet files against their recorded metadata."""

    def __init__(self, root_directory: str | Path) -> None:
        self._root_directory = (
            Path(root_directory).expanduser().resolve()
        )

    async def verify(
        self,
        dataset_version_id: UUID,
        data_kind: Literal["ohlcv", "bid_ask"],
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify a dataset-version object without blocking the event loop."""

        await run_blocking_safely(
            self._verify_sync,
            dataset_version_id,
            data_kind,
            expected,
        )

    def _verify_sync(
        self,
        dataset_version_id: UUID,
        data_kind: Literal["ohlcv", "bid_ask"],
        expected: StorageObjectMetadata,
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("dataset_version_id must be a UUID.")

        if data_kind not in ("ohlcv", "bid_ask"):
            raise ValueError("Unsupported Parquet data kind.")

        if not isinstance(expected, StorageObjectMetadata):
            raise TypeError(
                "expected must be StorageObjectMetadata."
            )

        schema = (
            OHLCV_PARQUET_SCHEMA
            if data_kind == "ohlcv"
            else BID_ASK_PARQUET_SCHEMA
        )

        path = (
            self._root_directory
            / data_kind
            / f"{dataset_version_id}.parquet"
        )

        if expected.storage_uri != path.as_uri():
            raise ParquetIntegrityError(
                "Storage URI does not match the expected dataset-version path."
            )

        if expected.storage_format != "parquet":
            raise ParquetIntegrityError(
                "Unsupported storage format."
            )

        if expected.checksum_algorithm != "sha256":
            raise ParquetIntegrityError(
                "Unsupported checksum algorithm."
            )

        if expected.record_count is None:
            raise ParquetIntegrityError(
                "Expected metadata must include a record count."
            )

        if not path.exists():
            raise FileNotFoundError(path)

        if path.is_symlink() or not path.is_file():
            raise ParquetIntegrityError(
                "Storage object must be a regular file, not a symbolic link."
            )

        actual_size = path.stat().st_size

        if actual_size != expected.size_bytes:
            raise ParquetIntegrityError(
                "Storage object size does not match recorded metadata."
            )

        actual_checksum = self._calculate_sha256(path)

        if actual_checksum != expected.checksum:
            raise ParquetIntegrityError(
                "Storage object checksum does not match recorded metadata."
            )

        parquet_file: pq.ParquetFile | None = None

        try:
            parquet_file = pq.ParquetFile(path)

            if not parquet_file.schema_arrow.equals(
                schema,
                check_metadata=True,
            ):
                raise ParquetIntegrityError(
                    "Parquet schema or schema version is incompatible."
                )

            actual_count = parquet_file.metadata.num_rows

            if actual_count != expected.record_count:
                raise ParquetIntegrityError(
                    "Parquet record count does not match recorded metadata."
                )

            first_timestamp = None
            last_timestamp = None

            for batch in parquet_file.iter_batches(
                batch_size=10_000,
                columns=["timestamp"],
            ):
                for timestamp in batch.column(0).to_pylist():
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

            if (
                first_timestamp != expected.first_timestamp
                or last_timestamp != expected.last_timestamp
            ):
                raise ParquetIntegrityError(
                    "Parquet observation boundaries do not match "
                    "recorded metadata."
                )

        except ParquetIntegrityError:
            raise
        except Exception as exc:
            raise ParquetIntegrityError(
                "Unable to verify Parquet file contents."
            ) from exc
        finally:
            if parquet_file is not None:
                parquet_file.close()

    @staticmethod
    def _calculate_sha256(path: Path) -> str:
        """Calculate SHA-256 incrementally."""

        digest = hashlib.sha256()

        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)

        return digest.hexdigest()
