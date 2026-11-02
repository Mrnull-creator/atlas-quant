
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from atlas_quant.data.validation import validate_ohlc


SYMBOL = "EURUSD"
CANDLE_COUNT = 1000

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_FILE = PROJECT_ROOT / "data" / "raw" / "eurusd_m1.csv"


def main():
    if not mt5.initialize():
        raise RuntimeError(
            f"Could not connect to MT5: {mt5.last_error()}"
        )

    try:
        if not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(
                f"Could not select {SYMBOL}. "
                "Check the broker's symbol name in Market Watch."
            )

        rates = mt5.copy_rates_from_pos(
            SYMBOL,
            mt5.TIMEFRAME_M1,
            0,
            CANDLE_COUNT,
        )

        if rates is None or len(rates) < 2:
            raise RuntimeError(
                f"Not enough candles returned: {mt5.last_error()}"
            )

        data = pd.DataFrame(rates)
        data["time"] = pd.to_datetime(
            data["time"], unit="s", utc=True
        )
        data = data.set_index("time").sort_index()

        # Exclude the latest candle, which may still be forming.
        data = data.iloc[:-1]

        data = validate_ohlc(data)

        OUTPUT_FILE.parent.mkdir(
            parents=True, exist_ok=True
        )
        data.to_csv(OUTPUT_FILE, index_label="time")

        print(f"Symbol: {SYMBOL}")
        print(f"Completed candles saved: {len(data)}")
        print(f"First candle: {data.index.min()}")
        print(f"Last candle: {data.index.max()}")
        print(f"Saved to: {OUTPUT_FILE}")
        print(data[["open", "high", "low", "close"]].tail())

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()