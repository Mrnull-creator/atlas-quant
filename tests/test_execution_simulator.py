
import pandas as pd
import pytest

from atlas_quant.backtesting.execution_simulator import (
    simulate_execution,
)


def make_data(positions, bids, asks):
    return pd.DataFrame(
        {
            "bid_close": bids,
            "ask_close": asks,
            "position": positions,
        },
        index=pd.date_range(
            "2026-01-01",
            periods=len(positions),
            freq="min",
            tz="UTC",
        ),
    )


def test_flat_position_preserves_cash():
    data = make_data(
        positions=[0, 0],
        bids=[1.1000, 1.1010],
        asks=[1.1002, 1.1012],
    )

    result = simulate_execution(data, initial_cash=10_000, units=1_000)

    assert result["equity"].tolist() == pytest.approx(
        [10_000, 10_000]
    )


def test_long_entry_pays_ask_and_is_marked_at_bid():
    data = make_data(
        positions=[1],
        bids=[1.1000],
        asks=[1.1002],
    )

    result = simulate_execution(data, initial_cash=10_000, units=1_000)

    # Buy 1,000 units at 1.1002, then value them at bid 1.1000.
    assert result["cash"].iloc[0] == pytest.approx(8_899.8)
    assert result["equity"].iloc[0] == pytest.approx(9_999.8)


def test_short_entry_sells_at_bid_and_is_marked_at_ask():
    data = make_data(
        positions=[-1],
        bids=[1.1000],
        asks=[1.1002],
    )

    result = simulate_execution(data, initial_cash=10_000, units=1_000)

    assert result["cash"].iloc[0] == pytest.approx(11_100.0)
    assert result["equity"].iloc[0] == pytest.approx(9_999.8)


def test_reversal_closes_old_side_and_opens_new_side():
    data = make_data(
        positions=[1, -1],
        bids=[1.1000, 1.1010],
        asks=[1.1002, 1.1012],
    )

    result = simulate_execution(data, initial_cash=10_000, units=1_000)

    # After reversal, short 1,000 units, valued at the ask.
    assert result["units"].tolist() == pytest.approx([1_000, -1_000])
    assert result["equity"].iloc[1] == pytest.approx(10_000.6)


def test_invalid_spread_is_rejected():
    data = make_data(
        positions=[0],
        bids=[1.1002],
        asks=[1.1000],
    )

    with pytest.raises(ValueError, match="Ask cannot be below bid"):
        simulate_execution(data)


def test_invalid_position_is_rejected():
    data = make_data(
        positions=[2],
        bids=[1.1000],
        asks=[1.1002],
    )

    with pytest.raises(ValueError, match="Positions"):
        simulate_execution(data)