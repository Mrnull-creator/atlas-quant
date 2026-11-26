from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import ExecutionBase


class FillORM(ExecutionBase):
    __tablename__ = "fills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "order_id"],
            ["execution.orders.account_id", "execution.orders.order_id"],
            ondelete="RESTRICT",
            name="fk_fills_account_order",
        ),
        ForeignKeyConstraint(
            [
                "execution_attempt_id",
                "account_id",
                "order_id",
                "fill_id",
            ],
            [
                "execution.execution_attempts.attempt_id",
                "execution.execution_attempts.account_id",
                "execution.execution_attempts.order_id",
                "execution.execution_attempts.fill_id",
            ],
            ondelete="RESTRICT",
            name="fk_fills_execution_attempt_identity",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("commission >= 0", name="commission_nonnegative"),
        CheckConstraint(
            "length(commission_currency) = 3 AND commission_currency = upper(commission_currency)",
            name="commission_currency_format",
        ),
        CheckConstraint("contract_size > 0", name="contract_size_positive"),
        CheckConstraint(
            "length(quote_currency) = 3 AND quote_currency = upper(quote_currency)",
            name="quote_currency_format",
        ),
        CheckConstraint(
            "accounting_metadata_version IN (0, 1)",
            name="accounting_metadata_version_valid",
        ),
        Index("ix_execution_fills_account_executed_at", "account_id", "executed_at"),
        Index("ix_execution_fills_order_executed_at", "order_id", "executed_at"),
        Index(
            "uq_execution_fills_execution_attempt_id",
            "execution_attempt_id",
            unique=True,
        ),
    )

    fill_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    execution_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    commission_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    contract_size: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False
    )
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    accounting_metadata_version: Mapped[int] = mapped_column(
        SmallInteger(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )