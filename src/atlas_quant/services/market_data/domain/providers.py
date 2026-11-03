import re
from dataclasses import dataclass
from typing import TypeVar

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)


EnumValue = TypeVar("EnumValue", AssetClass, Timeframe)


@dataclass(frozen=True, slots=True)
class MarketDataProvider:
    """Describes a registered market data provider and its capabilities."""

    provider_id: str
    name: str

    supported_asset_classes: tuple[AssetClass, ...]
    supported_timeframes: tuple[Timeframe, ...] = ()

    supports_historical_data: bool = True
    supports_quote_data: bool = False

    is_enabled: bool = True

    def __post_init__(self) -> None:
        """Validate and normalize provider metadata."""

        if not isinstance(self.provider_id, str):
            raise TypeError("provider_id must be a string.")

        normalized_id = self.provider_id.strip().lower()

        if not normalized_id:
            raise ValueError("provider_id cannot be empty.")

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized_id):
            raise ValueError(
                "provider_id must contain only lowercase letters, "
                "numbers, hyphens, and underscores."
            )

        object.__setattr__(self, "provider_id", normalized_id)

        if not isinstance(self.name, str):
            raise TypeError("Provider name must be a string.")

        normalized_name = self.name.strip()

        if not normalized_name:
            raise ValueError("Provider name cannot be empty.")

        object.__setattr__(self, "name", normalized_name)

        normalized_asset_classes = self._normalize_enum_collection(
            "supported_asset_classes",
            self.supported_asset_classes,
            AssetClass,
            allow_empty=False,
        )

        normalized_timeframes = self._normalize_enum_collection(
            "supported_timeframes",
            self.supported_timeframes,
            Timeframe,
            allow_empty=True,
        )

        object.__setattr__(
            self,
            "supported_asset_classes",
            normalized_asset_classes,
        )

        object.__setattr__(
            self,
            "supported_timeframes",
            normalized_timeframes,
        )

        self._validate_boolean(
            "supports_historical_data",
            self.supports_historical_data,
        )

        self._validate_boolean(
            "supports_quote_data",
            self.supports_quote_data,
        )

        self._validate_boolean(
            "is_enabled",
            self.is_enabled,
        )

        if not (
            self.supports_historical_data
            or self.supports_quote_data
        ):
            raise ValueError(
                "Provider must support historical data, quote data, "
                "or both."
            )

        if self.supports_historical_data and not normalized_timeframes:
            raise ValueError(
                "A provider supporting historical data must declare "
                "at least one supported timeframe."
            )

        if not self.supports_historical_data and normalized_timeframes:
            raise ValueError(
                "A provider without historical-data support cannot "
                "declare historical timeframes."
            )

    @staticmethod
    def _normalize_enum_collection(
        field_name: str,
        values: tuple[EnumValue, ...],
        expected_type: type[EnumValue],
        *,
        allow_empty: bool,
    ) -> tuple[EnumValue, ...]:
        """Validate and normalize an immutable enum collection."""

        if isinstance(values, (str, bytes)):
            raise TypeError(f"{field_name} must be a collection.")

        try:
            normalized_values = tuple(values)
        except TypeError as exc:
            raise TypeError(
                f"{field_name} must be an iterable collection."
            ) from exc

        if not allow_empty and not normalized_values:
            raise ValueError(f"{field_name} cannot be empty.")

        for value in normalized_values:
            if not isinstance(value, expected_type):
                raise TypeError(
                    f"Every value in {field_name} must be "
                    f"a {expected_type.__name__} value."
                )

        if len(normalized_values) != len(set(normalized_values)):
            raise ValueError(
                f"{field_name} cannot contain duplicate values."
            )

        return normalized_values

    @staticmethod
    def _validate_boolean(
        field_name: str,
        value: bool,
    ) -> None:
        """Require actual boolean values."""

        if not isinstance(value, bool):
            raise TypeError(f"{field_name} must be a boolean.")