"""Application-to-filesystem tests for raw dataset publication."""

import asyncio
import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from atlas_quant.services.market_data.application.raw_dataset_publication import (
    RawDatasetPublicationService,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
    DatasetVersion,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.infrastructure.raw_storage import (
    LocalRawDataStorage,
)


class FakeDatasetRepository:
    def __init__(self, dataset):
        self.dataset = dataset
        self.calls = []

    async def get_or_create_raw(self, dataset):
        self.calls.append(dataset)
        return self.dataset, True


class FakeLifecycleRepository:
    def __init__(self):
        self.staging_versions = []
        self.published_versions = []
        self.failed_versions = []

    async def create_next_staging_version(
        self,
        dataset_id,
        *,
        schema_version,
        storage_format,
    ):
        version = DatasetVersion(
            dataset_id=dataset_id,
            version_number=1,
            schema_version=schema_version,
            storage_format=storage_format,
            record_count=None,
        )

        self.staging_versions.append(version)
        return version

    async def publish_staging_version(
        self,
        dataset_version_id,
        metadata,
    ):
        staging = self.staging_versions[-1]

        assert staging.dataset_version_id == dataset_version_id

        version = DatasetVersion(
            dataset_id=staging.dataset_id,
            dataset_version_id=staging.dataset_version_id,
            version_number=staging.version_number,
            schema_version=staging.schema_version,
            storage_format=staging.storage_format,
            status=DatasetVersionStatus.AVAILABLE,
            record_count=None,
            size_bytes=metadata.size_bytes,
            storage_uri=metadata.storage_uri,
            checksum_algorithm=metadata.checksum_algorithm,
            checksum=metadata.checksum,
            created_at=staging.created_at,
            published_at=datetime.now(timezone.utc),
        )

        self.published_versions.append(version)
        return version

    async def fail_staging_version(self, dataset_version_id):
        self.failed_versions.append(dataset_version_id)
        return self.staging_versions[-1]


def test_raw_dataset_publication_with_real_filesystem_storage(tmp_path):
    async def scenario():
        dataset = Dataset(
            name="EURUSD Dukascopy raw payload",
            data_kind=DatasetKind.RAW_PROVIDER,
            asset_class=AssetClass.FOREX,
            instrument_id=uuid4(),
            instrument_symbol="EURUSD",
            provider_id="dukascopy",
        )

        repository = FakeDatasetRepository(dataset)
        lifecycle = FakeLifecycleRepository()
        storage = LocalRawDataStorage(tmp_path)

        service = RawDatasetPublicationService(
            repository,
            lifecycle,
            storage,
        )

        # Larger than the storage chunk size, so the real storage
        # implementation must preserve multiple chunks correctly.
        payload = bytes(range(256)) * 9000
        import io

        source = io.BytesIO(payload)

        result = await service.publish(
            dataset,
            source,
            storage_format="BI5",
        )

        version = result.dataset_version

        assert result.dataset == dataset
        assert result.dataset_created is True

        assert version.status == DatasetVersionStatus.AVAILABLE
        assert version.storage_format == "bi5"
        assert version.record_count is None
        assert version.size_bytes == len(payload)
        assert version.first_timestamp is None
        assert version.last_timestamp is None

        assert version.checksum_algorithm == "sha256"
        assert version.checksum == hashlib.sha256(payload).hexdigest()

        assert len(repository.calls) == 1
        assert len(lifecycle.staging_versions) == 1
        assert len(lifecycle.published_versions) == 1
        assert lifecycle.failed_versions == []

        # Read the actual filesystem object and compare every byte.
        with storage.read(version.dataset_version_id) as stored:
            assert stored.read() == payload

        # The original input stream remains owned by the caller.
        assert not source.closed
        assert source.tell() == len(payload)

        # The atomic write should leave no temporary files behind.
        raw_directory = tmp_path / "raw_provider"
        assert raw_directory.is_dir()
        assert len(list(raw_directory.glob("*.bin"))) == 1
        assert list(raw_directory.glob("*.tmp")) == []

    asyncio.run(scenario())