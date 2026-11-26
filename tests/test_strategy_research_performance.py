import math

import pandas as pd
import pytest

from atlas_quant.services.strategy_research.application.performance import (
    calculate_performance,
)


def _results(
    equity=None,
    positions=None,
    units=None,
    traded_units=None,
    commissions=None,
    slippage=None,
):
    equity = equity or [10_100.0, 10_200.0, 9_900.0, 10_300.0]
    positions = positions or [1.0, 1.0, -1.0, 0.0]
    units = units or [1_000.0, 1_000.0, -1_000.0, 0.0]
    traded_units = traded_units or [1_000.0, 0.0, 2_000.0, 1_000.0]
    commissions = commissions or [0.035, 0.0, 0.070, 0.035]
    slippage = slippage or [0.10, 0.0, 0.20, 0.10]

    return pd.DataFrame(
        {
            "equity": equity,
            "position": positions,
            "units": units,
            "traded_units": traded_units,
            "execution_price": [1.101, float("nan"), 1.102, 1.103],
            "commission": commissions,
            "slippage_cost": slippage,
            "bid_open": [1.1000, 1.1010, 1.1020, 1.1030],
            "ask_open": [1.1002, 1.1012, 1.1022, 1.1032],
            "bid_close": [1.1001, 1.1011, 1.1021, 1.1031],
            "ask_close": [1.1003, 1.1013, 1.1023, 1.1033],
        },
        index=pd.date_range(
            "2026-01-01",
            periods=4,
            freq="min",
            tz="UTC",
        ),
    )


def test_calculates_equity_and_return_metrics():
    metrics = calculate_performance(
        _results(),
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.observations == 4
    assert metrics.initial_equity == 10_000.0
    assert metrics.final_equity == 10_300.0
    assert metrics.net_profit_loss == 300.0
    assert metrics.total_return == pytest.approx(0.03)
    assert metrics.max_drawdown == pytest.approx(9_900 / 10_200 - 1)
    assert metrics.max_drawdown_duration_periods == 1


def test_reversal_counts_as_closing_one_trade_and_opening_another():
    metrics = calculate_performance(
        _results(),
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.trade_count == 2
    assert metrics.open_trade_count == 0


def test_calculates_execution_cost_attribution_without_reducing_equity_again():
    metrics = calculate_performance(
        _results(),
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.spread_cost == pytest.approx(0.4)
    assert metrics.slippage_cost == pytest.approx(0.4)
    assert metrics.commission_cost == pytest.approx(0.14)
    assert metrics.total_execution_cost == pytest.approx(0.94)
    assert metrics.final_equity == 10_300.0


def test_calculates_turnover_using_midpoint_not_execution_price():
    metrics = calculate_performance(
        _results(),
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    expected_notional = (
        1_000 * 1.1001
        + 2_000 * 1.1021
        + 1_000 * 1.1031
    )

    assert metrics.total_turnover == pytest.approx(
        expected_notional / 10_000.0
    )
    assert metrics.turnover_per_period == pytest.approx(
        metrics.total_turnover / 4
    )


def test_open_position_is_reported_at_end_of_backtest():
    results = _results(
        positions=[1.0, 1.0, 1.0, 1.0],
        units=[1_000.0] * 4,
        traded_units=[1_000.0, 0.0, 0.0, 0.0],
        commissions=[0.035, 0.0, 0.0, 0.0],
        slippage=[0.10, 0.0, 0.0, 0.0],
    )

    metrics = calculate_performance(
        results,
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.trade_count == 0
    assert metrics.open_trade_count == 1


def test_no_trade_results_have_zero_exposure_and_turnover():
    results = _results(
        equity=[10_000.0] * 4,
        positions=[0.0] * 4,
        units=[0.0] * 4,
        traded_units=[0.0] * 4,
        commissions=[0.0] * 4,
        slippage=[0.0] * 4,
    )

    metrics = calculate_performance(
        results,
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.trade_count == 0
    assert metrics.open_trade_count == 0
    assert metrics.average_exposure == 0.0
    assert metrics.max_exposure == 0.0
    assert metrics.total_turnover == 0.0
    assert metrics.turnover_per_period == 0.0
    assert metrics.profit_factor is None


def test_empty_results_return_safe_metrics():
    metrics = calculate_performance(
        pd.DataFrame(),
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.observations == 0
    assert metrics.final_equity == 10_000.0
    assert metrics.net_profit_loss == 0.0
    assert metrics.total_return == 0.0
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None
    assert metrics.profit_factor is None
    assert metrics.average_exposure is None
    assert metrics.turnover_per_period is None


def test_sharpe_and_sortino_are_undefined_when_returns_have_no_variation():
    results = _results(
        equity=[10_000.0] * 4,
        positions=[0.0] * 4,
        units=[0.0] * 4,
        traded_units=[0.0] * 4,
        commissions=[0.0] * 4,
        slippage=[0.0] * 4,
    )

    metrics = calculate_performance(
        results,
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None


def test_profit_factor_is_undefined_when_there_are_no_losses():
    results = _results(
        equity=[10_100.0, 10_200.0, 10_300.0, 10_400.0],
        positions=[0.0] * 4,
        units=[0.0] * 4,
        traded_units=[0.0] * 4,
        commissions=[0.0] * 4,
        slippage=[0.0] * 4,
    )

    metrics = calculate_performance(
        results,
        initial_equity=10_000.0,
        periods_per_year=252,
    )

    assert metrics.gross_period_profit > 0
    assert metrics.gross_period_loss == 0
    assert metrics.profit_factor is None


def test_rejects_naive_timestamps():
    results = _results()
    results.index = results.index.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_performance(
            results,
            initial_equity=10_000.0,
            periods_per_year=252,
        )


def test_rejects_invalid_periods_per_year():
    with pytest.raises(ValueError, match="positive integer"):
        calculate_performance(
            _results(),
            initial_equity=10_000.0,
            periods_per_year=0,
        )


def test_rejects_invalid_risk_free_rate():
    with pytest.raises(ValueError, match="greater than -1"):
        calculate_performance(
            _results(),
            initial_equity=10_000.0,
            periods_per_year=252,
            risk_free_rate_annual=-1.0,
        )


def test_rejects_negative_execution_costs():
    results = _results()
    results.loc[results.index[0], "commission"] = -0.01

    with pytest.raises(ValueError, match="negative costs"):
        calculate_performance(
            results,
            initial_equity=10_000.0,
            periods_per_year=252,
        )


def test_rejects_invalid_position_values():
    results = _results()
    results.loc[results.index[0], "position"] = 2.0

    with pytest.raises(ValueError, match="-1, 0, or 1"):
        calculate_performance(
            results,
            initial_equity=10_000.0,
            periods_per_year=252,
        )
