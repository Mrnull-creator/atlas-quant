import pandas as pd
import pytest

from src.atlas_quant.backtesting.engine import (
    run_backtest,
    summarize_backtest,
)


def test_backtest_calculates_returns():
    data = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0],
            "position": [1, 1, 1],
        }
    )

    result = run_backtest(data, spread_bps=0)

    assert len(result) == 2
    assert result["strategy_return"].iloc[0] == pytest.approx(0.01)
    assert result["strategy_return"].iloc[1] == pytest.approx(
        102 / 101 - 1
    )


def test_transaction_cost_on_entry():
    data = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0],
            "position": [1, 1, 1],
        }
    )

    result = run_backtest(data, spread_bps=2)

    assert result["trading_cost"].iloc[0] == pytest.approx(0.0001)


def test_invalid_position_rejected():
    data = pd.DataFrame(
        {
            "close": [100.0, 101.0],
            "position": [0, 2],
        }
    )

    with pytest.raises(ValueError):
        run_backtest(data)


def test_summary_contains_metrics():
    data = pd.DataFrame(
        {
            "close": [100.0, 101.0, 100.5],
            "position": [1, 1, 0],
        }
    )

    results = run_backtest(data, spread_bps=0)
    summary = summarize_backtest(results)

    assert "total_return_pct" in summary
    assert "max_drawdown_pct" in summary
    assert summary["observations"] == 2