from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    LedgerEntryType,
    OrderSide,
)
from atlas_quant.services.execution.domain.fills import Fill
from atlas_quant.services.execution.domain.ledger import CashLedgerEntry
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.execution.domain.portfolio import (
    apply_fill,
    create_portfolio,
    value_portfolio,
)
from atlas_quant.services.execution.domain.positions import Position, PositionMark
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument


D = Decimal
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_account() -> PaperTradingAccount:
    return PaperTradingAccount(
        name="Test Account",
        base_currency="USD",
        initial_capital=D("10000"),
        created_at=NOW,
    )


def make_instrument(
    quote_currency: str = "USD",
    contract_size: Decimal | None = None,
) -> Instrument:
    return Instrument(
        symbol="TEST",
        asset_class=AssetClass.EQUITY,
        quote_currency=quote_currency,
        contract_size=contract_size,
    )


def make_order(
    account: PaperTradingAccount,
    instrument: Instrument,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = D("10"),
) -> Order:
    return Order(
        account_id=account.account_id,
        instrument_id=instrument.instrument_id,
        side=side,
        quantity=quantity,
        created_at=NOW,
        updated_at=NOW,
    ).accept(NOW + timedelta(seconds=1))


def make_fill(
    order: Order,
    quantity: Decimal = D("10"),
    price: Decimal = D("100"),
    commission: Decimal = D("0"),
    commission_currency: str = "USD",
    executed_at: datetime = NOW + timedelta(seconds=2),
) -> Fill:
    return Fill(
        order_id=order.order_id,
        account_id=order.account_id,
        instrument_id=order.instrument_id,
        quantity=quantity,
        price=price,
        commission=commission,
        commission_currency=commission_currency,
        executed_at=executed_at,
    )


def apply(
    portfolio,
    order,
    fill,
    instrument,
):
    return apply_fill(
        portfolio,
        order,
        fill,
        quote_currency=instrument.quote_currency,
        contract_size=instrument.contract_size,
    )


def test_create_portfolio_records_initial_capital():
    account = make_account()
    portfolio = create_portfolio(account)

    assert portfolio.cash_balance == D("10000")
    assert len(portfolio.ledger_entries) == 1
    assert portfolio.ledger_entries[0].amount == D("10000")
    assert portfolio.ledger_entries[0].entry_type == LedgerEntryType.ADJUSTMENT


def test_ledger_entry_is_immutable_and_normalizes_currency():
    entry = CashLedgerEntry(
        account_id=uuid4(),
        entry_type=LedgerEntryType.ADJUSTMENT,
        amount=D("25"),
        currency="usd",
        reference_id=uuid4(),
        description="Test adjustment",
    )

    assert entry.currency == "USD"

    with pytest.raises(FrozenInstanceError):
        entry.amount = D("30")


def test_position_rejects_non_decimal_quantity():
    with pytest.raises(TypeError):
        Position(
            instrument_id=uuid4(),
            quote_currency="USD",
            quantity=10,
            average_entry_price=D("100"),
        )


def test_position_rejects_invalid_flat_average_price():
    with pytest.raises(ValueError):
        Position(
            instrument_id=uuid4(),
            quote_currency="USD",
            quantity=D("0"),
            average_entry_price=D("100"),
        )


def test_buy_fill_reduces_cash_and_creates_long_position():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    fill = make_fill(order)

    result = apply(portfolio, order, fill, instrument)

    assert result.order.filled_quantity == D("10")
    assert result.portfolio.cash_balance == D("9000")

    position = result.portfolio.positions[0]
    assert position.quantity == D("10")
    assert position.average_entry_price == D("100")
    assert position.realized_pnl == D("0")


def test_sell_fill_increases_cash_and_creates_short_position():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument, OrderSide.SELL)
    fill = make_fill(order)

    result = apply(portfolio, order, fill, instrument)

    assert result.portfolio.cash_balance == D("11000")

    position = result.portfolio.positions[0]
    assert position.quantity == D("-10")
    assert position.average_entry_price == D("100")


def test_commission_is_recorded_separately_and_reduces_cash():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    fill = make_fill(order, commission=D("2.50"))

    result = apply(portfolio, order, fill, instrument)

    assert result.portfolio.cash_balance == D("8997.50")
    assert len(result.portfolio.ledger_entries) == 3

    commission_entries = [
        item
        for item in result.portfolio.ledger_entries
        if item.entry_type == LedgerEntryType.COMMISSION
    ]
    assert len(commission_entries) == 1
    assert commission_entries[0].amount == D("-2.50")


def test_partial_close_realizes_pnl_and_retains_average_entry():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)

    buy_order = make_order(account, instrument)
    buy_result = apply(
        portfolio,
        buy_order,
        make_fill(buy_order),
        instrument,
    )

    sell_order = make_order(
        account,
        instrument,
        OrderSide.SELL,
        D("4"),
    )
    sell_result = apply(
        buy_result.portfolio,
        sell_order,
        make_fill(
            sell_order,
            quantity=D("4"),
            price=D("110"),
            executed_at=NOW + timedelta(seconds=3),
        ),
        instrument,
    )

    position = sell_result.portfolio.positions[0]
    assert position.quantity == D("6")
    assert position.average_entry_price == D("100")
    assert position.realized_pnl == D("40")


