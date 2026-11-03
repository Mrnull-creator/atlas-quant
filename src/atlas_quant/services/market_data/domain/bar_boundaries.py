
"""Domain models for market data bar boundary policies."""

import re
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlas_quant.services.market_data.domain.enums import Timeframe


class BarAlignmentMode(StrEnum):
    """Supported bar alignment strategies."""

    UTC_CLOCK = "utc_clock"
    SESSION = "session"
    CALENDAR_PERIOD = "calendar_period"


@dataclass(frozen=True, slots=True)
class BarBoundaryPolicy:
    """Defines how expected market data bars are aligned and constructed.

    Intraday timeframes support UTC clock or session alignment.

    Daily, weekly, and monthly timeframes require calendar-period
    alignment with an explicit IANA timezone and local period-start
    time. Weekly policies additionally require an ISO weekday
    defining the first day of the week (Monday=1 through Sunday=7).

    Partial final bars are supported only for session-aligned
    intraday policies.
    """

    policy_id: str
    timeframe: Timeframe
    alignment_mode: BarAlignmentMode

    boundary_timezone: str | None = None
    period_start_time: time | None = None
    week_start_day: int | None = None
    allow_partial_final_bar: bool = False

    version: str = "1.0"

    def __post_init__(self) -> None:
        """Validate and normalize the boundary policy."""

        if not isinstance(self.policy_id, str):
            raise TypeError("policy_id must be a string.")

        normalized_id = self.policy_id.strip().lower()

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized_id):
            raise ValueError(
                "policy_id must contain lowercase letters, "
                "numbers, hyphens, and underscores."
            )

        object.__setattr__(self, "policy_id", normalized_id)

        if not isinstance(self.timeframe, Timeframe):
            raise TypeError("timeframe must be a Timeframe value.")

        if not isinstance(self.alignment_mode, BarAlignmentMode):
            raise TypeError(
                "alignment_mode must be a BarAlignmentMode value."
            )

        if not isinstance(self.allow_partial_final_bar, bool):
            raise TypeError("allow_partial_final_bar must be a boolean.")

        if not isinstance(self.version, str):
            raise TypeError("version must be a string.")

        normalized_version = self.version.strip()

        if not normalized_version:
            raise ValueError("version cannot be empty.")

        object.__setattr__(self, "version", normalized_version)

        intraday_timeframes = {
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.M30,
            Timeframe.H1,
            Timeframe.H4,
        }

        calendar_timeframes = {
            Timeframe.D1,
            Timeframe.W1,
            Timeframe.MN1,
        }

        if self.timeframe in intraday_timeframes:
            self._validate_intraday_policy()
        elif self.timeframe in calendar_timeframes:
            self._validate_calendar_policy()
        else:
            raise ValueError(f"Unsupported timeframe: {self.timeframe}.")

    def _validate_intraday_policy(self) -> None:
        """Validate intraday alignment configuration."""

        if self.alignment_mode not in (
            BarAlignmentMode.UTC_CLOCK,
            BarAlignmentMode.SESSION,
        ):
            raise ValueError(
                "Intraday timeframes require UTC_CLOCK or SESSION "
                "alignment."
            )

        if self.boundary_timezone is not None:
            raise ValueError(
                "boundary_timezone is not supported for intraday policies."
            )

        if self.period_start_time is not None:
            raise ValueError(
                "period_start_time is only supported for calendar policies."
            )

        if self.week_start_day is not None:
            raise ValueError(
                "week_start_day is not supported for intraday policies."
            )

        if (
            self.allow_partial_final_bar
            and self.alignment_mode != BarAlignmentMode.SESSION
        ):
            raise ValueError(
                "Partial final bars require SESSION alignment."
            )

    def _validate_calendar_policy(self) -> None:
        """Validate daily, weekly, and monthly configuration."""

        if self.alignment_mode != BarAlignmentMode.CALENDAR_PERIOD:
            raise ValueError(
                "Daily, weekly, and monthly timeframes require "
                "CALENDAR_PERIOD alignment."
            )

        if not isinstance(self.boundary_timezone, str):
            raise TypeError(
                "Calendar-period policies require a boundary_timezone."
            )

        normalized_timezone = self.boundary_timezone.strip()

        if not normalized_timezone:
            raise ValueError("boundary_timezone cannot be empty.")

        try:
            ZoneInfo(normalized_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"Unknown IANA timezone: {normalized_timezone}."
            ) from exc

        object.__setattr__(
            self,
            "boundary_timezone",
            normalized_timezone,
        )

        if not isinstance(self.period_start_time, time):
            raise TypeError(
                "Calendar-period policies require a period_start_time."
            )

        if self.period_start_time.tzinfo is not None:
            raise ValueError(
                "period_start_time must be a naive local wall-clock time."
            )

        if self.allow_partial_final_bar:
            raise ValueError(
                "allow_partial_final_bar is only supported for "
                "session-aligned intraday policies."
            )

        if self.timeframe == Timeframe.W1:
            self._validate_week_start_day()
        elif self.week_start_day is not None:
            raise ValueError(
                "week_start_day is only supported for weekly policies."
            )

    def _validate_week_start_day(self) -> None:
        """Require an explicit ISO weekday for weekly policies."""

        if self.week_start_day is None:
            raise ValueError(
                "Weekly policies require an explicit week_start_day."
            )

        if (
            isinstance(self.week_start_day, bool)
            or not isinstance(self.week_start_day, int)
        ):
            raise TypeError("week_start_day must be an integer.")

        if not 1 <= self.week_start_day <= 7:
            raise ValueError(
                "week_start_day must be between 1 and 7."
            )