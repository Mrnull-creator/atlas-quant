
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = (
    PROJECT_ROOT
    / "data"
    / "external"
    / "dukascopy"
    / "m1"
    / "Forex"
    / "Majors"
    / "EUR-USD"
)

PIP_SIZE = 0.0001


def load_side(side):
    folder = BASE_DIR / side
    files = sorted(folder.glob("*.parquet"))

    if not files:
        raise FileNotFoundError(f"No files found in {folder}")

    frames = [pd.read_parquet(path) for path in files]
    data = pd.concat(frames, ignore_index=True)
    data["time"] = pd.to_datetime(data["time"], utc=True)

    return data.set_index("time").sort_index()


def main():
    bid = load_side("BID")
    ask = load_side("ASK")

    common = bid.index.intersection(ask.index)
    bid = bid.loc[common]
    ask = ask.loc[common]

    spread = ask["close"] - bid["close"]
    spread_pips = spread / PIP_SIZE

    print("\nATLAS QUANT — SPREAD OUTLIER AUDIT")
    print("=" * 55)
    print(f"Matched candles: {len(common):,}")

    print("\nSpread distribution (pips):")
    print(
        spread_pips.quantile(
            [0, 0.50, 0.90, 0.95, 0.99, 0.999, 1]
        ).to_string()
    )

    print("\nCounts above spread thresholds:")
    for threshold in [1, 2, 3, 5, 10, 20]:
        print(
            f"> {threshold:2} pips: "
            f"{(spread_pips > threshold).sum():,}"
        )

    print("\nLargest 15 closing spreads:")
    top = spread_pips.nlargest(15).rename("spread_pips")
    print(top.to_string())

    # Examine whether large spreads cluster by UTC hour.
    summary = pd.DataFrame(
        {
            "spread_pips": spread_pips,
            "hour_utc": common.hour,
        },
        index=common,
    )

    print("\nSpread statistics by UTC hour:")
    hourly = summary.groupby("hour_utc")["spread_pips"].agg(
        ["count", "median", "quantile"]
    )
    # Use explicit quantiles for easier interpretation.
    hourly = summary.groupby("hour_utc")["spread_pips"].agg(
        count="count",
        median="median",
        p95=lambda x: x.quantile(0.95),
    )
    print(hourly.round(3).to_string())

    print("\nLargest-spread candle details:")
    rows = top.index
    details = pd.DataFrame(
        {
            "bid_open": bid.loc[rows, "open"],
            "bid_high": bid.loc[rows, "high"],
            "bid_low": bid.loc[rows, "low"],
            "bid_close": bid.loc[rows, "close"],
            "ask_open": ask.loc[rows, "open"],
            "ask_high": ask.loc[rows, "high"],
            "ask_low": ask.loc[rows, "low"],
            "ask_close": ask.loc[rows, "close"],
            "spread_pips": spread_pips.loc[rows],
        },
        index=rows,
    )
    print(details.sort_values("spread_pips", ascending=False).to_string())


if __name__ == "__main__":
    main()