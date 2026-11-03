"""Domain models for provider-specific data availability schedules."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)


@dataclass(frozen=True, slots=True)
class ProviderAvailabilityWindow:
    """Defines a provider availability interval in local time.

    close_day_offset is:
    - 0 when availability ends on its opening date.
    - 1 when availability ends on the following date.

    A 00:00 to 00:00 interval with offset 1 represents 24 hours.
    """

    open_time: time
    close_time: time
    close_day_offset: int = 0

    def __post_init__(self) -> None:
        """Validate local availability boundaries."""

        for field_name in ("open_time", "close_time"):
            value = getattr(self, field_name)

            if not isinstance(value, time):
                raise TypeError(f"{field_name} must be a time value.")

            if value.tzinfo is not None:
                raise ValueError(
                    f"{field_name} must be a local time without timezone."
                )

        if (
            isinstance(self.close_day_offset, bool)
            or not isinstance(self.close_day_offset, int)
        ):
            raise TypeError("close_day_offset must be an integer.")

        if self.close_day_offset not in (0, 1):
            raise ValueError("close_day_offset must be 0 or 1.")

        if self.close_day_offset == 0:
            if self.close_time <= self.open_time:
                raise ValueError(
                    "A same-day availability window must close "
                    "after it opens."
                )
        elif self.close_time > self.open_time:
            raise ValueError(
                "A next-day availability window must close at or "
                "before its opening time."
            )


@dataclass(frozen=True, slots=True)
class WeeklyProviderAvailability:
    """Defines recurring availability for one ISO weekday."""

    weekday: int
    windows: tuple[ProviderAvailabilityWindow, ...]

    def __post_init__(self) -> None:
        """Validate and normalize weekly availability windows."""

        if (
            isinstance(self.weekday, bool)
            or not isinstance(self.weekday, int)
        ):
            raise TypeError("weekday must be an integer.")

        if not 1 <= self.weekday <= 7:
            raise ValueError("weekday must be between 1 and 7.")

        windows = self._normalize_windows(self.windows)

        if not windows:
            raise ValueError(
                "Weekly provider availability must contain a window."
            )

        object.__setattr__(self, "windows", windows)

    @staticmethod
    def _normalize_windows(
        windows: tuple[ProviderAvailabilityWindow, ...],
    ) -> tuple[ProviderAvailabilityWindow, ...]:
        """Validate and sort an immutable collection of windows."""

        if isinstance(windows, (str, bytes)):
            raise TypeError("windows must be a collection.")

        try:
            normalized = tuple(windows)
        except TypeError as exc:
            raise TypeError(
                "windows must be an iterable collection."
            ) from exc

        for window in normalized:
            if not isinstance(window, ProviderAvailabilityWindow):
                raise TypeError(
                    "Every window must be a "
                    "ProviderAvailabilityWindow."
                )

        if len(normalized) != len(set(normalized)):
            raise ValueError(
                "Availability windows cannot contain duplicates."
            )

        return tuple(
            sorted(
                normalized,
                key=lambda item: (
                    item.open_time,
                    item.close_time,
                    item.close_day_offset,
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ProviderAvailabilityException:
    """Overrides recurring provider availability for one local date.

    An empty windows collection means the provider is unavailable
    for the entire date.

    Non-empty windows replace the regular availability windows.
    """

    availability_date: date
    windows: tuple[ProviderAvailabilityWindow, ...] = ()

    reason: str = ""
    source: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize an availability exception."""

        if (
            not isinstance(self.availability_date, date)
            or isinstance(self.availability_date, datetime)
        ):
            raise TypeError(
                "availability_date must be a date, not a datetime."
            )

        windows = WeeklyProviderAvailability._normalize_windows(
            self.windows
        )
        object.__setattr__(self, "windows", windows)

        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string.")

        normalized_reason = self.reason.strip()

        if not normalized_reason:
            raise ValueError("reason cannot be empty.")

        object.__setattr__(self, "reason", normalized_reason)

        if self.source is not None:
            if not isinstance(self.source, str):
                raise TypeError("source must be a string or None.")

            normalized_source = self.source.strip()

            if not normalized_source:
                raise ValueError("source cannot be empty.")

            object.__setattr__(self, "source", normalized_source)