def test_full_close_leaves_flat_position_and_realized_pnl():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)

    buy_order = make_order(account, instrument)
    first = apply(portfolio, buy_order, make_fill(buy_order), instrument)

    sell_order = make_order(account, instrument, OrderSide.SELL)
    second = apply(
        first.portfolio,
        sell_order,
        make_fill(
            sell_order,
            price=D("110"),
            executed_at=NOW + timedelta(seconds=3),
        ),
        instrument,
    )

    position = second.portfolio.positions[0]
    assert position.quantity == D("0")
    assert position.average_entry_price == D("0")
    assert position.realized_pnl == D("100")


def test_reversal_closes_long_and_opens_short_at_fill_price():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)

    buy_order = make_order(account, instrument)
    first = apply(portfolio, buy_order, make_fill(buy_order), instrument)

    reverse_order = make_order(
        account,
        instrument,
        OrderSide.SELL,
        D("15"),
    )
    second = apply(
        first.portfolio,
        reverse_order,
        make_fill(
            reverse_order,
            quantity=D("15"),
            price=D("110"),
            executed_at=NOW + timedelta(seconds=3),
        ),
        instrument,
    )

    position = second.portfolio.positions[0]
    assert position.quantity == D("-5")
    assert position.average_entry_price == D("110")
    assert position.realized_pnl == D("100")


def test_short_position_realizes_profit_when_bought_back_lower():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)

    sell_order = make_order(account, instrument, OrderSide.SELL)
    first = apply(portfolio, sell_order, make_fill(sell_order), instrument)

    buy_order = make_order(account, instrument, OrderSide.BUY)
    second = apply(
        first.portfolio,
        buy_order,
        make_fill(
            buy_order,
            price=D("90"),
            executed_at=NOW + timedelta(seconds=3),
        ),
        instrument,
    )

    position = second.portfolio.positions[0]
    assert position.quantity == D("0")
    assert position.realized_pnl == D("100")


def test_contract_size_multiplies_trade_cash_and_pnl():
    account = make_account()
    instrument = make_instrument(contract_size=D("100"))
    portfolio = create_portfolio(account)
    order = make_order(account, instrument, quantity=D("2"))
    fill = make_fill(order, quantity=D("2"), price=D("10"))

    result = apply(portfolio, order, fill, instrument)

    assert result.portfolio.cash_balance == D("8000")
    assert result.portfolio.positions[0].quantity == D("2")


def test_duplicate_fill_is_rejected():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    fill = make_fill(order)

    first = apply(portfolio, order, fill, instrument)

    with pytest.raises(ValueError, match="already been applied"):
        apply(first.portfolio, order, fill, instrument)


def test_fill_from_another_order_is_rejected():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    other_order = make_order(account, instrument)
    fill = make_fill(other_order)

    with pytest.raises(ValueError, match="does not belong"):
        apply(portfolio, order, fill, instrument)


def test_quote_currency_must_match_account_currency():
    account = make_account()
    instrument = make_instrument(quote_currency="EUR")
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    fill = make_fill(order)

    with pytest.raises(ValueError, match="quote currency"):
        apply(portfolio, order, fill, instrument)


def test_commission_currency_must_match_account_currency():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    fill = make_fill(order, commission_currency="EUR")

    with pytest.raises(ValueError, match="Commission currency"):
        apply(portfolio, order, fill, instrument)


def test_fill_quantity_cannot_exceed_remaining_order_quantity():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument, quantity=D("5"))
    fill = make_fill(order, quantity=D("6"))

    with pytest.raises(ValueError, match="exceeds remaining"):
        apply(portfolio, order, fill, instrument)


def test_portfolio_equity_uses_bid_for_long_liquidation():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    result = apply(
        portfolio,
        order,
        make_fill(order, price=D("100")),
        instrument,
    )

    mark = PositionMark(
        instrument_id=instrument.instrument_id,
        bid=D("110"),
        ask=D("111"),
        timestamp=NOW + timedelta(minutes=1),
    )
    snapshot = value_portfolio(result.portfolio, (mark,))

    assert snapshot.cash_balance == D("9000")
    assert snapshot.position_market_value == D("1100")
    assert snapshot.equity == D("10100")
    assert snapshot.net_profit_loss == D("100")
    assert snapshot.unrealized_pnl == D("100")


def test_portfolio_equity_uses_ask_for_short_liquidation():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument, OrderSide.SELL)
    result = apply(
        portfolio,
        order,
        make_fill(order, price=D("100")),
        instrument,
    )

    mark = PositionMark(
        instrument_id=instrument.instrument_id,
        bid=D("89"),
        ask=D("90"),
        timestamp=NOW + timedelta(minutes=1),
    )
    snapshot = value_portfolio(result.portfolio, (mark,))

    assert snapshot.cash_balance == D("11000")
    assert snapshot.position_market_value == D("-900")
    assert snapshot.equity == D("10100")
    assert snapshot.unrealized_pnl == D("100")


