
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd


SYMBOL = "EURUSD"
START = datetime(2026, 10, 14, 15, 25, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 15, 36, tzinfo=timezone.utc)


def main():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")

    try:
        if not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(f"Could not select {SYMBOL}: {mt5.last_error()}")

        rates = mt5.copy_rates_range(
            SYMBOL,
            mt5.TIMEFRAME_M1,
            START,
            END,
        )

        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No rates returned: {mt5.last_error()}")

        data = pd.DataFrame(rates)
        data["time"] = pd.to_datetime(data["time"], unit="s", utc=True)

        print("\nATLAS QUANT — MT5 CANDLE VERIFICATION")
        print("=" * 48)
        print(f"Broker/server: {mt5.account_info().server}")
        print(f"Symbol: {SYMBOL}")
        print(data[["time", "open", "high", "low", "close", "tick_volume"]].to_string(index=False))

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()