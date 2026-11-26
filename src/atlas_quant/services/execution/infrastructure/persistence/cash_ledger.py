from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import ExecutionBase


class CashLedgerEntryORM(ExecutionBase):
    __tablename__ = "cash_ledger"
    __table_args__ = (
        CheckConstraint("entry_type IN ('trade', 'commission', 'adjustment')", name="entry_type_valid"),
        CheckConstraint("length(currency) = 3 AND currency = upper(currency)", name="currency_format"),
        Index("ix_execution_cash_ledger_account_occurred_at", "account_id", "occurred_at"),
        Index(
            "uq_execution_cash_ledger_trade_reference",
            "account_id",
            "reference_id",
            unique=True,
            postgresql_where=text("entry_type = 'trade'"),
        ),
        Index(
            "uq_execution_cash_ledger_commission_reference",
            "account_id",
            "reference_id",
            unique=True,
            postgresql_where=text("entry_type = 'commission'"),
        ),
    )

    entry_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("execution.paper_accounts.account_id", ondelete="RESTRICT", name="fk_cash_ledger_account"),
        nullable=False,
    )
    entry_type: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reference_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