@dataclass(frozen=True, slots=True)
class ProviderAvailabilitySchedule:
    """Defines a versioned provider availability schedule.

    A schedule applies to one provider, asset class, and timeframe.

    When instrument_symbol is supplied, the schedule is instrument-specific.
    Otherwise, it applies at the asset-class level.

    All local-time windows and exceptions use the configured IANA timezone.
    """

    provider_id: str
    asset_class: AssetClass
    timeframe: Timeframe
    timezone: str

    weekly_availability: tuple[WeeklyProviderAvailability, ...]
    exceptions: tuple[ProviderAvailabilityException, ...] = ()

    instrument_symbol: str | None = None
    version: str = "1.0"

    def __post_init__(self) -> None:
        """Validate and normalize the availability schedule."""

        if not isinstance(self.provider_id, str):
            raise TypeError("provider_id must be a string.")

        normalized_provider_id = self.provider_id.strip().lower()

        if not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*",
            normalized_provider_id,
        ):
            raise ValueError(
                "provider_id must contain lowercase letters, "
                "numbers, hyphens, and underscores."
            )

        object.__setattr__(
            self,
            "provider_id",
            normalized_provider_id,
        )

        if not isinstance(self.asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass value.")

        if not isinstance(self.timeframe, Timeframe):
            raise TypeError("timeframe must be a Timeframe value.")

        if not isinstance(self.timezone, str):
            raise TypeError("timezone must be a string.")

        normalized_timezone = self.timezone.strip()

        if not normalized_timezone:
            raise ValueError("timezone cannot be empty.")

        try:
            ZoneInfo(normalized_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"Unknown IANA timezone: {normalized_timezone}."
            ) from exc

        object.__setattr__(self, "timezone", normalized_timezone)

        if self.instrument_symbol is not None:
            if not isinstance(self.instrument_symbol, str):
                raise TypeError(
                    "instrument_symbol must be a string or None."
                )

            normalized_symbol = self.instrument_symbol.strip().upper()

            if not normalized_symbol:
                raise ValueError(
                    "instrument_symbol cannot be empty."
                )

            object.__setattr__(
                self,
                "instrument_symbol",
                normalized_symbol,
            )

        if not isinstance(self.version, str):
            raise TypeError("version must be a string.")

        normalized_version = self.version.strip()

        if not normalized_version:
            raise ValueError("version cannot be empty.")

        object.__setattr__(self, "version", normalized_version)

        weekly_availability = self._normalize_collection(
            "weekly_availability",
            self.weekly_availability,
            WeeklyProviderAvailability,
        )

        weekdays = [
            item.weekday for item in weekly_availability
        ]

        if len(weekdays) != len(set(weekdays)):
            raise ValueError(
                "weekly_availability cannot contain duplicate weekdays."
            )

        object.__setattr__(
            self,
            "weekly_availability",
            tuple(
                sorted(
                    weekly_availability,
                    key=lambda item: item.weekday,
                )
            ),
        )

        exceptions = self._normalize_collection(
            "exceptions",
            self.exceptions,
            ProviderAvailabilityException,
        )

        exception_dates = [
            item.availability_date for item in exceptions
        ]

        if len(exception_dates) != len(set(exception_dates)):
            raise ValueError(
                "Provider availability exceptions cannot contain "
                "duplicate dates."
            )

        object.__setattr__(
            self,
            "exceptions",
            tuple(
                sorted(
                    exceptions,
                    key=lambda item: item.availability_date,
                )
            ),
        )

    @staticmethod
    def _normalize_collection(
        field_name: str,
        values: tuple,
        expected_type: type,
    ) -> tuple:
        """Validate and normalize an immutable collection."""

        if isinstance(values, (str, bytes)):
            raise TypeError(f"{field_name} must be a collection.")

        try:
            normalized = tuple(values)
        except TypeError as exc:
            raise TypeError(
                f"{field_name} must be an iterable collection."
            ) from exc

        for value in normalized:
            if not isinstance(value, expected_type):
                raise TypeError(
                    f"Every item in {field_name} must be "
                    f"a {expected_type.__name__}."
                )

        return normalized