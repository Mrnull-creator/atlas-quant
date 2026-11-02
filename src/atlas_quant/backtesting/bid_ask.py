
import numpy as np
import pandas as pd


PIP_SIZE = 0.0001


def run_bid_ask_backtest(data: pd.DataFrame) -> pd.DataFrame:
    """Run a simplified midpoint backtest with spread costs.

    Required columns:
        mid_close: midpoint of bid and ask
        spread_pips: observed spread in pips
        position: target position (-1, 0, or 1)

    Position at row t applies to the return from close t
    to close t+1. Spread costs are approximated at each
    position change using the spread observed at close t.
    """
    required = {"mid_close", "spread_pips", "position"}
    missing = required - set(data.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    if data.empty:
        raise ValueError("Backtest data cannot be empty")

    if not data.index.is_monotonic_increasing:
        raise ValueError("Data must be chronological")

    result = data.copy()

    for column in ["mid_close", "spread_pips", "position"]:
        result[column] = pd.to_numeric(
            result[column], errors="raise"
        )

        values = result[column].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{column} must contain finite values")

    if (result["mid_close"] <= 0).any():
        raise ValueError("Midpoint prices must be positive")

    if (result["spread_pips"] < 0).any():
        raise ValueError("Spreads cannot be negative")

    if not result["position"].isin([-1, 0, 1]).all():
        raise ValueError("Positions must be -1, 0, or 1")

    result["market_return"] = (
        result["mid_close"].shift(-1) / result["mid_close"] - 1
    )

    previous_position = result["position"].shift(
        1, fill_value=0
    )

    result["turnover"] = (
        result["position"] - previous_position
    ).abs()

    half_spread_fraction = (
        result["spread_pips"] * PIP_SIZE
        / (2 * result["mid_close"])
    )

    result["spread_cost"] = (
        result["turnover"] * half_spread_fraction
    )

    result["strategy_return"] = (
        result["position"] * result["market_return"]
        - result["spread_cost"]
    )

    # The last candle has no next close for a realized return.
    result = result.iloc[:-1].copy()

    result["equity"] = (
        1 + result["strategy_return"]
    ).cumprod()

    return result