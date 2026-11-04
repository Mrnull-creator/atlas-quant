from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from atlas_quant.services.market_data.domain.enums import AssetClass


@dataclass(frozen=True, slots=True)
class Instrument:
    """Represents a financial instrument tracked by Atlas Quant."""

    symbol: str
    asset_class: AssetClass

    instrument_id: UUID = field(default_factory=uuid4)

    exchange: str | None = None

    base_currency: str | None = None
    quote_currency: str | None = None

    price_precision: int = 5
    quantity_precision: int = 2

    tick_size: Decimal | None = None
    contract_size: Decimal | None = None

    is_active: bool = True

    def __post_init__(self) -> None:
        """Validate and normalize instrument metadata."""

        if not isinstance(self.instrument_id, UUID):
            raise TypeError("instrument_id must be a UUID.")

        if not isinstance(self.symbol, str):
            raise TypeError("Instrument symbol must be a string.")

        normalized_symbol = self.symbol.strip().upper()

        if not normalized_symbol:
            raise ValueError("Instrument symbol cannot be empty.")

        object.__setattr__(self, "symbol", normalized_symbol)

        if not isinstance(self.asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass value.")

        if self.exchange is not None:
            if not isinstance(self.exchange, str):
                raise TypeError("Exchange must be a string.")

            normalized_exchange = self.exchange.strip().upper()

            if not normalized_exchange:
                raise ValueError("Exchange cannot be empty.")

            object.__setattr__(
                self,
                "exchange",
                normalized_exchange,
            )

        self._validate_currency("base_currency")
        self._validate_currency("quote_currency")

        self._validate_precision(
            "price_precision",
            self.price_precision,
        )

        self._validate_precision(
            "quantity_precision",
            self.quantity_precision,
        )

        self._validate_decimal("tick_size", self.tick_size)
        self._validate_decimal("contract_size", self.contract_size)

        if not isinstance(self.is_active, bool):
            raise TypeError("is_active must be a boolean.")

    @property
    def business_key(self) -> tuple[str, AssetClass, str | None]:
        """Return the normalized business uniqueness key."""

        return (
            self.symbol,
            self.asset_class,
            self.exchange,
        )

    def _validate_currency(self, field_name: str) -> None:
        """Validate optional currency identifiers."""

        value = getattr(self, field_name)

        if value is None:
            return

        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string.")

        normalized_value = value.strip().upper()

        if not normalized_value:
            raise ValueError(f"{field_name} cannot be empty.")

        object.__setattr__(self, field_name, normalized_value)

    @staticmethod
    def _validate_precision(
        field_name: str,
        value: int,
    ) -> None:
        """Validate decimal precision settings."""

        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{field_name} must be an integer.")

        if value < 0:
            raise ValueError(f"{field_name} cannot be negative.")

    @staticmethod
    def _validate_decimal(
        field_name: str,
        value: Decimal | None,
    ) -> None:
        """Validate optional positive decimal values."""

        if value is None:
            return

        if not isinstance(value, Decimal):
            raise TypeError(
                f"{field_name} must use Decimal, not float or another type."
            )

        if not value.is_finite() or value <= Decimal("0"):
            raise ValueError(
                f"{field_name} must be a finite positive decimal."
            )