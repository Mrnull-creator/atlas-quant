"""Application contracts for dataset lifecycle operations."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.domain.datasets import DatasetVersion


class DatasetLifecycleRepository(Protocol):
    """Persistence operations requiring coordinated dataset transactions."""

    async def create_next_staging_version(
        self,
        dataset_id: UUID,
        *,
        schema_version: str,
        storage_format: str,
    ) -> DatasetVersion:
        """Allocate and persist the next staging version atomically."""
        ...

    async def publish_staging_version(
        self,
        dataset_version_id: UUID,
        metadata: StorageObjectMetadata,
    ) -> DatasetVersion:
        """Publish a verified staging version and advance the latest pointer.

        Both the version publication and dataset latest-version update
        must be committed in one database transaction.
        """
        ...

    async def fail_staging_version(
        self,
        dataset_version_id: UUID,
    ) -> DatasetVersion:
        """Transition a staging version to failed atomically.

        Failed versions are retained permanently and cannot be published.
        """
        ...

    async def list_staging_versions_for_recovery(
        self,
        *,
        created_before: datetime,
        limit: int = 100,
    ) -> list[DatasetVersion]:
        """List old staging versions for inspection by a recovery process.

        This operation only discovers candidates. It must not automatically
        fail, publish, or otherwise modify the returned versions.
        """
        ...