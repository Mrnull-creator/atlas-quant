
from pathlib import Path

import pandas as pd

from src.atlas_quant.data.validation import validate_ohlc


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_FILE = PROJECT_ROOT / "data" / "raw" / "eurusd_m1.csv"


def main():
    data = pd.read_csv(DATA_FILE, index_col="time")
    data.index = pd.to_datetime(data.index, utc=True)

    validated = validate_ohlc(data)

    time_gaps = validated.index.to_series().diff()
    gaps = time_gaps[time_gaps > pd.Timedelta(minutes=1)]

    print("ATLAS QUANT — DATA QUALITY REPORT")
    print("-" * 40)
    print(f"Rows: {len(validated)}")
    print(f"Duplicate timestamps: {validated.index.has_duplicates}")
    print(f"Missing values: {validated.isna().sum().sum()}")
    print(f"First timestamp: {validated.index.min()}")
    print(f"Last timestamp: {validated.index.max()}")
    print(f"Intervals longer than 1 minute: {len(gaps)}")

    if not gaps.empty:
        print("\nLargest observed gaps:")
        print(gaps.sort_values(ascending=False).head())

    print("\nOHLC summary:")
    print(validated[["open", "high", "low", "close"]].describe())


if __name__ == "__main__":
    main()