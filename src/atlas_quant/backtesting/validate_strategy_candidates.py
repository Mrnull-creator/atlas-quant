
from pathlib import Path

import pandas as pd

from atlas_quant.backtesting.next_open_simulator import simulate_next_open
from atlas_quant.strategies.momentum import StrategyConfig, generate_signals


ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = (
    ROOT / "data" / "external" / "dukascopy" / "m1"
    / "Forex" / "Majors" / "EUR-USD"
)
OUTPUT_FILE = ROOT / "data" / "processed" / "strategy_candidate_validation.csv"

INITIAL_CASH = 10_000.0
FIXED_UNITS = 1_000.0

TRAIN_START = pd.Timestamp("2024-01-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-01-01", tz="UTC")
DATA_END = pd.Timestamp("2026-10-14", tz="UTC")


def load_market_data():
    """Load matching historical bid/ask candles."""
    bid_files = sorted((DATA_ROOT / "BID").glob("*.parquet"))
    ask_files = sorted((DATA_ROOT / "ASK").glob("*.parquet"))

    if not bid_files or not ask_files:
        raise FileNotFoundError(f"Bid/ask Parquet files not found under {DATA_ROOT}")

    bid = pd.concat([pd.read_parquet(p) for p in bid_files])
    ask = pd.concat([pd.read_parquet(p) for p in ask_files])

    for frame in (bid, ask):
        if "time" not in frame.columns:
            raise ValueError("Expected a 'time' column in each Parquet file.")
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame.set_index("time", inplace=True)
        frame.sort_index(inplace=True)

    fields = ["open", "high", "low", "close"]
    for name, frame in (("bid", bid), ("ask", ask)):
        missing = set(fields) - set(frame.columns)
        if missing:
            raise ValueError(f"{name} data missing columns: {sorted(missing)}")

    bid = bid[fields].add_prefix("bid_")
    ask = ask[fields].add_prefix("ask_")

    data = bid.join(ask, how="inner").sort_index()
    data = data[~data.index.duplicated(keep="last")]
    data = data.loc[(data.index >= TRAIN_START) & (data.index < DATA_END)]

    if data.empty:
        raise ValueError("No matched market data in the requested date range.")

    if (data["ask_open"] < data["bid_open"]).any():
        raise ValueError("Ask open is below bid open.")
    if (data["ask_close"] < data["bid_close"]).any():
        raise ValueError("Ask close is below bid close.")

    data["mid_close"] = (data["bid_close"] + data["ask_close"]) / 2
    print(f"Loaded {len(data):,} matched observations.")
    print(f"Available data: {data.index.min()} through {data.index.max()}")
    return data


def simulate_period(data, positions, period_name):
    """Run the main simulator on one independently initialized date period."""
    if period_name == "Research 2024-2025":
        start, end = TRAIN_START, HOLDOUT_START
    elif period_name == "Holdout 2026":
        start, end = HOLDOUT_START, DATA_END
    else:
        raise ValueError(f"Unknown period: {period_name}")

    period = data.loc[(data.index >= start) & (data.index < end)].copy()
    period["position"] = positions.reindex(period.index).fillna(0).astype(int)

    if len(period) < 2:
        raise ValueError(f"Not enough observations for {period_name}.")

    # This uses the same fixed-unit, next-open execution model as the main
    # backtester. The first row is dropped by simulate_next_open.
    result = simulate_next_open(
        period,
        initial_cash=INITIAL_CASH,
        units=FIXED_UNITS,
    )

    equity = result["equity"]
    if equity.empty:
        raise ValueError(f"No equity results for {period_name}.")

    peak = equity.cummax().clip(lower=INITIAL_CASH)
    drawdown = equity / peak - 1.0

    changes = int(result["position"].diff().fillna(result["position"].iloc[0]).ne(0).sum())

    return {
        "strategy": "",
        "period": period_name,
        "start": str(start.date()),
        "end_exclusive": str(end.date()),
        "observations": len(period),
        "final_equity": round(float(equity.iloc[-1]), 2),
        "return_pct": round((float(equity.iloc[-1]) / INITIAL_CASH - 1) * 100, 2),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "position_changes": changes,
    }


def main():
    print("Loading historical bid/ask data...")
    data = load_market_data()
    midpoint = data["mid_close"]

    print("Generating strategy positions...")
    candidates = {
        "Momentum 10/30": generate_signals(
            midpoint, StrategyConfig(fast_period=10, slow_period=30)
        )["position"],
        "Momentum 30/100": generate_signals(
            midpoint, StrategyConfig(fast_period=30, slow_period=100)
        )["position"],
    }



    # Generate a breakout signal at candle close using the
    # previous 20 completed candles' midpoint highs and lows.
    midpoint_high = (
        data["bid_high"] + data["ask_high"]
    ) / 2
    midpoint_low = (
        data["bid_low"] + data["ask_low"]
    ) / 2

    previous_high = midpoint_high.rolling(20).max().shift(1)
    previous_low = midpoint_low.rolling(20).min().shift(1)

    breakout_signal = pd.Series(0, index=data.index, dtype=int)
    breakout_signal[midpoint > previous_high] = 1
    breakout_signal[midpoint < previous_low] = -1

    # Execute the close-generated signal at the next candle's open.
    candidates["Breakout 20"] = (
        breakout_signal.shift(1).fillna(0).astype(int)
    )

    rows = []
    for strategy_name, positions in candidates.items():
        for period_name in ("Research 2024-2025", "Holdout 2026"):
            row = simulate_period(data, positions, period_name)
            row["strategy"] = strategy_name
            rows.append(row)

    results = pd.DataFrame(rows)
    results = results[
        [
            "strategy", "period", "start", "end_exclusive", "observations",
            "final_equity", "return_pct", "max_drawdown_pct", "position_changes",
        ]
    ]

    print("\nVALIDATION RESULTS")
    print(results.to_string(index=False))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved results to: {OUTPUT_FILE}")
    print(f"Position size: {FIXED_UNITS:,.0f} EUR per signal.")
    print("Each period starts with $10,000 and no position.")
    print("This simulator excludes margin, financing, commissions and slippage.")

if __name__ == "__main__":
    main()