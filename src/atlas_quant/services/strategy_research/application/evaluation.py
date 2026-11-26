"""Reproducible strategy evaluation using the canonical next-open simulator."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
from numbers import Integral, Real
from decimal import Decimal
from uuid import UUID

import pandas as pd

from atlas_quant.backtesting.next_open_simulator import simulate_next_open
from atlas_quant.services.strategy_research.application.performance import (
    calculate_performance,
)
from atlas_quant.services.strategy_research.application.registry import (
    StrategyImplementationRegistry,
)
from atlas_quant.services.strategy_research.domain.performance import (
    PerformanceMetrics,
)
from atlas_quant.services.strategy_research.domain.strategies import (
    StrategySignal,
    StrategyVersion,
)


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Explicit assumptions governing one independent simulation."""

    initial_cash: float = 10_000.0
    units: float = 1_000.0
    commission_per_1000_units_per_side_usd: float = 0.0
    slippage_pips: float = 0.0
    pip_size: float = 0.0001
    periods_per_year: int = 252
    risk_free_rate_annual: float = 0.0

    def __post_init__(self) -> None:
        numeric_fields = (
            "initial_cash",
            "units",
            "commission_per_1000_units_per_side_usd",
            "slippage_pips",
            "pip_size",
            "risk_free_rate_annual",
        )

        for name in numeric_fields:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} must be a finite number.")

        if self.initial_cash <= 0 or self.units <= 0:
            raise ValueError("initial_cash and units must be positive.")

        if self.commission_per_1000_units_per_side_usd < 0:
            raise ValueError("Commission cannot be negative.")

        if self.slippage_pips < 0:
            raise ValueError("Slippage cannot be negative.")

        if self.pip_size <= 0:
            raise ValueError("pip_size must be positive.")

        if (
            isinstance(self.periods_per_year, bool)
            or not isinstance(self.periods_per_year, Integral)
            or self.periods_per_year <= 0
        ):
            raise ValueError("periods_per_year must be a positive integer.")

        if self.risk_free_rate_annual <= -1:
            raise ValueError("risk_free_rate_annual must be greater than -1.")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Performance and provenance for one evaluation window."""

    strategy_id: UUID
    strategy_version_id: UUID
    instrument_id: UUID
    version_number: int
    implementation_key: str
    implementation_version: str
    parameters_json: str

    requested_start: datetime
    requested_end_exclusive: datetime
    actual_start: datetime
    actual_end: datetime

    data_fingerprint: str
    config: EvaluationConfig
    metrics: PerformanceMetrics

    # Canonical simulator output. Treat this DataFrame as read-only.
    simulation_results: pd.DataFrame


@dataclass(frozen=True, slots=True)
class InSampleOutOfSampleResult:
    """Independent in-sample and out-of-sample evaluation results."""

    in_sample: EvaluationResult
    out_of_sample: EvaluationResult
    split_at: datetime


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One fixed-parameter walk-forward test fold."""

    fold_number: int
    training_start: datetime
    training_end_exclusive: datetime
    test_start: datetime
    test_end_exclusive: datetime
    evaluation: EvaluationResult


_REQUIRED_MARKET_COLUMNS = {
    "bar_close_time",
    "bid_open",
    "ask_open",
    "bid_close",
    "ask_close",
}


def _utc_timestamp(value: datetime | pd.Timestamp, name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)

    if pd.isna(timestamp):
        raise ValueError(f"{name} cannot be missing.")

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")

    return timestamp.tz_convert("UTC")


