from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest

from atlas_quant.backtesting.next_open_simulator import simulate_next_open
from atlas_quant.services.strategy_research.domain.strategies import (
    ExecutionTiming,
    StrategyVersion,
)
from atlas_quant.services.strategy_research.infrastructure.momentum import (
    MomentumStrategyImplementation,
)


def make_execution_data(positions):
    index = pd.date_range(
        "2026-01-01 10:00",
        periods=len(positions),
        freq="min",
        tz="UTC",
    )
    return pd.DataFrame(
        {
            "bid_open": [1.1000] * len(positions),
            "ask_open": [1.1002] * len(positions),
            "bid_close": [1.1000] * len(positions),
            "ask_close": [1.1002] * len(positions),
            "position": positions,
        },
        index=index,
    )


def test_execution_audit_records_entry_and_exit_fills():
    data = make_execution_data([0, 1, 0])

    result = simulate_next_open(
        data,
        initial_cash=10_000,
        units=1_000,
        commission_per_1000_units_per_side_usd=0.035,
        slippage_pips=1.0,
    )

    entry = result.iloc[0]
    exit_ = result.iloc[1]

    assert entry["execution_side"] == "buy"
    assert entry["traded_units"] == pytest.approx(1_000)
    assert entry["execution_price"] == pytest.approx(1.1003)
    assert entry["slippage_cost"] == pytest.approx(0.10)
    assert entry["commission"] == pytest.approx(0.035)

    assert exit_["execution_side"] == "sell"
    assert exit_["traded_units"] == pytest.approx(1_000)
    assert exit_["execution_price"] == pytest.approx(1.0999)
    assert exit_["slippage_cost"] == pytest.approx(0.10)
    assert exit_["commission"] == pytest.approx(0.035)

    assert not pd.isna(result["execution_price"].iloc[0])
    assert result["cash"].iloc[1] == pytest.approx(
        result["equity"].iloc[1]
    )


def test_no_trade_rows_have_empty_execution_audit():
    data = make_execution_data([0, 0, 0])

    result = simulate_next_open(data)

    assert (result["traded_units"] == 0).all()
    assert result["execution_side"].isna().all()
    assert result["execution_price"].isna().all()
    assert (result["slippage_cost"] == 0).all()
    assert (result["commission"] == 0).all()


def test_reversal_audit_records_full_two_sided_turnover():
    data = make_execution_data([0, 1, -1])

    result = simulate_next_open(
        data,
        units=1_000,
        commission_per_1000_units_per_side_usd=0.035,
    )

    reversal = result.iloc[1]

    assert reversal["execution_side"] == "sell"
    assert reversal["traded_units"] == pytest.approx(2_000)
    assert reversal["execution_price"] == pytest.approx(1.1000)
    assert reversal["commission"] == pytest.approx(0.070)
    assert reversal["units"] == pytest.approx(-1_000)


def test_timezone_naive_execution_timestamps_are_rejected():
    data = make_execution_data([0, 1])
    data.index = data.index.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        simulate_next_open(data)


def test_boolean_cost_setting_is_rejected():
    data = make_execution_data([0, 1])

    with pytest.raises(ValueError, match="finite number"):
        simulate_next_open(data, slippage_pips=True)


def make_momentum_version():
    return StrategyVersion(
        strategy_id=uuid4(),
        version_number=1,
        implementation_key="momentum",
        implementation_version="1.0.0",
        parameters_json='{"fast_period":2,"slow_period":3}',
        execution_timing=ExecutionTiming.NEXT_BAR_OPEN,
    )


def test_momentum_omits_warmup_rows_and_uses_next_available_open():
    opens = pd.DatetimeIndex(
        [
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:01:00Z",
            "2026-01-01T00:02:00Z",
            "2026-01-01T00:10:00Z",
            "2026-01-01T00:11:00Z",
        ]
    )

    closes = [
        timestamp + timedelta(minutes=1)
        for timestamp in opens
    ]

    market_data = pd.DataFrame(
        {
            "close": [1.0, 2.0, 3.0, 2.0, 1.0],
            "bar_close_time": closes,
        },
        index=opens,
    )

    implementation = MomentumStrategyImplementation()
    signals = implementation.generate_signals(
        instrument_id=uuid4(),
        strategy_version=make_momentum_version(),
        market_data=market_data,
    )

    # The first two rows are indicator warmup and emit no signal.
    assert len(signals) == 2

    first_signal = signals[0]

    # The first valid indicator value is at the third bar.
    assert first_signal.generated_at == closes[2].to_pydatetime()

    # The following bar opens after a session/data gap.
    assert first_signal.effective_at == opens[3].to_pydatetime()
    assert first_signal.effective_at > first_signal.generated_at

    assert isinstance(first_signal.target_exposure, Decimal)