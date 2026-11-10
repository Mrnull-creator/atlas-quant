"""SQLAlchemy persistence mapping for market data datasets."""

from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.infrastructure.persistence.base import (
    MarketDataBase,
)


class DatasetORM(MarketDataBase):
    """Database representation of a logical market data dataset."""

    __tablename__ = "datasets"

    __table_args__ = (
        CheckConstraint(
            """
            (
                data_kind = 'raw_provider'
                AND provider_id IS NOT NULL
            )
            OR
            (
                data_kind = 'ohlcv'
                AND instrument_id IS NOT NULL
                AND instrument_symbol IS NOT NULL
                AND timeframe IS NOT NULL
            )
            OR
            (
                data_kind = 'bid_ask'
                AND instrument_id IS NOT NULL
                AND instrument_symbol IS NOT NULL
                AND timeframe IS NULL
            )
            """,
            name="valid_dataset_identity",
        ),
        ForeignKeyConstraint(
            [
                "instrument_id",
                "instrument_symbol",
                "asset_class",
            ],
            [
                "instruments.instrument_id",
                "instruments.symbol",
                "instruments.asset_class",
            ],
            name="fk_datasets_instrument_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "latest_version_id"],
            [
                "dataset_versions.dataset_id",
                "dataset_versions.dataset_version_id",
            ],
            name="fk_datasets_latest_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),

        Index(
            "uq_datasets_ohlcv_instrument_timeframe",
            "instrument_id",
            "timeframe",
            unique=True,
            postgresql_where=text("data_kind = 'ohlcv'"),
            sqlite_where=text("data_kind = 'ohlcv'"),
        ),
        Index(
            "uq_datasets_bid_ask_instrument",
            "instrument_id",
            unique=True,
            postgresql_where=text("data_kind = 'bid_ask'"),
            sqlite_where=text("data_kind = 'bid_ask'"),
        ),

    )

    dataset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    data_kind: Mapped[DatasetKind] = mapped_column(
        Enum(
            DatasetKind,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="dataset_kind_values",
        ),
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

    instrument_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "instruments.instrument_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    instrument_symbol: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    provider_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey(
            "market_data_providers.provider_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    timeframe: Mapped[Timeframe | None] = mapped_column(
        Enum(
            Timeframe,
            values_callable=lambda enum_type: [
                member.value for member in enum_type
            ],
            native_enum=False,
            create_constraint=True,
            name="timeframe_values",
        ),
        nullable=True,
    )

    latest_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