def test_equity_reconciles_after_close_and_commission():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)

    buy_order = make_order(account, instrument)
    first = apply(
        portfolio,
        buy_order,
        make_fill(buy_order, commission=D("1")),
        instrument,
    )

    sell_order = make_order(account, instrument, OrderSide.SELL)
    second = apply(
        first.portfolio,
        sell_order,
        make_fill(
            sell_order,
            price=D("110"),
            commission=D("1"),
            executed_at=NOW + timedelta(seconds=3),
        ),
        instrument,
    )

    mark = PositionMark(
        instrument_id=instrument.instrument_id,
        bid=D("110"),
        ask=D("111"),
        timestamp=NOW + timedelta(minutes=1),
    )
    snapshot = value_portfolio(second.portfolio, (mark,))

    assert snapshot.equity == D("10098")
    assert snapshot.net_profit_loss == D("98")
    assert snapshot.realized_pnl == D("100")
    assert snapshot.unrealized_pnl == D("0")
    assert snapshot.commission_cost == D("2")


def test_missing_position_mark_is_rejected():
    account = make_account()
    instrument = make_instrument()
    portfolio = create_portfolio(account)
    order = make_order(account, instrument)
    result = apply(portfolio, order, make_fill(order), instrument)

    with pytest.raises(ValueError, match="Missing mark"):
        value_portfolio(result.portfolio, ())


def test_portfolio_state_rejects_cash_ledger_mismatch():
    account = make_account()
    portfolio = create_portfolio(account)

    from atlas_quant.services.execution.domain.portfolio import PortfolioState

    with pytest.raises(ValueError, match="does not reconcile"):
        PortfolioState(
            account=account,
            cash_balance=D("9999"),
            ledger_entries=portfolio.ledger_entries,
        )
def test_order_fill_timestamp_cannot_precede_acceptance():
    account = make_account()
    instrument = make_instrument()
    order = make_order(account, instrument)
    fill = make_fill(order, executed_at=NOW)

    portfolio = create_portfolio(account)

    with pytest.raises(ValueError, match="cannot move backwards"):
        apply(portfolio, order, fill, instrument)


def test_order_cancellation_timestamp_cannot_precede_acceptance():
    account = make_account()
    instrument = make_instrument()
    order = make_order(account, instrument)

    with pytest.raises(ValueError, match="cannot move backwards"):
        order.cancel(NOW)


def test_processed_fill_ids_must_reconcile_with_ledger():
    from atlas_quant.services.execution.domain.portfolio import PortfolioState

    account = make_account()
    instrument = make_instrument()
    order = make_order(account, instrument)
    fill = make_fill(order)
    result = apply(create_portfolio(account), order, fill, instrument)

    with pytest.raises(ValueError, match="Processed fill IDs must reconcile"):
        PortfolioState(
            account=account,
            cash_balance=result.portfolio.cash_balance,
            positions=result.portfolio.positions,
            ledger_entries=result.portfolio.ledger_entries,
            processed_fill_ids=frozenset(),
        )


def test_valuation_rejects_future_dated_position_mark():
    account = make_account()
    instrument = make_instrument()
    order = make_order(account, instrument)
    result = apply(
        create_portfolio(account),
        order,
        make_fill(order),
        instrument,
    )
    mark = PositionMark(
        instrument_id=instrument.instrument_id,
        bid=D("100"),
        ask=D("101"),
        timestamp=NOW + timedelta(minutes=3),
    )

    with pytest.raises(ValueError, match="later than the valuation time"):
        value_portfolio(
            result.portfolio,
            (mark,),
            as_of=NOW + timedelta(minutes=2),
        )


def test_valuation_can_reject_stale_position_mark():
    account = make_account()
    instrument = make_instrument()
    order = make_order(account, instrument)
    result = apply(
        create_portfolio(account),
        order,
        make_fill(order),
        instrument,
    )
    mark = PositionMark(
        instrument_id=instrument.instrument_id,
        bid=D("100"),
        ask=D("101"),
        timestamp=NOW + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="older than max_mark_age"):
        value_portfolio(
            result.portfolio,
            (mark,),
            as_of=NOW + timedelta(minutes=3),
            max_mark_age=timedelta(seconds=30),
        )


def test_valuation_records_explicit_valuation_time():
    account = make_account()
    instrument = make_instrument()
    order = make_order(account, instrument)
    result = apply(
        create_portfolio(account),
        order,
        make_fill(order),
        instrument,
    )
    valuation_time = NOW + timedelta(minutes=2)
    mark = PositionMark(
        instrument_id=instrument.instrument_id,
        bid=D("100"),
        ask=D("101"),
        timestamp=NOW + timedelta(minutes=1),
    )

    snapshot = value_portfolio(
        result.portfolio,
        (mark,),
        as_of=valuation_time,
        max_mark_age=timedelta(minutes=2),
    )

    assert snapshot.valued_at == valuation_time
