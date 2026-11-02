
from pathlib import Path

import pandas as pd

from atlas_quant.backtesting.next_open_simulator import (
    simulate_next_open,
)
from atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals,
)


DATA_DIR = Path(
    "data/external/dukascopy/m1/Forex/Majors/EUR-USD"
)
OUTPUT_DIR = Path("data/processed")
OUTPUT_FILE = OUTPUT_DIR / "eurusd_m1_next_open_backtest.csv"

INITIAL_CASH = 10_000.0
UNITS = 1_000.0


def load_side(side: str) -> pd.DataFrame:
    """Load and validate monthly historical quote files."""
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

        required = {"open", "close"}
        missing = required - set(frame.columns)

        if missing:
            raise ValueError(
                f"{file_path.name} is missing: {sorted(missing)}"
            )

        frames.append(frame[["open", "close"]])

    combined = pd.concat(frames).sort_index()

    if combined.index.has_duplicates:
        raise ValueError(
            f"Duplicate timestamps found in {side} data"
        )

    return combined


def main() -> None:
    print("Loading historical bid and ask quotes...")

    bid = load_side("BID").rename(
        columns={
            "open": "bid_open",
            "close": "bid_close",
        }
    )

    ask = load_side("ASK").rename(
        columns={
            "open": "ask_open",
            "close": "ask_close",
        }
    )

    # Only use timestamps present on both quote sides.
    data = bid.join(ask, how="inner").sort_index()

    if data.empty:
        raise ValueError("No matching bid/ask observations")

    if (data["ask_open"] < data["bid_open"]).any():
        raise ValueError("Ask open is below bid open")

    if (data["ask_close"] < data["bid_close"]).any():
        raise ValueError("Ask close is below bid close")

    print(f"Matched candles: {len(data):,}")
    print(f"First candle:    {data.index.min()}")
    print(f"Last candle:     {data.index.max()}")

    # Use mid-closes to calculate the strategy signal.
    mid_close = (
        data["bid_close"] + data["ask_close"]
    ) / 2

    config = StrategyConfig(
        fast_period=10,
        slow_period=30,
    )

    signals = generate_signals(mid_close, config)
    data["position"] = signals["position"]

    print("Running next-open execution simulation...")

    results = simulate_next_open(
        data[
            [
                "bid_open",
                "ask_open",
                "bid_close",
                "ask_close",
                "position",
            ]
        ],
        initial_cash=INITIAL_CASH,
        units=UNITS,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_FILE)

    final_equity = float(results["equity"].iloc[-1])
    total_return = final_equity / INITIAL_CASH - 1
    max_drawdown = float(results["drawdown"].min())

    position_changes = int(
        results["position"].diff().fillna(
            results["position"].iloc[0]
        ).ne(0).sum()
    )

    print("\n========== NEXT-OPEN BACKTEST ==========")
    print(f"Initial cash:      ${INITIAL_CASH:,.2f}")
    print(f"Position size:     {UNITS:,.0f} EUR units")
    print(f"Final equity:      ${final_equity:,.2f}")
    print(f"Total return:      {total_return:.2%}")
    print(f"Maximum drawdown:  {max_drawdown:.2%}")
    print(f"Position changes:  {position_changes:,}")
    print(f"Result rows:       {len(results):,}")
    print(f"Saved to:          {OUTPUT_FILE}")
    print("========================================")


if __name__ == "__main__":
    main()