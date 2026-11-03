
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from atlas_quant.services.market_data.domain.enums import Timeframe


class OHLCVBarContract(BaseModel):
    """Standardized historical OHLCV market data contract.

    The timestamp represents the opening time of the bar.
    All timestamps are normalized to UTC.
    Prices must be finite positive Decimal values.
    Volume must be finite and nonnegative when provided.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    symbol: str
    timeframe: Timeframe
    timestamp: datetime

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    volume: Decimal | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        """Normalize the instrument symbol."""

        value = value.strip().upper()

        if not value:
            raise ValueError("Instrument symbol cannot be empty.")

        return value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Require a timezone-aware timestamp and normalize it to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "timestamp must include timezone information."
            )

        return value.astimezone(timezone.utc)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        """Require finite positive prices."""

        if not value.is_finite() or value <= Decimal("0"):
            raise ValueError(
                "Price values must be finite and greater than zero."
            )

        return value

    @field_validator("volume")
    @classmethod
    def validate_volume(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        """Require finite nonnegative volume when provided."""

        if value is None:
            return None

        if not value.is_finite() or value < Decimal("0"):
            raise ValueError(
                "Volume must be finite and nonnegative."
            )

        return value

    @model_validator(mode="after")
    def validate_ohlc_relationships(self):
        """Ensure that OHLC prices form a logically valid bar."""

        if self.high < max(self.open, self.close, self.low):
            raise ValueError(
                "High must be greater than or equal to "
                "open, close, and low."
            )

        if self.low > min(self.open, self.close, self.high):
            raise ValueError(
                "Low must be less than or equal to "
                "open, close, and high."
            )

        return self


class BidAskQuoteContract(BaseModel):
    """Standardized bid/ask market quote contract.

    The timestamp represents the observation time.
    All timestamps are normalized to UTC.

    Crossed quotes are preserved for separate data-quality assessment.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    symbol: str
    timestamp: datetime

    bid: Decimal
    ask: Decimal

    bid_size: Decimal | None = None
    ask_size: Decimal | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        """Normalize the instrument symbol."""

        value = value.strip().upper()

        if not value:
            raise ValueError("Instrument symbol cannot be empty.")

        return value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Require a timezone-aware timestamp and normalize it to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "timestamp must include timezone information."
            )

        return value.astimezone(timezone.utc)

    @field_validator("bid", "ask")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        """Require finite positive bid and ask prices."""

        if not value.is_finite() or value <= Decimal("0"):
            raise ValueError(
                "Bid and ask prices must be finite and positive."
            )

        return value

    @field_validator("bid_size", "ask_size")
    @classmethod
    def validate_size(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        """Require finite nonnegative quote sizes when provided."""

        if value is None:
            return None

        if not value.is_finite() or value < Decimal("0"):
            raise ValueError(
                "Quote sizes must be finite and nonnegative."
            )

        return value