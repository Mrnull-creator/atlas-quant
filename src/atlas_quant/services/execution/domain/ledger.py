from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from atlas_quant.services.execution.domain.enums import LedgerEntryType


@dataclass(frozen=True, slots=True)
class CashLedgerEntry:
    account_id: UUID
    entry_type: LedgerEntryType
    amount: Decimal
    currency: str
    reference_id: UUID
    description: str

    entry_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        for name in ("account_id", "entry_id", "reference_id"):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID.")

        if not isinstance(self.entry_type, LedgerEntryType):
            raise TypeError("entry_type must be a LedgerEntryType.")

        if not isinstance(self.amount, Decimal):
            raise TypeError("amount must use Decimal.")

        if not self.amount.is_finite():
            raise ValueError("amount must be finite.")

        if not isinstance(self.currency, str):
            raise TypeError("currency must be a string.")

        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter currency code.")

        object.__setattr__(self, "currency", currency)

        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description cannot be empty.")

        object.__setattr__(self, "description", self.description.strip())

        if not isinstance(self.occurred_at, datetime):
            raise TypeError("occurred_at must be a datetime.")

        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must be timezone-aware.")

        object.__setattr__(
            self,
            "occurred_at",
            self.occurred_at.astimezone(timezone.utc),
        )