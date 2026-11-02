from pathlib import Path

import pandas as pd

from atlas_quant.backtesting.execution_simulator import (
    simulate_execution,
)
from atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals,
)


DATA_DIR = Path("data/external/dukascopy/m1/Forex/Majors/EUR-USD")
OUTPUT_DIR = Path("data/processed")
OUTPUT_FILE = OUTPUT_DIR / "eurusd_m1_execution_backtest.csv"

INITIAL_CASH = 10_000.0
UNITS = 1_000.0


def load_side(side: str) -> pd.DataFrame:
    """Load all monthly Parquet files for one quote side."""
    side_dir = DATA_DIR / side
    files = sorted(side_dir.glob("*.parquet"))

    if not files:
        raise FileNotFoundError(
            f"No Parquet files found in {side_dir}"
        )

    frames = []

    for file_path in files:
        frame = pd.read_parquet(file_path)

        if "time" in frame.columns:
            frame["time"] = pd.to_datetime(
                frame["time"], utc=True
            )
            frame = frame.set_index("time")
        else:
            frame.index = pd.to_datetime(
                frame.index, utc=True
            )

        if "close" not in frame.columns:
            raise ValueError(
                f"'close' column missing from {file_path}"
            )

        frames.append(frame[["close"]])

    combined = pd.concat(frames).sort_index()

    if combined.index.has_duplicates:
        raise ValueError(
            f"Duplicate timestamps found in {side} data"
        )

    return combined


def main() -> None:
    print("Loading historical bid and ask data...")

    bid = load_side("BID").rename(
        columns={"close": "bid_close"}
    )
    ask = load_side("ASK").rename(
        columns={"close": "ask_close"}
    )

    # Keep only timestamps present on both quote sides.
    data = bid.join(ask, how="inner").sort_index()

    if data.empty:
        raise ValueError("No matching bid/ask observations")

    if (data["ask_close"] < data["bid_close"]).any():
        raise ValueError("Found ask prices below bid prices")

    data["mid_close"] = (
        data["bid_close"] + data["ask_close"]
    ) / 2

    data["spread_pips"] = (
        data["ask_close"] - data["bid_close"]
    ) / 0.0001

    print(f"Matched observations: {len(data):,}")
    print(f"First timestamp: {data.index.min()}")
    print(f"Last timestamp:  {data.index.max()}")
    print(
        "Median spread: "
        f"{data['spread_pips'].median():.2f} pips"
    )

    print("\nGenerating strategy positions...")

    config = StrategyConfig(
        fast_period=10,
        slow_period=30,
    )

    signals = generate_signals(data["mid_close"], config)

    if "position" not in signals.columns:
        raise ValueError(
            "Strategy output has no 'position' column. "
            f"Available columns: {list(signals.columns)}"
        )

    data["position"] = signals["position"]

    # Simulate fixed-size positions using executable bid/ask prices.
    print("Running execution simulation...")

    results = simulate_execution(
        data[["bid_close", "ask_close", "position"]],
        initial_cash=INITIAL_CASH,
        units=UNITS,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_FILE)

    final_equity = float(results["equity"].iloc[-1])
    total_return = final_equity / INITIAL_CASH - 1
    max_drawdown = float(results["drawdown"].min())

    position_changes = int(
        data["position"].diff().fillna(
            data["position"].iloc[0]
        ).ne(0).sum()
    )

    print("\n========== EXECUTION BACKTEST ==========")
    print(f"Initial cash:       ${INITIAL_CASH:,.2f}")
    print(f"Fixed position:     {UNITS:,.0f} EUR units")
    print(f"Final equity:       ${final_equity:,.2f}")
    print(f"Total return:       {total_return:.2%}")
    print(f"Maximum drawdown:   {max_drawdown:.2%}")
    print(f"Position changes:   {position_changes:,}")
    print(f"Observations:       {len(results):,}")
    print(f"Results saved to:   {OUTPUT_FILE}")
    print("========================================")


if __name__ == "__main__":
    main()