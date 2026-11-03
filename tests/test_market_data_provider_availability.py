"""Tests for provider availability domain models."""

from datetime import date, datetime, time

import pytest

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilityException,
    ProviderAvailabilitySchedule,
    ProviderAvailabilityWindow,
    WeeklyProviderAvailability,
)


def make_window(
    open_time: time = time(0, 0),
    close_time: time = time(23, 59),
    close_day_offset: int = 0,
) -> ProviderAvailabilityWindow:
    return ProviderAvailabilityWindow(
        open_time=open_time,
        close_time=close_time,
        close_day_offset=close_day_offset,
    )


def make_weekly(
    weekday: int = 1,
    windows: tuple | None = None,
) -> WeeklyProviderAvailability:
    if windows is None:
        windows = (make_window(),)

    return WeeklyProviderAvailability(
        weekday=weekday,
        windows=windows,
    )


def make_exception(
    availability_date: date = date(2026, 1, 5),
    windows: tuple = (),
    reason: str = "Provider maintenance",
    source: str | None = None,
) -> ProviderAvailabilityException:
    return ProviderAvailabilityException(
        availability_date=availability_date,
        windows=windows,
        reason=reason,
        source=source,
    )


def make_schedule(
    weekly_availability: tuple | None = None,
    exceptions: tuple = (),
    **overrides,
) -> ProviderAvailabilitySchedule:
    if weekly_availability is None:
        weekly_availability = (make_weekly(),)

    values = {
        "provider_id": "test-provider",
        "asset_class": AssetClass.FOREX,
        "timeframe": Timeframe.M1,
        "timezone": "UTC",
        "weekly_availability": weekly_availability,
        "exceptions": exceptions,
    }
    values.update(overrides)

    return ProviderAvailabilitySchedule(**values)


# Availability window tests


def test_valid_same_day_window():
    window = make_window(
        time(9, 0),
        time(17, 0),
    )

    assert window.open_time == time(9, 0)
    assert window.close_time == time(17, 0)
    assert window.close_day_offset == 0


def test_valid_overnight_window():
    window = make_window(
        time(22, 0),
        time(2, 0),
        close_day_offset=1,
    )

    assert window.close_day_offset == 1


def test_valid_24_hour_window():
    window = make_window(
        time(0, 0),
        time(0, 0),
        close_day_offset=1,
    )

    assert window.close_day_offset == 1


@pytest.mark.parametrize(
    ("open_time", "close_time"),
    [
        (time(9, 0), time(9, 0)),
        (time(17, 0), time(9, 0)),
    ],
)
def test_same_day_window_requires_close_after_open(
    open_time,
    close_time,
):
    with pytest.raises(ValueError, match="close after it opens"):
        make_window(open_time, close_time)


def test_overnight_window_rejects_close_after_opening_time():
    with pytest.raises(ValueError, match="next-day"):
        make_window(
            time(22, 0),
            time(23, 0),
            close_day_offset=1,
        )


def test_window_rejects_invalid_close_day_offset():
    with pytest.raises(ValueError, match="0 or 1"):
        make_window(
            time(9, 0),
            time(17, 0),
            close_day_offset=2,
        )


def test_window_rejects_boolean_close_day_offset():
    with pytest.raises(TypeError, match="integer"):
        make_window(
            time(9, 0),
            time(17, 0),
            close_day_offset=True,
        )


def test_window_rejects_non_integer_close_day_offset():
    with pytest.raises(TypeError, match="integer"):
        make_window(
            time(9, 0),
            time(17, 0),
            close_day_offset=1.0,
        )


@pytest.mark.parametrize("field_name", ["open_time", "close_time"])
def test_window_rejects_non_time_boundary(field_name):
    values = {
        "open_time": time(9, 0),
        "close_time": time(17, 0),
        "close_day_offset": 0,
    }
    values[field_name] = "09:00"

    with pytest.raises(TypeError, match="time value"):
        ProviderAvailabilityWindow(**values)


def test_window_rejects_timezone_aware_local_time():
    with pytest.raises(ValueError, match="without timezone"):
        make_window(
            time(9, 0, tzinfo=__import__("datetime").timezone.utc),
            time(17, 0),
        )


# Weekly availability tests


def test_weekly_availability_sorts_windows():
    weekly = make_weekly(
        windows=(
            make_window(time(13, 0), time(17, 0)),
            make_window(time(9, 0), time(12, 0)),
        ),
    )

    assert weekly.windows == (
        make_window(time(9, 0), time(12, 0)),
        make_window(time(13, 0), time(17, 0)),
    )


def test_weekly_availability_rejects_invalid_weekday():
    with pytest.raises(ValueError, match="between 1 and 7"):
        make_weekly(weekday=8)


def test_weekly_availability_rejects_boolean_weekday():
    with pytest.raises(TypeError, match="integer"):
        make_weekly(weekday=True)


def test_weekly_availability_rejects_empty_windows():
    with pytest.raises(ValueError, match="contain a window"):
        make_weekly(windows=())


def test_weekly_availability_rejects_invalid_window_type():
    with pytest.raises(TypeError, match="ProviderAvailabilityWindow"):
        make_weekly(windows=("not a window",))


