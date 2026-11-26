"""Repository interfaces for the Market Data Service.

These interfaces define how application services access domain data.
Implementations belong in the infrastructure layer.
"""


from datetime import timedelta
from typing import Protocol
from uuid import UUID

from atlas_quant.services.market_data.domain.ingestion import IngestionJob
from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttempt,
)

from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageSourceType,
    DatasetVersionLineage,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetVersion,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.ingestion import IngestionJob
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)
from atlas_quant.services.market_data.domain.validation import ValidationReport


class InstrumentRepository(Protocol):
    """Persistence interface for financial instruments."""

    async def get_by_id(self, instrument_id: UUID) -> Instrument | None:
        """Retrieve an instrument by its permanent UUID."""
        ...

    async def get_by_business_key(
        self,
        symbol: str,
        asset_class: AssetClass,
        exchange: str | None,
    ) -> Instrument | None:
        """Retrieve an instrument by its business uniqueness key."""
        ...

    async def list_all(self) -> tuple[Instrument, ...]:
        """Retrieve all registered instruments."""
        ...

    async def add(self, instrument: Instrument) -> None:
        """Persist a newly registered instrument."""
        ...

    async def update(self, instrument: Instrument) -> None:
        """Update an existing instrument."""
        ...


class ProviderRepository(Protocol):
    """Persistence interface for market data providers."""

    async def get_by_id(self, provider_id: str) -> MarketDataProvider | None:
        """Retrieve a provider by its identifier."""
        ...

    async def list_all(self) -> tuple[MarketDataProvider, ...]:
        """Retrieve all registered providers."""
        ...

    async def add(self, provider: MarketDataProvider) -> None:
        """Persist a newly registered provider."""
        ...

    async def update(self, provider: MarketDataProvider) -> None:
        """Update an existing provider."""
        ...


class DatasetRepository(Protocol):
    """Persistence interface for logical dataset identities."""

    async def get_by_id(self, dataset_id: UUID) -> Dataset | None:
        """Retrieve a dataset by its identifier."""
        ...

    async def list_all(self) -> tuple[Dataset, ...]:
        """Retrieve all registered datasets."""
        ...

    async def get_or_create(
        self,
        dataset: Dataset,
    ) -> tuple[Dataset, bool]:
        """Atomically retrieve or create a normalized dataset.

        Returns the canonical dataset and a boolean indicating whether
        this call created it.
        """
        ...
    async def get_or_create_raw(
        self,
        dataset: Dataset,
    ) -> tuple[Dataset, bool]:
        """Atomically retrieve or create a raw provider dataset."""
        ...

    async def add(self, dataset: Dataset) -> None:
        """Persist a new logical dataset."""
        ...

    async def update(self, dataset: Dataset) -> None:
        """Update an existing dataset identity."""
        ...


class DatasetVersionRepository(Protocol):
    """Persistence interface for immutable dataset versions."""

    async def get_by_id(self, version_id: UUID) -> DatasetVersion | None:
        """Retrieve a dataset version by its identifier."""
        ...

    async def get_by_version_number(
        self,
        dataset_id: UUID,
        version_number: int,
    ) -> DatasetVersion | None:
        """Retrieve a specific version of a logical dataset."""
        ...

    async def list_by_dataset_id(
        self,
        dataset_id: UUID,
    ) -> tuple[DatasetVersion, ...]:
        """Retrieve the versions belonging to a logical dataset."""
        ...

    async def add(self, version: DatasetVersion) -> None:
        """Persist a new immutable dataset version."""
        ...



