
import pandas as pd
import pytest

from atlas_quant.backtesting.next_open_simulator import (
    simulate_next_open,
)


def make_data(positions, bid_open, ask_open, bid_close, ask_close):
    index = pd.date_range(
        "2026-01-01 10:00",
        periods=len(positions),
        freq="min",
        tz="UTC",
    )

    return pd.DataFrame(
        {
            "bid_open": bid_open,
            "ask_open": ask_open,
            "bid_close": bid_close,
            "ask_close": ask_close,
            "position": positions,
        },
        index=index,
    )


def test_long_entry_uses_ask_open_and_bid_close():
    data = make_data(
        positions=[0, 1],
        bid_open=[1.1000, 1.1010],
        ask_open=[1.1002, 1.1012],
        bid_close=[1.1000, 1.1020],
        ask_close=[1.1002, 1.1022],
    )

    result = simulate_next_open(
        data, initial_cash=10_000, units=1_000
    )

    assert len(result) == 1
    assert result["units"].iloc[0] == 1_000
    assert result["cash"].iloc[0] == pytest.approx(
        10_000 - 1_000 * 1.1012
    )
    assert result["equity"].iloc[0] == pytest.approx(
        10_000 + 1_000 * (1.1020 - 1.1012)
    )


def test_short_entry_uses_bid_open_and_ask_close():
    data = make_data(
        positions=[0, -1],
        bid_open=[1.1000, 1.1010],
        ask_open=[1.1002, 1.1012],
        bid_close=[1.1000, 1.1005],
        ask_close=[1.1002, 1.1007],
    )

    result = simulate_next_open(
        data, initial_cash=10_000, units=1_000
    )

    assert result["units"].iloc[0] == -1_000
    assert result["equity"].iloc[0] == pytest.approx(
        10_000 + 1_000 * (1.1010 - 1.1007)
    )


def test_reversal_closes_old_position_and_opens_new_one():
    data = make_data(
        positions=[1, -1, -1],
        bid_open=[1.1000, 1.1010, 1.1000],
        ask_open=[1.1002, 1.1012, 1.1002],
        bid_close=[1.1000, 1.1008, 1.0998],
        ask_close=[1.1002, 1.1010, 1.1000],
    )

    result = simulate_next_open(
        data, initial_cash=10_000, units=1_000
    )

    assert result["units"].tolist() == [-1_000, -1_000]


def test_invalid_ask_bid_spread_is_rejected():
    data = make_data(
        positions=[0, 1],
        bid_open=[1.1000, 1.1010],
        ask_open=[1.1002, 1.1009],
        bid_close=[1.1000, 1.1010],
        ask_close=[1.1002, 1.1012],
    )

    with pytest.raises(ValueError, match="Ask open"):
        simulate_next_open(data)


def test_invalid_position_is_rejected():
    data = make_data(
        positions=[0, 2],
        bid_open=[1.1000, 1.1010],
        ask_open=[1.1002, 1.1012],
        bid_close=[1.1000, 1.1010],
        ask_close=[1.1002, 1.1012],
    )

    with pytest.raises(ValueError, match="Positions"):
        simulate_next_open(data)


def test_initial_spread_loss_counts_toward_max_drawdown():
    data = make_data(
        positions=[0, 1],
        bid_open=[1.1000, 1.1000],
        ask_open=[1.1002, 1.1002],
        bid_close=[1.1000, 1.1000],
        ask_close=[1.1002, 1.1002],
    )

    result = simulate_next_open(
        data, initial_cash=10_000, units=1_000
    )

    expected_equity = 10_000 - 1_000 * (1.1002 - 1.1000)

    assert result["equity"].iloc[0] == pytest.approx(
        expected_equity
    )
    assert result["peak_equity"].iloc[0] == pytest.approx(
        10_000
    )
    assert result["drawdown"].iloc[0] == pytest.approx(
        expected_equity / 10_000 - 1
    )


def test_commission_reduces_equity_by_exact_amount():
    data = make_data(
        positions=[0, 1],
        bid_open=[1.1000, 1.1000],
        ask_open=[1.1002, 1.1002],
        bid_close=[1.1000, 1.1000],
        ask_close=[1.1002, 1.1002],
    )

    result = simulate_next_open(
        data,
        initial_cash=10_000,
        units=1_000,
        commission_per_1000_units_per_side_usd=0.035,
    )

    assert result["commission"].iloc[0] == pytest.approx(0.035)
    assert result["equity"].iloc[0] == pytest.approx(
        10_000 - 0.20 - 0.035
    )


