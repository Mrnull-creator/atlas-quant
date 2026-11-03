
"""Tests for calendar schedule resolution."""

from datetime import date, datetime, time, timezone

import pytest

from atlas_quant.services.market_data.domain.calendar import (
    CalendarException,
    SessionWindow,
    TradingCalendar,
    WeeklySession,
)
from atlas_quant.services.market_data.application.calendar_schedule import (
    CalendarScheduleResolver,
)


def make_window(
    open_time: time,
    close_time: time,
    close_day_offset: int = 0,
) -> SessionWindow:
    return SessionWindow(
        open_time=open_time,
        close_time=close_time,
        close_day_offset=close_day_offset,
    )


def make_calendar(
    weekly_sessions=(),
    exceptions=(),
    timezone_name="America/New_York",
) -> TradingCalendar:
    return TradingCalendar(
        calendar_id="test-market",
        name="Test Market",
        timezone=timezone_name,
        weekly_sessions=weekly_sessions,
        exceptions=exceptions,
    )


def resolve(calendar, start_date, end_date):
    return CalendarScheduleResolver().resolve(
        calendar,
        start_date,
        end_date,
    )


def test_resolves_regular_session_to_utc():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(
                    make_window(time(9, 0), time(17, 0)),
                ),
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 5),
        date(2026, 1, 5),
    )

    assert len(result) == 1
    assert result[0].start_utc == datetime(
        2026, 1, 5, 14, 0, tzinfo=timezone.utc
    )
    assert result[0].end_utc == datetime(
        2026, 1, 5, 22, 0, tzinfo=timezone.utc
    )


def test_date_range_is_inclusive():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(make_window(time(9, 0), time(17, 0)),),
            ),
            WeeklySession(
                weekday=2,
                windows=(make_window(time(9, 0), time(17, 0)),),
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 5),
        date(2026, 1, 6),
    )

    assert len(result) == 2
    assert [item.trading_date for item in result] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]


def test_closed_weekday_produces_no_intervals():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(make_window(time(9, 0), time(17, 0)),),
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 6),
        date(2026, 1, 6),
    )

    assert result == ()


def test_empty_calendar_produces_no_intervals():
    calendar = make_calendar()

    result = resolve(
        calendar,
        date(2026, 1, 5),
        date(2026, 1, 7),
    )

    assert result == ()


def test_multiple_sessions_are_resolved():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(
                    make_window(time(9, 0), time(12, 0)),
                    make_window(time(13, 0), time(17, 0)),
                ),
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 5),
        date(2026, 1, 5),
    )

    assert len(result) == 2
    assert result[0].end_utc < result[1].start_utc


def test_overnight_session_is_split_at_local_midnight():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 4),
        date(2026, 1, 5),
    )

    assert len(result) == 2

    assert result[0].trading_date == date(2026, 1, 4)
    assert result[0].origin_date == date(2026, 1, 4)
    assert result[0].start_utc == datetime(
        2026, 1, 5, 3, 0, tzinfo=timezone.utc
    )
    assert result[0].end_utc == datetime(
        2026, 1, 5, 5, 0, tzinfo=timezone.utc
    )

    assert result[1].trading_date == date(2026, 1, 5)
    assert result[1].origin_date == date(2026, 1, 4)
    assert result[1].start_utc == datetime(
        2026, 1, 5, 5, 0, tzinfo=timezone.utc
    )
    assert result[1].end_utc == datetime(
        2026, 1, 5, 7, 0, tzinfo=timezone.utc
    )


def test_overnight_tail_is_suppressed_by_full_day_closure():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
            ),
        ),
        exceptions=(
            CalendarException(
                trading_date=date(2026, 1, 5),
                reason="Public holiday",
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 4),
        date(2026, 1, 5),
    )

    assert len(result) == 1
    assert result[0].trading_date == date(2026, 1, 4)
    assert result[0].end_utc == datetime(
        2026, 1, 5, 5, 0, tzinfo=timezone.utc
    )


