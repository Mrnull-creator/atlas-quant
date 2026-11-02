
from pathlib import Path

import pandas as pd

from atlas_quant.backtesting.bid_ask import (
    run_bid_ask_backtest,
)
from atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals,
)


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

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "eurusd_m1_bid_ask_backtest.csv"
)


def load_side(side):
    files = sorted((BASE_DIR / side).glob("*.parquet"))

    if not files:
        raise FileNotFoundError(
            f"No Parquet files found for {side}: {BASE_DIR / side}"
        )

    data = pd.concat(
        [pd.read_parquet(path) for path in files],
        ignore_index=True,
    )

    data["time"] = pd.to_datetime(data["time"], utc=True)
    data = data.set_index("time").sort_index()

    if data.index.has_duplicates:
        raise ValueError(f"Duplicate timestamps in {side} data")

    return data


def main():
    print("Loading historical bid/ask data...")

    bid = load_side("BID")
    ask = load_side("ASK")

    common = bid.index.intersection(ask.index)
    bid = bid.loc[common]
    ask = ask.loc[common]

    data = pd.DataFrame(index=common)
    data["bid_close"] = bid["close"]
    data["ask_close"] = ask["close"]

    data["spread_pips"] = (
        data["ask_close"] - data["bid_close"]
    ) / 0.0001

    if (data["spread_pips"] < 0).any():
        raise ValueError("Negative spreads found")

    data["mid_close"] = (
        data["bid_close"] + data["ask_close"]
    ) / 2

    config = StrategyConfig(
        fast_period=10,
        slow_period=30,
    )

    signals = generate_signals(
        data["mid_close"],
        config,
    )

    data["signal"] = signals["signal"]
    data["position"] = signals["position"]

    results = run_bid_ask_backtest(data)

    if results.empty:
        raise ValueError("No backtest results produced")

    total_return = (results["equity"].iloc[-1] - 1) * 100

    drawdown = (
        results["equity"] / results["equity"].cummax()
    ) - 1

    gross_returns = (
        results["position"] * results["market_return"]
    )

    gross_equity = (1 + gross_returns).cumprod()
    gross_return = (gross_equity.iloc[-1] - 1) * 100

    print("\nATLAS QUANT — TESTED BID/ASK EXPERIMENT")
    print("=" * 55)
    print(f"Matched observations: {len(data):,}")
    print(f"Evaluated intervals:  {len(results):,}")
    print(f"From: {data.index.min()}")
    print(f"To:   {data.index.max()}")
    print(f"Median spread: {data['spread_pips'].median():.3f} pips")
    print(f"Gross compounded return: {gross_return:.4f}%")
    print(f"Net compounded return:   {total_return:.4f}%")
    print(f"Maximum drawdown:         {drawdown.min() * 100:.4f}%")
    print(f"Position-change events:   {(results['turnover'] > 0).sum():,}")
    print(f"Total turnover:           {results['turnover'].sum():,.1f}")
    print(
        "Summed modeled spread costs: "
        f"{results['spread_cost'].sum() * 100:.4f}%"
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_FILE, index_label="time")

    print(f"\nSaved results to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()