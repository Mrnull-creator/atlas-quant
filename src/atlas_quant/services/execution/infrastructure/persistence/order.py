from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import ExecutionBase


class OrderORM(ExecutionBase):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("account_id", "order_id", name="uq_execution_orders_account_order"),
        Index(
            "uq_execution_orders_one_active_per_account",
            "account_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'accepted', 'partially_filled')"
            ),
        ),
        Index(
            "uq_execution_orders_account_signal",
            "account_id",
            "signal_id",
            unique=True,
            postgresql_where=text("signal_id IS NOT NULL"),
        ),
        CheckConstraint("side IN ('buy', 'sell')", name="side_valid"),
        CheckConstraint("order_type = 'market'", name="order_type_valid"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'partially_filled', 'filled', 'cancelled', 'rejected')",
            name="status_valid",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("filled_quantity >= 0 AND filled_quantity <= quantity", name="filled_quantity_range"),
        CheckConstraint(
            "(status = 'filled' AND filled_quantity = quantity) OR "
            "(status = 'partially_filled' AND filled_quantity > 0 AND filled_quantity < quantity) OR "
            "(status IN ('pending', 'accepted', 'rejected') AND filled_quantity = 0) OR "
            "status = 'cancelled'",
            name="status_fill_consistency",
        ),
        CheckConstraint("updated_at >= created_at", name="timestamps_ordered"),
    )

    order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("execution.paper_accounts.account_id", ondelete="RESTRICT", name="fk_orders_account"),
        nullable=False,
    )
    instrument_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(12), nullable=False, default="market", server_default="market")
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False, default=Decimal("0"), server_default="0")
    strategy_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    signal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
