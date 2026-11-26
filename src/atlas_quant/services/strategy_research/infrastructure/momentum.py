"""Adapter connecting the existing momentum strategy to Strategy Research."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd

from atlas_quant.services.strategy_research.domain.strategies import (
    ExecutionTiming,
    StrategySignal,
    StrategyVersion,
)
from atlas_quant.strategies.momentum import (
    StrategyConfig,
    generate_signals as generate_momentum_signals,
)


class MomentumStrategyImplementation:
    """Versioned adapter for the existing moving-average momentum strategy."""

    implementation_key = "momentum"
    implementation_version = "1.0.0"

    def validate_parameters(
        self,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not isinstance(parameters, Mapping):
            raise TypeError("Strategy parameters must be a mapping.")

        allowed = {"fast_period", "slow_period"}
        unknown = set(parameters) - allowed

        if unknown:
            raise ValueError(
                f"Unknown momentum parameters: {sorted(unknown)!r}"
            )

        fast_period = parameters.get("fast_period", 10)
        slow_period = parameters.get("slow_period", 30)

        for name, value in (
            ("fast_period", fast_period),
            ("slow_period", slow_period),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer.")

        config = StrategyConfig(
            fast_period=fast_period,
            slow_period=slow_period,
        )

        return {
            "fast_period": config.fast_period,
            "slow_period": config.slow_period,
        }

    def generate_signals(
        self,
        *,
        instrument_id: UUID,
        strategy_version: StrategyVersion,
        market_data: pd.DataFrame,
    ) -> tuple[StrategySignal, ...]:
        if (
            strategy_version.implementation_key
            != self.implementation_key
            or strategy_version.implementation_version
            != self.implementation_version
        ):
            raise ValueError(
                "Strategy version does not identify momentum 1.0.0."
            )

        if strategy_version.execution_timing != ExecutionTiming.NEXT_BAR_OPEN:
            raise ValueError(
                "Momentum 1.0.0 supports NEXT_BAR_OPEN execution only."
            )

        parameters = self.validate_parameters(
            strategy_version.parameters
        )
        config = StrategyConfig(**parameters)

        if not isinstance(market_data, pd.DataFrame):
            raise TypeError("market_data must be a pandas DataFrame.")

        if market_data.empty:
            raise ValueError("Market data cannot be empty.")

        required_columns = {"close", "bar_close_time"}
        missing = required_columns - set(market_data.columns)

        if missing:
            raise ValueError(
                f"Market data is missing columns: {sorted(missing)!r}"
            )

        if not isinstance(market_data.index, pd.DatetimeIndex):
            raise TypeError(
                "Market data index must contain bar opening timestamps."
            )

        if market_data.index.tz is None:
            raise ValueError(
                "Bar opening timestamps must be timezone-aware."
            )

        if market_data.index.hasnans:
            raise ValueError("Bar opening timestamps cannot be missing.")

        if not market_data.index.is_monotonic_increasing:
            raise ValueError("Market data must be chronological.")

        if not market_data.index.is_unique:
            raise ValueError("Bar opening timestamps must be unique.")

        open_times = market_data.index.tz_convert("UTC")

        close_times = tuple(
            self._as_utc_timestamp(value, "bar_close_time")
            for value in market_data["bar_close_time"]
        )

        for index, (open_time, close_time) in enumerate(
            zip(open_times, close_times)
        ):
            if close_time <= open_time:
                raise ValueError(
                    "Each bar_close_time must be later than its bar opening time."
                )

            if index + 1 < len(open_times):
                next_open = open_times[index + 1]

                if next_open < close_time:
                    raise ValueError(
                        "The next bar cannot open before the current bar closes."
                    )

        prices = pd.Series(
            market_data["close"].to_numpy(),
            index=open_times,
            name="close",
        )

        calculated = generate_momentum_signals(
            prices,
            config,
        )

        signals: list[StrategySignal] = []

        # A signal is valid only when both indicators are available.
        # A genuine zero signal after warmup remains an actionable flat target.
        # The last bar is excluded because its next opening time is unknown.
        for index in range(len(open_times) - 1):
            fast_ma = calculated["fast_ma"].iloc[index]
            slow_ma = calculated["slow_ma"].iloc[index]

            if pd.isna(fast_ma) or pd.isna(slow_ma):
                continue

            target = int(calculated["signal"].iloc[index])

            signals.append(
                StrategySignal(
                    strategy_version_id=strategy_version.strategy_version_id,
                    instrument_id=instrument_id,
                    generated_at=close_times[index].to_pydatetime(),
                    effective_at=open_times[index + 1].to_pydatetime(),
                    target_exposure=Decimal(target),
                    rationale="Momentum fast/slow moving-average signal.",
                )
            )

        return tuple(signals)

    @staticmethod
    def _as_utc_timestamp(value: Any, field_name: str) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)

        if pd.isna(timestamp):
            raise ValueError(f"{field_name} cannot contain missing timestamps.")

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{field_name} values must be timezone-aware.")

        return timestamp.tz_convert("UTC")