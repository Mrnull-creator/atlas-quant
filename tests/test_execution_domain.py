from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    OrderSide,
    OrderStatus,
)
from atlas_quant.services.execution.domain.fills import Fill
from atlas_quant.services.execution.domain.orders import Order


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def make_order(**overrides):
    values = {
        "account_id": uuid4(),
        "instrument_id": uuid4(),
        "side": OrderSide.BUY,
        "quantity": Decimal("1000"),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Order(**values)


def test_account_normalizes_currency():
    account = PaperTradingAccount(
        name="Research Account",
        base_currency=" usd ",
        initial_capital=Decimal("10000"),
    )

    assert account.base_currency == "USD"


def test_account_rejects_float_capital():
    with pytest.raises(TypeError, match="Decimal"):
        PaperTradingAccount(
            name="Research",
            base_currency="USD",
            initial_capital=10000.0,
        )


def test_account_rejects_invalid_currency():
    with pytest.raises(ValueError, match="three-letter"):
        PaperTradingAccount(
            name="Research",
            base_currency="US",
            initial_capital=Decimal("10000"),
        )


def test_order_starts_pending():
    order = make_order()

    assert order.status == OrderStatus.PENDING
    assert order.filled_quantity == Decimal("0")


def test_order_acceptance():
    order = make_order().accept(NOW)

    assert order.status == OrderStatus.ACCEPTED


def test_order_rejection():
    order = make_order().reject(NOW)

    assert order.status == OrderStatus.REJECTED


def test_accepted_order_can_be_partially_filled():
    order = make_order().accept(NOW)
    updated = order.apply_fill(Decimal("400"), NOW)

    assert updated.status == OrderStatus.PARTIALLY_FILLED
    assert updated.filled_quantity == Decimal("400")
    assert order.filled_quantity == Decimal("0")


def test_partial_order_can_be_filled_completely():
    order = make_order().accept(NOW)
    order = order.apply_fill(Decimal("400"), NOW)
    order = order.apply_fill(Decimal("600"), NOW)

    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("1000")


def test_order_rejects_overfill():
    order = make_order().accept(NOW)

    with pytest.raises(ValueError, match="exceeds"):
        order.apply_fill(Decimal("1001"), NOW)


def test_pending_order_cannot_be_filled():
    with pytest.raises(ValueError, match="active"):
        make_order().apply_fill(Decimal("100"), NOW)


def test_filled_order_cannot_be_cancelled():
    order = make_order().accept(NOW)
    order = order.apply_fill(Decimal("1000"), NOW)

    with pytest.raises(ValueError, match="active"):
        order.cancel(NOW)


def test_partially_filled_order_can_be_cancelled():
    order = make_order().accept(NOW)
    order = order.apply_fill(Decimal("400"), NOW)
    order = order.cancel(NOW)

    assert order.status == OrderStatus.CANCELLED
    assert order.filled_quantity == Decimal("400")


def test_order_is_immutable():
    order = make_order()

    with pytest.raises(FrozenInstanceError):
        order.quantity = Decimal("2000")


def test_fill_validates_and_normalizes_currency():
    fill = Fill(
        order_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        quantity=Decimal("100"),
        price=Decimal("1.1000"),
        commission=Decimal("0.05"),
        commission_currency=" usd ",
        executed_at=NOW,
    )

    assert fill.commission_currency == "USD"


def test_fill_rejects_float_price():
    with pytest.raises(TypeError, match="Decimal"):
        Fill(
            order_id=uuid4(),
            account_id=uuid4(),
            instrument_id=uuid4(),
            quantity=Decimal("100"),
            price=1.1,
            commission=Decimal("0"),
            commission_currency="USD",
            executed_at=NOW,
        )


def test_fill_is_immutable():
    fill = Fill(
        order_id=uuid4(),
        account_id=uuid4(),
        instrument_id=uuid4(),
        quantity=Decimal("100"),
        price=Decimal("1.1"),
        commission=Decimal("0"),
        commission_currency="USD",
        executed_at=NOW,
    )

    with pytest.raises(FrozenInstanceError):
        fill.price = Decimal("1.2")