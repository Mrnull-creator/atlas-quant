
import numpy as np
import pandas as pd
import pytest

from atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals,
)


def test_strategy_output():
    prices = pd.Series(
        np.linspace(1.10, 1.20, 100)
    )

    result = generate_signals(
        prices,
        StrategyConfig(fast_period=5, slow_period=15),
    )

    assert len(result) == len(prices)
    assert {"price", "fast_ma", "slow_ma",
            "signal", "position"}.issubset(result.columns)


def test_position_is_delayed():
    prices = pd.Series(
        np.linspace(1.10, 1.20, 100)
    )

    result = generate_signals(
        prices,
        StrategyConfig(fast_period=5, slow_period=15),
    )

    assert result["position"].iloc[0] == 0
    assert result["position"].iloc[-1] == 1


def test_invalid_periods():
    with pytest.raises(ValueError):
        StrategyConfig(fast_period=30, slow_period=10)


def test_invalid_prices():
    prices = pd.Series([1.10, 1.11, np.nan, 1.13])

    with pytest.raises(ValueError):
        generate_signals(prices)

def test_position_uses_previous_row_signal():
    prices = pd.Series(
        [1.10, 1.11, 1.12, 1.13, 1.12, 1.11, 1.10, 1.09],
        index=pd.date_range(
            "2026-01-01",
            periods=8,
            freq="min",
            tz="UTC",
        ),
    )

    result = generate_signals(
        prices,
        StrategyConfig(fast_period=2, slow_period=3),
    )

    expected_position = result["signal"].shift(
        1, fill_value=0
    )

    ready = (
        result["fast_ma"].notna()
        & result["slow_ma"].notna()
    )
    expected_position.loc[~ready] = 0

    pd.testing.assert_series_equal(
        result["position"],
        expected_position.rename("position"),
    )
def test_position_timing_matches_explicit_expected_values():
    prices = pd.Series(
        [10, 11, 12, 13, 12, 11, 10, 9],
        index=pd.date_range(
            "2026-01-01",
            periods=8,
            freq="min",
            tz="UTC",
        ),
        dtype=float,
    )

    result = generate_signals(
        prices,
        StrategyConfig(fast_period=2, slow_period=3),
    )

    # Independently specified expected values.
    # The first valid moving-average comparison occurs at index 2.
    # A signal at index 2 must not affect the position until index 3.
    expected_signals = [0, 0, 1, 1, 1, -1, -1, -1]
    expected_positions = [0, 0, 0, 1, 1, 1, -1, -1]

    assert result["signal"].tolist() == expected_signals
    assert result["position"].tolist() == expected_positions