
from pathlib import Path

import pandas as pd

from atlas_quant.backtesting.next_open_simulator import simulate_next_open
from atlas_quant.backtesting.validate_strategy_candidates import (
    DATA_END,
    FIXED_UNITS,
    HOLDOUT_START,
    INITIAL_CASH,
    TRAIN_START,
    load_market_data,
)
from atlas_quant.strategies.momentum import StrategyConfig, generate_signals


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_FILE = (
    PROJECT_ROOT / "data" / "processed"
    / "next_open_cost_sensitivity.csv"
)

SCENARIOS = [
    {
        "scenario": "Baseline",
        "commission": 0.0,
        "slippage_pips": 0.0,
    },
    {
        "scenario": "Low illustrative cost",
        "commission": 0.035,
        "slippage_pips": 0.1,
    },
    {
        "scenario": "Higher illustrative cost",
        "commission": 0.035,
        "slippage_pips": 0.3,
    },
]

PERIODS = [
    ("Research 2024-2025", TRAIN_START, HOLDOUT_START),
    ("Holdout 2026", HOLDOUT_START, DATA_END),
]


def main():
    print("Loading validated historical bid/ask data...")
    data = load_market_data()
    midpoint = data["mid_close"]

    candidates = {
        "Momentum 10/30": generate_signals(
            midpoint,
            StrategyConfig(fast_period=10, slow_period=30),
        )["position"],
        "Momentum 30/100": generate_signals(
            midpoint,
            StrategyConfig(fast_period=30, slow_period=100),
        )["position"],
    }

    rows = []

    for strategy_name, positions in candidates.items():
        for period_name, start, end in PERIODS:
            period = data.loc[
                (data.index >= start) & (data.index < end)
            ].copy()

            period["position"] = (
                positions.reindex(period.index)
                .fillna(0)
                .astype(int)
            )

            for scenario in SCENARIOS:
                result = simulate_next_open(
                    period,
                    initial_cash=INITIAL_CASH,
                    units=FIXED_UNITS,
                    commission_per_1000_units_per_side_usd=(
                        scenario["commission"]
                    ),
                    slippage_pips=scenario["slippage_pips"],
                )

                equity = result["equity"]
                peak = equity.cummax().clip(lower=INITIAL_CASH)
                drawdown = equity / peak - 1.0

                rows.append(
                    {
                        "strategy": strategy_name,
                        "period": period_name,
                        "scenario": scenario["scenario"],
                        "commission_per_1000_per_side_usd": (
                            scenario["commission"]
                        ),
                        "slippage_pips": scenario["slippage_pips"],
                        "final_equity": round(float(equity.iloc[-1]), 2),
                        "return_pct": round(
                            (float(equity.iloc[-1]) / INITIAL_CASH - 1)
                            * 100,
                            2,
                        ),
                        "max_drawdown_pct": round(
                            float(drawdown.min()) * 100, 2
                        ),
                        "position_changes": int(
                            result["position"].diff()
                            .fillna(result["position"].iloc[0])
                            .ne(0)
                            .sum()
                        ),
                        "total_commission_usd": round(
                            float(result["commission"].sum()), 2
                        ),
                    }
                )

    report = pd.DataFrame(rows)

    print("\nNEXT-OPEN COST SENSITIVITY")
    print(report.to_string(index=False))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved results to: {OUTPUT_FILE}")
    print("Each period starts independently with $10,000 and no position.")
    print("Spread is included through bid/ask prices.")
    print("Commission and slippage scenarios are illustrative, not broker quotes.")


if __name__ == "__main__":
    main()