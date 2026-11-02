
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "eurusd_m1_bid_ask_backtest.csv"
)


def main():
    data = pd.read_csv(
        INPUT_FILE,
        index_col="time",
        parse_dates=["time"],
    )

    required = {
        "position",
        "market_return",
        "turnover",
        "spread_cost",
        "strategy_return",
        "spread_pips",
    }

    missing = required - set(data.columns)
    if missing:
        raise ValueError(
            f"Missing columns: {sorted(missing)}"
        )

    data = data.sort_index()

    gross_returns = (
        data["position"] * data["market_return"]
    )

    net_returns = data["strategy_return"]

    gross_equity = (1 + gross_returns).cumprod()
    net_equity = (1 + net_returns).cumprod()

    trades = data[data["turnover"] > 0]

    print("\nATLAS QUANT — BACKTEST DIAGNOSTICS")
    print("=" * 55)

    print(f"Intervals: {len(data):,}")
    print(f"Position-change events: {len(trades):,}")
    print(f"Total turnover: {data['turnover'].sum():,.1f}")

    print("\nPerformance comparison")
    print("-" * 55)
    print(
        "Gross compounded return: "
        f"{(gross_equity.iloc[-1] - 1) * 100:.4f}%"
    )
    print(
        "Net compounded return:   "
        f"{(net_equity.iloc[-1] - 1) * 100:.4f}%"
    )
    print(
        "Sum of gross bar returns: "
        f"{gross_returns.sum() * 100:.4f}%"
    )
    print(
        "Sum of modeled costs:     "
        f"{data['spread_cost'].sum() * 100:.4f}%"
    )

    print("\nSpreads when positions change")
    print("-" * 55)

    if trades.empty:
        print("No position changes found.")
    else:
        print(
            trades["spread_pips"]
            .quantile([0.50, 0.90, 0.95, 0.99])
            .to_string()
        )

        print(
            "Trade events with spread > 1 pip: "
            f"{(trades['spread_pips'] > 1).sum():,}"
        )

        print(
            "Trade events with spread > 5 pips: "
            f"{(trades['spread_pips'] > 5).sum():,}"
        )

        print(
            "Trade events with spread > 10 pips: "
            f"{(trades['spread_pips'] > 10).sum():,}"
        )

    print("\nPosition distribution")
    print("-" * 55)

    print(
        data["position"]
        .value_counts()
        .sort_index()
        .rename(index={-1: "Short", 0: "Flat", 1: "Long"})
        .to_string()
    )

    print("\nMonthly net returns")
    print("-" * 55)

    monthly_equity = (1 + net_returns).groupby(
        data.index.to_period("M")
    ).prod()

    monthly_returns = (monthly_equity - 1) * 100
    print(monthly_returns.tail(12).round(3).to_string())


if __name__ == "__main__":
    main()