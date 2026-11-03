
"""Tests for trading calendar domain models."""

from datetime import date, time

import pytest

from atlas_quant.services.market_data.domain.calendar import (
    CalendarException,
    SessionWindow,
    TradingCalendar,
    WeeklySession,
)


def make_window(
    open_time: time = time(9, 0),
    close_time: time = time(17, 0),
    close_day_offset: int = 0,
) -> SessionWindow:
    return SessionWindow(
        open_time=open_time,
        close_time=close_time,
        close_day_offset=close_day_offset,
    )


def make_calendar(**overrides) -> TradingCalendar:
    values = {
        "calendar_id": "test-market",
        "name": "Test Market",
        "timezone": "America/New_York",
        "weekly_sessions": (
            WeeklySession(
                weekday=1,
                windows=(make_window(),),
            ),
        ),
    }
    values.update(overrides)
    return TradingCalendar(**values)


def test_session_window_accepts_same_day_session():
    window = make_window()

    assert window.open_time == time(9, 0)
    assert window.close_time == time(17, 0)
    assert window.close_day_offset == 0


def test_session_window_accepts_overnight_session():
    window = make_window(
        time(22, 0),
        time(2, 0),
        close_day_offset=1,
    )

    assert window.close_day_offset == 1


def test_session_window_accepts_full_day_session():
    window = make_window(
        time(0, 0),
        time(0, 0),
        close_day_offset=1,
    )

    assert window.close_day_offset == 1


@pytest.mark.parametrize(
    ("open_time", "close_time", "offset"),
    [
        (time(9, 0), time(9, 0), 0),
        (time(17, 0), time(9, 0), 0),
        (time(9, 0), time(17, 0), 1),
        (time(22, 0), time(23, 0), 1),
    ],
)
def test_session_window_rejects_invalid_boundaries(
    open_time,
    close_time,
    offset,
):
    with pytest.raises(ValueError):
        make_window(open_time, close_time, offset)


def test_session_window_rejects_invalid_offset():
    with pytest.raises(ValueError):
        make_window(
            time(22, 0),
            time(2, 0),
            close_day_offset=2,
        )


def test_session_window_rejects_boolean_offset():
    with pytest.raises(TypeError):
        make_window(
            time(22, 0),
            time(2, 0),
            close_day_offset=True,
        )


def test_session_window_rejects_timezone_aware_local_time():
    from datetime import timezone

    with pytest.raises(ValueError):
        make_window(
            time(9, 0, tzinfo=timezone.utc),
            time(17, 0),
        )


def test_weekly_session_accepts_multiple_windows():
    session = WeeklySession(
        weekday=1,
        windows=(
            make_window(time(13, 0), time(17, 0)),
            make_window(time(9, 0), time(12, 0)),
        ),
    )

    assert len(session.windows) == 2
    assert session.windows[0].open_time == time(9, 0)
    assert session.windows[1].open_time == time(13, 0)


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_weekly_session_rejects_invalid_weekday(weekday):
    with pytest.raises(ValueError):
        WeeklySession(weekday=weekday, windows=(make_window(),))


def test_weekly_session_rejects_boolean_weekday():
    with pytest.raises(TypeError):
        WeeklySession(weekday=True, windows=(make_window(),))


def test_weekly_session_rejects_empty_windows():
    with pytest.raises(ValueError):
        WeeklySession(weekday=1, windows=())


def test_weekly_session_rejects_duplicate_windows():
    window = make_window()

    with pytest.raises(ValueError):
        WeeklySession(
            weekday=1,
            windows=(window, window),
        )


def test_calendar_exception_can_represent_full_closure():
    exception = CalendarException(
        trading_date=date(2026, 12, 25),
        reason="Public holiday",
        source="Official exchange calendar",
    )

    assert exception.sessions == ()
    assert exception.reason == "Public holiday"


def test_calendar_exception_can_override_session_hours():
    exception = CalendarException(
        trading_date=date(2026, 12, 24),
        sessions=(make_window(time(9, 0), time(13, 0)),),
        reason="Early close",
    )

    assert len(exception.sessions) == 1
    assert exception.sessions[0].close_time == time(13, 0)


def test_calendar_exception_rejects_empty_reason():
    with pytest.raises(ValueError):
        CalendarException(
            trading_date=date(2026, 12, 25),
            reason=" ",
        )


def test_calendar_exception_rejects_datetime_value():
    from datetime import datetime

    with pytest.raises(TypeError):
        CalendarException(
            trading_date=datetime(2026, 12, 25),
            reason="Invalid date",
        )


def test_calendar_normalizes_identity_and_sorts_sessions():
    calendar = make_calendar(
        calendar_id="  TEST-MARKET ",
        name=" Test Market ",
        weekly_sessions=(
            WeeklySession(weekday=5, windows=(make_window(),)),
            WeeklySession(weekday=1, windows=(make_window(),)),
        ),
    )

    assert calendar.calendar_id == "test-market"
    assert calendar.name == "Test Market"
    assert [item.weekday for item in calendar.weekly_sessions] == [1, 5]


def test_calendar_accepts_valid_iana_timezone():
    calendar = make_calendar(timezone="Europe/London")

    assert calendar.timezone == "Europe/London"


def test_calendar_rejects_unknown_timezone():
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        make_calendar(timezone="Not/A_Timezone")


def test_calendar_rejects_empty_identity():
    with pytest.raises(ValueError):
        make_calendar(calendar_id=" ")


def test_calendar_rejects_duplicate_weekdays():
    with pytest.raises(ValueError, match="duplicate weekdays"):
        make_calendar(
            weekly_sessions=(
                WeeklySession(weekday=1, windows=(make_window(),)),
                WeeklySession(weekday=1, windows=(make_window(time(18, 0), time(20, 0)),)),
            )
        )


def test_calendar_sorts_exceptions_by_date():
    calendar = make_calendar(
        exceptions=(
            CalendarException(
                trading_date=date(2026, 12, 31),
                reason="Year end",
            ),
            CalendarException(
                trading_date=date(2026, 12, 25),
                reason="Holiday",
            ),
        ),
    )

    assert [
        item.trading_date for item in calendar.exceptions
    ] == [
        date(2026, 12, 25),
        date(2026, 12, 31),
    ]


def test_calendar_rejects_duplicate_exception_dates():
    exception = CalendarException(
        trading_date=date(2026, 12, 25),
        reason="Holiday",
    )

    with pytest.raises(ValueError, match="duplicate dates"):
        make_calendar(exceptions=(exception, exception))


def test_calendar_rejects_invalid_weekly_session_type():
    with pytest.raises(TypeError):
        make_calendar(weekly_sessions=("Monday",))


def test_calendar_rejects_invalid_exception_type():
    with pytest.raises(TypeError):
        make_calendar(exceptions=("Christmas",))


def test_calendar_is_immutable():
    from dataclasses import FrozenInstanceError

    calendar = make_calendar()

    with pytest.raises(FrozenInstanceError):
        calendar.name = "Changed"