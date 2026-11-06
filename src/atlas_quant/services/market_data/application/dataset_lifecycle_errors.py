"""Errors raised by dataset lifecycle persistence operations."""


class DatasetLifecycleRepositoryError(Exception):
    """Base exception for dataset lifecycle repository operations."""


class DatasetLifecycleDatasetNotFoundError(
    DatasetLifecycleRepositoryError
):
    """Raised when a requested dataset does not exist."""


class DatasetLifecycleVersionNotFoundError(
    DatasetLifecycleRepositoryError
):
    """Raised when a requested dataset version does not exist."""


class DatasetLifecycleVersionConflictError(
    DatasetLifecycleRepositoryError
):
    """Raised when a dataset version identity or number conflicts."""


class DatasetLifecycleInvalidVersionStateError(
    DatasetLifecycleRepositoryError
):
    """Raised when a version cannot undergo the requested transition."""


class DatasetLifecycleInvalidPublicationMetadataError(
    DatasetLifecycleRepositoryError
):
    """Raised when storage metadata is insufficient for publication."""


class DatasetLifecycleInvalidRecoveryQueryError(
    DatasetLifecycleRepositoryError
):
    """Raised when recovery discovery parameters are invalid."""