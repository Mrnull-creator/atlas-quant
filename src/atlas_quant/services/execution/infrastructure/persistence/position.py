from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import ExecutionBase


class PositionORM(ExecutionBase):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint(
            "(quantity = 0 AND average_entry_price = 0) OR "
            "(quantity <> 0 AND average_entry_price > 0)",
            name="quantity_average_price_consistency",
        ),
        CheckConstraint("contract_size > 0", name="contract_size_positive"),
        CheckConstraint(
            "length(quote_currency) = 3 AND quote_currency = upper(quote_currency)",
            name="quote_currency_format",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("execution.paper_accounts.account_id", ondelete="RESTRICT", name="fk_positions_account"),
        primary_key=True,
    )
    instrument_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    average_entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    contract_size: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
