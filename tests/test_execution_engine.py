from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.execution.application.simulated_execution import (
    ExecutionOutcome,
    MissingLiquidityPolicy,
    SimulatedExecutionConfig,
    SimulatedExecutionEngine,
)
from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    AccountStatus,
    OrderSide,
    OrderStatus,
)
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def account():
    return PaperTradingAccount(
        name="Test paper account",
        base_currency="USD",
        initial_capital=Decimal("100000"),
        created_at=NOW,
    )


@pytest.fixture
def instrument():
    return Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        price_precision=5,
        quantity_precision=2,
        tick_size=Decimal("0.0001"),
    )


@pytest.fixture
def quote():
    return BidAskQuote(
        symbol="EURUSD",
        timestamp=NOW,
        bid=Decimal("1.1000"),
        ask=Decimal("1.1002"),
        bid_size=Decimal("5000"),
        ask_size=Decimal("5000"),
    )


def make_order(account, instrument, side=OrderSide.BUY, quantity="1000"):
    return Order(
        account_id=account.account_id,
        instrument_id=instrument.instrument_id,
        side=side,
        quantity=Decimal(quantity),
        created_at=NOW,
        updated_at=NOW,
    )


def execute(engine, order, account, instrument, quote, seconds=1, config=None):
    return engine.execute(
        order=order,
        account=account,
        instrument=instrument,
        quote=quote,
        executed_at=NOW + timedelta(seconds=seconds),
        config=config,
    )


def test_buy_executes_at_ask_and_charges_commission(account, instrument, quote):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        quote,
        config=SimulatedExecutionConfig(
            commission_per_1000_units=Decimal("2.50")
        ),
    )

    assert result.outcome is ExecutionOutcome.FILLED
    assert result.order.status is OrderStatus.FILLED
    assert result.fills[0].price == Decimal("1.1002")
    assert result.fills[0].commission == Decimal("2.50")
    assert result.fills[0].commission_currency == "USD"
    assert result.reference_price == Decimal("1.1002")
    assert result.slippage_cost == Decimal("0")


def test_sell_executes_at_bid(account, instrument, quote):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument, OrderSide.SELL),
        account,
        instrument,
        quote,
    )

    assert result.outcome is ExecutionOutcome.FILLED
    assert result.fills[0].price == Decimal("1.1000")


def test_buy_slippage_is_adverse_and_reported(account, instrument, quote):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        quote,
        config=SimulatedExecutionConfig(slippage_ticks=2),
    )

    assert result.fills[0].price == Decimal("1.1004")
    assert result.slippage_cost == Decimal("0.2000")


def test_sell_slippage_is_adverse_and_reported(account, instrument, quote):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument, OrderSide.SELL),
        account,
        instrument,
        quote,
        config=SimulatedExecutionConfig(slippage_ticks=2),
    )

    assert result.fills[0].price == Decimal("1.0998")
    assert result.slippage_cost == Decimal("0.2000")


def test_quote_liquidity_caps_fill_and_order_can_finish_later(
    account, instrument, quote
):
    engine = SimulatedExecutionEngine()
    order = make_order(account, instrument)

    first = execute(
        engine,
        order,
        account,
        instrument,
        replace(quote, ask_size=Decimal("400")),
    )

    assert first.outcome is ExecutionOutcome.PARTIALLY_FILLED
    assert first.fills[0].quantity == Decimal("400.00")
    assert first.order.filled_quantity == Decimal("400.00")

    second_quote = replace(
        quote,
        timestamp=NOW + timedelta(seconds=2),
        ask_size=Decimal("1000"),
    )
    second = engine.execute(
        first.order,
        account,
        instrument,
        second_quote,
        NOW + timedelta(seconds=3),
    )

    assert second.outcome is ExecutionOutcome.FILLED
    assert second.fills[0].quantity == Decimal("600.00")
    assert second.order.filled_quantity == Decimal("1000.00")


def test_missing_liquidity_does_not_assume_full_fill_by_default(
    account, instrument, quote
):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        replace(quote, ask_size=None),
    )

    assert result.outcome is ExecutionOutcome.NO_LIQUIDITY
    assert result.order.status is OrderStatus.ACCEPTED
    assert result.fills == ()
    assert result.liquidity_assumed is False


