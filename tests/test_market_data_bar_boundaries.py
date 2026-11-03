
"""Tests for market data bar boundary policies."""

from dataclasses import FrozenInstanceError
from datetime import time

import pytest

from atlas_quant.services.market_data.domain.bar_boundaries import (
    BarAlignmentMode,
    BarBoundaryPolicy,
)
from atlas_quant.services.market_data.domain.enums import Timeframe


def make_policy(**overrides) -> BarBoundaryPolicy:
    values = {
        "policy_id": "test-policy",
        "timeframe": Timeframe.M1,
        "alignment_mode": BarAlignmentMode.UTC_CLOCK,
    }
    values.update(overrides)
    return BarBoundaryPolicy(**values)


def make_calendar_policy(
    timeframe: Timeframe = Timeframe.D1,
    **overrides,
) -> BarBoundaryPolicy:
    values = {
        "timeframe": timeframe,
        "alignment_mode": BarAlignmentMode.CALENDAR_PERIOD,
        "boundary_timezone": "UTC",
        "period_start_time": time(0, 0),
    }
    if timeframe == Timeframe.W1:
        values["week_start_day"] = 1
    values.update(overrides)
    return make_policy(**values)


@pytest.mark.parametrize(
    "timeframe",
    [
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
    ],
)
@pytest.mark.parametrize(
    "alignment",
    [BarAlignmentMode.UTC_CLOCK, BarAlignmentMode.SESSION],
)
def test_intraday_policies_accept_supported_alignment_modes(
    timeframe: Timeframe,
    alignment: BarAlignmentMode,
) -> None:
    policy = make_policy(
        timeframe=timeframe,
        alignment_mode=alignment,
    )

    assert policy.timeframe == timeframe
    assert policy.alignment_mode == alignment


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.D1, Timeframe.W1, Timeframe.MN1],
)
def test_calendar_period_policies_accept_calendar_timeframes(
    timeframe: Timeframe,
) -> None:
    policy = make_calendar_policy(timeframe)

    assert policy.timeframe == timeframe
    assert policy.alignment_mode == BarAlignmentMode.CALENDAR_PERIOD
    assert policy.period_start_time == time(0, 0)


def test_calendar_policy_accepts_provider_specific_rollover_time() -> None:
    policy = make_calendar_policy(
        boundary_timezone="America/New_York",
        period_start_time=time(17, 0),
    )

    assert policy.boundary_timezone == "America/New_York"
    assert policy.period_start_time == time(17, 0)


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.D1, Timeframe.W1, Timeframe.MN1],
)
def test_calendar_period_policies_require_timezone(
    timeframe: Timeframe,
) -> None:
    with pytest.raises(TypeError, match="boundary_timezone"):
        make_calendar_policy(timeframe, boundary_timezone=None)


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.D1, Timeframe.W1, Timeframe.MN1],
)
def test_calendar_period_policies_require_period_start_time(
    timeframe: Timeframe,
) -> None:
    with pytest.raises(TypeError, match="period_start_time"):
        make_calendar_policy(timeframe, period_start_time=None)


def test_calendar_policy_rejects_timezone_aware_period_start_time() -> None:
    from datetime import timezone

    with pytest.raises(ValueError, match="naive local"):
        make_calendar_policy(
            period_start_time=time(17, 0, tzinfo=timezone.utc),
        )


def test_weekly_policy_requires_week_start_day() -> None:
    with pytest.raises(ValueError, match="week_start_day"):
        make_policy(
            timeframe=Timeframe.W1,
            alignment_mode=BarAlignmentMode.CALENDAR_PERIOD,
            boundary_timezone="UTC",
            period_start_time=time(0, 0),
        )


@pytest.mark.parametrize("weekday", [1, 2, 3, 4, 5, 6, 7])
def test_weekly_policy_accepts_valid_week_start_days(weekday: int) -> None:
    policy = make_calendar_policy(
        Timeframe.W1,
        week_start_day=weekday,
    )

    assert policy.week_start_day == weekday


@pytest.mark.parametrize("weekday", [0, 8, -1, 10])
def test_weekly_policy_rejects_invalid_week_start_days(
    weekday: int,
) -> None:
    with pytest.raises(ValueError, match="week_start_day"):
        make_calendar_policy(
            Timeframe.W1,
            week_start_day=weekday,
        )


