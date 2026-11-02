
from pathlib import Path

import pandas as pd

from src.atlas_quant.data.validation import validate_ohlc
from src.atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals,
)
from src.atlas_quant.backtesting.engine import (
    run_backtest,
    summarize_backtest,
)


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

    rows = []

    for spread_bps in [0.0, 0.5, 1.0, 2.0, 3.0]:
        results = run_backtest(
            experiment,
            spread_bps=spread_bps,
            commission_bps_per_side=0.0,
        )

        stats = summarize_backtest(results)

        rows.append({
            "assumed_spread_bps": spread_bps,
            "total_return_pct": stats["total_return_pct"],
            "max_drawdown_pct": stats["max_drawdown_pct"],
            "profitable_bar_pct": stats["profitable_bar_pct"],
        })

    report = pd.DataFrame(rows)

    print("\nATLAS QUANT — COST SENSITIVITY")
    print("Same data and strategy; only estimated spread changes.")
    print(report.to_string(index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()