def test_weekly_availability_rejects_duplicate_windows():
    window = make_window(time(9, 0), time(17, 0))

    with pytest.raises(ValueError, match="duplicates"):
        make_weekly(windows=(window, window))


# Exception tests


def test_full_day_exception_has_no_windows():
    exception = make_exception()

    assert exception.windows == ()
    assert exception.reason == "Provider maintenance"


def test_exception_sorts_replacement_windows():
    exception = make_exception(
        windows=(
            make_window(time(13, 0), time(17, 0)),
            make_window(time(9, 0), time(12, 0)),
        ),
    )

    assert exception.windows[0].open_time == time(9, 0)
    assert exception.windows[1].open_time == time(13, 0)


def test_exception_rejects_datetime_instead_of_date():
    with pytest.raises(TypeError, match="not a datetime"):
        make_exception(
            availability_date=datetime(2026, 1, 5),
        )


def test_exception_rejects_empty_reason():
    with pytest.raises(ValueError, match="cannot be empty"):
        make_exception(reason="   ")


def test_exception_rejects_non_string_reason():
    with pytest.raises(TypeError, match="reason must be a string"):
        make_exception(reason=123)


def test_exception_normalizes_reason_and_source():
    exception = make_exception(
        reason="  Scheduled maintenance  ",
        source="  Provider notice  ",
    )

    assert exception.reason == "Scheduled maintenance"
    assert exception.source == "Provider notice"


def test_exception_rejects_empty_source():
    with pytest.raises(ValueError, match="source cannot be empty"):
        make_exception(source="   ")


# Complete schedule tests


def test_schedule_normalizes_provider_id():
    schedule = make_schedule(
        provider_id="  Dukascopy  ",
    )

    assert schedule.provider_id == "dukascopy"


def test_schedule_normalizes_instrument_symbol():
    schedule = make_schedule(
        instrument_symbol="  eur/usd  ",
    )

    assert schedule.instrument_symbol == "EUR/USD"


def test_schedule_supports_asset_class_wide_scope():
    schedule = make_schedule()

    assert schedule.instrument_symbol is None
    assert schedule.asset_class == AssetClass.FOREX


def test_schedule_supports_instrument_specific_scope():
    schedule = make_schedule(
        instrument_symbol="EUR/USD",
    )

    assert schedule.instrument_symbol == "EUR/USD"


def test_schedule_normalizes_weekly_availability():
    schedule = make_schedule(
        weekly_availability=(
            make_weekly(weekday=5),
            make_weekly(weekday=1),
            make_weekly(weekday=3),
        ),
    )

    assert [
        item.weekday for item in schedule.weekly_availability
    ] == [1, 3, 5]


def test_schedule_normalizes_exceptions():
    schedule = make_schedule(
        exceptions=(
            make_exception(date(2026, 1, 7)),
            make_exception(date(2026, 1, 5)),
        ),
    )

    assert [
        item.availability_date for item in schedule.exceptions
    ] == [
        date(2026, 1, 5),
        date(2026, 1, 7),
    ]


def test_schedule_rejects_empty_provider_id():
    with pytest.raises(ValueError, match="provider_id"):
        make_schedule(provider_id="   ")


def test_schedule_rejects_invalid_provider_id():
    with pytest.raises(ValueError, match="provider_id"):
        make_schedule(provider_id="provider name")


def test_schedule_rejects_invalid_asset_class():
    with pytest.raises(TypeError, match="AssetClass"):
        make_schedule(asset_class="forex")


def test_schedule_rejects_invalid_timeframe():
    with pytest.raises(TypeError, match="Timeframe"):
        make_schedule(timeframe="1m")


def test_schedule_rejects_unknown_timezone():
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        make_schedule(timezone="Invalid/Timezone")


def test_schedule_rejects_empty_timezone():
    with pytest.raises(ValueError, match="timezone cannot be empty"):
        make_schedule(timezone="   ")


def test_schedule_rejects_empty_instrument_symbol():
    with pytest.raises(ValueError, match="instrument_symbol cannot be empty"):
        make_schedule(instrument_symbol="   ")


def test_schedule_rejects_empty_version():
    with pytest.raises(ValueError, match="version cannot be empty"):
        make_schedule(version="   ")


def test_schedule_rejects_duplicate_weekdays():
    with pytest.raises(ValueError, match="duplicate weekdays"):
        make_schedule(
            weekly_availability=(
                make_weekly(weekday=1),
                make_weekly(weekday=1),
            ),
        )


def test_schedule_rejects_duplicate_exception_dates():
    exception = make_exception()

    with pytest.raises(ValueError, match="duplicate dates"):
        make_schedule(
            exceptions=(exception, exception),
        )


def test_schedule_rejects_invalid_weekly_item():
    with pytest.raises(TypeError, match="WeeklyProviderAvailability"):
        make_schedule(weekly_availability=("invalid",))


def test_schedule_rejects_invalid_exception_item():
    with pytest.raises(TypeError, match="ProviderAvailabilityException"):
        make_schedule(exceptions=("invalid",))


def test_schedule_is_immutable():
    schedule = make_schedule()

    with pytest.raises(AttributeError):
        schedule.provider_id = "another-provider"