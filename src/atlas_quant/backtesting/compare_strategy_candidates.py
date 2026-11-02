
from pathlib import Path

import numpy as np
import pandas as pd


DATA_DIR = Path(
    "data/external/dukascopy/m1/Forex/Majors/EUR-USD"
)
OUTPUT_DIR = Path("data/processed")
OUTPUT_FILE = OUTPUT_DIR / "strategy_candidate_comparison.csv"

INITIAL_CASH = 10_000.0
UNITS = 1_000.0


def load_side(side):
    frames = []

    for file_path in sorted((DATA_DIR / side).glob("*.parquet")):
        frame = pd.read_parquet(file_path)

        if "time" in frame.columns:
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
            frame = frame.set_index("time")
        else:
            frame.index = pd.to_datetime(frame.index, utc=True)

        frames.append(frame[["open", "close"]])

    if not frames:
        raise FileNotFoundError(f"No data files found for {side}")

    result = pd.concat(frames).sort_index()

    if result.index.has_duplicates:
        raise ValueError(f"Duplicate timestamps in {side} data")

    return result


def momentum_position(prices, fast, slow):
    fast_ma = prices.rolling(fast, min_periods=fast).mean()
    slow_ma = prices.rolling(slow, min_periods=slow).mean()

    signal = pd.Series(
        np.where(fast_ma > slow_ma, 1,
                 np.where(fast_ma < slow_ma, -1, 0)),
        index=prices.index,
    )

    ready = fast_ma.notna() & slow_ma.notna()
    signal.loc[~ready] = 0

    return signal.shift(1, fill_value=0).astype(int)


def breakout_position(prices, window):
    # Prior candles only: today's signal does not use today's close.
    prior_high = prices.shift(1).rolling(
        window, min_periods=window
    ).max()
    prior_low = prices.shift(1).rolling(
        window, min_periods=window
    ).min()

    signal = pd.Series(0, index=prices.index, dtype=int)
    signal.loc[prices > prior_high] = 1
    signal.loc[prices < prior_low] = -1

    ready = prior_high.notna() & prior_low.notna()
    signal.loc[~ready] = 0

    # Hold the last signal between breakouts.
    signal = signal.replace(0, np.nan).ffill().fillna(0)

    return signal.shift(1, fill_value=0).astype(int)


def simulate(data, position):
    # Exclude the first row, matching the existing simulator convention.
    frame = data.iloc[1:].copy()
    position = position.reindex(frame.index).astype(int)

    cash = INITIAL_CASH
    current_units = 0.0
    equity_values = []
    changes = []
    spread_costs = []

    for timestamp, row in frame.iterrows():
        target_units = int(position.loc[timestamp]) * UNITS
        delta_units = target_units - current_units

        # Buy at ask, sell at bid.
        if delta_units > 0:
            cash -= delta_units * float(row["ask_open"])
        elif delta_units < 0:
            cash += (-delta_units) * float(row["bid_open"])

        # Estimate execution cost against the opening mid-price.
        mid_open = (
            float(row["ask_open"]) + float(row["bid_open"])
        ) / 2
        cost = abs(delta_units) * (
            float(row["ask_open"]) - float(row["bid_open"])
        ) / 2

        current_units = target_units

        if current_units > 0:
            liquidation_value = current_units * float(row["bid_close"])
        elif current_units < 0:
            liquidation_value = current_units * float(row["ask_close"])
        else:
            liquidation_value = 0.0

        equity_values.append(cash + liquidation_value)
        changes.append(delta_units != 0)
        spread_costs.append(cost)

    equity = pd.Series(equity_values, index=frame.index)
    peak = equity.cummax().clip(lower=INITIAL_CASH)
    drawdown = equity / peak - 1

    return {
        "final_equity": float(equity.iloc[-1]),
        "return_pct": (float(equity.iloc[-1]) / INITIAL_CASH - 1) * 100,
        "max_drawdown_pct": float(drawdown.min()) * 100,
        "position_changes": int(sum(changes)),
        "estimated_spread_cost": float(sum(spread_costs)),
        "long_pct": float((position.loc[frame.index] == 1).mean()) * 100,
        "short_pct": float((position.loc[frame.index] == -1).mean()) * 100,
        "flat_pct": float((position.loc[frame.index] == 0).mean()) * 100,
    }


def main():
    print("Loading historical bid/ask data...")

    bid = load_side("BID").rename(
        columns={"open": "bid_open", "close": "bid_close"}
    )
    ask = load_side("ASK").rename(
        columns={"open": "ask_open", "close": "ask_close"}
    )

    data = bid.join(ask, how="inner").sort_index()

    if data.empty:
        raise ValueError("No matching bid/ask observations")

    if (data["ask_open"] < data["bid_open"]).any():
        raise ValueError("Ask open is below bid open")
    if (data["ask_close"] < data["bid_close"]).any():
        raise ValueError("Ask close is below bid close")

    mid_close = (data["bid_close"] + data["ask_close"]) / 2

    candidates = {
        "Momentum 10/30": momentum_position(mid_close, 10, 30),
        "Momentum 30/100": momentum_position(mid_close, 30, 100),
        "Breakout 20": breakout_position(mid_close, 20),
    }

    rows = []

    print(f"Matched observations: {len(data):,}")
    print(f"First timestamp: {data.index.min()}")
    print(f"Last timestamp:  {data.index.max()}")
    print("\nRunning candidate simulations...")

    for name, position in candidates.items():
        metrics = simulate(data, position)
        metrics["strategy"] = name
        rows.append(metrics)

    result = pd.DataFrame(rows).set_index("strategy")
    result = result[
        [
            "final_equity",
            "return_pct",
            "max_drawdown_pct",
            "position_changes",
            "estimated_spread_cost",
            "long_pct",
            "short_pct",
            "flat_pct",
        ]
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_FILE)

    print("\n========== STRATEGY CANDIDATES ==========")
    print(result.to_string(
        formatters={
            "final_equity": "${:,.2f}".format,
            "return_pct": "{:.2f}%".format,
            "max_drawdown_pct": "{:.2f}%".format,
            "estimated_spread_cost": "${:,.2f}".format,
            "long_pct": "{:.2f}%".format,
            "short_pct": "{:.2f}%".format,
            "flat_pct": "{:.2f}%".format,
        }
    ))
    print(f"\nSaved comparison to: {OUTPUT_FILE}")
    print(
        "\nResearch note: These are in-sample results, not proof of "
        "future profitability. Costs exclude slippage, financing, "
        "commissions, and broker constraints."
    )


if __name__ == "__main__":
    main()