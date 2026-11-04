"""SQLAlchemy persistence mapping for market data providers."""

from sqlalchemy import Boolean, CheckConstraint, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class ProviderORM(MarketDataBase):
    """Database representation of a market data provider."""

    __tablename__ = "market_data_providers"

    __table_args__ = (
        CheckConstraint(
            "supports_historical_data OR supports_quote_data",
            name="at_least_one_data_capability",
        ),
    )

    provider_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    supported_asset_classes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
    )

    supported_timeframes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
    )

    supports_historical_data: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    supports_quote_data: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