class IngestionJobRepository(Protocol):
    """Persistence interface for ingestion jobs and execution ownership."""

    async def get_by_id(self, job_id: UUID) -> IngestionJob | None:
        """Retrieve an ingestion job by its identifier."""
        ...

    async def claim_execution(self, job_id: UUID) -> bool:
        """Legacy claim API retained during orchestrator migration.

        New execution code must use claim_attempt instead.
        """
        ...

    async def claim_attempt(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_duration: timedelta = timedelta(seconds=60),
    ) -> IngestionAttempt | None:
        """Acquire a new authoritative execution attempt.

        Returns None when the job is not running or another worker
        still holds a valid lease.
        """
        ...

    async def renew_attempt_lease(
        self,
        job_id: UUID,
        attempt_id: UUID,
        fencing_token: int,
        worker_id: str,
        *,
        lease_duration: timedelta = timedelta(seconds=60),
    ) -> IngestionAttempt:
        """Renew the lease of the current authoritative attempt.

        Raises an execution ownership error if the attempt is stale,
        expired, superseded, or owned by another worker.
        """
        ...

    async def update_owned(
        self,
        job: IngestionJob,
        attempt_id: UUID,
        fencing_token: int,
        worker_id: str,
    ) -> None:
        """Persist a job update only while the caller owns its active attempt."""
        ...

    async def list_all(self) -> tuple[IngestionJob, ...]:
        """Retrieve registered ingestion jobs."""
        ...

    async def add(self, job: IngestionJob) -> None:
        """Persist a newly created ingestion job."""
        ...

    async def update(self, job: IngestionJob) -> None:
        """Persist an ingestion job lifecycle change."""
        ...



class ValidationReportRepository(Protocol):
    """Persistence interface for immutable validation reports."""

    async def get_by_id(self, report_id: UUID) -> ValidationReport | None:
        """Retrieve a validation report by its identifier."""
        ...

    async def list_by_ingestion_job_id(
        self,
        ingestion_job_id: UUID,
    ) -> tuple[ValidationReport, ...]:
        """Retrieve reports associated with an ingestion job."""
        ...

    async def list_by_dataset_version_id(
        self,
        dataset_version_id: UUID,
    ) -> tuple[ValidationReport, ...]:
        """Retrieve reports associated with a dataset version."""
        ...

    async def add(self, report: ValidationReport) -> None:
        """Persist a new immutable validation report."""
        ...


class DatasetVersionLineageRepository(Protocol):
    """Persistence interface for dataset-version lineage relationships."""

    async def get_by_id(
        self,
        lineage_id: UUID,
    ) -> DatasetVersionLineage | None:
        """Retrieve a lineage relationship by its identifier."""
        ...

    async def list_by_target_version_id(
        self,
        target_dataset_version_id: UUID,
    ) -> tuple[DatasetVersionLineage, ...]:
        """Retrieve all sources contributing to a dataset version."""
        ...

    async def list_by_source(
        self,
        source_type: DatasetLineageSourceType,
        source_id: UUID,
    ) -> tuple[DatasetVersionLineage, ...]:
        """Retrieve all lineage relationships originating from a source."""
        ...

    async def add(self, lineage: DatasetVersionLineage) -> None:
        """Persist a new lineage relationship."""
        ...


class ProviderInstrumentMappingRepository(Protocol):
    """Persistence interface for provider-specific instrument identifiers."""

    async def get_by_id(
        self,
        mapping_id: UUID,
    ) -> ProviderInstrumentMapping | None:
        """Retrieve a mapping by its permanent UUID."""
        ...

    async def get_by_provider_identifier(
        self,
        provider_id: str,
        provider_symbol: str,
        provider_exchange: str | None = None,
    ) -> ProviderInstrumentMapping | None:
        """Retrieve the active mapping for a provider identifier."""
        ...

    async def list_by_instrument_id(
        self,
        instrument_id: UUID,
    ) -> tuple[ProviderInstrumentMapping, ...]:
        """Retrieve all mappings associated with a canonical instrument."""
        ...

    async def list_by_provider_id(
        self,
        provider_id: str,
    ) -> tuple[ProviderInstrumentMapping, ...]:
        """Retrieve all mappings registered for a provider."""
        ...

    async def add(self, mapping: ProviderInstrumentMapping) -> None:
        """Persist a new provider instrument mapping."""
        ...

    async def update(self, mapping: ProviderInstrumentMapping) -> None:
        """Update an existing mapping without changing its identity."""
        ...
