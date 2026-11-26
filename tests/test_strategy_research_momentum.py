from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pandas as pd
import pytest

from atlas_quant.services.strategy_research.application.registry import (
    InMemoryStrategyImplementationRegistry,
)
from atlas_quant.services.strategy_research.domain.strategies import (
    ExecutionTiming,
    Strategy,
    StrategyVersion,
)
from atlas_quant.services.strategy_research.infrastructure.momentum import (
    MomentumStrategyImplementation,
)


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def make_version(parameters=None, implementation_version="1.0.0"):
    strategy = Strategy(
        name="Momentum",
        strategy_key="momentum",
    )

    import json

    return StrategyVersion(
        strategy_id=strategy.strategy_id,
        version_number=1,
        implementation_key="momentum",
        implementation_version=implementation_version,
        parameters_json=json.dumps(
            parameters or {"fast_period": 2, "slow_period": 3}
        ),
    )


def make_market_data():
    opens = pd.date_range(
        START,
        periods=5,
        freq="min",
    )

    closes = opens + pd.Timedelta(minutes=1)

    return pd.DataFrame(
        {
            "close": [1.0, 2.0, 3.0, 2.0, 1.0],
            "bar_close_time": closes,
        },
        index=opens,
    )


def test_momentum_parameters_use_existing_defaults():
    implementation = MomentumStrategyImplementation()

    assert implementation.validate_parameters({}) == {
        "fast_period": 10,
        "slow_period": 30,
    }


def test_momentum_parameters_are_validated():
    implementation = MomentumStrategyImplementation()

    assert implementation.validate_parameters(
        {"fast_period": 2, "slow_period": 5}
    ) == {
        "fast_period": 2,
        "slow_period": 5,
    }

    with pytest.raises(ValueError, match="slow_period"):
        implementation.validate_parameters(
            {"fast_period": 5, "slow_period": 2}
        )


def test_momentum_rejects_unknown_parameters():
    implementation = MomentumStrategyImplementation()

    with pytest.raises(ValueError, match="Unknown"):
        implementation.validate_parameters(
            {"fast_period": 2, "slow_period": 3, "threshold": 0.5}
        )


def test_momentum_rejects_boolean_periods():
    implementation = MomentumStrategyImplementation()

    with pytest.raises(TypeError, match="integer"):
        implementation.validate_parameters(
            {"fast_period": True, "slow_period": 3}
        )


def test_momentum_generates_next_bar_open_signals():
    implementation = MomentumStrategyImplementation()
    version = make_version()
    instrument_id = uuid4()

    signals = implementation.generate_signals(
        instrument_id=instrument_id,
        strategy_version=version,
        market_data=make_market_data(),
    )

    # The first two bars are indicator warmup.
    # Bars three and four have valid indicators and known next opens.
    # The fifth bar has no known next opening timestamp.
    assert len(signals) == 2

    signal = signals[0]

    assert signal.target_exposure == 1
    assert signal.generated_at == START + timedelta(minutes=3)
    assert signal.effective_at == START + timedelta(minutes=3)
    assert signal.instrument_id == instrument_id
    assert signal.strategy_version_id == version.strategy_version_id


def test_momentum_signal_uses_completed_bar_not_shifted_position():
    implementation = MomentumStrategyImplementation()
    version = make_version()

    signals = implementation.generate_signals(
        instrument_id=uuid4(),
        strategy_version=version,
        market_data=make_market_data(),
    )

    # The signal from the third completed bar becomes effective
    # at the fourth bar's open. It is not shifted a second time.
    assert signals[0].target_exposure == 1
    assert signals[0].generated_at == START + timedelta(minutes=3)
    assert signals[0].effective_at == START + timedelta(minutes=3)


def test_momentum_rejects_wrong_implementation_version():
    implementation = MomentumStrategyImplementation()
    version = make_version(implementation_version="2.0.0")

    with pytest.raises(ValueError, match="does not identify"):
        implementation.generate_signals(
            instrument_id=uuid4(),
            strategy_version=version,
            market_data=make_market_data(),
        )


def test_momentum_rejects_naive_open_timestamps():
    implementation = MomentumStrategyImplementation()
    version = make_version()
    data = make_market_data()
    data.index = data.index.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        implementation.generate_signals(
            instrument_id=uuid4(),
            strategy_version=version,
            market_data=data,
        )


def test_momentum_rejects_naive_close_timestamps():
    implementation = MomentumStrategyImplementation()
    version = make_version()
    data = make_market_data()
    data["bar_close_time"] = data["bar_close_time"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        implementation.generate_signals(
            instrument_id=uuid4(),
            strategy_version=version,
            market_data=data,
        )


def test_momentum_rejects_missing_market_data_columns():
    implementation = MomentumStrategyImplementation()
    version = make_version()

    with pytest.raises(ValueError, match="missing columns"):
        implementation.generate_signals(
            instrument_id=uuid4(),
            strategy_version=version,
            market_data=pd.DataFrame(
                {"close": [1.0, 2.0]},
                index=pd.date_range(START, periods=2, freq="min"),
            ),
        )


def test_momentum_can_be_registered_and_resolved():
    registry = InMemoryStrategyImplementationRegistry()
    implementation = MomentumStrategyImplementation()

    registry.register(implementation)

    assert registry.resolve("momentum", "1.0.0") is implementation


def test_momentum_rejects_unsupported_execution_timing():
    implementation = MomentumStrategyImplementation()
    version = make_version()

    assert version.execution_timing == ExecutionTiming.NEXT_BAR_OPEN