def test_slippage_reduces_long_equity_by_exact_amount():
    data = make_data(
        positions=[0, 1],
        bid_open=[1.1000, 1.1000],
        ask_open=[1.1002, 1.1002],
        bid_close=[1.1000, 1.1000],
        ask_close=[1.1002, 1.1002],
    )

    result = simulate_next_open(
        data,
        initial_cash=10_000,
        units=1_000,
        slippage_pips=1.0,
    )

    assert result["equity"].iloc[0] == pytest.approx(
        10_000 - 0.20 - 0.10
    )


def test_reversal_commission_charges_both_trade_legs():
    data = make_data(
        positions=[0, 1, -1],
        bid_open=[1.1000, 1.1000, 1.1000],
        ask_open=[1.1002, 1.1002, 1.1002],
        bid_close=[1.1000, 1.1000, 1.1000],
        ask_close=[1.1002, 1.1002, 1.1002],
    )

    result = simulate_next_open(
        data,
        initial_cash=10_000,
        units=1_000,
        commission_per_1000_units_per_side_usd=0.035,
    )

    assert result["commission"].iloc[1] == pytest.approx(0.070)


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        (
            "commission_per_1000_units_per_side_usd",
            -0.01,
            "Commission",
        ),
        ("slippage_pips", -0.1, "Slippage"),
        ("pip_size", 0.0, "Pip size"),
    ],
)
def test_invalid_cost_settings_are_rejected(
    setting, value, message
):
    data = make_data(
        positions=[0, 1],
        bid_open=[1.1000, 1.1000],
        ask_open=[1.1002, 1.1002],
        bid_close=[1.1000, 1.1000],
        ask_close=[1.1002, 1.1002],
    )

    with pytest.raises(ValueError, match=message):
        simulate_next_open(data, **{setting: value})


def test_first_input_row_is_excluded():
    data = make_data(
        positions=[1, -1, 1],
        bid_open=[1.1000, 1.1010, 1.1020],
        ask_open=[1.1002, 1.1012, 1.1022],
        bid_close=[1.1000, 1.1010, 1.1020],
        ask_close=[1.1002, 1.1012, 1.1022],
    )

    result = simulate_next_open(
        data, initial_cash=10_000, units=1_000
    )

    assert len(result) == 2
    assert result["position"].tolist() == [-1, 1]
    assert result["units"].tolist() == [-1_000, 1_000]
    assert result.index[0] == data.index[1]
    assert result.index[1] == data.index[2]

def test_long_entry_and_exit_reconcile_cash_and_equity():
    data = make_data(
        positions=[0, 1, 0],
        bid_open=[1.1000, 1.1000, 1.1010],
        ask_open=[1.1002, 1.1002, 1.1012],
        bid_close=[1.1000, 1.1005, 1.1010],
        ask_close=[1.1002, 1.1007, 1.1012],
    )

    result = simulate_next_open(
        data,
        initial_cash=10_000,
        units=1_000,
        commission_per_1000_units_per_side_usd=0.035,
    )

    # Row two opens the long; row three closes it.
    assert result["position"].tolist() == [1, 0]
    assert result["units"].tolist() == [1_000, 0]

    # Entry: buy 1,000 units at ask 1.1002 and pay $0.035.
    expected_entry_cash = 10_000 - 1_000 * 1.1002 - 0.035
    assert result["cash"].iloc[0] == pytest.approx(
        expected_entry_cash
    )

    # At the first close, mark the long at bid 1.1005.
    expected_open_equity = (
        expected_entry_cash + 1_000 * 1.1005
    )
    assert result["equity"].iloc[0] == pytest.approx(
        expected_open_equity
    )

    # Exit: sell at bid 1.1010 and pay another $0.035.
    expected_final_cash = (
        expected_entry_cash + 1_000 * 1.1010 - 0.035
    )
    assert result["cash"].iloc[1] == pytest.approx(
        expected_final_cash
    )

    # With no open position, equity must equal cash.
    assert result["equity"].iloc[1] == pytest.approx(
        expected_final_cash
    )

    # Realized profit includes the $0.80 price gain and $0.07 fees.
    assert result["equity"].iloc[1] == pytest.approx(
        10_000 + 0.80 - 0.07
    )

