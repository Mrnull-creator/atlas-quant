"""Domain model for provider-specific instrument identifiers."""

from dataclasses import dataclass, field
from re import fullmatch
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class ProviderInstrumentMapping:
    """Map an external provider identifier to a canonical instrument."""

    instrument_id: UUID
    provider_id: str
    provider_symbol: str
    provider_exchange: str | None = None
    mapping_id: UUID = field(default_factory=uuid4)
    is_active: bool = True

    def __post_init__(self) -> None:
        """Normalize and validate the mapping identity and attributes."""

        if not isinstance(self.mapping_id, UUID):
            raise TypeError("mapping_id must be a UUID.")

        if not isinstance(self.instrument_id, UUID):
            raise TypeError("instrument_id must be a UUID.")

        if not isinstance(self.provider_id, str):
            raise TypeError("provider_id must be a string.")

        normalized_provider_id = self.provider_id.strip().lower()

        if not normalized_provider_id:
            raise ValueError("provider_id must not be empty.")

        if fullmatch(
            r"[a-z0-9][a-z0-9_-]*",
            normalized_provider_id,
        ) is None:
            raise ValueError("provider_id contains invalid characters.")

        object.__setattr__(
            self,
            "provider_id",
            normalized_provider_id,
        )

        if not isinstance(self.provider_symbol, str):
            raise TypeError("provider_symbol must be a string.")

        normalized_symbol = self.provider_symbol.strip()

        if not normalized_symbol:
            raise ValueError("provider_symbol must not be empty.")

        # Provider symbol casing is deliberately preserved.
        object.__setattr__(
            self,
            "provider_symbol",
            normalized_symbol,
        )

        if self.provider_exchange is not None:
            if not isinstance(self.provider_exchange, str):
                raise TypeError(
                    "provider_exchange must be a string or None."
                )

            normalized_exchange = self.provider_exchange.strip()

            if not normalized_exchange:
                raise ValueError(
                    "provider_exchange must not be empty when supplied."
                )

            # Provider exchange casing is deliberately preserved.
            object.__setattr__(
                self,
                "provider_exchange",
                normalized_exchange,
            )

        if not isinstance(self.is_active, bool):
            raise TypeError("is_active must be a boolean.")

    @property
    def provider_identifier(self) -> tuple[str, str, str | None]:
        """Return the provider-scoped external identifier."""

        return (
            self.provider_id,
            self.provider_symbol,
            self.provider_exchange,
        )