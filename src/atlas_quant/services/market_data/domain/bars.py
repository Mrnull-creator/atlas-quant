
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from atlas_quant.services.market_data.domain.enums import Timeframe


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """Represents one normalized historical OHLCV market data bar.

    The timestamp represents the opening time of the bar.
    All timestamps are normalized to UTC.
    Prices must be finite positive Decimal values.
    Volume must be finite and nonnegative when provided.
    """

    symbol: str
    timeframe: Timeframe
    timestamp: datetime

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    volume: Decimal | None = None

    def __post_init__(self) -> None:
        """Validate and normalize the market data bar."""

        if not isinstance(self.symbol, str):
            raise TypeError("Instrument symbol must be a string.")

        normalized_symbol = self.symbol.strip().upper()

        if not normalized_symbol:
            raise ValueError("Instrument symbol cannot be empty.")

        object.__setattr__(self, "symbol", normalized_symbol)

        if not isinstance(self.timeframe, Timeframe):
            raise TypeError("timeframe must be a Timeframe value.")

        self._validate_timestamp()

        for field_name in ("open", "high", "low", "close"):
            value = getattr(self, field_name)
            self._validate_decimal(field_name, value)

            if value <= Decimal("0"):
                raise ValueError(
                    f"{field_name} must be greater than zero."
                )

        if self.volume is not None:
            self._validate_decimal("volume", self.volume)

            if self.volume < Decimal("0"):
                raise ValueError("Volume cannot be negative.")

        self._validate_ohlc_relationships()

    def _validate_timestamp(self) -> None:
        """Require a timezone-aware timestamp and normalize it to UTC."""

        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime value.")

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError(
                "timestamp must include timezone information."
            )

        object.__setattr__(
            self,
            "timestamp",
            self.timestamp.astimezone(timezone.utc),
        )

    @staticmethod
    def _validate_decimal(
        field_name: str,
        value: Decimal,
    ) -> None:
        """Require finite Decimal values."""

        if not isinstance(value, Decimal):
            raise TypeError(
                f"{field_name} must use Decimal, not float or another type."
            )

        if not value.is_finite():
            raise ValueError(
                f"{field_name} must be a finite decimal value."
            )

    def _validate_ohlc_relationships(self) -> None:
        """Ensure the OHLC prices form a logically valid bar."""

        if self.high < max(self.open, self.close, self.low):
            raise ValueError(
                "High must be greater than or equal to open, close, and low."
            )

        if self.low > min(self.open, self.close, self.high):
            raise ValueError(
                "Low must be less than or equal to open, close, and high."
            )