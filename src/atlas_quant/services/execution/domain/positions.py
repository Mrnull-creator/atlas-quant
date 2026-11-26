from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: UUID
    quote_currency: str
    quantity: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal = ZERO
    contract_size: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, UUID):
            raise TypeError("instrument_id must be a UUID.")

        if not isinstance(self.quote_currency, str):
            raise TypeError("quote_currency must be a string.")

        currency = self.quote_currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("quote_currency must be a three-letter currency code.")

        object.__setattr__(self, "quote_currency", currency)

        for name in (
            "quantity",
            "average_entry_price",
            "realized_pnl",
            "contract_size",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must use Decimal.")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite.")

        if self.average_entry_price < ZERO:
            raise ValueError("average_entry_price cannot be negative.")

        if self.contract_size <= ZERO:
            raise ValueError("contract_size must be positive.")

        if self.quantity == ZERO and self.average_entry_price != ZERO:
            raise ValueError("A flat position must have zero average entry price.")

        if self.quantity != ZERO and self.average_entry_price <= ZERO:
            raise ValueError("An open position must have a positive average entry price.")


@dataclass(frozen=True, slots=True)
class PositionMark:
    instrument_id: UUID
    bid: Decimal
    ask: Decimal
    timestamp: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, UUID):
            raise TypeError("instrument_id must be a UUID.")

        for name in ("bid", "ask"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must use Decimal.")
            if not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be finite and positive.")

        if self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid.")

        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime.")

        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")

        object.__setattr__(
            self,
            "timestamp",
            self.timestamp.astimezone(timezone.utc),
        )