def test_explicit_missing_liquidity_policy_assumes_full_fill(
    account, instrument, quote
):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        replace(quote, ask_size=None),
        config=SimulatedExecutionConfig(
            missing_liquidity_policy=MissingLiquidityPolicy.ASSUME_FULL_FILL
        ),
    )

    assert result.outcome is ExecutionOutcome.FILLED
    assert result.fills[0].quantity == Decimal("1000")
    assert result.liquidity_assumed is True


def test_zero_liquidity_leaves_order_accepted_without_fill(
    account, instrument, quote
):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        replace(quote, ask_size=Decimal("0")),
    )

    assert result.outcome is ExecutionOutcome.NO_LIQUIDITY
    assert result.order.status is OrderStatus.ACCEPTED
    assert result.fills == ()


def test_stale_quote_rejects_pending_order(account, instrument, quote):
    stale = replace(quote, timestamp=NOW - timedelta(seconds=10))
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        stale,
    )

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.order.status is OrderStatus.REJECTED
    assert "older" in result.reason


def test_crossed_quote_rejects_pending_order(account, instrument, quote):
    crossed = replace(
        quote,
        bid=Decimal("1.1010"),
        ask=Decimal("1.1000"),
    )
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        crossed,
    )

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.order.status is OrderStatus.REJECTED
    assert "Crossed" in result.reason


def test_future_quote_is_rejected(account, instrument, quote):
    future = replace(quote, timestamp=NOW + timedelta(seconds=2))
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        future,
    )

    assert result.outcome is ExecutionOutcome.REJECTED
    assert "later" in result.reason


def test_currency_mismatch_is_rejected(account, instrument, quote):
    eur_account = replace(account, base_currency="EUR")
    result = execute(
        SimulatedExecutionEngine(),
        make_order(eur_account, instrument),
        eur_account,
        instrument,
        quote,
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "quote currency" in result.reason


def test_inactive_account_is_rejected(account, instrument, quote):
    disabled = replace(account, status=AccountStatus.DISABLED)
    result = execute(
        SimulatedExecutionEngine(),
        make_order(disabled, instrument),
        disabled,
        instrument,
        quote,
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "not active" in result.reason


def test_inactive_instrument_is_rejected(account, instrument, quote):
    inactive = replace(instrument, is_active=False)
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, inactive),
        account,
        inactive,
        quote,
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "inactive" in result.reason


def test_quote_symbol_mismatch_is_rejected(account, instrument, quote):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        replace(quote, symbol="GBPUSD"),
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "symbol" in result.reason


def test_instrument_id_mismatch_is_rejected(account, instrument, quote):
    other_instrument = Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        tick_size=Decimal("0.0001"),
    )
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, other_instrument),
        account,
        instrument,
        quote,
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "instrument_id" in result.reason


def test_order_quantity_precision_is_enforced(account, instrument, quote):
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument, quantity="1.001"),
        account,
        instrument,
        quote,
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "precision" in result.reason


def test_slippage_requires_tick_size(account, quote):
    no_tick = Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        quote_currency="USD",
        price_precision=5,
        quantity_precision=2,
    )
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, no_tick),
        account,
        no_tick,
        quote,
        config=SimulatedExecutionConfig(slippage_ticks=1),
    )

    assert result.order.status is OrderStatus.REJECTED
    assert "tick_size" in result.reason


def test_nonpositive_sell_execution_price_does_not_create_fill(
    account, instrument
):
    tiny_quote = BidAskQuote(
        symbol="EURUSD",
        timestamp=NOW,
        bid=Decimal("0.0001"),
        ask=Decimal("0.0002"),
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
    )
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument, OrderSide.SELL),
        account,
        instrument,
        tiny_quote,
        config=SimulatedExecutionConfig(slippage_ticks=1),
    )

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.fills == ()
    assert result.order.status is OrderStatus.ACCEPTED


def test_cancel_active_order(account, instrument, quote):
    first = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        replace(quote, ask_size=Decimal("100")),
    )
    cancelled = SimulatedExecutionEngine.cancel(
        first.order,
        NOW + timedelta(seconds=2),
    )

    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.filled_quantity == Decimal("100.00")


def test_already_rejected_order_is_not_executed(account, instrument, quote):
    rejected = make_order(account, instrument).reject(
        NOW + timedelta(seconds=1)
    )
    result = execute(
        SimulatedExecutionEngine(),
        rejected,
        account,
        instrument,
        quote,
        seconds=2,
    )

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.order == rejected
    assert result.fills == ()