def test_short_entry_and_exit_reconcile_cash_and_equity():
    data = make_data(
        positions=[0, -1, 0],
        bid_open=[1.1000, 1.1000, 1.0990],
        ask_open=[1.1002, 1.1002, 1.0992],
        bid_close=[1.1000, 1.0995, 1.0990],
        ask_close=[1.1002, 1.0997, 1.0992],
    )

    result = simulate_next_open(
        data,
        initial_cash=10_000,
        units=1_000,
        commission_per_1000_units_per_side_usd=0.035,
    )

    # Row two opens the short; row three closes it.
    assert result["position"].tolist() == [-1, 0]
    assert result["units"].tolist() == [-1_000, 0]

    # Entry: sell 1,000 units at bid 1.1000 and pay $0.035.
    expected_entry_cash = 10_000 + 1_000 * 1.1000 - 0.035
    assert result["cash"].iloc[0] == pytest.approx(
        expected_entry_cash
    )

    # Mark the short at the executable ask close of 1.0997.
    expected_open_equity = (
        expected_entry_cash - 1_000 * 1.0997
    )
    assert result["equity"].iloc[0] == pytest.approx(
        expected_open_equity
    )

    # Exit: buy back at ask 1.0992 and pay another $0.035.
    expected_final_cash = (
        expected_entry_cash - 1_000 * 1.0992 - 0.035
    )
    assert result["cash"].iloc[1] == pytest.approx(
        expected_final_cash
    )

    # Once flat, equity equals cash.
    assert result["equity"].iloc[1] == pytest.approx(
        expected_final_cash
    )

    # The short gains $0.80 before the two $0.035 commissions.
    assert result["equity"].iloc[1] == pytest.approx(
        10_000 + 0.80 - 0.07
    )

def test_reversal_accounting_matches_independent_calculation():
    data = make_data(
        positions=[0, 1, -1],
        bid_open=[1.1000, 1.1000, 1.1010],
        ask_open=[1.1002, 1.1002, 1.1012],
        bid_close=[1.1000, 1.1005, 1.1000],
        ask_close=[1.1002, 1.1007, 1.1002],
    )

    initial_cash = 10_000.0
    units = 1_000.0
    commission_per_1000 = 0.035

    result = simulate_next_open(
        data,
        initial_cash=initial_cash,
        units=units,
        commission_per_1000_units_per_side_usd=commission_per_1000,
    )

    # First executed row: buy 1,000 EUR at ask 1.1002.
    cash_after_entry = (
        initial_cash
        - units * 1.1002
        - commission_per_1000
    )
    equity_after_entry = cash_after_entry + units * 1.1005

    # Second executed row: sell 2,000 EUR at bid 1.1010.
    # This closes the long and opens a short of 1,000 EUR.
    reversal_commission = 2 * commission_per_1000
    cash_after_reversal = (
        cash_after_entry
        + 2 * units * 1.1010
        - reversal_commission
    )

    # The resulting short is marked at ask close 1.1002.
    equity_after_reversal = cash_after_reversal - units * 1.1002

    assert result["position"].tolist() == [1, -1]
    assert result["units"].tolist() == [units, -units]

    assert result["commission"].iloc[0] == pytest.approx(0.035)
    assert result["commission"].iloc[1] == pytest.approx(0.070)

    assert result["cash"].iloc[0] == pytest.approx(cash_after_entry)
    assert result["equity"].iloc[0] == pytest.approx(equity_after_entry)

    assert result["cash"].iloc[1] == pytest.approx(cash_after_reversal)
    assert result["equity"].iloc[1] == pytest.approx(equity_after_reversal)

    # Independent final reconciliation: +$0.80 realized,
    # +$0.80 unrealized, less $0.105 total commissions.
    assert result["equity"].iloc[1] == pytest.approx(10_001.495)

def test_returns_peak_equity_and_drawdown_are_consistent():
    data = make_data(
        positions=[0, 1, 1, 1, 0],
        bid_open=[1.1000, 1.1000, 1.1010, 1.0990, 1.1020],
        ask_open=[1.1002, 1.1002, 1.1012, 1.0992, 1.1022],
        bid_close=[1.1000, 1.1010, 1.0990, 1.1020, 1.1020],
        ask_close=[1.1002, 1.1012, 1.0992, 1.1022, 1.1022],
    )

    initial_cash = 10_000.0

    result = simulate_next_open(
        data,
        initial_cash=initial_cash,
        units=1_000,
    )

    # Independently reconstruct returns from the equity series.
    equity = result["equity"]
    expected_returns = equity.pct_change()
    expected_returns.iloc[0] = equity.iloc[0] / initial_cash - 1

    pd.testing.assert_series_equal(
        result["return"].reset_index(drop=True),
        expected_returns.reset_index(drop=True),
        check_names=False,
    )

    # The running peak must never fall below initial cash.
    expected_peaks = equity.cummax().clip(lower=initial_cash)

    pd.testing.assert_series_equal(
        result["peak_equity"].reset_index(drop=True),
        expected_peaks.reset_index(drop=True),
        check_names=False,
    )

    # Drawdown is measured relative to the running equity peak.
    expected_drawdown = equity / expected_peaks - 1

    pd.testing.assert_series_equal(
        result["drawdown"].reset_index(drop=True),
        expected_drawdown.reset_index(drop=True),
        check_names=False,
    )

    assert (result["drawdown"] <= 0).all()
    assert result["peak_equity"].iloc[0] == pytest.approx(max(initial_cash, equity.iloc[0])
)