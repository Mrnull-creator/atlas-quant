
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "eurusd_m1_bid_ask_backtest.csv"
)


def compounded_return(returns):
    return ((1 + returns).prod() - 1) * 100


def main():
    data = pd.read_csv(
        INPUT_FILE,
        index_col="time",
        parse_dates=["time"],
    ).sort_index()

    market = data["market_return"]
    strategy_gross = data["position"] * market
    strategy_net = data["strategy_return"]

    benchmarks = {
        "Always long EUR/USD": market,
        "Always short EUR/USD": -market,
        "Atlas Quant (gross)": strategy_gross,
        "Atlas Quant (net)": strategy_net,
        "Flat (no trades)": pd.Series(
            0.0, index=data.index
        ),
    }

    print("\nATLAS QUANT — BENCHMARK COMPARISON")
    print("=" * 60)
    print(f"From: {data.index.min()}")
    print(f"To:   {data.index.max()}")
    print(f"Intervals: {len(data):,}\n")

    print("Full-period compounded returns")
    print("-" * 60)

    for name, returns in benchmarks.items():
        result = compounded_return(returns)
        print(f"{name:25s} {result:>10.3f}%")

    print("\nAnnual compounded returns")
    print("-" * 60)

    # Remove timezone only for grouping by calendar year.
    years = data.index.tz_localize(None).year

    for year in sorted(set(years)):
        mask = years == year
        print(f"\n{year}")

        for name, returns in benchmarks.items():
            result = compounded_return(returns.loc[mask])
            print(f"  {name:25s} {result:>10.3f}%")

    print("\nPosition-change frequency")
    print("-" * 60)

    changes = data["turnover"] > 0
    print(f"Position-change events: {changes.sum():,}")
    print(
        "Events per 1,000 intervals: "
        f"{changes.mean() * 1000:.2f}"
    )

    print("\nResearch note:")
    print(
        "These are simplified close-to-close comparisons. "
        "They do not model broker fills, financing, "
        "slippage, or margin."
    )


if __name__ == "__main__":
    main()