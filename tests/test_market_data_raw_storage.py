"""Tests for immutable filesystem storage of original provider payloads."""

import asyncio
import hashlib
import io
import threading
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.infrastructure.raw_storage import (
    LocalRawDataStorage,
    RawStorageIntegrityError,
)


def test_raw_storage_preserves_exact_payload_and_metadata(tmp_path) -> None:
    dataset_version_id = uuid4()
    payload = b"\x00\x01BI5\xff\xfe\r\noriginal-provider-payload"

    storage = LocalRawDataStorage(tmp_path)
    source = io.BytesIO(payload)

    metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            source,
            "bi5",
        )
    )

    expected_path = (
        tmp_path
        / "raw_provider"
        / f"{dataset_version_id}.bin"
    )

    assert expected_path.is_file()
    assert expected_path.read_bytes() == payload
    assert metadata.storage_uri == expected_path.resolve().as_uri()
    assert metadata.storage_format == "bi5"
    assert metadata.checksum_algorithm == "sha256"
    assert metadata.checksum == hashlib.sha256(payload).hexdigest()
    assert metadata.size_bytes == len(payload)
    assert metadata.record_count is None
    assert metadata.first_timestamp is None
    assert metadata.last_timestamp is None

    # The storage implementation must not take ownership of the source.
    assert not source.closed

    asyncio.run(storage.verify(dataset_version_id, metadata))


def test_raw_storage_read_returns_original_bytes(tmp_path) -> None:
    dataset_version_id = uuid4()
    payload = b"\x00\xfforiginal binary data"

    storage = LocalRawDataStorage(tmp_path)

    asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(payload),
            "bi5",
        )
    )

    with storage.read(dataset_version_id) as source:
        assert source.read() == payload


def test_raw_storage_rejects_overwrite_and_preserves_original(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    original_payload = b"original payload"
    replacement_payload = b"replacement payload"

    storage = LocalRawDataStorage(tmp_path)

    first_metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(original_payload),
            "bi5",
        )
    )

    original_path = (
        tmp_path
        / "raw_provider"
        / f"{dataset_version_id}.bin"
    )
    original_bytes = original_path.read_bytes()

    with pytest.raises(FileExistsError):
        asyncio.run(
            storage.write(
                dataset_version_id,
                io.BytesIO(replacement_payload),
                "bi5",
            )
        )

    assert original_path.read_bytes() == original_bytes
    assert original_path.read_bytes() == original_payload
    assert first_metadata.checksum == hashlib.sha256(
        original_payload
    ).hexdigest()


def test_raw_storage_verification_detects_corruption(tmp_path) -> None:
    dataset_version_id = uuid4()
    payload = b"original provider payload"

    storage = LocalRawDataStorage(tmp_path)

    metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(payload),
            "bi5",
        )
    )

    path = (
        tmp_path
        / "raw_provider"
        / f"{dataset_version_id}.bin"
    )

    path.write_bytes(b"corruptd provider payload")

    with pytest.raises(
        RawStorageIntegrityError,
        match="checksum",
    ):
        asyncio.run(storage.verify(dataset_version_id, metadata))


def test_raw_storage_verification_detects_size_mismatch(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    payload = b"provider payload"

    storage = LocalRawDataStorage(tmp_path)

    metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(payload),
            "bi5",
        )
    )

    path = (
        tmp_path
        / "raw_provider"
        / f"{dataset_version_id}.bin"
    )

    path.write_bytes(payload + b"extra")

    with pytest.raises(
        RawStorageIntegrityError,
        match="size",
    ):
        asyncio.run(storage.verify(dataset_version_id, metadata))


def test_raw_storage_verification_detects_missing_object(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    storage = LocalRawDataStorage(tmp_path)

    metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(b"provider payload"),
            "bi5",
        )
    )

    path = (
        tmp_path
        / "raw_provider"
        / f"{dataset_version_id}.bin"
    )
    path.unlink()

    with pytest.raises(FileNotFoundError):
        asyncio.run(storage.verify(dataset_version_id, metadata))


def test_raw_storage_rejects_incompatible_metadata(tmp_path) -> None:
    dataset_version_id = uuid4()
    storage = LocalRawDataStorage(tmp_path)

    metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(b"provider payload"),
            "bi5",
        )
    )

    incompatible_metadata = StorageObjectMetadata(
        storage_uri=metadata.storage_uri,
        storage_format=metadata.storage_format,
        checksum_algorithm=metadata.checksum_algorithm,
        checksum=metadata.checksum,
        size_bytes=metadata.size_bytes,
        record_count=1,
    )

    with pytest.raises(
        RawStorageIntegrityError,
        match="record count",
    ):
        asyncio.run(
            storage.verify(
                dataset_version_id,
                incompatible_metadata,
            )
        )


def test_raw_storage_rejects_invalid_storage_format(tmp_path) -> None:
    storage = LocalRawDataStorage(tmp_path)

    with pytest.raises(ValueError, match="storage_format"):
        asyncio.run(
            storage.write(
                uuid4(),
                io.BytesIO(b"provider payload"),
                "../invalid",
            )
        )

    assert not list(tmp_path.rglob("*.bin"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_raw_storage_accepts_empty_payload_without_modification(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    storage = LocalRawDataStorage(tmp_path)

    metadata = asyncio.run(
        storage.write(
            dataset_version_id,
            io.BytesIO(b""),
            "bi5",
        )
    )

    assert metadata.size_bytes == 0
    assert metadata.checksum == hashlib.sha256(b"").hexdigest()
    assert metadata.record_count is None

    asyncio.run(storage.verify(dataset_version_id, metadata))


def test_raw_storage_cancellation_waits_for_worker_and_removes_file(
    tmp_path,
) -> None:
    dataset_version_id = uuid4()
    storage = LocalRawDataStorage(tmp_path)

    worker_started = threading.Event()
    release_worker = threading.Event()

    original_write_sync = storage._write_sync

    def blocking_write(*args):
        worker_started.set()

        if not release_worker.wait(timeout=10):
            raise TimeoutError("Test worker was not released.")

        return original_write_sync(*args)

    storage._write_sync = blocking_write

    async def scenario() -> None:
        task = asyncio.create_task(
            storage.write(
                dataset_version_id,
                io.BytesIO(b"provider payload"),
                "bi5",
            )
        )

        assert await asyncio.to_thread(
            worker_started.wait,
            5,
        )

        task.cancel()
        await asyncio.sleep(0.05)

        # The caller must wait until the filesystem worker finishes.
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
        / "raw_provider"
        / f"{dataset_version_id}.bin"
    )

    assert not final_path.exists()
    assert not list(tmp_path.rglob("*.tmp"))