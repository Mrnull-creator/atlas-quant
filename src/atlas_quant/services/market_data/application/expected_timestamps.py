
"""Generate expected OHLCV bar-opening timestamps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from atlas_quant.services.market_data.domain.bar_boundaries import (
    BarAlignmentMode,
    BarBoundaryPolicy,
)
from atlas_quant.services.market_data.domain.calendar import TradingCalendar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilitySchedule,
)
from atlas_quant.services.market_data.application.calendar_schedule import (
    CalendarScheduleResolver,
    ResolvedSessionInterval,
)
from atlas_quant.services.market_data.application.provider_availability_schedule import (
    ProviderAvailabilityScheduleResolver,
    ResolvedProviderAvailabilityInterval,
)


UTC = timezone.utc

_INTRADAY_DURATIONS = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
}


@dataclass(frozen=True, slots=True)
class ExpectedTimestampResult:
    """Expected bar openings and the coverage intervals used to derive them."""

    instrument_symbol: str
    timeframe: Timeframe
    expected_timestamps: tuple[datetime, ...]
    market_intervals: tuple[ResolvedSessionInterval, ...]
    provider_intervals: tuple[ResolvedProviderAvailabilityInterval, ...]

    def __post_init__(self) -> None:
        normalized: list[datetime] = []

        for value in self.expected_timestamps:
            if not isinstance(value, datetime):
                raise TypeError("Expected timestamps must be datetime values.")

            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Expected timestamps must be timezone-aware.")

            normalized.append(value.astimezone(UTC))

        object.__setattr__(
            self,
            "expected_timestamps",
            tuple(sorted(set(normalized))),
        )


class ExpectedTimestampGenerator:
    """Coordinate market sessions, provider availability and bar boundaries."""

    def __init__(
        self,
        calendar_resolver: CalendarScheduleResolver | None = None,
        provider_resolver: ProviderAvailabilityScheduleResolver | None = None,
    ) -> None:
        self._calendar_resolver = (
            calendar_resolver or CalendarScheduleResolver()
        )
        self._provider_resolver = (
            provider_resolver or ProviderAvailabilityScheduleResolver()
        )

    def generate(
        self,
        instrument: Instrument,
        calendar: TradingCalendar,
        provider_schedule: ProviderAvailabilitySchedule,
        policy: BarBoundaryPolicy,
        start: datetime,
        end: datetime,
    ) -> ExpectedTimestampResult:
        """Generate expected UTC bar-opening timestamps in [start, end)."""

        start_utc = self._normalize_datetime(start, "start")
        end_utc = self._normalize_datetime(end, "end")

        if end_utc <= start_utc:
            raise ValueError("end must be later than start.")

        self._validate_inputs(instrument, provider_schedule, policy)

        if policy.alignment_mode is BarAlignmentMode.CALENDAR_PERIOD:
            coverage_start, coverage_end = (
                self._calendar_period_coverage_range(
                    policy,
                    start_utc,
                    end_utc,
                )
            )
        else:
            coverage_start, coverage_end = start_utc, end_utc

        market_intervals = self._resolve_market_intervals(
            calendar,
            coverage_start,
            coverage_end,
        )
        provider_intervals = self._resolve_provider_intervals(
            provider_schedule,
            coverage_start,
            coverage_end,
        )

        if policy.alignment_mode is BarAlignmentMode.UTC_CLOCK:
            expected = self._generate_utc_clock(
                market_intervals,
                provider_intervals,
                policy,
                start_utc,
                end_utc,
            )
        elif policy.alignment_mode is BarAlignmentMode.SESSION:
            expected = self._generate_session_aligned(
                market_intervals,
                provider_intervals,
                policy,
                start_utc,
                end_utc,
            )
        else:
            expected = self._generate_calendar_periods(
                market_intervals,
                provider_intervals,
                policy,
                start_utc,
                end_utc,
            )

        return ExpectedTimestampResult(
            instrument_symbol=instrument.symbol,
            timeframe=policy.timeframe,
            expected_timestamps=tuple(expected),
            market_intervals=market_intervals,
            provider_intervals=provider_intervals,
        )

    @staticmethod
    def _normalize_datetime(value: datetime, name: str) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{name} must be a datetime.")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware.")

        return value.astimezone(UTC)

    @staticmethod
    def _validate_inputs(
        instrument: Instrument,
        provider_schedule: ProviderAvailabilitySchedule,
        policy: BarBoundaryPolicy,
    ) -> None:
        if instrument.asset_class != provider_schedule.asset_class:
            raise ValueError(
                "Instrument asset class does not match provider schedule."
            )

        if provider_schedule.timeframe != policy.timeframe:
            raise ValueError(
                "Provider schedule timeframe does not match bar policy."
            )

        if (
            provider_schedule.instrument_symbol is not None
            and provider_schedule.instrument_symbol != instrument.symbol
        ):
            raise ValueError(
                "Provider schedule instrument symbol does not match instrument."
            )

    def _resolve_market_intervals(
        self,
        calendar: TradingCalendar,
        start: datetime,
        end: datetime,
    ) -> tuple[ResolvedSessionInterval, ...]:
        zone = ZoneInfo(calendar.timezone)
        first_date = start.astimezone(zone).date() - timedelta(days=1)
        last_date = end.astimezone(zone).date()

        return self._calendar_resolver.resolve(
            calendar,
            first_date,
            last_date,
        )

    def _resolve_provider_intervals(
        self,
        schedule: ProviderAvailabilitySchedule,
        start: datetime,
        end: datetime,
    ) -> tuple[ResolvedProviderAvailabilityInterval, ...]:
        zone = ZoneInfo(schedule.timezone)
        first_date = start.astimezone(zone).date() - timedelta(days=1)
        last_date = end.astimezone(zone).date()

        return self._provider_resolver.resolve(
            schedule,
            first_date,
            last_date,
        )

    def _calendar_period_coverage_range(
        self,
        policy: BarBoundaryPolicy,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime]:
        """Return complete period boundaries needed for requested openings."""

        zone = ZoneInfo(policy.boundary_timezone)

        boundaries = self._calendar_period_boundaries(
            policy,
            start.astimezone(zone).date(),
            end.astimezone(zone).date(),
            zone,
        )

        relevant_periods = [
            (period_start, period_end)
            for period_start, period_end in zip(
                boundaries,
                boundaries[1:],
            )
            if start <= period_start < end
        ]

        if not relevant_periods:
            return start, end

        return relevant_periods[0][0], relevant_periods[-1][1]

    def _resolve_calendar_period_boundaries(
        self,
        policy: BarBoundaryPolicy,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, ...]:
        """Build period boundaries around the requested local date range."""

        zone = ZoneInfo(policy.boundary_timezone)

        return self._calendar_period_boundaries(
            policy,
            start.astimezone(zone).date(),
            end.astimezone(zone).date(),
            zone,
        )

    def _calendar_period_boundaries(
        self,
        policy: BarBoundaryPolicy,
        first_date: date,
        last_date: date,
        zone: ZoneInfo,
    ) -> tuple[datetime, ...]:
        start_date = first_date - timedelta(days=8)
        end_date = last_date + timedelta(days=40)
        dates: list[date] = []

        if policy.timeframe is Timeframe.D1:
            current = start_date
            while current <= end_date:
                dates.append(current)
                current += timedelta(days=1)

        elif policy.timeframe is Timeframe.W1:
            current = start_date - timedelta(
                days=(start_date.isoweekday() - policy.week_start_day) % 7
            )
            while current <= end_date:
                dates.append(current)
                current += timedelta(days=7)

        elif policy.timeframe is Timeframe.MN1:
            current = date(start_date.year, start_date.month, 1)

            while current <= end_date:
                dates.append(current)

                if current.month == 12:
                    current = date(current.year + 1, 1, 1)
                else:
                    current = date(current.year, current.month + 1, 1)

        else:
            raise ValueError(
                "Calendar-period alignment requires D1, W1 or MN1."
            )

        boundaries = [
            self._local_boundary(
                datetime.combine(day, policy.period_start_time),
                zone,
            )
            for day in dates
        ]

        return tuple(sorted(set(boundaries)))

    @staticmethod
    def _merge_market_sessions(
        intervals: tuple[ResolvedSessionInterval, ...],
    ) -> tuple[tuple[datetime, datetime, date], ...]:
        """Rejoin midnight-split segments from the same original session."""

        ordered = sorted(
            intervals,
            key=lambda item: (item.origin_date, item.start_utc),
        )
        merged: list[tuple[datetime, datetime, date]] = []

        for interval in ordered:
            if (
                merged
                and merged[-1][2] == interval.origin_date
                and merged[-1][1] == interval.start_utc
            ):
                previous = merged[-1]
                merged[-1] = (
                    previous[0],
                    interval.end_utc,
                    previous[2],
                )
            else:
                merged.append(
                    (
                        interval.start_utc,
                        interval.end_utc,
                        interval.origin_date,
                    )
                )

        return tuple(merged)

    @staticmethod
    def _merge_provider_intervals(
        intervals: tuple[ResolvedProviderAvailabilityInterval, ...],
    ) -> tuple[tuple[datetime, datetime], ...]:
        ordered = sorted(intervals, key=lambda item: item.start_utc)
        merged: list[tuple[datetime, datetime]] = []

        for interval in ordered:
            if merged and merged[-1][1] == interval.start_utc:
                merged[-1] = (merged[-1][0], interval.end_utc)
            else:
                merged.append((interval.start_utc, interval.end_utc))

        return tuple(merged)

    @staticmethod
    def _covered(
        start: datetime,
        end: datetime,
        provider_intervals: tuple[tuple[datetime, datetime], ...],
    ) -> bool:
        return any(
            available_start <= start and available_end >= end
            for available_start, available_end in provider_intervals
        )

    @staticmethod
    def _intraday_duration(policy: BarBoundaryPolicy) -> timedelta:
        try:
            return _INTRADAY_DURATIONS[policy.timeframe]
        except KeyError as exc:
            raise ValueError(
                f"{policy.timeframe} is not an intraday timeframe."
            ) from exc

    def _generate_utc_clock(
        self,
        market_intervals: tuple[ResolvedSessionInterval, ...],
        provider_intervals: tuple[ResolvedProviderAvailabilityInterval, ...],
        policy: BarBoundaryPolicy,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, ...]:
        duration = self._intraday_duration(policy)
        providers = self._merge_provider_intervals(provider_intervals)
        expected: list[datetime] = []

        for market_start, market_end, _ in self._merge_market_sessions(
            market_intervals
        ):
            candidate = self._ceil_to_boundary(market_start, duration)

            while candidate + duration <= market_end:
                candle_end = candidate + duration

                if (
                    start <= candidate < end
                    and self._covered(candidate, candle_end, providers)
                ):
                    expected.append(candidate)

                candidate += duration

        return tuple(sorted(set(expected)))

    def _generate_session_aligned(
        self,
        market_intervals: tuple[ResolvedSessionInterval, ...],
        provider_intervals: tuple[ResolvedProviderAvailabilityInterval, ...],
        policy: BarBoundaryPolicy,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, ...]:
        duration = self._intraday_duration(policy)
        providers = self._merge_provider_intervals(provider_intervals)
        expected: list[datetime] = []

        for market_start, market_end, _ in self._merge_market_sessions(
            market_intervals
        ):
            candidate = market_start

            while candidate < market_end:
                candle_end = candidate + duration
                final_end = min(candle_end, market_end)
                is_partial = final_end < candle_end

                if is_partial and not policy.allow_partial_final_bar:
                    break

                if (
                    start <= candidate < end
                    and self._covered(candidate, final_end, providers)
                ):
                    expected.append(candidate)

                candidate += duration

        return tuple(sorted(set(expected)))

    def _generate_calendar_periods(
        self,
        market_intervals: tuple[ResolvedSessionInterval, ...],
        provider_intervals: tuple[ResolvedProviderAvailabilityInterval, ...],
        policy: BarBoundaryPolicy,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, ...]:
        boundaries = self._resolve_calendar_period_boundaries(
            policy,
            start,
            end,
        )
        providers = self._merge_provider_intervals(provider_intervals)
        market = self._merge_market_sessions(market_intervals)

        expected: list[datetime] = []

        for period_start, period_end in zip(boundaries, boundaries[1:]):
            if not (start <= period_start < end):
                continue

            relevant_market = [
                (market_start, market_end)
                for market_start, market_end, _ in market
                if market_start < period_end and market_end > period_start
            ]

            if not relevant_market:
                continue

            all_covered = True

            for market_start, market_end in relevant_market:
                clipped_start = max(market_start, period_start)
                clipped_end = min(market_end, period_end)

                if not self._covered(
                    clipped_start,
                    clipped_end,
                    providers,
                ):
                    all_covered = False
                    break

            if all_covered:
                expected.append(period_start)

        return tuple(expected)

    @staticmethod
    def _local_boundary(
        local_value: datetime,
        zone: ZoneInfo,
    ) -> datetime:
        candidates: set[datetime] = set()

        for fold in (0, 1):
            aware = local_value.replace(tzinfo=zone, fold=fold)
            utc_value = aware.astimezone(UTC)

            if (
                utc_value.astimezone(zone).replace(tzinfo=None)
                == local_value
            ):
                candidates.add(utc_value)

        if len(candidates) != 1:
            raise ValueError(
                f"Calendar period boundary is ambiguous or nonexistent: "
                f"{local_value} in {zone.key}."
            )

        return next(iter(candidates))

    @staticmethod
    def _ceil_to_boundary(
        value: datetime,
        duration: timedelta,
    ) -> datetime:
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        elapsed = value - epoch
        units = elapsed // duration

        if epoch + units * duration < value:
            units += 1

        return epoch + units * duration