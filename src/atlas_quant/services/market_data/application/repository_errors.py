"""Application-level errors raised by repository implementations."""


class InstrumentRepositoryError(Exception):
    """Base exception for instrument repository operations."""


class InstrumentBusinessKeyConflictError(InstrumentRepositoryError):
    """Raised when an instrument business key violates uniqueness."""


class InstrumentRepositoryRecordNotFoundError(InstrumentRepositoryError):
    """Raised when an instrument expected by a repository operation is absent."""


class ProviderRepositoryError(Exception):
    """Base exception for provider repository operations."""


class ProviderRepositoryDuplicateError(ProviderRepositoryError):
    """Raised when a provider identifier violates uniqueness."""


class ProviderRepositoryRecordNotFoundError(ProviderRepositoryError):
    """Raised when a provider expected by a repository operation is absent."""


class ProviderInstrumentMappingRepositoryError(Exception):
    """Base exception for provider instrument mapping operations."""


class ProviderInstrumentMappingConflictError(
    ProviderInstrumentMappingRepositoryError
):
    """Raised when an active provider identifier is already mapped."""


class ProviderInstrumentMappingRecordNotFoundError(
    ProviderInstrumentMappingRepositoryError
):
    """Raised when a requested mapping does not exist."""


class DatasetRepositoryError(Exception):
    """Base exception for dataset repository operations."""


class DatasetRepositoryDuplicateError(DatasetRepositoryError):
    """Raised when a dataset identifier already exists."""


class DatasetRepositoryRecordNotFoundError(DatasetRepositoryError):
    """Raised when a requested dataset does not exist."""


class DatasetVersionRepositoryError(Exception):
    """Base exception for dataset version repository operations."""


class DatasetVersionRepositoryConflictError(
    DatasetVersionRepositoryError
):
    """Raised when a dataset version identity or number already exists."""


class DatasetVersionRepositoryDatasetNotFoundError(
    DatasetVersionRepositoryError
):
    """Raised when the parent dataset does not exist."""


class DatasetVersionRepositoryRecordNotFoundError(
    DatasetVersionRepositoryError
):
    """Raised when a requested dataset version does not exist."""


class IngestionJobRepositoryError(Exception):
    """Base exception for ingestion job repository operations."""


class IngestionJobRepositoryConflictError(IngestionJobRepositoryError):
    """Raised when an ingestion job identifier already exists."""


class IngestionJobRepositoryRecordNotFoundError(
    IngestionJobRepositoryError
):
    """Raised when a requested ingestion job does not exist."""


class IngestionJobRepositoryInvalidTransitionError(
    IngestionJobRepositoryError
):
    """Raised when an ingestion job update violates lifecycle rules."""


class IngestionJobRepositoryIntegrityError(IngestionJobRepositoryError):
    """Raised when ingestion job persistence violates a database constraint."""


class IngestionJobRepositoryExecutionOwnershipError(
    IngestionJobRepositoryError
):
    """Raised when a worker no longer owns the current ingestion attempt."""


class DatasetVersionLineageRepositoryError(Exception):
    """Base exception for dataset-version lineage repository operations."""


class DatasetVersionLineageRepositoryConflictError(
    DatasetVersionLineageRepositoryError
):
    """Raised when a lineage identifier already exists."""


class DatasetVersionLineageRepositoryRecordNotFoundError(
    DatasetVersionLineageRepositoryError
):
    """Raised when a requested lineage relationship does not exist."""


class DatasetVersionLineageRepositoryIntegrityError(
    DatasetVersionLineageRepositoryError
):
    """Raised when lineage persistence violates a database constraint."""