"""Immutable filesystem storage for original provider payloads."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.infrastructure.parquet.async_io import (
    run_blocking_safely,
)


class RawStorageIntegrityError(RuntimeError):
    """Raised when a raw provider payload fails integrity verification."""


class LocalRawDataStorage:
    """Preserves original provider payloads as immutable binary objects."""

    _CHUNK_SIZE = 1024 * 1024
    _STORAGE_FORMAT_PATTERN = re.compile(
        r"[a-z0-9][a-z0-9_-]{0,31}"
    )

    def __init__(self, root_directory: str | Path) -> None:
        self._root_directory = (
            Path(root_directory).expanduser().resolve()
        )

    async def write(
        self,
        dataset_version_id: UUID,
        source: BinaryIO,
        storage_format: str,
    ) -> StorageObjectMetadata:
        """Preserve a binary payload without loading it into memory."""

        return await run_blocking_safely(
            self._write_sync,
            dataset_version_id,
            source,
            storage_format,
            cancel_cleanup=lambda _: self._unlink_quietly(
                self._object_path(dataset_version_id)
            ),
        )

    def read(self, dataset_version_id: UUID) -> BinaryIO:
        """Open a stored raw payload.

        The caller owns the returned stream and must close it.
        """

        path = self._object_path(dataset_version_id)

        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)

        return path.open("rb")

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify the stored bytes against their recorded metadata."""

        await run_blocking_safely(
            self._verify_sync,
            dataset_version_id,
            expected,
        )

    def _write_sync(
        self,
        dataset_version_id: UUID,
        source: BinaryIO,
        storage_format: str,
    ) -> StorageObjectMetadata:
        self._validate_dataset_version_id(dataset_version_id)
        self._validate_storage_format(storage_format)
        storage_format = storage_format.strip().lower()

        if not callable(getattr(source, "read", None)):
            raise TypeError("source must be a readable binary stream.")

        target_directory = self._root_directory / "raw_provider"
        target_directory.mkdir(parents=True, exist_ok=True)

        final_path = self._object_path(dataset_version_id)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{dataset_version_id}.",
            suffix=".tmp",
            dir=target_directory,
        )

        temporary_path = Path(temporary_name)
        published = False

        digest = hashlib.sha256()
        size_bytes = 0

        try:
            with os.fdopen(file_descriptor, "wb") as output:
                while True:
                    chunk = source.read(self._CHUNK_SIZE)

                    if not isinstance(chunk, bytes):
                        raise TypeError(
                            "Raw provider streams must return bytes."
                        )

                    if not chunk:
                        break

                    output.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)

                output.flush()
                os.fsync(output.fileno())

            checksum = digest.hexdigest()

            # Atomic publication without overwriting an existing object.
            os.link(temporary_path, final_path)
            published = True

            self._unlink_quietly(temporary_path)

            return StorageObjectMetadata(
                storage_uri=final_path.as_uri(),
                storage_format=storage_format,
                checksum_algorithm="sha256",
                checksum=checksum,
                size_bytes=size_bytes,
                record_count=None,
                first_timestamp=None,
                last_timestamp=None,
            )

        except BaseException:
            self._unlink_quietly(temporary_path)

            if published:
                self._unlink_quietly(final_path)

            # If fdopen itself failed, close the original descriptor.
            try:
                os.close(file_descriptor)
            except OSError:
                pass

            raise

    def _verify_sync(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        self._validate_dataset_version_id(dataset_version_id)

        if not isinstance(expected, StorageObjectMetadata):
            raise TypeError(
                "expected must be StorageObjectMetadata."
            )

        self._validate_storage_format(expected.storage_format)

        path = self._object_path(dataset_version_id)

        if expected.storage_uri != path.as_uri():
            raise RawStorageIntegrityError(
                "Storage URI does not match the expected dataset-version path."
            )

        if expected.checksum_algorithm != "sha256":
            raise RawStorageIntegrityError(
                "Unsupported raw storage checksum algorithm."
            )

        if expected.record_count is not None:
            raise RawStorageIntegrityError(
                "Raw provider payloads must not have a record count."
            )

        if (
            expected.first_timestamp is not None
            or expected.last_timestamp is not None
        ):
            raise RawStorageIntegrityError(
                "Raw provider payloads must not have observation boundaries."
            )

        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(path)

        actual_size = path.stat().st_size

        if actual_size != expected.size_bytes:
            raise RawStorageIntegrityError(
                "Raw payload size does not match recorded metadata."
            )

        actual_checksum = self._calculate_sha256(path)

        if actual_checksum != expected.checksum:
            raise RawStorageIntegrityError(
                "Raw payload checksum does not match recorded metadata."
            )

    def _object_path(self, dataset_version_id: UUID) -> Path:
        self._validate_dataset_version_id(dataset_version_id)

        return (
            self._root_directory
            / "raw_provider"
            / f"{dataset_version_id}.bin"
        )

    @staticmethod
    def _validate_dataset_version_id(
        dataset_version_id: UUID,
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("dataset_version_id must be a UUID.")

    @classmethod
    def _validate_storage_format(cls, storage_format: str) -> None:
        if not isinstance(storage_format, str):
            raise TypeError("storage_format must be a string.")

        normalized = storage_format.strip().lower()

        if not cls._STORAGE_FORMAT_PATTERN.fullmatch(normalized):
            raise ValueError("storage_format has an invalid format.")

    @staticmethod
    def _calculate_sha256(path: Path) -> str:
        """Calculate SHA-256 incrementally."""

        digest = hashlib.sha256()

        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        """Remove a file without masking the original outcome."""

        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass