
from pathlib import Path

import pandas as pd

from src.atlas_quant.data.validation import validate_ohlc
from src.atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals,
)
from src.atlas_quant.backtesting.engine import run_backtest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_FILE = PROJECT_ROOT / "data" / "raw" / "eurusd_m1.csv"


def main():
    data = pd.read_csv(DATA_FILE, index_col="time")
    data.index = pd.to_datetime(data.index, utc=True)
    data = validate_ohlc(data)

    signals = generate_signals(
        data["close"],
        StrategyConfig(fast_period=10, slow_period=30),
    )

    experiment = data.join(
        signals[["signal", "position"]]
    )

    results = run_backtest(
        experiment,
        spread_bps=1.0,
        commission_bps_per_side=0.0,
    )

    positions = results["position"]
    changes = positions.diff().fillna(positions.iloc[0]).abs()
    change_events = changes[changes > 0]

    print("\nATLAS QUANT — TURNOVER AUDIT")
    print("=" * 40)
    print(f"Evaluated intervals: {len(results)}")
    print(f"Long intervals: {(positions == 1).sum()}")
    print(f"Short intervals: {(positions == -1).sum()}")
    print(f"Flat intervals: {(positions == 0).sum()}")
    print(f"Position-change events: {len(change_events)}")
    print(f"Total position turnover: {results['turnover'].sum():.0f}")
    print(
        "Total modeled trading costs (% of price notional): "
        f"{results['trading_cost'].sum() * 100:.4f}%"
    )

    print("\nPosition changes:")
    if change_events.empty:
        print("No position changes during the evaluated sample.")
    else:
        print(change_events.value_counts().sort_index().to_string())

    print("\nLargest absolute one-bar price returns:")
    print(
        results["market_return"]
        .abs()
        .nlargest(5)
        .to_string()
    )


if __name__ == "__main__":
    main()