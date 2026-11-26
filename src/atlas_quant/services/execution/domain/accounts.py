from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from atlas_quant.services.execution.domain.enums import AccountStatus


@dataclass(frozen=True, slots=True)
class PaperTradingAccount:
    name: str
    base_currency: str
    initial_capital: Decimal

    account_id: UUID = field(default_factory=uuid4)
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, UUID):
            raise TypeError("account_id must be a UUID.")

        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Account name cannot be empty.")

        object.__setattr__(self, "name", self.name.strip())

        if not isinstance(self.base_currency, str):
            raise TypeError("base_currency must be a string.")

        currency = self.base_currency.strip().upper()

        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("base_currency must be a three-letter currency code.")

        object.__setattr__(self, "base_currency", currency)

        if not isinstance(self.initial_capital, Decimal):
            raise TypeError("initial_capital must use Decimal.")

        if (
            not self.initial_capital.is_finite()
            or self.initial_capital <= Decimal("0")
        ):
            raise ValueError("initial_capital must be finite and positive.")

        if not isinstance(self.status, AccountStatus):
            raise TypeError("status must be an AccountStatus.")

        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime.")

        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware.")

        object.__setattr__(
            self,
            "created_at",
            self.created_at.astimezone(timezone.utc),
        )