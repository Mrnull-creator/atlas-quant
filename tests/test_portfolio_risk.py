from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import AccountStatus, OrderSide
from atlas_quant.services.execution.domain.portfolio import create_portfolio
from atlas_quant.services.execution.domain.positions import Position, PositionMark
from atlas_quant.services.execution.domain.risk import RiskOutcome, RiskPolicy
from atlas_quant.services.execution.application.portfolio_risk import (
    evaluate_portfolio_risk,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture
def account():
    return PaperTradingAccount(
        name="Risk Test",
        base_currency="USD",
        initial_capital=D("10000"),
        created_at=NOW,
    )


@pytest.fixture
def instrument():
    return Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        quantity_precision=2,
        contract_size=D("1"),
    )


@pytest.fixture
def quote():
    return BidAskQuote(
        symbol="EURUSD",
        timestamp=NOW,
        bid=D("99"),
        ask=D("101"),
        bid_size=D("100000"),
        ask_size=D("100000"),
    )


@pytest.fixture
def policy():
    return RiskPolicy(
        max_position_exposure=D("0.50"),
        max_gross_portfolio_exposure=D("1.00"),
        max_order_exposure=D("0.25"),
    )


def evaluate(portfolio, instrument, quote, exposure, policy, **kwargs):
    return evaluate_portfolio_risk(
        portfolio,
        instrument,
        quote,
        exposure,
        policy,
        evaluated_at=NOW,
        **kwargs,
    )


def test_approves_exposure_within_all_limits(
    account, instrument, quote, policy
):
    decision = evaluate(
        create_portfolio(account),
        instrument,
        quote,
        D("0.20"),
        policy,
    )

    assert decision.outcome == RiskOutcome.APPROVED
    assert decision.requested_exposure == D("0.20")
    assert decision.target_quantity == D("19.80")
    assert decision.order_side == OrderSide.BUY
    assert decision.order_quantity == D("19.80")
    assert decision.equity == D("10000")


