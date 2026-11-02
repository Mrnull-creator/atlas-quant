
from pathlib import Path
import pandas as pd

INPUT_FILE = Path("data/processed/eurusd_m1_next_open_backtest.csv")
UNITS = 1_000.0
PIP_SIZE = 0.0001
INITIAL_CASH = 10_000.0


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Backtest file not found: {INPUT_FILE}")

    data = pd.read_csv(
        INPUT_FILE, parse_dates=["time"], index_col="time"
    ).sort_index()

    required = {
        "position", "bid_open", "ask_open",
        "bid_close", "ask_close", "equity",
    }
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    position = data["position"].astype(int)
    previous = position.shift(1, fill_value=0)
    change = position - previous
    traded_units = change.abs() * UNITS

    open_spread = data["ask_open"] - data["bid_open"]
    close_spread = data["ask_close"] - data["bid_close"]

    # Estimated execution cost versus mid-price fills.
    execution_cost = traded_units * open_spread / 2

    long_bars = int((position == 1).sum())
    short_bars = int((position == -1).sum())
    flat_bars = int((position == 0).sum())

    entries = int(((previous == 0) & (position != 0)).sum())
    exits = int(((previous != 0) & (position == 0)).sum())
    reversals = int(
        ((previous != 0) & (position != 0) &
         (previous != position)).sum()
    )
    changed_bars = int((change != 0).sum())

    print("\n========== TRADING ACTIVITY AUDIT ==========")
    print(f"Observations: {len(data):,}")
    print(f"First timestamp: {data.index.min()}")
    print(f"Last timestamp:  {data.index.max()}")

    print("\nPosition occupancy:")
    print(f"Long bars:  {long_bars:,} ({long_bars / len(data):.2%})")
    print(f"Short bars: {short_bars:,} ({short_bars / len(data):.2%})")
    print(f"Flat bars:  {flat_bars:,} ({flat_bars / len(data):.2%})")

    print("\nPosition transitions:")
    print(f"Entries from flat: {entries:,}")
    print(f"Exits to flat: {exits:,}")
    print(f"Direct reversals: {reversals:,}")
    print(f"Bars with position change: {changed_bars:,}")
    print(f"Total absolute units traded: {traded_units.sum():,.0f} EUR")

    print("\nSpread statistics:")
    print(f"Mean opening spread: {open_spread.mean() / PIP_SIZE:.3f} pips")
    print(f"Median opening spread: {open_spread.median() / PIP_SIZE:.3f} pips")
    print(f"Mean closing spread: {close_spread.mean() / PIP_SIZE:.3f} pips")

    total_cost = float(execution_cost.sum())
    print("\nEstimated execution spread cost:")
    print(f"Total: ${total_cost:,.2f}")
    print(f"As % of starting cash: {total_cost / INITIAL_CASH:.2%}")
    if changed_bars:
        print(f"Cost per position change: ${total_cost / changed_bars:.4f}")

    final_equity = float(data["equity"].iloc[-1])
    print("\nBacktest outcome:")
    print(f"Final equity: ${final_equity:,.2f}")
    print(f"Net equity change: ${final_equity - INITIAL_CASH:,.2f}")
    print("============================================")
    print(
        "\nNote: The existing backtest already uses bid/ask prices. "
        "Do not subtract this estimated spread cost a second time."
    )


if __name__ == "__main__":
    main()