def _validate_market_data(market_data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(market_data, pd.DataFrame):
        raise TypeError("market_data must be a pandas DataFrame.")

    if market_data.empty:
        raise ValueError("market_data cannot be empty.")

    if not isinstance(market_data.index, pd.DatetimeIndex):
        raise TypeError("Market data index must be a DatetimeIndex.")

    if market_data.index.tz is None:
        raise ValueError("Market data timestamps must be timezone-aware.")

    if market_data.index.hasnans:
        raise ValueError("Market data timestamps cannot contain NaT.")

    if not market_data.index.is_monotonic_increasing:
        raise ValueError("Market data must be chronological.")

    if market_data.index.has_duplicates:
        raise ValueError("Market data timestamps must be unique.")

    missing = _REQUIRED_MARKET_COLUMNS - set(market_data.columns)
    if missing:
        raise ValueError(
            f"Market data is missing columns: {sorted(missing)}"
        )

    result = market_data.copy()
    result.index = result.index.tz_convert("UTC")

    if result["bar_close_time"].isna().any():
        raise ValueError("bar_close_time cannot contain missing values.")

    close_times = pd.to_datetime(
        result["bar_close_time"],
        errors="raise",
        utc=True,
    )

    if (close_times.array <= result.index.array).any():
        raise ValueError("Each bar must close after its opening timestamp.")

    result["bar_close_time"] = close_times

    # Derive the strategy close from authoritative bid/ask observations.
    for column in ("bid_open", "ask_open", "bid_close", "ask_close"):
        result[column] = pd.to_numeric(result[column], errors="raise")
        if not result[column].map(
            lambda value: math.isfinite(float(value)) and value > 0
        ).all():
            raise ValueError(f"{column} must contain finite positive prices.")

    if (result["ask_open"] < result["bid_open"]).any():
        raise ValueError("ask_open cannot be below bid_open.")
    if (result["ask_close"] < result["bid_close"]).any():
        raise ValueError("ask_close cannot be below bid_close.")

    result["close"] = (result["bid_close"] + result["ask_close"]) / 2.0

    return result


def _fingerprint(data: pd.DataFrame) -> str:
    """Hash the exact ordered observations used by the strategy and simulator."""

    digest = hashlib.sha256()
    digest.update("\x1f".join(map(str, data.columns)).encode("utf-8"))

    hashes = pd.util.hash_pandas_object(data, index=True)
    digest.update(hashes.to_numpy().tobytes())

    return digest.hexdigest()


def _evaluation_positions(
    signals: tuple[StrategySignal, ...],
    strategy_version: StrategyVersion,
    instrument_id: UUID,
    index: pd.DatetimeIndex,
) -> pd.Series:
    positions = pd.Series(0, index=index, dtype="int64")
    seen_effective_times: set[pd.Timestamp] = set()

    for signal in signals:
        if signal.strategy_version_id != strategy_version.strategy_version_id:
            raise ValueError("Strategy emitted a signal for a different version.")

        if signal.instrument_id != instrument_id:
            raise ValueError("Strategy emitted a signal for a different instrument.")

        effective_at = _utc_timestamp(signal.effective_at, "signal.effective_at")

        if effective_at not in index:
            raise ValueError(
                "Signal effective_at does not match a market-data opening timestamp."
            )

        if effective_at in seen_effective_times:
            raise ValueError("Strategy emitted duplicate signals for one opening time.")

        seen_effective_times.add(effective_at)

        exposure = signal.target_exposure
        if not isinstance(exposure, Decimal):
            raise TypeError("Signal target exposure must be Decimal.")

        if exposure not in (
            Decimal("-1"),
            Decimal("0"),
            Decimal("1"),
        ):
            raise ValueError(
                "The fixed-unit simulator supports only -1, 0, and 1 exposure."
            )

        positions.loc[effective_at] = int(exposure)

    return positions


def evaluate_strategy(
    *,
    registry: StrategyImplementationRegistry,
    strategy_version: StrategyVersion,
    instrument_id: UUID,
    market_data: pd.DataFrame,
    start: datetime | pd.Timestamp,
    end_exclusive: datetime | pd.Timestamp,
    config: EvaluationConfig = EvaluationConfig(),
) -> EvaluationResult:
    """Evaluate one exact strategy version over an independent time window.

    One observation immediately before the requested start is retained as
    a timing anchor. Its position is forced flat and the simulator excludes
    it. This permits a signal generated by that candle to execute at the
    first evaluation candle's open.

    Strategy indicators receive only data strictly before end_exclusive.
    No future observations are supplied to the implementation.
    """

    if not isinstance(strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be a StrategyVersion.")

    if not isinstance(instrument_id, UUID):
        raise TypeError("instrument_id must be a UUID.")

    if not isinstance(config, EvaluationConfig):
        raise TypeError("config must be an EvaluationConfig.")

    start_time = _utc_timestamp(start, "start")
    end_time = _utc_timestamp(end_exclusive, "end_exclusive")

    if start_time >= end_time:
        raise ValueError("start must be earlier than end_exclusive.")

    data = _validate_market_data(market_data)

    prefix = data.loc[data.index < end_time].copy()
    evaluation_data = prefix.loc[prefix.index >= start_time].copy()

    if evaluation_data.empty:
        raise ValueError("No market observations fall inside the evaluation window.")

    first_evaluation_time = evaluation_data.index[0]
    anchor_data = prefix.loc[prefix.index < first_evaluation_time]

    if anchor_data.empty:
        raise ValueError(
            "At least one preceding market observation is required as a timing anchor."
        )

    anchor_time = anchor_data.index[-1]
    simulation_input = prefix.loc[anchor_time:].copy()

    implementation = registry.resolve(
        strategy_version.implementation_key,
        strategy_version.implementation_version,
    )

    signals = tuple(
        implementation.generate_signals(
            instrument_id=instrument_id,
            strategy_version=strategy_version,
            market_data=prefix,
        )
    )

    all_positions = _evaluation_positions(
        signals,
        strategy_version,
        instrument_id,
        prefix.index,
    )

    # The anchor is deliberately flat. The simulator starts with no position.
    simulation_input["position"] = all_positions.reindex(
        simulation_input.index
    ).fillna(0).astype(int)
    simulation_input.loc[anchor_time, "position"] = 0

    simulation_results = simulate_next_open(
        simulation_input,
        initial_cash=config.initial_cash,
        units=config.units,
        commission_per_1000_units_per_side_usd=(
            config.commission_per_1000_units_per_side_usd
        ),
        slippage_pips=config.slippage_pips,
        pip_size=config.pip_size,
    )

    if simulation_results.empty:
        raise ValueError("The evaluation window produced no simulation results.")

    # The anchor must not appear in the simulator output.
    if simulation_results.index[0] != first_evaluation_time:
        raise ValueError(
            "The simulator output does not begin at the first evaluation candle."
        )

    metrics = calculate_performance(
        simulation_results,
        initial_equity=config.initial_cash,
        periods_per_year=config.periods_per_year,
        risk_free_rate_annual=config.risk_free_rate_annual,
    )

    return EvaluationResult(
        strategy_id=strategy_version.strategy_id,
        strategy_version_id=strategy_version.strategy_version_id,
        instrument_id=instrument_id,
        version_number=strategy_version.version_number,
        implementation_key=strategy_version.implementation_key,
        implementation_version=strategy_version.implementation_version,
        parameters_json=strategy_version.parameters_json,
        requested_start=start_time,
        requested_end_exclusive=end_time,
        actual_start=simulation_results.index[0],
        actual_end=simulation_results.index[-1],
        data_fingerprint=_fingerprint(prefix),
        config=config,
        metrics=metrics,
        simulation_results=simulation_results,
    )


def evaluate_in_sample_out_of_sample(
    *,
    registry: StrategyImplementationRegistry,
    strategy_version: StrategyVersion,
    instrument_id: UUID,
    market_data: pd.DataFrame,
    split_at: datetime | pd.Timestamp,
    start: datetime | pd.Timestamp | None = None,
    end_exclusive: datetime | pd.Timestamp | None = None,
    config: EvaluationConfig = EvaluationConfig(),
) -> InSampleOutOfSampleResult:
    """Evaluate two non-overlapping periods with independent capital and positions."""

    data = _validate_market_data(market_data)
    split_time = _utc_timestamp(split_at, "split_at")

    if split_time not in data.index:
        raise ValueError("split_at must match a market-data opening timestamp.")

    if start is None:
        if len(data.index) < 2:
            raise ValueError("At least two observations are required.")
        start_time = data.index[1]
    else:
        start_time = _utc_timestamp(start, "start")

    if end_exclusive is None:
        end_time = data.index[-1] + pd.Timedelta(nanoseconds=1)
    else:
        end_time = _utc_timestamp(end_exclusive, "end_exclusive")

    if not start_time < split_time < end_time:
        raise ValueError("Expected start < split_at < end_exclusive.")

    in_sample = evaluate_strategy(
        registry=registry,
        strategy_version=strategy_version,
        instrument_id=instrument_id,
        market_data=data,
        start=start_time,
        end_exclusive=split_time,
        config=config,
    )

    out_of_sample = evaluate_strategy(
        registry=registry,
        strategy_version=strategy_version,
        instrument_id=instrument_id,
        market_data=data,
        start=split_time,
        end_exclusive=end_time,
        config=config,
    )

    return InSampleOutOfSampleResult(
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        split_at=split_time.to_pydatetime(),
    )


def evaluate_walk_forward(
    *,
    registry: StrategyImplementationRegistry,
    strategy_version: StrategyVersion,
    instrument_id: UUID,
    market_data: pd.DataFrame,
    training_bars: int,
    test_bars: int,
    step_bars: int | None = None,
    config: EvaluationConfig = EvaluationConfig(),
) -> tuple[WalkForwardFold, ...]:
    """Evaluate fixed strategy parameters over rolling, non-overlapping test folds.

    Training windows define the historical context for each fold. This
    version does not optimize or retrain strategy parameters. Strategy
    implementations receive only observations available up to each test
    fold's end.
    """

    for name, value in (
        ("training_bars", training_bars),
        ("test_bars", test_bars),
    ):
        if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
            raise ValueError(f"{name} must be a positive integer.")

    if step_bars is None:
        step_bars = test_bars

    if (
        isinstance(step_bars, bool)
        or not isinstance(step_bars, Integral)
        or step_bars < test_bars
    ):
        raise ValueError("step_bars must be an integer at least as large as test_bars.")

    data = _validate_market_data(market_data)

    if len(data) < training_bars + test_bars:
        raise ValueError("Not enough observations for one complete walk-forward fold.")

    folds: list[WalkForwardFold] = []
    test_start_index = int(training_bars)
    fold_number = 1

    while test_start_index + test_bars <= len(data):
        test_end_index = test_start_index + test_bars

        training_start = data.index[test_start_index - training_bars]
        test_start = data.index[test_start_index]

        if test_end_index < len(data):
            test_end_exclusive = data.index[test_end_index]
        else:
            test_end_exclusive = (
                data.index[test_end_index - 1]
                + pd.Timedelta(nanoseconds=1)
            )

        # Restrict each fold to its rolling training context plus test data.
        fold_market_data = data.loc[data.index >= training_start].copy()

        evaluation = evaluate_strategy(
            registry=registry,
            strategy_version=strategy_version,
            instrument_id=instrument_id,
            market_data=fold_market_data,
            start=test_start,
            end_exclusive=test_end_exclusive,
            config=config,
        )

        folds.append(
            WalkForwardFold(
                fold_number=fold_number,
                training_start=training_start.to_pydatetime(),
                training_end_exclusive=test_start.to_pydatetime(),
                test_start=test_start.to_pydatetime(),
                test_end_exclusive=test_end_exclusive,
                evaluation=evaluation,
            )
        )

        fold_number += 1
        test_start_index += int(step_bars)

    if not folds:
        raise ValueError("No complete walk-forward test folds were generated.")

    return tuple(folds)
