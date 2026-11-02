
import pandas as pd
import pytest

from atlas_quant.backtesting.bid_ask import (
    run_bid_ask_backtest,
)


def make_data():
    return pd.DataFrame(
        {
            "mid_close": [
                1.1000, 1.1010, 1.1020, 1.1010, 1.1000
            ],
            "spread_pips": [2.0, 2.0, 2.0, 2.0, 2.0],
            "position": [0, 1, 1, 0, 0],
        },
        index=pd.date_range(
            "2026-01-01",
            periods=5,
            freq="min",
            tz="UTC",
        ),
    )


def test_backtest_uses_next_close_for_return():
    result = run_bid_ask_backtest(make_data())

    assert len(result) == 4
    assert result["market_return"].iloc[0] == pytest.approx(
        1.1010 / 1.1000 - 1
    )


def test_entry_and_exit_have_spread_costs():
    result = run_bid_ask_backtest(make_data())

    assert result["turnover"].tolist() == [0, 1, 0, 1]

    expected_entry_cost = 0.0001 / 1.1010
    expected_exit_cost = 0.0001 / 1.1010

    assert result["spread_cost"].iloc[1] == pytest.approx(
        expected_entry_cost
    )
    assert result["spread_cost"].iloc[3] == pytest.approx(
        expected_exit_cost
    )


def test_reversal_has_two_units_of_turnover():
    data = make_data()
    data["position"] = [0, 1, -1, -1, 0]

    result = run_bid_ask_backtest(data)

    assert result["turnover"].iloc[2] == 2


def test_negative_spread_is_rejected():
    data = make_data()
    data.loc[data.index[0], "spread_pips"] = -1

    with pytest.raises(ValueError, match="Spreads"):
        run_bid_ask_backtest(data)


def test_invalid_position_is_rejected():
    data = make_data()
    data.loc[data.index[0], "position"] = 2

    with pytest.raises(ValueError, match="Positions"):
        run_bid_ask_backtest(data)