
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategyConfig:
    fast_period: int = 10
    slow_period: int = 30

    def __post_init__(self):
        if self.fast_period < 1:
            raise ValueError("fast_period must be positive")

        if self.slow_period <= self.fast_period:
            raise ValueError(
                "slow_period must exceed fast_period"
            )


def generate_signals(
    prices: pd.Series,
    config: StrategyConfig = StrategyConfig(),
) -> pd.DataFrame:
    """Generate research signals from chronological prices.

    Signal:
        1  = bullish
       -1  = bearish
        0  = moving averages are equal or unavailable
    """

    if prices.empty:
        raise ValueError("Price data cannot be empty")

    if not prices.index.is_monotonic_increasing:
        raise ValueError("Prices must be chronological")

    values = prices.to_numpy(dtype=float)

    if not np.isfinite(values).all():
        raise ValueError("Prices contain invalid values")

    if (values <= 0).any():
        raise ValueError("Prices must be positive")

    fast_ma = prices.rolling(
        window=config.fast_period,
        min_periods=config.fast_period,
    ).mean()

    slow_ma = prices.rolling(
        window=config.slow_period,
        min_periods=config.slow_period,
    ).mean()

    signal = pd.Series(
        np.select(
            [fast_ma > slow_ma, fast_ma < slow_ma],
            [1, -1],
            default=0,
        ),
        index=prices.index,
        name="signal",
    ).astype("int8")

    result = pd.DataFrame(
        {
            "price": prices,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "signal": signal,
            # Use the previous completed observation's signal.
            "position": signal.shift(1, fill_value=0),
        },
        index=prices.index,
    )

    # Do not trade until both averages are available.
    ready = fast_ma.notna() & slow_ma.notna()
    result.loc[~ready, "signal"] = 0
    result.loc[~ready, "position"] = 0

    return result