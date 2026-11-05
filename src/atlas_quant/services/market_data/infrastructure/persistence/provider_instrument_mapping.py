"""SQLAlchemy persistence mapping for provider instrument identifiers."""

from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class ProviderInstrumentMappingORM(MarketDataBase):
    """Database representation of a provider-specific instrument mapping."""

    __tablename__ = "provider_instrument_mappings"

    __table_args__ = (
        Index(
            "uq_provider_instrument_mappings_active_provider_identifier",
            "provider_id",
            "provider_symbol",
            "provider_exchange",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    mapping_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "instruments.instrument_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    provider_id: Mapped[str] = mapped_column(
        String,
        ForeignKey(
            "market_data_providers.provider_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    provider_symbol: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    # Empty string represents a missing provider exchange.
    # This keeps the external identifier consistently unique.
    provider_exchange: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
        server_default="",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )