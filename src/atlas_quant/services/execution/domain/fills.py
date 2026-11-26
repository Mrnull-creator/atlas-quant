from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: UUID
    account_id: UUID
    instrument_id: UUID

    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_currency: str

    fill_id: UUID = field(default_factory=uuid4)
    executed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        for name in (
            "fill_id",
            "order_id",
            "account_id",
            "instrument_id",
        ):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID.")

        for name, value, allow_zero in (
            ("quantity", self.quantity, False),
            ("price", self.price, False),
            ("commission", self.commission, True),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must use Decimal.")

            if not value.is_finite():
                raise ValueError(f"{name} must be finite.")

            if value < Decimal("0") or (
                not allow_zero and value == Decimal("0")
            ):
                raise ValueError(
                    f"{name} must be {'nonnegative' if allow_zero else 'positive'}."
                )

        if not isinstance(self.commission_currency, str):
            raise TypeError("commission_currency must be a string.")

        currency = self.commission_currency.strip().upper()

        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(
                "commission_currency must be a three-letter currency code."
            )

        object.__setattr__(self, "commission_currency", currency)

        if not isinstance(self.executed_at, datetime):
            raise TypeError("executed_at must be a datetime.")

        if (
            self.executed_at.tzinfo is None
            or self.executed_at.utcoffset() is None
        ):
            raise ValueError("executed_at must be timezone-aware.")

        object.__setattr__(
            self,
            "executed_at",
            self.executed_at.astimezone(timezone.utc),
        )