def test_execution_time_cannot_precede_order_update(account, instrument, quote):
    accepted = make_order(account, instrument).accept(
        NOW + timedelta(seconds=2)
    )

    with pytest.raises(ValueError, match="earlier"):
        execute(
            SimulatedExecutionEngine(),
            accepted,
            account,
            instrument,
            quote,
            seconds=1,
        )


def test_config_rejects_negative_commission():
    with pytest.raises(ValueError, match="nonnegative"):
        SimulatedExecutionConfig(
            commission_per_1000_units=Decimal("-1")
        )


def test_config_rejects_boolean_slippage_ticks():
    with pytest.raises(TypeError, match="integer"):
        SimulatedExecutionConfig(slippage_ticks=True)


def test_config_rejects_zero_quote_age():
    with pytest.raises(ValueError, match="positive"):
        SimulatedExecutionConfig(max_quote_age=timedelta(0))


def test_unbounded_quote_age_can_be_explicitly_configured(
    account, instrument, quote
):
    old_quote = replace(quote, timestamp=NOW - timedelta(days=1))
    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        old_quote,
        config=SimulatedExecutionConfig(max_quote_age=None),
    )

    assert result.outcome is ExecutionOutcome.FILLED

def test_zero_slippage_preserves_off_tick_executable_quote(
    account, instrument
):
    off_tick_quote = BidAskQuote(
        symbol="EURUSD",
        timestamp=NOW,
        bid=Decimal("1.10003"),
        ask=Decimal("1.10007"),
        bid_size=Decimal("5000"),
        ask_size=Decimal("5000"),
    )

    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        off_tick_quote,
        config=SimulatedExecutionConfig(slippage_ticks=0),
    )

    assert result.outcome is ExecutionOutcome.FILLED
    assert result.reference_price == Decimal("1.10007")
    assert result.fills[0].price == Decimal("1.10007")
    assert result.slippage_cost == Decimal("0")


def test_tick_slippage_rejects_off_tick_executable_quote(
    account, instrument
):
    off_tick_quote = BidAskQuote(
        symbol="EURUSD",
        timestamp=NOW,
        bid=Decimal("1.10003"),
        ask=Decimal("1.10007"),
        bid_size=Decimal("5000"),
        ask_size=Decimal("5000"),
    )

    result = execute(
        SimulatedExecutionEngine(),
        make_order(account, instrument),
        account,
        instrument,
        off_tick_quote,
        config=SimulatedExecutionConfig(slippage_ticks=1),
    )

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.order.status is OrderStatus.REJECTED
    assert result.fills == ()
    assert "not aligned" in result.reason

from uuid import UUID, uuid4


def test_simulator_preserves_caller_supplied_fill_id(
    account, instrument, quote
):
    expected_fill_id = uuid4()
    engine = SimulatedExecutionEngine()
    order = make_order(account, instrument)
    execution_time = NOW + timedelta(seconds=1)

    result = engine.execute(
        order=order,
        account=account,
        instrument=instrument,
        quote=quote,
        executed_at=execution_time,
        fill_id=expected_fill_id,
    )

    assert result.outcome is ExecutionOutcome.FILLED
    assert len(result.fills) == 1
    assert result.fills[0].fill_id == expected_fill_id


def test_simulator_repeated_execution_with_same_fill_id_is_deterministic(
    account, instrument, quote
):
    fill_id = uuid4()
    engine = SimulatedExecutionEngine()
    order = make_order(account, instrument)
    execution_time = NOW + timedelta(seconds=1)

    first = engine.execute(
        order=order,
        account=account,
        instrument=instrument,
        quote=quote,
        executed_at=execution_time,
        fill_id=fill_id,
    )

    second = engine.execute(
        order=order,
        account=account,
        instrument=instrument,
        quote=quote,
        executed_at=execution_time,
        fill_id=fill_id,
    )

    assert first == second
    assert first.fills[0].fill_id == second.fills[0].fill_id


def test_simulator_generates_fill_id_when_none_is_supplied(
    account, instrument, quote
):
    engine = SimulatedExecutionEngine()
    order = make_order(account, instrument)

    first = execute(engine, order, account, instrument, quote)
    second = execute(engine, order, account, instrument, quote)

    assert isinstance(first.fills[0].fill_id, UUID)
    assert isinstance(second.fills[0].fill_id, UUID)
    assert first.fills[0].fill_id != second.fills[0].fill_id
