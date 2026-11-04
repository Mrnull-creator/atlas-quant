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
