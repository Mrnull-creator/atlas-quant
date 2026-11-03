from datetime import date, datetime, time, timedelta, timezone

import pytest

from atlas_quant.services.market_data.application.provider_availability_schedule import (
    ProviderAvailabilityScheduleResolver,
    ResolvedProviderAvailabilityInterval,
)
from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilityException,
    ProviderAvailabilitySchedule,
    ProviderAvailabilityWindow,
    WeeklyProviderAvailability,
)
from atlas_quant.services.market_data.domain.enums import AssetClass, Timeframe


UTC = timezone.utc


def make_window(
    open_time: time,
    close_time: time,
    close_day_offset: int = 0,
) -> ProviderAvailabilityWindow:
    return ProviderAvailabilityWindow(
        open_time=open_time,
        close_time=close_time,
        close_day_offset=close_day_offset,
    )


def make_weekly(
    weekday: int,
    *windows: ProviderAvailabilityWindow,
) -> WeeklyProviderAvailability:
    return WeeklyProviderAvailability(
        weekday=weekday,
        windows=windows,
    )


def make_exception(
    availability_date: date,
    *windows: ProviderAvailabilityWindow,
    reason: str = "Test exception",
) -> ProviderAvailabilityException:
    return ProviderAvailabilityException(
        availability_date=availability_date,
        windows=windows,
        reason=reason,
    )


def make_schedule(
    weekly_availability=(),
    exceptions=(),
    timezone_name: str = "America/New_York",
) -> ProviderAvailabilitySchedule:
    return ProviderAvailabilitySchedule(
        provider_id="test-provider",
        asset_class=AssetClass.FOREX,
        timeframe=Timeframe.M1,
        timezone=timezone_name,
        weekly_availability=weekly_availability,
        exceptions=exceptions,
    )


def resolve(
    schedule: ProviderAvailabilitySchedule,
    start_date: date,
    end_date: date,
) -> tuple[ResolvedProviderAvailabilityInterval, ...]:
    return ProviderAvailabilityScheduleResolver().resolve(
        schedule,
        start_date,
        end_date,
    )


def utc_datetime(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_resolves_regular_weekday_availability_to_utc():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                1,
                make_window(time(9), time(17)),
            ),
        )
    )

    result = resolve(schedule, date(2026, 1, 5), date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].availability_date == date(2026, 1, 5)
    assert result[0].origin_date == date(2026, 1, 5)
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 14)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 22)


def test_date_range_is_inclusive():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(1, make_window(time(9), time(10))),
            make_weekly(2, make_window(time(9), time(10))),
            make_weekly(3, make_window(time(9), time(10))),
        )
    )

    result = resolve(schedule, date(2026, 1, 5), date(2026, 1, 6))

    assert [item.availability_date for item in result] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]


def test_returns_empty_when_no_weekly_availability_exists():
    schedule = make_schedule()

    assert resolve(schedule, date(2026, 1, 5), date(2026, 1, 7)) == ()


def test_resolves_multiple_windows_on_same_day():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                1,
                make_window(time(9), time(12)),
                make_window(time(13), time(17)),
            ),
        )
    )

    result = resolve(schedule, date(2026, 1, 5), date(2026, 1, 5))

    assert len(result) == 2
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 14)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 17)
    assert result[1].start_utc == utc_datetime(2026, 1, 5, 18)
    assert result[1].end_utc == utc_datetime(2026, 1, 5, 22)


def test_splits_overnight_availability_at_local_midnight():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(22), time(2), close_day_offset=1),
            ),
        )
    )

    result = resolve(schedule, date(2026, 1, 4), date(2026, 1, 5))

    assert len(result) == 2

    assert result[0].availability_date == date(2026, 1, 4)
    assert result[0].origin_date == date(2026, 1, 4)
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 3)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 5)

    assert result[1].availability_date == date(2026, 1, 5)
    assert result[1].origin_date == date(2026, 1, 4)
    assert result[1].start_utc == utc_datetime(2026, 1, 5, 5)
    assert result[1].end_utc == utc_datetime(2026, 1, 5, 7)


def test_full_day_exception_suppresses_previous_overnight_tail():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(22), time(2), close_day_offset=1),
            ),
        ),
        exceptions=(
            make_exception(date(2026, 1, 5), reason="Provider unavailable"),
        ),
    )

    result = resolve(schedule, date(2026, 1, 4), date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].availability_date == date(2026, 1, 4)
    assert result[0].origin_date == date(2026, 1, 4)
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 3)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 5)


def test_replacement_exception_suppresses_previous_overnight_tail():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(22), time(2), close_day_offset=1),
            ),
            make_weekly(1, make_window(time(9), time(17))),
        ),
        exceptions=(
            make_exception(
                date(2026, 1, 5),
                make_window(time(10), time(12)),
                reason="Reduced provider availability",
            ),
        ),
    )

    result = resolve(schedule, date(2026, 1, 4), date(2026, 1, 5))

    assert len(result) == 2

    assert result[0].availability_date == date(2026, 1, 4)
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 3)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 5)

    assert result[1].availability_date == date(2026, 1, 5)
    assert result[1].origin_date == date(2026, 1, 5)
    assert result[1].start_utc == utc_datetime(2026, 1, 5, 15)
    assert result[1].end_utc == utc_datetime(2026, 1, 5, 17)


