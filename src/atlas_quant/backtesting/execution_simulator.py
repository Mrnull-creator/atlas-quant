
import numpy as np
import pandas as pd


def simulate_execution(
    data: pd.DataFrame,
    initial_cash: float = 10_000.0,
    units: float = 1_000.0,
) -> pd.DataFrame:
    """Simulate EUR/USD positions using historical bid/ask closes.

    Required columns:
        bid_close: executable sell price
        ask_close: executable buy price
        position: target direction, -1, 0, or 1

    Signals are assumed to be available before the close at which
    the target position is executed. This is a simplified research
    assumption, not a guarantee of actual broker execution.

    Long positions are marked at bid; short positions at ask.
    Commission, slippage, financing, margin, and rejected orders
    are not modeled.
    """
    required = {"bid_close", "ask_close", "position"}
    missing = required - set(data.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    if data.empty:
        raise ValueError("Data cannot be empty")

    if not data.index.is_monotonic_increasing:
        raise ValueError("Data must be chronological")

    result = data.copy()

    for column in ["bid_close", "ask_close", "position"]:
        result[column] = pd.to_numeric(
            result[column], errors="raise"
        )
        values = result[column].to_numpy(dtype=float)

        if not np.isfinite(values).all():
            raise ValueError(f"{column} must contain finite values")

    if (result["bid_close"] <= 0).any():
        raise ValueError("Bid prices must be positive")

    if (result["ask_close"] <= 0).any():
        raise ValueError("Ask prices must be positive")

    if (result["ask_close"] < result["bid_close"]).any():
        raise ValueError("Ask cannot be below bid")

    if not result["position"].isin([-1, 0, 1]).all():
        raise ValueError("Positions must be -1, 0, or 1")

    if initial_cash <= 0 or units <= 0:
        raise ValueError("Initial cash and units must be positive")

    # We hold the target position after each candle's close.
    # Cash changes only when opening, closing, or reversing.
    cash = float(initial_cash)
    current_units = 0.0
    records = []

    for timestamp, row in result.iterrows():
        target = int(row["position"])
        bid = float(row["bid_close"])
        ask = float(row["ask_close"])

        target_units = target * units
        delta_units = target_units - current_units

        # Positive delta buys at ask; negative delta sells at bid.
        if delta_units > 0:
            cash -= delta_units * ask
        elif delta_units < 0:
            cash += (-delta_units) * bid

        current_units = target_units

        # Liquidation value uses the executable side of the market.
        if current_units > 0:
            liquidation_value = current_units * bid
        elif current_units < 0:
            liquidation_value = current_units * ask
        else:
            liquidation_value = 0.0

        equity = cash + liquidation_value

        records.append(
            {
                "time": timestamp,
                "position": target,
                "cash": cash,
                "units": current_units,
                "bid_close": bid,
                "ask_close": ask,
                "equity": equity,
            }
        )

    output = pd.DataFrame(records).set_index("time")
    output["return"] = output["equity"].pct_change().fillna(
        output["equity"].iloc[0] / initial_cash - 1
    )
    output["peak_equity"] = output["equity"].cummax()
    output["drawdown"] = (
        output["equity"] / output["peak_equity"] - 1
    )

    return output