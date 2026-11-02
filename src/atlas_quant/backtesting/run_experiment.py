
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
OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "eurusd_m1_momentum_backtest.csv"
)


def main():
    # Load and validate historical candles.
    data = pd.read_csv(DATA_FILE, index_col="time")
    data.index = pd.to_datetime(data.index, utc=True)
    data = validate_ohlc(data)

    # Generate moving-average signals using closing prices.
    config = StrategyConfig(fast_period=10, slow_period=30)
    signals = generate_signals(data["close"], config)

    experiment = data.join(
        signals[["fast_ma", "slow_ma", "signal", "position"]]
    )

    # Preliminary cost assumption: 1 basis point of
    # notional per unit of turnover; no commission yet.
    results = run_backtest(
        experiment,
        spread_bps=1.0,
        commission_bps_per_side=0.0,
    )

    summary = summarize_backtest(results)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_FILE, index_label="time")

    print("\nATLAS QUANT — FIRST BACKTEST")
    print("=" * 40)
    print(f"Instrument: EUR/USD")
    print(f"Timeframe: 1 minute")
    print(f"Fast moving average: {config.fast_period}")
    print(f"Slow moving average: {config.slow_period}")
    print(f"Input candles: {len(data)}")
    print(f"Evaluated returns: {len(results)}")
    print("Estimated spread cost: 1.0 basis point")
    print("Commission assumption: 0.0 basis points per side")

    print("\nPerformance statistics:")
    for name, value in summary.items():
        print(f"{name}: {value:.4f}" if isinstance(value, float)
              else f"{name}: {value}")

    print(f"\nResults saved to:\n{OUTPUT_FILE}")
    print("\nMost recent backtest rows:")
    print(
        results[
            ["close", "signal", "position",
             "strategy_return", "equity"]
        ].tail()
    )


if __name__ == "__main__":
    main()