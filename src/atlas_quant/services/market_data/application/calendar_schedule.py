
"""Resolve trading calendars into effective UTC session intervals."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from atlas_quant.services.market_data.domain.calendar import (
    SessionWindow,
    TradingCalendar,
)


@dataclass(frozen=True, slots=True)
class ResolvedSessionInterval:
    """One effective session interval represented in UTC."""

    trading_date: date
    origin_date: date
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self) -> None:
        """Validate interval boundaries."""

        if not isinstance(self.trading_date, date):
            raise TypeError("trading_date must be a date.")

        if not isinstance(self.origin_date, date):
            raise TypeError("origin_date must be a date.")

        for field_name in ("start_utc", "end_utc"):
            value = getattr(self, field_name)

            if not isinstance(value, datetime):
                raise TypeError(f"{field_name} must be a datetime.")

            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware.")

            object.__setattr__(
                self,
                field_name,
                value.astimezone(timezone.utc),
            )

        if self.end_utc <= self.start_utc:
            raise ValueError("Session interval must have positive duration.")


@dataclass(frozen=True, slots=True)
class _CandidateSession:
    """Internal session interval before local-date exception filtering."""

    origin_date: date
    is_exception_session: bool
    start_utc: datetime
    end_utc: datetime


class CalendarScheduleResolver:
    """Resolve effective trading sessions for a local date range.

    Date ranges are inclusive and use the calendar's local timezone.

    Calendar exceptions have date-wide precedence. A full-day closure
    suppresses all sessions on that local date, including overnight
    sessions originating on the previous date. Replacement sessions
    are the only sessions permitted to originate on an exception date.
    """

    def resolve(
        self,
        calendar: TradingCalendar,
        start_date: date,
        end_date: date,
    ) -> tuple[ResolvedSessionInterval, ...]:
        """Resolve a calendar into non-overlapping UTC intervals."""

        if not isinstance(calendar, TradingCalendar):
            raise TypeError("calendar must be a TradingCalendar.")

        self._validate_date("start_date", start_date)
        self._validate_date("end_date", end_date)

        if end_date < start_date:
            raise ValueError("end_date cannot be earlier than start_date.")

        zone = ZoneInfo(calendar.timezone)

        weekly_sessions = {
            session.weekday: session
            for session in calendar.weekly_sessions
        }

        exceptions = {
            item.trading_date: item
            for item in calendar.exceptions
        }

        candidates = self._build_candidates(
            zone=zone,
            weekly_sessions=weekly_sessions,
            exceptions=exceptions,
            start_date=start_date,
            end_date=end_date,
        )

        resolved: list[ResolvedSessionInterval] = []

        current_date = start_date

        while current_date <= end_date:
            day_start = self._local_to_utc(
                datetime.combine(current_date, time.min),
                zone,
            )

            next_date = current_date + timedelta(days=1)

            day_end = self._local_to_utc(
                datetime.combine(next_date, time.min),
                zone,
            )

            exception_applies = current_date in exceptions

            daily_intervals: list[ResolvedSessionInterval] = []

            for candidate in candidates:
                if exception_applies and not (
                    candidate.origin_date == current_date
                    and candidate.is_exception_session
                ):
                    continue

                interval_start = max(
                    candidate.start_utc,
                    day_start,
                )
                interval_end = min(
                    candidate.end_utc,
                    day_end,
                )

                if interval_start >= interval_end:
                    continue

                daily_intervals.append(
                    ResolvedSessionInterval(
                        trading_date=current_date,
                        origin_date=candidate.origin_date,
                        start_utc=interval_start,
                        end_utc=interval_end,
                    )
                )

            daily_intervals.sort(key=lambda item: item.start_utc)

            self._validate_no_overlaps(
                current_date,
                daily_intervals,
            )

            resolved.extend(daily_intervals)
            current_date = next_date

        return tuple(resolved)

    @staticmethod
    def _validate_date(field_name: str, value: date) -> None:
        """Require a date without accepting datetime subclasses."""

        if not isinstance(value, date) or isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a date, not a datetime.")

    def _build_candidates(
        self,
        *,
        zone: ZoneInfo,
        weekly_sessions: dict,
        exceptions: dict,
        start_date: date,
        end_date: date,
    ) -> tuple[_CandidateSession, ...]:
        """Build sessions originating within the relevant date range."""

        candidates: list[_CandidateSession] = []

        # Include the previous local date for overnight sessions
        # extending into the requested start date.
        origin_date = start_date - timedelta(days=1)

        while origin_date <= end_date:
            exception = exceptions.get(origin_date)

            if exception is not None:
                windows = exception.sessions
                is_exception_session = True
            else:
                weekly = weekly_sessions.get(origin_date.isoweekday())
                windows = weekly.windows if weekly is not None else ()
                is_exception_session = False

            for window in windows:
                candidates.append(
                    self._build_candidate(
                        origin_date=origin_date,
                        window=window,
                        is_exception_session=is_exception_session,
                        zone=zone,
                    )
                )

            origin_date += timedelta(days=1)

        return tuple(candidates)

    def _build_candidate(
        self,
        *,
        origin_date: date,
        window: SessionWindow,
        is_exception_session: bool,
        zone: ZoneInfo,
    ) -> _CandidateSession:
        """Convert one local session window into UTC boundaries."""

        closing_date = origin_date + timedelta(
            days=window.close_day_offset
        )

        local_start = datetime.combine(
            origin_date,
            window.open_time,
        )
        local_end = datetime.combine(
            closing_date,
            window.close_time,
        )

        return _CandidateSession(
            origin_date=origin_date,
            is_exception_session=is_exception_session,
            start_utc=self._local_to_utc(local_start, zone),
            end_utc=self._local_to_utc(local_end, zone),
        )

    @staticmethod
    def _local_to_utc(
        local_value: datetime,
        zone: ZoneInfo,
    ) -> datetime:
        """Convert an unambiguous local datetime into UTC.

        Nonexistent local times occur during forward daylight-saving
        transitions. Ambiguous local times occur during backward
        transitions. Both are rejected rather than guessed.
        """

        if local_value.tzinfo is not None:
            raise ValueError("Expected a naive local datetime.")

        valid_utc_values: set[datetime] = set()

        for fold in (0, 1):
            aware_value = local_value.replace(
                tzinfo=zone,
                fold=fold,
            )

            utc_value = aware_value.astimezone(timezone.utc)

            round_trip = (
                utc_value.astimezone(zone)
                .replace(tzinfo=None)
            )

            if round_trip == local_value:
                valid_utc_values.add(utc_value)

        if not valid_utc_values:
            raise ValueError(
                f"Nonexistent local time {local_value.isoformat()} "
                f"in timezone {zone.key}."
            )

        if len(valid_utc_values) > 1:
            raise ValueError(
                f"Ambiguous local time {local_value.isoformat()} "
                f"in timezone {zone.key}."
            )

        return next(iter(valid_utc_values))

    @staticmethod
    def _validate_no_overlaps(
        trading_date: date,
        intervals: list[ResolvedSessionInterval],
    ) -> None:
        """Reject overlapping effective intervals on a local date."""

        previous: ResolvedSessionInterval | None = None

        for current in intervals:
            if (
                previous is not None
                and current.start_utc < previous.end_utc
            ):
                raise ValueError(
                    "Overlapping trading sessions detected for "
                    f"{trading_date.isoformat()}."
                )

            previous = current