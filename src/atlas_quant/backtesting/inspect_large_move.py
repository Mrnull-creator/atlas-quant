
from pathlib import Path

import pandas as pd

from src.atlas_quant.data.validation import validate_ohlc


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_FILE = PROJECT_ROOT / "data" / "raw" / "eurusd_m1.csv"


def main():
    data = pd.read_csv(DATA_FILE, index_col="time")
    data.index = pd.to_datetime(data.index, utc=True)
    data = validate_ohlc(data)

    data["return"] = data["close"].pct_change()

    largest_time = data["return"].abs().idxmax()

    # Show five candles before and five after the largest move.
    location = data.index.get_loc(largest_time)
    start = max(0, location - 5)
    end = min(len(data), location + 6)

    columns = ["open", "high", "low", "close", "tick_volume", "return"]

    print("\nATLAS QUANT — LARGE-MOVE INSPECTION")
    print("=" * 55)
    print(f"Largest move timestamp: {largest_time}")
    print(f"Largest absolute close return: "
          f"{data.loc[largest_time, 'return'] * 100:.4f}%")
    print("\nCandles around the move:")
    print(data.iloc[start:end][columns].to_string())


if __name__ == "__main__":
    main()