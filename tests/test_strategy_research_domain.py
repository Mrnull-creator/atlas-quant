from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.strategy_research.domain.strategies import (
    ExecutionTiming,
    Strategy,
    StrategySignal,
    StrategyVersion,
)


UTC = timezone.utc
NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def make_strategy():
    return Strategy(
        name="Momentum",
        strategy_key="momentum",
    )


def make_version(strategy):
    return StrategyVersion(
        strategy_id=strategy.strategy_id,
        version_number=1,
        implementation_key="momentum",
        implementation_version="1.0.0",
        parameters_json='{"fast_period":10,"slow_period":30}',
    )


def test_strategy_identity_is_stable_and_normalized():
    strategy = Strategy(
        name="  Momentum  ",
        strategy_key="  MOMENTUM ",
    )

    assert strategy.name == "Momentum"
    assert strategy.strategy_key == "momentum"
    assert isinstance(strategy.strategy_id, type(uuid4()))


def test_strategy_rejects_invalid_key():
    with pytest.raises(ValueError, match="strategy_key"):
        Strategy(name="Momentum", strategy_key="Momentum strategy")


def test_strategy_version_canonicalizes_parameters():
    strategy = make_strategy()

    version = StrategyVersion(
        strategy_id=strategy.strategy_id,
        version_number=1,
        implementation_key="momentum",
        implementation_version="1.0",
        parameters_json='{"slow_period":30, "fast_period":10}',
    )

    assert version.parameters_json == (
        '{"fast_period":10,"slow_period":30}'
    )


def test_strategy_version_parameters_are_independently_copied():
    version = make_version(make_strategy())

    parameters = version.parameters
    parameters["fast_period"] = 999

    assert version.parameters["fast_period"] == 10


def test_strategy_version_rejects_non_object_parameters():
    strategy = make_strategy()

    with pytest.raises(ValueError, match="JSON object"):
        StrategyVersion(
            strategy_id=strategy.strategy_id,
            version_number=1,
            implementation_key="momentum",
            implementation_version="1.0",
            parameters_json="[1, 2, 3]",
        )


def test_strategy_version_rejects_non_finite_parameters():
    strategy = make_strategy()

    with pytest.raises(ValueError):
        StrategyVersion(
            strategy_id=strategy.strategy_id,
            version_number=1,
            implementation_key="momentum",
            implementation_version="1.0",
            parameters_json='{"threshold":NaN}',
        )


def test_strategy_version_rejects_zero_version():
    strategy = make_strategy()

    with pytest.raises(ValueError):
        StrategyVersion(
            strategy_id=strategy.strategy_id,
            version_number=0,
            implementation_key="momentum",
            implementation_version="1.0",
        )


def test_strategy_version_defaults_to_next_bar_open():
    version = make_version(make_strategy())

    assert version.execution_timing == ExecutionTiming.NEXT_BAR_OPEN


def test_signal_accepts_long_short_and_partial_exposure():
    strategy = make_strategy()
    version = make_version(strategy)
    instrument_id = uuid4()

    signal = StrategySignal(
        strategy_version_id=version.strategy_version_id,
        instrument_id=instrument_id,
        generated_at=NOW,
        effective_at=NOW + timedelta(minutes=1),
        target_exposure=Decimal("0.5"),
    )

    assert signal.target_exposure == Decimal("0.5")


@pytest.mark.parametrize(
    "exposure",
    [
        Decimal("-1.1"),
        Decimal("1.1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_signal_rejects_invalid_exposure(exposure):
    version = make_version(make_strategy())

    with pytest.raises(ValueError):
        StrategySignal(
            strategy_version_id=version.strategy_version_id,
            instrument_id=uuid4(),
            generated_at=NOW,
            effective_at=NOW,
            target_exposure=exposure,
        )


def test_signal_rejects_float_exposure():
    version = make_version(make_strategy())

    with pytest.raises(TypeError, match="Decimal"):
        StrategySignal(
            strategy_version_id=version.strategy_version_id,
            instrument_id=uuid4(),
            generated_at=NOW,
            effective_at=NOW,
            target_exposure=0.5,
        )


def test_signal_rejects_effective_time_before_generation():
    version = make_version(make_strategy())

    with pytest.raises(ValueError, match="effective_at"):
        StrategySignal(
            strategy_version_id=version.strategy_version_id,
            instrument_id=uuid4(),
            generated_at=NOW,
            effective_at=NOW - timedelta(minutes=1),
            target_exposure=Decimal("1"),
        )


def test_domain_timestamps_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        Strategy(
            name="Momentum",
            strategy_key="momentum",
            created_at=datetime(2026, 11, 22),
        )