def test_exception_replacement_session_overrides_weekly_schedule():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(make_window(time(9, 0), time(17, 0)),),
            ),
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
            ),
        ),
        exceptions=(
            CalendarException(
                trading_date=date(2026, 1, 5),
                sessions=(
                    make_window(time(10, 0), time(12, 0)),
                ),
                reason="Special trading hours",
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 4),
        date(2026, 1, 5),
    )

    monday_intervals = [
        item for item in result
        if item.trading_date == date(2026, 1, 5)
    ]

    assert len(monday_intervals) == 1
    assert monday_intervals[0].start_utc == datetime(
        2026, 1, 5, 15, 0, tzinfo=timezone.utc
    )
    assert monday_intervals[0].end_utc == datetime(
        2026, 1, 5, 17, 0, tzinfo=timezone.utc
    )


def test_exception_replacement_can_create_session_on_closed_weekday():
    calendar = make_calendar(
        exceptions=(
            CalendarException(
                trading_date=date(2026, 1, 5),
                sessions=(
                    make_window(time(10, 0), time(12, 0)),
                ),
                reason="Special opening",
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 5),
        date(2026, 1, 5),
    )

    assert len(result) == 1
    assert result[0].trading_date == date(2026, 1, 5)


def test_previous_date_exception_replacement_can_extend_overnight():
    calendar = make_calendar(
        exceptions=(
            CalendarException(
                trading_date=date(2026, 1, 4),
                sessions=(
                    make_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
                reason="Special overnight session",
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 1, 5),
        date(2026, 1, 5),
    )

    assert len(result) == 1
    assert result[0].origin_date == date(2026, 1, 4)
    assert result[0].start_utc == datetime(
        2026, 1, 5, 5, 0, tzinfo=timezone.utc
    )
    assert result[0].end_utc == datetime(
        2026, 1, 5, 7, 0, tzinfo=timezone.utc
    )


def test_overlapping_sessions_are_rejected():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(
                    make_window(time(9, 0), time(12, 0)),
                    make_window(time(11, 0), time(14, 0)),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="Overlapping"):
        resolve(
            calendar,
            date(2026, 1, 5),
            date(2026, 1, 5),
        )


def test_overlapping_overnight_and_next_day_sessions_are_rejected():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
            ),
            WeeklySession(
                weekday=1,
                windows=(
                    make_window(time(1, 0), time(4, 0)),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="Overlapping"):
        resolve(
            calendar,
            date(2026, 1, 4),
            date(2026, 1, 5),
        )


def test_nonexistent_daylight_saving_time_is_rejected():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(time(2, 30), time(4, 0)),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="Nonexistent local time"):
        resolve(
            calendar,
            date(2026, 3, 8),
            date(2026, 3, 8),
        )


def test_ambiguous_daylight_saving_time_is_rejected():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(time(1, 30), time(3, 0)),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="Ambiguous local time"):
        resolve(
            calendar,
            date(2026, 11, 1),
            date(2026, 11, 1),
        )


def test_session_across_spring_daylight_saving_transition():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_window(time(1, 0), time(4, 0)),
                ),
            ),
        ),
    )

    result = resolve(
        calendar,
        date(2026, 3, 8),
        date(2026, 3, 8),
    )

    assert len(result) == 1
    assert result[0].start_utc == datetime(
        2026, 3, 8, 6, 0, tzinfo=timezone.utc
    )
    assert result[0].end_utc == datetime(
        2026, 3, 8, 8, 0, tzinfo=timezone.utc
    )


def test_resolver_rejects_datetime_as_date_argument():
    calendar = make_calendar()

    with pytest.raises(TypeError):
        resolve(
            calendar,
            datetime(2026, 1, 5),
            date(2026, 1, 5),
        )


def test_resolver_rejects_reversed_date_range():
    calendar = make_calendar()

    with pytest.raises(ValueError, match="earlier"):
        resolve(
            calendar,
            date(2026, 1, 6),
            date(2026, 1, 5),
        )


def test_resolver_rejects_invalid_calendar_argument():
    with pytest.raises(TypeError):
        resolve(
            "not a calendar",
            date(2026, 1, 5),
            date(2026, 1, 5),
        )