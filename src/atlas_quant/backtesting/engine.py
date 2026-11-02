import numpy as np
import pandas as pd


def run_backtest(
    data: pd.DataFrame,
    spread_bps: float = 1.0,
    commission_bps_per_side: float = 0.0,
) -> pd.DataFrame:
    """Backtest a signal-based strategy on OHLC candles.

    Required columns:
        close: closing price
        position: target position, -1, 0, or 1

    The position on each row applies to the return from
    that row's close to the next row's close.

    Costs are simplified estimates in basis points of
    price notional. This is a research approximation,
    not a broker-accurate execution simulator.
    """
    required = {"close", "position"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    if data.empty:
        raise ValueError("Backtest data cannot be empty")

    if spread_bps < 0 or commission_bps_per_side < 0:
        raise ValueError("Trading costs cannot be negative")

    result = data.copy()

    if not result.index.is_monotonic_increasing:
        raise ValueError("Data must be chronological")

    close = pd.to_numeric(result["close"], errors="raise")
    position = pd.to_numeric(
        result["position"], errors="raise"
    )

    if not np.isfinite(close.to_numpy(dtype=float)).all():
        raise ValueError("Close prices must be finite")

    if (close <= 0).any():
        raise ValueError("Close prices must be positive")

    if not np.isfinite(position.to_numpy(dtype=float)).all():
        raise ValueError("Positions must be finite")

    if not position.isin([-1, 0, 1]).all():
        raise ValueError("Positions must be -1, 0, or 1")

    result["close"] = close
    result["position"] = position

    # Return earned from this close to the next close.
    result["market_return"] = close.shift(-1).div(close) - 1

    # Turnover estimates position changes, including entry,
    # exit, and reversal.
    previous_position = position.shift(1, fill_value=0)
    result["turnover"] = (position - previous_position).abs()

    # Spread is approximated as a full-spread cost on each
    # unit of turnover. Commission is charged per side.
    cost_per_unit = (
    spread_bps / 2 + commission_bps_per_side
) / 10_000

    result["trading_cost"] = result["turnover"] * cost_per_unit

    result["strategy_return"] = (
        result["position"] * result["market_return"]
        - result["trading_cost"]
    )

    # Last row has no next close, so it cannot have a realized
    # close-to-close return.
    result = result.iloc[:-1].copy()

    result["equity"] = (1 + result["strategy_return"]).cumprod()

    return result


def summarize_backtest(results: pd.DataFrame) -> dict:
    """Return basic performance statistics for a backtest."""
    if results.empty:
        raise ValueError("Backtest results cannot be empty")

    returns = results["strategy_return"]
    equity = results["equity"]

    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1

    return {
        "observations": int(len(results)),
        "total_return_pct": float((equity.iloc[-1] - 1) * 100),
        "max_drawdown_pct": float(drawdown.min() * 100),
        "profitable_bar_pct": float((returns > 0).mean() * 100),
    }