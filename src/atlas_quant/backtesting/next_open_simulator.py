
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
    """Simulate fixed-size EUR/USD positions at next-candle open.

    Required columns:
        bid_open, ask_open, bid_close, ask_close, position

    Position at row t is assumed to have been determined using
    information available by the close of row t-1.

    Orders execute at row t's opening bid/ask, with optional
    adverse slippage. Commission is charged on each traded unit
    per side. Open positions are marked to the executable
    closing side of the market.

    This simplified spot-style simulation excludes financing,
    margin, and broker constraints. Spread is accounted for
    through bid/ask prices and is not charged separately.
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

    if not data.index.is_monotonic_increasing:
        raise ValueError("Data must be chronological")

    if data.index.has_duplicates:
        raise ValueError("Duplicate timestamps are not allowed")

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

    for column in [
        "bid_open",
        "ask_open",
        "bid_close",
        "ask_close",
    ]:
        if (result[column] <= 0).any():
            raise ValueError(
                f"{column} prices must be positive"
            )

    if (result["ask_open"] < result["bid_open"]).any():
        raise ValueError("Ask open cannot be below bid open")

    if (result["ask_close"] < result["bid_close"]).any():
        raise ValueError("Ask close cannot be below bid close")

    if not result["position"].isin([-1, 0, 1]).all():
        raise ValueError("Positions must be -1, 0, or 1")

    numeric_settings = {
        "initial_cash": initial_cash,
        "units": units,
        "commission_per_1000_units_per_side_usd":
            commission_per_1000_units_per_side_usd,
        "slippage_pips": slippage_pips,
        "pip_size": pip_size,
    }

    for name, value in numeric_settings.items():
        if not np.isscalar(value) or not np.isfinite(value):
            raise ValueError(f"{name} must be finite")

    if initial_cash <= 0 or units <= 0:
        raise ValueError(
            "Initial cash and units must be positive"
        )

    if commission_per_1000_units_per_side_usd < 0:
        raise ValueError("Commission cannot be negative")

    if slippage_pips < 0:
        raise ValueError("Slippage cannot be negative")

    if pip_size <= 0:
        raise ValueError("Pip size must be positive")

    slippage_price = slippage_pips * pip_size

    # The first row's position was determined before this dataset
    # begins, so exclude it to avoid assuming an earlier signal.
    result = result.iloc[1:].copy()

    if result.empty:
        raise ValueError(
            "At least two observations are required"
        )

    cash = float(initial_cash)
    current_units = 0.0
    records = []

    for timestamp, row in result.iterrows():
        target_units = int(row["position"]) * units

        bid_open = float(row["bid_open"])
        ask_open = float(row["ask_open"])
        bid_close = float(row["bid_close"])
        ask_close = float(row["ask_close"])

        delta_units = target_units - current_units

        # Apply adverse slippage to the execution price.
        if delta_units > 0:
            execution_price = ask_open + slippage_price
            cash -= delta_units * execution_price
        elif delta_units < 0:
            execution_price = bid_open - slippage_price
            cash += (-delta_units) * execution_price

        # Charge commission on every unit traded, including
        # both the closing and opening legs of a reversal.
        commission = (
            abs(delta_units)
            / 1_000.0
            * commission_per_1000_units_per_side_usd
        )
        cash -= commission

        current_units = target_units

        # Mark the open position at its executable close-side price.
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
                "bid_open": bid_open,
                "ask_open": ask_open,
                "bid_close": bid_close,
                "ask_close": ask_close,
                "commission": commission,
                "equity": equity,
            }
        )

    output = pd.DataFrame(records).set_index("time")

    previous_equity = output["equity"].shift(1)
    output["return"] = (
        output["equity"] / previous_equity - 1
    )
    output.iloc[0, output.columns.get_loc("return")] = (
        output["equity"].iloc[0] / initial_cash - 1
    )

    # Include initial cash as a potential equity peak.
    output["peak_equity"] = (
        output["equity"].cummax().clip(lower=initial_cash)
    )
    output["drawdown"] = (
        output["equity"] / output["peak_equity"] - 1
    )

    return output