
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

CHECK_TIMES = [
    "2025-12-24 22:04:00+00:00",
    "2024-10-10 21:04:00+00:00",
    "2025-12-25 07:47:00+00:00",
]


def load_side(side):
    files = sorted((BASE_DIR / side).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No {side} files found")

    data = pd.concat(
        [pd.read_parquet(path) for path in files],
        ignore_index=True,
    )
    data["time"] = pd.to_datetime(data["time"], utc=True)
    return data.set_index("time").sort_index()


def main():
    bid = load_side("BID")
    ask = load_side("ASK")

    common = bid.index.intersection(ask.index)
    bid = bid.loc[common]
    ask = ask.loc[common]

    combined = pd.DataFrame(index=common)

    for side_name, frame in [("bid", bid), ("ask", ask)]:
        combined[f"{side_name}_open"] = frame["open"]
        combined[f"{side_name}_high"] = frame["high"]
        combined[f"{side_name}_low"] = frame["low"]
        combined[f"{side_name}_close"] = frame["close"]
        combined[f"{side_name}_volume"] = frame["volume"]
        combined[f"{side_name}_range_pips"] = (
            frame["high"] - frame["low"]
        ) / 0.0001

    combined["spread_pips"] = (
        combined["ask_close"] - combined["bid_close"]
    ) / 0.0001

    columns = [
        "bid_close", "ask_close", "spread_pips",
        "bid_range_pips", "ask_range_pips",
        "bid_volume", "ask_volume",
    ]

    print("\nATLAS QUANT — STALE-QUOTE INVESTIGATION")
    print("=" * 65)

    for timestamp in CHECK_TIMES:
        target = pd.Timestamp(timestamp)

        print(f"\nAround {target}")
        print("-" * 65)

        if target not in combined.index:
            print("Timestamp not found.")
            continue

        location = combined.index.get_loc(target)
        start = max(0, location - 5)
        end = min(len(combined), location + 6)

        print(combined.iloc[start:end][columns].to_string())

    print("\nSpread above 10 pips by calendar date:")
    extreme = combined[combined["spread_pips"] > 10]
    if extreme.empty:
        print("No observations.")
    else:
        print(
            extreme.groupby(extreme.index.date)
            .size()
            .sort_values(ascending=False)
            .head(15)
            .to_string()
        )


if __name__ == "__main__":
    main()