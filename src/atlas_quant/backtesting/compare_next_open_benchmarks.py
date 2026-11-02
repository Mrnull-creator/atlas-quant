
from pathlib import Path

import pandas as pd

from atlas_quant.backtesting.next_open_simulator import (
    simulate_next_open,
)


INPUT_FILE = Path(
    "data/processed/eurusd_m1_next_open_backtest.csv"
)
OUTPUT_FILE = Path(
    "data/processed/eurusd_next_open_benchmark_comparison.csv"
)

INITIAL_CASH = 10_000.0
UNITS = 1_000.0

PRICE_COLUMNS = [
    "bid_open",
    "ask_open",
    "bid_close",
    "ask_close",
]


def load_backtest_data() -> pd.DataFrame:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Backtest results not found: {INPUT_FILE}"
        )

    data = pd.read_csv(
        INPUT_FILE,
        index_col=0,
        parse_dates=True,
    )

    data.index = pd.to_datetime(data.index, utc=True)
    data = data.sort_index()

    required = set(PRICE_COLUMNS + ["position"])
    missing = required - set(data.columns)

    if missing:
        raise ValueError(
            f"Input file is missing columns: {sorted(missing)}"
        )

    if data.empty:
        raise ValueError("Backtest data is empty")

    if data.index.has_duplicates:
        raise ValueError("Duplicate timestamps found")

    return data[PRICE_COLUMNS + ["position"]].copy()


def run_benchmark(
    prices: pd.DataFrame,
    position: int | None,
) -> pd.DataFrame:
    data = prices.copy()

    if position is not None:
        data["position"] = position

    # The simulator excludes its first input row because the
    # position before the dataset begins is unknown. Add a
    # synthetic row so every saved backtest observation is used.
    synthetic = data.iloc[[0]].copy()
    synthetic.index = [
        data.index[0] - pd.Timedelta(minutes=1)
    ]
    synthetic["position"] = 0

    simulation_input = pd.concat([synthetic, data])

    return simulate_next_open(
        simulation_input,
        initial_cash=INITIAL_CASH,
        units=UNITS,
    )


def main() -> None:
    print("Loading saved next-open backtest data...")
    data = load_backtest_data()

    print(f"Observations: {len(data):,}")
    print(f"First timestamp: {data.index[0]}")
    print(f"Last timestamp:  {data.index[-1]}")

    benchmarks = {
        "Momentum 10/30": None,
        "Always long": 1,
        "Always short": -1,
        "Always flat": 0,
    }

    summary_rows = []

    for name, position in benchmarks.items():
        result = run_benchmark(data, position)

        equity = result["equity"]
        final_equity = float(equity.iloc[-1])
        total_return = final_equity / INITIAL_CASH - 1
        max_drawdown = float(result["drawdown"].min())

        summary_rows.append(
            {
                "benchmark": name,
                "initial_cash": INITIAL_CASH,
                "position_size_eur": (
                    "Strategy positions"
                    if position is None
                    else position * UNITS
                ),
                "final_equity": final_equity,
                "total_return_pct": total_return * 100,
                "max_drawdown_pct": max_drawdown * 100,
            }
        )

    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values(
        "final_equity",
        ascending=False,
    )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    summary.to_csv(OUTPUT_FILE, index=False)

    print("\n========== BENCHMARK COMPARISON ==========")
    print(
        summary.to_string(
            index=False,
            formatters={
                "initial_cash": "${:,.2f}".format,
                "final_equity": "${:,.2f}".format,
                "total_return_pct": "{:.2f}%".format,
                "max_drawdown_pct": "{:.2f}%".format,
            },
        )
    )

    print(f"\nSaved comparison to: {OUTPUT_FILE}")
    print(
        "\nNote: Results exclude commissions, slippage, "
        "financing, margin, and broker constraints."
    )


if __name__ == "__main__":
    main()