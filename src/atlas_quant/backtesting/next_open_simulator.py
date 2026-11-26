"""Auditable next-bar-open execution simulator using bid/ask prices."""

import numbers

import numpy as np
import pandas as pd


def simulate_next_open(
    data: pd.DataFrame,
    initial_cash: float = 10_000.0,
    units: float = 1_000.0,
    commission_per_1000_units_per_side_usd: float = 0.0,
    slippage_pips: float = 0.0,
    pip_size: float = 0.0001,
) -> pd.DataFrame:
    """Simulate fixed-size positions at the next available bar open.

    Required columns:
        bid_open, ask_open, bid_close, ask_close, position

    ``position`` is the target exposure effective at that row's open.
    The first row is excluded because its target may have been generated
    before the supplied dataset begins.

    Buys execute at ask and sells at bid. Slippage is applied adversely.
    Open positions are marked at the executable closing side.

    Spread is included in bid/ask prices and is never charged separately.
    Commission is charged per 1,000 units traded, per side.

    This simplified spot-style simulation excludes financing, margin,
    leverage constraints, broker rules, and partial fills.
    """
    required = {
        "bid_open",
        "ask_open",
        "bid_close",
        "ask_close",
        "position",
    }
    missing = required - set(data.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    if data.empty:
        raise ValueError("Data cannot be empty")

    if not isinstance(data.index, pd.DatetimeIndex):
        raise TypeError("Data index must be a DatetimeIndex")

    if data.index.tz is None:
        raise ValueError("Bar timestamps must be timezone-aware")

    if data.index.hasnans:
        raise ValueError("Bar timestamps cannot be missing")

    if not data.index.is_monotonic_increasing:
        raise ValueError("Data must be chronological")

    if data.index.has_duplicates:
        raise ValueError("Duplicate timestamps are not allowed")

    numeric_settings = {
        "initial_cash": initial_cash,
        "units": units,
        "commission_per_1000_units_per_side_usd":
            commission_per_1000_units_per_side_usd,
        "slippage_pips": slippage_pips,
        "pip_size": pip_size,
    }

    for name, value in numeric_settings.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, numbers.Real)
            or not np.isfinite(value)
        ):
            raise ValueError(f"{name} must be a finite number")

    if initial_cash <= 0 or units <= 0:
        raise ValueError("Initial cash and units must be positive")

    if commission_per_1000_units_per_side_usd < 0:
        raise ValueError("Commission cannot be negative")

    if slippage_pips < 0:
        raise ValueError("Slippage cannot be negative")

    if pip_size <= 0:
        raise ValueError("Pip size must be positive")

    result = data.copy()

    numeric_columns = [
        "bid_open",
        "ask_open",
        "bid_close",
        "ask_close",
        "position",
    ]

    for column in numeric_columns:
        result[column] = pd.to_numeric(
            result[column], errors="raise"
        )
        values = result[column].to_numpy(dtype=float)

        if not np.isfinite(values).all():
            raise ValueError(
                f"{column} must contain finite values"
            )

    price_columns = [
        "bid_open",
        "ask_open",
        "bid_close",
        "ask_close",
    ]

    for column in price_columns:
        if (result[column] <= 0).any():
            raise ValueError(f"{column} prices must be positive")

    if (result["ask_open"] < result["bid_open"]).any():
        raise ValueError("Ask open cannot be below bid open")

    if (result["ask_close"] < result["bid_close"]).any():
        raise ValueError("Ask close cannot be below bid close")

    if not result["position"].isin([-1, 0, 1]).all():
        raise ValueError("Positions must be -1, 0, or 1")

    # The first target may have been generated before this dataset.
    # Only targets with a preceding in-dataset completed bar are used.
    result = result.iloc[1:].copy()

    if result.empty:
        raise ValueError("At least two observations are required")

    cash = float(initial_cash)
    current_units = 0.0
    slippage_price = float(slippage_pips * pip_size)
    records = []

    for timestamp, row in result.iterrows():
        target_units = int(row["position"]) * float(units)
        delta_units = target_units - current_units

        bid_open = float(row["bid_open"])
        ask_open = float(row["ask_open"])
        bid_close = float(row["bid_close"])
        ask_close = float(row["ask_close"])

        traded_units = abs(delta_units)
        execution_price = None
        execution_side = None
        slippage_cost = 0.0

        if delta_units > 0:
            execution_side = "buy"
            execution_price = ask_open + slippage_price
            slippage_cost = traded_units * slippage_price
            cash -= traded_units * execution_price

        elif delta_units < 0:
            execution_side = "sell"
            execution_price = bid_open - slippage_price
            slippage_cost = traded_units * slippage_price
            cash += traded_units * execution_price

        commission = (
            traded_units
            / 1_000.0
            * float(commission_per_1000_units_per_side_usd)
        )
        cash -= commission

        current_units = target_units

        if current_units > 0:
            liquidation_value = current_units * bid_close
        elif current_units < 0:
            liquidation_value = current_units * ask_close
        else:
            liquidation_value = 0.0

        equity = cash + liquidation_value

        records.append(
            {
                "time": timestamp,
                "position": int(row["position"]),
                "cash": cash,
                "units": current_units,
                "traded_units": traded_units,
                "execution_side": execution_side,
                "execution_price": execution_price,
                "slippage_cost": slippage_cost,
                "commission": commission,
                "bid_open": bid_open,
                "ask_open": ask_open,
                "bid_close": bid_close,
                "ask_close": ask_close,
                "equity": equity,
            }
        )

    output = pd.DataFrame(records).set_index("time")

    previous_equity = output["equity"].shift(1)
    output["return"] = output["equity"] / previous_equity - 1
    output.iloc[0, output.columns.get_loc("return")] = (
        output["equity"].iloc[0] / initial_cash - 1
    )

    output["peak_equity"] = (
        output["equity"].cummax().clip(lower=initial_cash)
    )
    output["drawdown"] = (
        output["equity"] / output["peak_equity"] - 1
    )

    return output