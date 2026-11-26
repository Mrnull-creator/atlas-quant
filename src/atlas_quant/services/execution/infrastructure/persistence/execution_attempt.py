"""Persistence mapping for durable execution attempts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import ExecutionBase


class ExecutionAttemptORM(ExecutionBase):
    __tablename__ = "execution_attempts"

    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "order_id"],
            [
                "execution.orders.account_id",
                "execution.orders.order_id",
            ],
            ondelete="RESTRICT",
            name="fk_execution_attempts_account_order",
        ),
        UniqueConstraint(
            "order_id",
            "attempt_number",
            name="uq_execution_attempts_order_attempt_number",
        ),
        UniqueConstraint(
            "fill_id",
            name="uq_execution_attempts_fill_id",
        ),
        UniqueConstraint(
            "attempt_id",
            "account_id",
            "order_id",
            "fill_id",
            name="uq_execution_attempts_attempt_account_order_fill",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="attempt_number_positive",
        ),
        CheckConstraint(
            "status IN ('prepared', 'completed', 'failed')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'prepared' AND outcome IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'completed' AND outcome IN "
            "('filled', 'partially_filled', 'no_liquidity', 'rejected') "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND outcome IS NULL "
            "AND completed_at IS NOT NULL AND reason IS NOT NULL)",
            name="lifecycle_consistency",
        ),
        CheckConstraint(
            "quote_bid > 0 AND quote_ask > 0",
            name="quote_prices_positive",
        ),
        CheckConstraint(
            "(quote_bid_size IS NULL OR quote_bid_size >= 0) AND "
            "(quote_ask_size IS NULL OR quote_ask_size >= 0)",
            name="quote_sizes_nonnegative",
        ),
        CheckConstraint(
            "commission_per_1000_units >= 0",
            name="commission_nonnegative",
        ),
        CheckConstraint(
            "slippage_ticks >= 0",
            name="slippage_ticks_nonnegative",
        ),
        CheckConstraint(
            "max_quote_age_microseconds IS NULL OR "
            "max_quote_age_microseconds > 0",
            name="max_quote_age_positive",
        ),
        CheckConstraint(
            "missing_liquidity_policy IN ('reject', 'assume_full_fill')",
            name="liquidity_policy_valid",
        ),
        CheckConstraint(
            "price_precision >= 0 AND quantity_precision >= 0",
            name="precision_nonnegative",
        ),
        CheckConstraint(
            "tick_size IS NULL OR tick_size > 0",
            name="tick_size_positive",
        ),
        CheckConstraint(
            "contract_size IS NULL OR contract_size > 0",
            name="contract_size_positive",
        ),
        CheckConstraint(
            "length(quote_currency) = 3 AND "
            "quote_currency = upper(quote_currency)",
            name="quote_currency_format",
        ),
        CheckConstraint(
            "base_currency IS NULL OR "
            "(length(base_currency) = 3 AND "
            "base_currency = upper(base_currency))",
            name="base_currency_format",
        ),
        CheckConstraint(
            "reference_price IS NULL OR reference_price > 0",
            name="reference_price_positive",
        ),
        CheckConstraint(
            "execution_price IS NULL OR execution_price > 0",
            name="execution_price_positive",
        ),
        CheckConstraint(
            "slippage_cost >= 0",
            name="slippage_cost_nonnegative",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="timestamps_ordered",
        ),
        Index(
            "uq_execution_attempts_one_prepared_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'prepared'"),
        ),
        Index(
            "ix_execution_attempts_order_created_at",
            "order_id",
            "created_at",
        ),
    )

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    fill_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="prepared",
        server_default="prepared",
    )

    # Immutable quote snapshot.
    quote_symbol: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    quote_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    quote_bid: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    quote_ask: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    quote_bid_size: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    quote_ask_size: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    # Immutable instrument execution snapshot.
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    instrument_symbol: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    asset_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    exchange: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    base_currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )

    quote_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    price_precision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    quantity_precision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    tick_size: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    contract_size: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    instrument_is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    # Immutable simulation configuration.
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    commission_per_1000_units: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
    )

    slippage_ticks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    max_quote_age_microseconds: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    missing_liquidity_policy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    # Execution result, populated when the attempt completes.
    outcome: Mapped[str | None] = mapped_column(
        String(24),
        nullable=True,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reference_price: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    execution_price: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 18),
        nullable=True,
    )

    slippage_cost: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    liquidity_assumed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )