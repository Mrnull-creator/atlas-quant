
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlas_quant.services.market_data.domain.enums import AssetClass


class InstrumentContract(BaseModel):
    """Standardized instrument metadata exchange contract."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    symbol: str
    asset_class: AssetClass

    exchange: str | None = None

    base_currency: str | None = None
    quote_currency: str | None = None

    price_precision: int = Field(default=5, ge=0, strict=True)
    quantity_precision: int = Field(default=2, ge=0, strict=True)

    tick_size: Decimal | None = None
    contract_size: Decimal | None = None

    is_active: bool = True

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        """Normalize the instrument symbol."""

        value = value.strip().upper()

        if not value:
            raise ValueError("Instrument symbol cannot be empty.")

        return value

    @field_validator("exchange", "base_currency", "quote_currency")
    @classmethod
    def normalize_identifiers(
        cls,
        value: str | None,
    ) -> str | None:
        """Normalize optional instrument identifiers."""

        if value is None:
            return None

        value = value.strip().upper()

        if not value:
            raise ValueError("Identifier cannot be empty.")

        return value

    @field_validator("tick_size", "contract_size")
    @classmethod
    def validate_positive_decimal(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        """Require optional contract dimensions to be finite and positive."""

        if value is None:
            return None

        if not value.is_finite() or value <= Decimal("0"):
            raise ValueError(
                "Value must be a finite positive decimal."
            )

        return value