"""SQLAlchemy persistence mapping for financial instruments."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class InstrumentORM(MarketDataBase):
    """Database representation of a financial instrument."""

    __tablename__ = "instruments"

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "asset_class",
            "exchange",
            name="uq_instruments_business_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "price_precision >= 0",
            name="price_precision_nonnegative",
        ),
        CheckConstraint(
            "quantity_precision >= 0",
            name="quantity_precision_nonnegative",
        ),
        CheckConstraint(
            "tick_size IS NULL OR tick_size > 0",
            name="tick_size_positive",
        ),
        CheckConstraint(
            "contract_size IS NULL OR contract_size > 0",
            name="contract_size_positive",
        ),
    )

    instrument_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    symbol: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(
            AssetClass,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="asset_class_values",
        ),
        nullable=False,
    )

    exchange: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    base_currency: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    quote_currency: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    price_precision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
    )

    quantity_precision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
    )

    tick_size: Mapped[Decimal | None] = mapped_column(
        Numeric,
        nullable=True,
    )

    contract_size: Mapped[Decimal | None] = mapped_column(
        Numeric,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
