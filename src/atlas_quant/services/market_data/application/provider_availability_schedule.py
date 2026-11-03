
"""Resolve provider availability schedules into effective UTC intervals."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilitySchedule,
    ProviderAvailabilityWindow,
)


@dataclass(frozen=True, slots=True)
class ResolvedProviderAvailabilityInterval:
    """One effective provider availability interval represented in UTC."""

    availability_date: date
    origin_date: date
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self) -> None:
        """Validate interval boundaries."""

        for field_name in ("availability_date", "origin_date"):
            value = getattr(self, field_name)

            if not isinstance(value, date) or isinstance(value, datetime):
                raise TypeError(
                    f"{field_name} must be a date, not a datetime."
                )

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
            raise ValueError(
                "Provider availability interval must have positive duration."
            )


@dataclass(frozen=True, slots=True)
class _CandidateAvailability:
    """Internal availability interval before local-date filtering."""

    origin_date: date
    is_exception_window: bool
    start_utc: datetime
    end_utc: datetime


class ProviderAvailabilityScheduleResolver:
    """Resolve effective provider availability for a local date range.

    Date ranges are inclusive and use the schedule's local timezone.

    Provider availability exceptions have date-wide precedence. An
    unavailable date suppresses all availability on that local date,
    including overnight windows originating on the previous date.
    Replacement windows are the only windows permitted to originate
    on an exception date.
    """

    def resolve(
        self,
        schedule: ProviderAvailabilitySchedule,
        start_date: date,
        end_date: date,
    ) -> tuple[ResolvedProviderAvailabilityInterval, ...]:
        """Resolve a schedule into non-overlapping UTC intervals."""

        if not isinstance(schedule, ProviderAvailabilitySchedule):
            raise TypeError(
                "schedule must be a ProviderAvailabilitySchedule."
            )

        self._validate_date("start_date", start_date)
        self._validate_date("end_date", end_date)

        if end_date < start_date:
            raise ValueError("end_date cannot be earlier than start_date.")

        zone = ZoneInfo(schedule.timezone)

        weekly_availability = {
            item.weekday: item
            for item in schedule.weekly_availability
        }

        exceptions = {
            item.availability_date: item
            for item in schedule.exceptions
        }

        candidates = self._build_candidates(
            zone=zone,
            weekly_availability=weekly_availability,
            exceptions=exceptions,
            start_date=start_date,
            end_date=end_date,
        )

        resolved: list[ResolvedProviderAvailabilityInterval] = []

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

            daily_intervals: list[
                ResolvedProviderAvailabilityInterval
            ] = []

            for candidate in candidates:
                if exception_applies and not (
                    candidate.origin_date == current_date
                    and candidate.is_exception_window
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
                    ResolvedProviderAvailabilityInterval(
                        availability_date=current_date,
                        origin_date=candidate.origin_date,
                        start_utc=interval_start,
                        end_utc=interval_end,
                    )
                )

            daily_intervals.sort(
                key=lambda item: (
                    item.start_utc,
                    item.end_utc,
                    item.origin_date,
                )
            )

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
            raise TypeError(
                f"{field_name} must be a date, not a datetime."
            )

    def _build_candidates(
        self,
        *,
        zone: ZoneInfo,
        weekly_availability: dict,
        exceptions: dict,
        start_date: date,
        end_date: date,
    ) -> tuple[_CandidateAvailability, ...]:
        """Build windows originating within the relevant date range."""

        candidates: list[_CandidateAvailability] = []

        # Include the previous local date for overnight windows
        # extending into the requested start date.
        origin_date = start_date - timedelta(days=1)

        while origin_date <= end_date:
            exception = exceptions.get(origin_date)

            if exception is not None:
                windows = exception.windows
                is_exception_window = True
            else:
                weekly = weekly_availability.get(
                    origin_date.isoweekday()
                )
                windows = weekly.windows if weekly is not None else ()
                is_exception_window = False

            for window in windows:
                candidates.append(
                    self._build_candidate(
                        origin_date=origin_date,
                        window=window,
                        is_exception_window=is_exception_window,
                        zone=zone,
                    )
                )

            origin_date += timedelta(days=1)

        return tuple(candidates)

    def _build_candidate(
        self,
        *,
        origin_date: date,
        window: ProviderAvailabilityWindow,
        is_exception_window: bool,
        zone: ZoneInfo,
    ) -> _CandidateAvailability:
        """Convert one local availability window into UTC boundaries."""

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

        return _CandidateAvailability(
            origin_date=origin_date,
            is_exception_window=is_exception_window,
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
        availability_date: date,
        intervals: list[ResolvedProviderAvailabilityInterval],
    ) -> None:
        """Reject overlapping effective intervals on a local date."""

        previous: ResolvedProviderAvailabilityInterval | None = None

        for current in intervals:
            if (
                previous is not None
                and current.start_utc < previous.end_utc
            ):
                raise ValueError(
                    "Overlapping provider availability windows detected "
                    f"for {availability_date.isoformat()}."
                )

            previous = current