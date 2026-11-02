
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data" / "external" / "dukascopy"

PRICE_COLUMNS = ["open", "high", "low", "close"]


def load_side(side):
    files = sorted(
        (DATA_DIR / "m1" / "Forex" / "Majors" / "EUR-USD" / side)
        .glob("*.parquet")
    )

    if not files:
        raise FileNotFoundError(f"No {side} Parquet files found")

    frames = []
    for path in files:
        frame = pd.read_parquet(path)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frames.append(frame)

    data = pd.concat(frames, ignore_index=True)
    data = data.sort_values("time").reset_index(drop=True)

    return data, files


def inspect_side(data, side):
    print(f"\n{side} DATA QUALITY")
    print("-" * 45)
    print(f"Rows: {len(data):,}")
    print(f"First timestamp: {data['time'].min()}")
    print(f"Last timestamp:  {data['time'].max()}")
    print(f"Duplicate timestamps: {data['time'].duplicated().sum():,}")
    print(f"Missing values in OHLC: {data[PRICE_COLUMNS].isna().sum().sum():,}")

    prices = data[PRICE_COLUMNS].to_numpy(dtype=float)
    finite = np.isfinite(prices).all(axis=1)
    positive = (prices > 0).all(axis=1)

    print(f"Rows with non-finite OHLC: {(~finite).sum():,}")
    print(f"Rows with non-positive OHLC: {(~positive).sum():,}")

    valid = finite & positive
    ohlc = data.loc[valid, PRICE_COLUMNS]

    bad_ohlc = (
        (ohlc["high"] < ohlc[["open", "close", "low"]].max(axis=1))
        | (ohlc["low"] > ohlc[["open", "close", "high"]].min(axis=1))
    )
    print(f"Invalid OHLC relationships: {bad_ohlc.sum():,}")

    times = pd.DatetimeIndex(data["time"].drop_duplicates().sort_values())
    gaps = times.to_series().diff()
    long_gaps = gaps[gaps > pd.Timedelta(minutes=1)]

    print(f"Gaps longer than 1 minute: {len(long_gaps):,}")
    if not long_gaps.empty:
        print("Largest timestamp gaps:")
        print(long_gaps.nlargest(5).to_string())


def main():
    print("\nATLAS QUANT — FULL DUKASCOPY AUDIT")
    print("=" * 55)

    bid, bid_files = load_side("BID")
    ask, ask_files = load_side("ASK")

    print(f"Bid files: {len(bid_files)}")
    print(f"Ask files: {len(ask_files)}")

    inspect_side(bid, "BID")
    inspect_side(ask, "ASK")

    # Align both feeds by timestamp before calculating spreads.
    bid_indexed = bid.set_index("time")
    ask_indexed = ask.set_index("time")

    common = bid_indexed.index.intersection(ask_indexed.index)
    bid_only = bid_indexed.index.difference(ask_indexed.index)
    ask_only = ask_indexed.index.difference(bid_indexed.index)

    print("\nBID / ASK ALIGNMENT")
    print("-" * 45)
    print(f"Common timestamps: {len(common):,}")
    print(f"Bid-only timestamps: {len(bid_only):,}")
    print(f"Ask-only timestamps: {len(ask_only):,}")

    if len(common) == 0:
        raise ValueError("No matching bid/ask timestamps found")

    spread = (
        ask_indexed.loc[common, "close"]
        - bid_indexed.loc[common, "close"]
    )

    print("\nCLOSE SPREAD (ASK CLOSE - BID CLOSE)")
    print("-" * 45)
    print(f"Negative spreads: {(spread < 0).sum():,}")
    print(f"Zero spreads: {(spread == 0).sum():,}")
    print(f"Median spread (price): {spread.median():.8f}")
    print(f"Median spread (pips): {spread.median() / 0.0001:.3f}")
    print(f"95th percentile (pips): {spread.quantile(0.95) / 0.0001:.3f}")
    print(f"99th percentile (pips): {spread.quantile(0.99) / 0.0001:.3f}")
    print(f"Maximum spread (pips): {spread.max() / 0.0001:.3f}")

    print("\nLargest positive close spreads:")
    print(
        (spread.nlargest(5) / 0.0001)
        .rename("spread_pips")
        .to_string()
    )


if __name__ == "__main__":
    main()