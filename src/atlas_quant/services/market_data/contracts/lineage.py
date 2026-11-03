
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataLineageContract(BaseModel):
    """Tracks the origin and processing history of market data.

    This contract links normalized observations to their original
    provider data and ingestion process.

    Original provider data must remain independently preserved.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    provider: str = Field(min_length=1)
    provider_symbol: str = Field(min_length=1)

    source_record_id: str | None = None

    source_timestamp: datetime
    ingestion_timestamp: datetime

    provider_schema_version: str | None = None

    raw_data_uri: str = Field(min_length=1)

    raw_data_sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    @field_validator(
        "provider",
        "provider_symbol",
        "source_record_id",
        "provider_schema_version",
        "raw_data_uri",
    )
    @classmethod
    def normalize_identifiers(
        cls,
        value: str | None,
    ) -> str | None:
        """Remove surrounding whitespace and reject empty identifiers."""

        if value is None:
            return None

        value = value.strip()

        if not value:
            raise ValueError("Identifier cannot be empty.")

        return value

    @field_validator(
        "source_timestamp",
        "ingestion_timestamp",
    )
    @classmethod
    def normalize_timestamps(
        cls,
        value: datetime,
    ) -> datetime:
        """Require timezone-aware timestamps and normalize them to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "Lineage timestamps must include timezone information."
            )

        return value.astimezone(timezone.utc)

    @field_validator("raw_data_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        """Require a valid 64-character hexadecimal SHA-256 digest."""

        value = value.strip().lower()

        if len(value) != 64:
            raise ValueError(
                "SHA-256 checksum must contain exactly 64 characters."
            )

        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError(
                "SHA-256 checksum must contain hexadecimal characters."
            ) from exc

        return value