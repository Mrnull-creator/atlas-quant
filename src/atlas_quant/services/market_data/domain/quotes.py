
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BidAskQuote:
    """Represents a normalized bid/ask market quote.

    The timestamp represents the observation time.
    All timestamps are normalized to UTC.

    Crossed quotes are preserved so that data-quality
    validation can assess them separately.
    """

    symbol: str
    timestamp: datetime

    bid: Decimal
    ask: Decimal

    bid_size: Decimal | None = None
    ask_size: Decimal | None = None

    def __post_init__(self) -> None:
        """Validate and normalize the market quote."""

        if not isinstance(self.symbol, str):
            raise TypeError("Instrument symbol must be a string.")

        normalized_symbol = self.symbol.strip().upper()

        if not normalized_symbol:
            raise ValueError("Instrument symbol cannot be empty.")

        object.__setattr__(self, "symbol", normalized_symbol)

        self._validate_timestamp()

        self._validate_positive_decimal("bid", self.bid)
        self._validate_positive_decimal("ask", self.ask)

        if self.bid_size is not None:
            self._validate_non_negative_decimal(
                "bid_size",
                self.bid_size,
            )

        if self.ask_size is not None:
            self._validate_non_negative_decimal(
                "ask_size",
                self.ask_size,
            )

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
    def _validate_positive_decimal(
        field_name: str,
        value: Decimal,
    ) -> None:
        """Require a finite positive Decimal value."""

        if not isinstance(value, Decimal):
            raise TypeError(
                f"{field_name} must use Decimal, not float or another type."
            )

        if not value.is_finite() or value <= Decimal("0"):
            raise ValueError(
                f"{field_name} must be a finite positive decimal."
            )

    @staticmethod
    def _validate_non_negative_decimal(
        field_name: str,
        value: Decimal,
    ) -> None:
        """Require a finite nonnegative Decimal value."""

        if not isinstance(value, Decimal):
            raise TypeError(
                f"{field_name} must use Decimal, not float or another type."
            )

        if not value.is_finite() or value < Decimal("0"):
            raise ValueError(
                f"{field_name} must be a finite nonnegative decimal."
            )