def test_reduces_target_to_position_limit(
    account, instrument, quote
):
    policy = RiskPolicy(
        max_position_exposure=D("0.10"),
        max_gross_portfolio_exposure=D("1.00"),
        max_order_exposure=D("1.00"),
    )

    decision = evaluate(
        create_portfolio(account),
        instrument,
        quote,
        D("0.50"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REDUCED
    assert decision.target_quantity == D("10.10")
    assert any("position" in reason for reason in decision.reasons)


def test_reduces_order_to_individual_order_limit(
    account, instrument, quote
):
    policy = RiskPolicy(
        max_position_exposure=D("1.00"),
        max_gross_portfolio_exposure=D("2.00"),
        max_order_exposure=D("0.05"),
    )

    decision = evaluate(
        create_portfolio(account),
        instrument,
        quote,
        D("0.50"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REDUCED
    assert decision.order_quantity == D("4.95")
    assert decision.target_quantity == D("4.95")
    assert any("individual order" in reason for reason in decision.reasons)


def test_uses_sell_side_for_short_target(
    account, instrument, quote, policy
):
    decision = evaluate(
        create_portfolio(account),
        instrument,
        quote,
        D("-0.20"),
        policy,
    )

    assert decision.outcome == RiskOutcome.APPROVED
    assert decision.target_quantity == D("-20.20")
    assert decision.order_side == OrderSide.SELL


def test_allows_risk_reducing_order_when_position_limit_is_breached(
    account, instrument, quote
):
    portfolio = create_portfolio(account)
    position = Position(
        instrument_id=instrument.instrument_id,
        quote_currency="USD",
        quantity=D("100"),
        average_entry_price=D("100"),
        contract_size=D("1"),
    )
    portfolio = replace(portfolio, positions=(position,))

    policy = RiskPolicy(
        max_position_exposure=D("0.10"),
        max_gross_portfolio_exposure=D("0.20"),
        max_order_exposure=D("0.01"),
    )

    decision = evaluate(
        portfolio,
        instrument,
        quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REDUCED
    assert decision.target_quantity < position.quantity
    assert decision.order_side == OrderSide.SELL
    assert decision.order_quantity > D("0")
    assert any("Risk-reducing" in reason for reason in decision.reasons)


def test_does_not_reverse_position_while_portfolio_is_over_limit(
    account, instrument, quote
):
    portfolio = create_portfolio(account)
    position = Position(
        instrument_id=instrument.instrument_id,
        quote_currency="USD",
        quantity=D("100"),
        average_entry_price=D("100"),
    )
    portfolio = replace(portfolio, positions=(position,))

    policy = RiskPolicy(
        max_position_exposure=D("0.10"),
        max_gross_portfolio_exposure=D("0.20"),
        max_order_exposure=D("0.01"),
    )

    decision = evaluate(
        portfolio,
        instrument,
        quote,
        D("-0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REDUCED
    assert decision.target_quantity == D("0")
    assert decision.order_side == OrderSide.SELL
    assert decision.order_quantity == D("100")


def test_rejects_inactive_account(account, instrument, quote, policy):
    disabled = replace(account, status=AccountStatus.DISABLED)

    decision = evaluate(
        create_portfolio(disabled),
        instrument,
        quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED
    assert decision.order_quantity == D("0")
    assert decision.approved_exposure is None


def test_rejects_inactive_instrument(account, instrument, quote, policy):
    inactive = replace(instrument, is_active=False)

    decision = evaluate(
        create_portfolio(account),
        inactive,
        quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED


def test_rejects_stale_quote(account, instrument, quote, policy):
    stale_quote = replace(quote, timestamp=NOW - timedelta(seconds=10))

    decision = evaluate(
        create_portfolio(account),
        instrument,
        stale_quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED
    assert "older" in decision.reasons[0]


def test_rejects_future_quote(account, instrument, quote, policy):
    future_quote = replace(quote, timestamp=NOW + timedelta(seconds=1))

    decision = evaluate(
        create_portfolio(account),
        instrument,
        future_quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED
    assert "future" in decision.reasons[0]


def test_rejects_crossed_quote(account, instrument, quote, policy):
    crossed = replace(quote, bid=D("102"), ask=D("101"))

    decision = evaluate(
        create_portfolio(account),
        instrument,
        crossed,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED
    assert "crossed" in decision.reasons[0]


def test_rejects_currency_mismatch(account, quote, policy):
    instrument = Instrument(
        symbol="EURGBP",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="GBP",
    )
    quote = replace(quote, symbol="EURGBP")

    decision = evaluate(
        create_portfolio(account),
        instrument,
        quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED
    assert "FX conversion" in decision.reasons[0]


def test_requires_marks_for_other_open_positions(
    account, instrument, quote, policy
):
    other_instrument_id = Instrument(
        symbol="GBPUSD",
        asset_class=AssetClass.FOREX,
        base_currency="GBP",
        quote_currency="USD",
    ).instrument_id

    portfolio = create_portfolio(account)
    other_position = Position(
        instrument_id=other_instrument_id,
        quote_currency="USD",
        quantity=D("10"),
        average_entry_price=D("1.25"),
    )
    portfolio = replace(portfolio, positions=(other_position,))

    decision = evaluate(
        portfolio,
        instrument,
        quote,
        D("0.10"),
        policy,
    )

    assert decision.outcome == RiskOutcome.REJECTED
    assert "mark is required" in decision.reasons[0]


def test_uses_existing_position_mark_and_includes_gross_exposure(
    account, instrument, quote
):
    other_instrument_id = Instrument(
        symbol="GBPUSD",
        asset_class=AssetClass.FOREX,
        base_currency="GBP",
        quote_currency="USD",
    ).instrument_id

    portfolio = create_portfolio(account)
    other_position = Position(
        instrument_id=other_instrument_id,
        quote_currency="USD",
        quantity=D("100"),
        average_entry_price=D("1.25"),
    )
    portfolio = replace(portfolio, positions=(other_position,))

    mark = PositionMark(
        instrument_id=other_instrument_id,
        bid=D("1.20"),
        ask=D("1.21"),
        timestamp=NOW,
    )
    policy = RiskPolicy(
        max_position_exposure=D("1.00"),
        max_gross_portfolio_exposure=D("0.10"),
        max_order_exposure=D("1.00"),
    )

    decision = evaluate(
        portfolio,
        instrument,
        quote,
        D("0.50"),
        policy,
        position_marks=(mark,),
    )

    assert decision.outcome == RiskOutcome.REDUCED
    assert decision.target_quantity == D("9.01")
    assert any("gross" in reason or "position" in reason for reason in decision.reasons)


def test_policy_rejects_nonpositive_or_non_decimal_limits():
    with pytest.raises(ValueError):
        RiskPolicy(D("0"), D("1"), D("1"))

    with pytest.raises(TypeError):
        RiskPolicy(0.5, D("1"), D("1"))