@pytest.mark.parametrize("weekday", [True, False, 1.5, "1"])
def test_weekly_policy_rejects_non_integer_week_start_days(
    weekday: object,
) -> None:
    with pytest.raises(TypeError, match="week_start_day"):
        make_calendar_policy(
            Timeframe.W1,
            week_start_day=weekday,
        )


@pytest.mark.parametrize(
    "timeframe",
    [
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
    ],
)
def test_intraday_policy_rejects_calendar_period_alignment(
    timeframe: Timeframe,
) -> None:
    with pytest.raises(ValueError, match="Intraday"):
        make_policy(
            timeframe=timeframe,
            alignment_mode=BarAlignmentMode.CALENDAR_PERIOD,
            boundary_timezone="UTC",
        )


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.D1, Timeframe.W1, Timeframe.MN1],
)
def test_calendar_timeframe_rejects_session_alignment(
    timeframe: Timeframe,
) -> None:
    with pytest.raises(ValueError, match="CALENDAR_PERIOD"):
        make_policy(
            timeframe=timeframe,
            alignment_mode=BarAlignmentMode.SESSION,
            boundary_timezone="UTC",
            period_start_time=time(0, 0),
            week_start_day=1 if timeframe == Timeframe.W1 else None,
        )


def test_intraday_policy_rejects_boundary_timezone() -> None:
    with pytest.raises(ValueError, match="boundary_timezone"):
        make_policy(boundary_timezone="UTC")


def test_intraday_policy_rejects_period_start_time() -> None:
    with pytest.raises(ValueError, match="period_start_time"):
        make_policy(period_start_time=time(0, 0))


def test_intraday_policy_rejects_week_start_day() -> None:
    with pytest.raises(ValueError, match="week_start_day"):
        make_policy(week_start_day=1)


def test_calendar_policy_rejects_unknown_timezone() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        make_calendar_policy(boundary_timezone="Not/A_Timezone")


def test_calendar_policy_normalizes_timezone_whitespace() -> None:
    policy = make_calendar_policy(boundary_timezone=" UTC ")

    assert policy.boundary_timezone == "UTC"


def test_calendar_policy_rejects_empty_timezone() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        make_calendar_policy(boundary_timezone=" ")


def test_partial_final_bar_is_allowed_for_session_alignment() -> None:
    policy = make_policy(
        alignment_mode=BarAlignmentMode.SESSION,
        allow_partial_final_bar=True,
    )

    assert policy.allow_partial_final_bar is True


def test_partial_final_bar_is_rejected_for_utc_alignment() -> None:
    with pytest.raises(ValueError, match="SESSION alignment"):
        make_policy(allow_partial_final_bar=True)


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.D1, Timeframe.W1, Timeframe.MN1],
)
def test_partial_final_bar_is_rejected_for_calendar_periods(
    timeframe: Timeframe,
) -> None:
    with pytest.raises(ValueError, match="session-aligned intraday"):
        make_calendar_policy(
            timeframe,
            allow_partial_final_bar=True,
        )


def test_non_weekly_calendar_policy_rejects_week_start_day() -> None:
    with pytest.raises(ValueError, match="only supported for weekly"):
        make_calendar_policy(
            Timeframe.D1,
            week_start_day=1,
        )


def test_policy_id_is_normalized() -> None:
    policy = make_policy(policy_id="  My-Policy_01  ")

    assert policy.policy_id == "my-policy_01"


@pytest.mark.parametrize(
    "policy_id",
    ["", " ", "-invalid", "bad id", "UPPER CASE"],
)
def test_policy_rejects_invalid_policy_id(policy_id: str) -> None:
    with pytest.raises(ValueError, match="policy_id"):
        make_policy(policy_id=policy_id)


def test_policy_rejects_empty_version() -> None:
    with pytest.raises(ValueError, match="version"):
        make_policy(version=" ")


def test_policy_is_immutable() -> None:
    policy = make_policy()

    with pytest.raises(FrozenInstanceError):
        policy.policy_id = "changed"  # type: ignore[misc]