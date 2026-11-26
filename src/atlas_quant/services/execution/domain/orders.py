from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from atlas_quant.services.execution.domain.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
)


@dataclass(frozen=True, slots=True)
class Order:
    account_id: UUID
    instrument_id: UUID
    side: OrderSide
    quantity: Decimal

    order_id: UUID = field(default_factory=uuid4)
    order_type: OrderType = OrderType.MARKET
    status: OrderStatus = OrderStatus.PENDING

    filled_quantity: Decimal = Decimal("0")

    strategy_version_id: UUID | None = None
    signal_id: UUID | None = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        for name in ("account_id", "instrument_id", "order_id"):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID.")

        for name in ("strategy_version_id", "signal_id"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, UUID):
                raise TypeError(f"{name} must be a UUID or None.")

        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be an OrderSide.")

        if not isinstance(self.order_type, OrderType):
            raise TypeError("order_type must be an OrderType.")

        if not isinstance(self.status, OrderStatus):
            raise TypeError("status must be an OrderStatus.")

        self._validate_quantity("quantity", self.quantity, positive=True)
        self._validate_quantity(
            "filled_quantity", self.filled_quantity, positive=False
        )

        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity cannot exceed quantity.")

        if self.status == OrderStatus.FILLED:
            if self.filled_quantity != self.quantity:
                raise ValueError("A filled order must be completely filled.")

        if self.status == OrderStatus.PARTIALLY_FILLED:
            if not Decimal("0") < self.filled_quantity < self.quantity:
                raise ValueError(
                    "A partially filled order must have a partial quantity."
                )

        if self.status in (
            OrderStatus.PENDING,
            OrderStatus.ACCEPTED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        ) and self.filled_quantity != Decimal("0"):
            if self.status != OrderStatus.CANCELLED:
                raise ValueError(
                    "This order status cannot contain a filled quantity."
                )

        self._normalize_timestamp("created_at")
        self._normalize_timestamp("updated_at")

        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at.")

    @staticmethod
    def _validate_quantity(
        name: str,
        value: Decimal,
        positive: bool,
    ) -> None:
        if not isinstance(value, Decimal):
            raise TypeError(f"{name} must use Decimal.")

        if not value.is_finite():
            raise ValueError(f"{name} must be finite.")

        if positive and value <= Decimal("0"):
            raise ValueError(f"{name} must be positive.")

        if not positive and value < Decimal("0"):
            raise ValueError(f"{name} cannot be negative.")

    def _normalize_timestamp(self, name: str) -> None:
        value = getattr(self, name)

        if not isinstance(value, datetime):
            raise TypeError(f"{name} must be a datetime.")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware.")

        object.__setattr__(
            self,
            name,
            value.astimezone(timezone.utc),
        )

    def accept(self, at: datetime | None = None) -> "Order":
        if self.status != OrderStatus.PENDING:
            raise ValueError("Only pending orders can be accepted.")

        return self._transition(OrderStatus.ACCEPTED, at)

    def reject(self, at: datetime | None = None) -> "Order":
        if self.status != OrderStatus.PENDING:
            raise ValueError("Only pending orders can be rejected.")

        return self._transition(OrderStatus.REJECTED, at)

    def cancel(self, at: datetime | None = None) -> "Order":
        if self.status not in (
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            raise ValueError("Only active orders can be cancelled.")

        return self._transition(OrderStatus.CANCELLED, at)

    def apply_fill(
        self,
        fill_quantity: Decimal,
        at: datetime | None = None,
    ) -> "Order":
        if self.status not in (
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            raise ValueError("Only active orders can receive fills.")

        self._validate_quantity(
            "fill_quantity",
            fill_quantity,
            positive=True,
        )

        new_filled = self.filled_quantity + fill_quantity

        if new_filled > self.quantity:
            raise ValueError("Fill quantity exceeds remaining order quantity.")

        new_status = (
            OrderStatus.FILLED
            if new_filled == self.quantity
            else OrderStatus.PARTIALLY_FILLED
        )

        timestamp = _utc_now_or_validate(at)
        if timestamp < self.updated_at:
            raise ValueError("Order update timestamp cannot move backwards.")

        return replace(
            self,
            filled_quantity=new_filled,
            status=new_status,
            updated_at=timestamp,
        )

    def _transition(
        self,
        status: OrderStatus,
        at: datetime | None,
    ) -> "Order":
        timestamp = _utc_now_or_validate(at)
        if timestamp < self.updated_at:
            raise ValueError("Order update timestamp cannot move backwards.")

        return replace(
            self,
            status=status,
            updated_at=timestamp,
        )


def _utc_now_or_validate(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)

    if not isinstance(value, datetime):
        raise TypeError("Timestamp must be a datetime.")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware.")

    return value.astimezone(timezone.utc)