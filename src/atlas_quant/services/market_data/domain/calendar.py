
"""Domain models for trading calendars and market sessions."""

import re
from dataclasses import dataclass
from datetime import date, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """Defines a local-time trading session window.

    close_day_offset is:
    - 0 when the session closes on its opening day.
    - 1 when the session closes on the following day.

    A 00:00 to 00:00 session with offset 1 represents 24 hours.
    """

    open_time: time
    close_time: time
    close_day_offset: int = 0

    def __post_init__(self) -> None:
        """Validate local session boundaries."""

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
                    "A same-day session must close after it opens."
                )
        elif self.close_time > self.open_time:
            raise ValueError(
                "A next-day session must close at or before "
                "its opening time."
            )


@dataclass(frozen=True, slots=True)
class WeeklySession:
    """Defines trading sessions for one ISO weekday.

    ISO weekdays range from 1 (Monday) to 7 (Sunday).
    Multiple windows allow daily breaks.
    """

    weekday: int
    windows: tuple[SessionWindow, ...]

    def __post_init__(self) -> None:
        """Validate and normalize weekly session windows."""

        if (
            isinstance(self.weekday, bool)
            or not isinstance(self.weekday, int)
        ):
            raise TypeError("weekday must be an integer.")

        if not 1 <= self.weekday <= 7:
            raise ValueError("weekday must be between 1 and 7.")

        windows = self._normalize_windows(self.windows)

        if not windows:
            raise ValueError("A weekly session must contain a window.")

        object.__setattr__(self, "windows", windows)

    @staticmethod
    def _normalize_windows(
        windows: tuple[SessionWindow, ...],
    ) -> tuple[SessionWindow, ...]:
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
            if not isinstance(window, SessionWindow):
                raise TypeError(
                    "Every window must be a SessionWindow."
                )

        if len(normalized) != len(set(normalized)):
            raise ValueError("Session windows cannot contain duplicates.")

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
class CalendarException:
    """Overrides the regular schedule for one local calendar date.

    An empty sessions collection represents a full-day closure.
    Non-empty sessions replace the regular sessions for that date.
    """

    trading_date: date
    sessions: tuple[SessionWindow, ...] = ()

    reason: str = ""
    source: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize a calendar exception."""

        if (
            not isinstance(self.trading_date, date)
            or isinstance(self.trading_date, __import__("datetime").datetime)
        ):
            raise TypeError("trading_date must be a date, not a datetime.")

        windows = WeeklySession._normalize_windows(self.sessions)
        object.__setattr__(self, "sessions", windows)

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
class TradingCalendar:
    """Defines a versioned trading calendar in an IANA timezone."""

    calendar_id: str
    name: str
    timezone: str

    weekly_sessions: tuple[WeeklySession, ...]
    exceptions: tuple[CalendarException, ...] = ()

    version: str = "1.0"

    def __post_init__(self) -> None:
        """Validate and normalize the trading calendar."""

        if not isinstance(self.calendar_id, str):
            raise TypeError("calendar_id must be a string.")

        normalized_id = self.calendar_id.strip().lower()

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized_id):
            raise ValueError(
                "calendar_id must contain lowercase letters, "
                "numbers, hyphens, and underscores."
            )

        object.__setattr__(self, "calendar_id", normalized_id)

        if not isinstance(self.name, str):
            raise TypeError("name must be a string.")

        normalized_name = self.name.strip()

        if not normalized_name:
            raise ValueError("name cannot be empty.")

        object.__setattr__(self, "name", normalized_name)

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

        if not isinstance(self.version, str):
            raise TypeError("version must be a string.")

        normalized_version = self.version.strip()

        if not normalized_version:
            raise ValueError("version cannot be empty.")

        object.__setattr__(self, "version", normalized_version)

        weekly_sessions = self._normalize_collection(
            "weekly_sessions",
            self.weekly_sessions,
            WeeklySession,
        )

        weekdays = [session.weekday for session in weekly_sessions]

        if len(weekdays) != len(set(weekdays)):
            raise ValueError(
                "weekly_sessions cannot contain duplicate weekdays."
            )

        object.__setattr__(
            self,
            "weekly_sessions",
            tuple(sorted(weekly_sessions, key=lambda item: item.weekday)),
        )

        exceptions = self._normalize_collection(
            "exceptions",
            self.exceptions,
            CalendarException,
        )

        exception_dates = [
            exception.trading_date for exception in exceptions
        ]

        if len(exception_dates) != len(set(exception_dates)):
            raise ValueError(
                "Calendar exceptions cannot contain duplicate dates."
            )

        object.__setattr__(
            self,
            "exceptions",
            tuple(
                sorted(
                    exceptions,
                    key=lambda item: item.trading_date,
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