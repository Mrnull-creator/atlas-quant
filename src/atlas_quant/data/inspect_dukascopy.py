
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data" / "external" / "dukascopy"


def main():
    files = sorted(DATA_DIR.rglob("*.parquet"))

    print("\nATLAS QUANT — DUKASCOPY DATA AUDIT")
    print("=" * 55)
    print(f"Data directory: {DATA_DIR}")
    print(f"Parquet files found: {len(files)}")

    if not files:
        raise FileNotFoundError(
            f"No Parquet files found under {DATA_DIR}"
        )

    for path in files[:4]:
        print(f"\nFile: {path.relative_to(DATA_DIR)}")

        frame = pd.read_parquet(path)

        print(f"Rows: {len(frame)}")
        print(f"Columns: {list(frame.columns)}")
        print("\nData types:")
        print(frame.dtypes.to_string())
        print("\nFirst 3 rows:")
        print(frame.head(3).to_string())
        print("\nLast 3 rows:")
        print(frame.tail(3).to_string())


if __name__ == "__main__":
    main()