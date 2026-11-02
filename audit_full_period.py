from atlas_quant.backtesting.validate_strategy_candidates import (
    INITIAL_CASH,
    FIXED_UNITS,
    load_market_data,
)
from atlas_quant.backtesting.next_open_simulator import simulate_next_open
from atlas_quant.strategies.momentum import StrategyConfig, generate_signals


def main():
    data = load_market_data()
    positions = generate_signals(
        data["mid_close"],
        StrategyConfig(fast_period=10, slow_period=30),
    )["position"]

    data["position"] = positions.reindex(data.index).fillna(0).astype(int)

    result = simulate_next_open(
        data[
            [
                "bid_open",
                "ask_open",
                "bid_close",
                "ask_close",
                "position",
            ]
        ],
        initial_cash=INITIAL_CASH,
        units=FIXED_UNITS,
    )

    final_equity = float(result["equity"].iloc[-1])
    total_return = final_equity / INITIAL_CASH - 1
    max_drawdown = float(result["drawdown"].min())

    changes = int(
        result["position"].diff().fillna(
            result["position"].iloc[0]
        ).ne(0).sum()
    )

    print("\nFULL-PERIOD AUDIT — MOMENTUM 10/30")
    print(f"Final equity:     ${final_equity:,.2f}")
    print(f"Total return:     {total_return:.2%}")
    print(f"Max drawdown:     {max_drawdown:.2%}")
    print(f"Position changes: {changes:,}")
    print(f"Result rows:      {len(result):,}")


if __name__ == "__main__":
    main()