def test_exception_can_open_availability_on_unscheduled_weekday():
    schedule = make_schedule(
        exceptions=(
            make_exception(
                date(2026, 1, 5),
                make_window(time(10), time(12)),
            ),
        )
    )

    result = resolve(schedule, date(2026, 1, 5), date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 15)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 17)


def test_previous_date_exception_can_create_overnight_availability():
    schedule = make_schedule(
        exceptions=(
            make_exception(
                date(2026, 1, 4),
                make_window(time(22), time(2), close_day_offset=1),
            ),
        )
    )

    result = resolve(schedule, date(2026, 1, 5), date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].availability_date == date(2026, 1, 5)
    assert result[0].origin_date == date(2026, 1, 4)
    assert result[0].start_utc == utc_datetime(2026, 1, 5, 5)
    assert result[0].end_utc == utc_datetime(2026, 1, 5, 7)


def test_detects_overlapping_windows_on_same_day():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                1,
                make_window(time(9), time(13)),
                make_window(time(12), time(17)),
            ),
        )
    )

    with pytest.raises(ValueError, match="Overlapping provider availability"):
        resolve(schedule, date(2026, 1, 5), date(2026, 1, 5))


def test_detects_overlapping_overnight_and_next_day_windows():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(22), time(2), close_day_offset=1),
            ),
            make_weekly(
                1,
                make_window(time(1), time(3)),
            ),
        )
    )

    with pytest.raises(ValueError, match="Overlapping provider availability"):
        resolve(schedule, date(2026, 1, 4), date(2026, 1, 5))


def test_rejects_nonexistent_local_availability_boundary():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(2, 30), time(4)),
            ),
        )
    )

    with pytest.raises(ValueError, match="Nonexistent local time"):
        resolve(schedule, date(2026, 3, 8), date(2026, 3, 8))


def test_rejects_ambiguous_local_availability_boundary():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(1, 30), time(2, 30)),
            ),
        )
    )

    with pytest.raises(ValueError, match="Ambiguous local time"):
        resolve(schedule, date(2026, 11, 1), date(2026, 11, 1))


def test_availability_interval_crossing_spring_dst_transition_uses_utc_boundaries():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(
                7,
                make_window(time(1), time(4)),
            ),
        )
    )

    result = resolve(schedule, date(2026, 3, 8), date(2026, 3, 8))

    assert len(result) == 1
    assert result[0].start_utc == utc_datetime(2026, 3, 8, 6)
    assert result[0].end_utc == utc_datetime(2026, 3, 8, 8)
    assert result[0].end_utc - result[0].start_utc == timedelta(hours=2)


def test_rejects_datetime_as_start_date():
    schedule = make_schedule()

    with pytest.raises(TypeError, match="date, not a datetime"):
        resolve(
            schedule,
            datetime(2026, 1, 5, tzinfo=UTC),
            date(2026, 1, 5),
        )


def test_rejects_datetime_as_end_date():
    schedule = make_schedule()

    with pytest.raises(TypeError, match="date, not a datetime"):
        resolve(
            schedule,
            date(2026, 1, 5),
            datetime(2026, 1, 5, tzinfo=UTC),
        )


def test_rejects_reversed_date_range():
    schedule = make_schedule()

    with pytest.raises(ValueError, match="start_date"):
        resolve(schedule, date(2026, 1, 6), date(2026, 1, 5))


def test_rejects_invalid_schedule_argument():
    with pytest.raises(TypeError, match="ProviderAvailabilitySchedule"):
        resolve(None, date(2026, 1, 5), date(2026, 1, 5))


def test_resolved_interval_normalizes_boundaries_to_utc():
    from zoneinfo import ZoneInfo

    start = datetime(
        2026, 1, 5, 9, tzinfo=ZoneInfo("America/New_York")
    )
    end = datetime(
        2026, 1, 5, 10, tzinfo=ZoneInfo("America/New_York")
    )

    interval = ResolvedProviderAvailabilityInterval(
        availability_date=date(2026, 1, 5),
        origin_date=date(2026, 1, 5),
        start_utc=start,
        end_utc=end,
    )

    assert interval.start_utc == utc_datetime(2026, 1, 5, 14)
    assert interval.end_utc == utc_datetime(2026, 1, 5, 15)
    assert interval.start_utc.tzinfo == UTC


def test_resolved_interval_rejects_naive_boundaries():
    with pytest.raises(ValueError, match="timezone-aware"):
        ResolvedProviderAvailabilityInterval(
            availability_date=date(2026, 1, 5),
            origin_date=date(2026, 1, 5),
            start_utc=datetime(2026, 1, 5, 14),
            end_utc=datetime(2026, 1, 5, 15),
        )


def test_resolved_interval_rejects_non_positive_duration():
    instant = utc_datetime(2026, 1, 5, 14)

    with pytest.raises(ValueError, match="positive duration"):
        ResolvedProviderAvailabilityInterval(
            availability_date=date(2026, 1, 5),
            origin_date=date(2026, 1, 5),
            start_utc=instant,
            end_utc=instant,
        )