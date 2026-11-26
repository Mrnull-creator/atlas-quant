from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import ExecutionBase


class PaperAccountORM(ExecutionBase):
    __tablename__ = "paper_accounts"
    __table_args__ = (
        CheckConstraint("length(base_currency) = 3 AND base_currency = upper(base_currency)", name="base_currency_format"),
        CheckConstraint("initial_capital > 0", name="initial_capital_positive"),
        CheckConstraint("state_revision >= 0", name="state_revision_nonnegative"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("updated_at >= created_at", name="timestamps_ordered"),
    )

    account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    current_cash: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    state_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
