import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atlas_quant.services.execution.application import (
    paper_trading_workflow as workflow_module,
)
from atlas_quant.services.execution.application.paper_trading_workflow import (
    PaperTradingWorkflow,
    WorkflowOutcome,
)
from atlas_quant.services.execution.domain.enums import OrderSide, OrderStatus
from atlas_quant.services.execution.domain.execution_attempts import (
    ExecutionAttemptOutcome,
    ExecutionAttemptStatus,
)
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.execution.domain.risk import RiskOutcome
from atlas_quant.services.execution.infrastructure.persistence.execution_repository import (
    StaleAccountStateError,
)


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def make_signal(*, signal_id=None, instrument_id=None):
    return SimpleNamespace(
        signal_id=signal_id or uuid4(),
        strategy_version_id=uuid4(),
        instrument_id=instrument_id or uuid4(),
        generated_at=NOW,
        effective_at=NOW,
        target_exposure=Decimal("0.5"),
    )


def make_instrument(instrument_id):
    # ExecutionAttemptInputs requires the actual Instrument domain type.
    instrument = object.__new__(workflow_module.Instrument)
    object.__setattr__(instrument, "instrument_id", instrument_id)
    object.__setattr__(instrument, "symbol", "EURUSD")
    object.__setattr__(instrument, "asset_class", "forex")
    object.__setattr__(instrument, "exchange", None)
    object.__setattr__(instrument, "base_currency", "EUR")
    object.__setattr__(instrument, "quote_currency", "USD")
    object.__setattr__(instrument, "price_precision", 5)
    object.__setattr__(instrument, "quantity_precision", 2)
    object.__setattr__(instrument, "tick_size", Decimal("0.00001"))
    object.__setattr__(instrument, "contract_size", Decimal("1"))
    object.__setattr__(instrument, "is_active", True)
    return instrument


def make_quote(*, timestamp=NOW):
    return workflow_module.BidAskQuote(
        symbol="EURUSD",
        timestamp=timestamp,
        bid=Decimal("1.10000"),
        ask=Decimal("1.10010"),
        bid_size=Decimal("100000"),
        ask_size=Decimal("100000"),
    )


def make_decision(
    *,
    outcome=RiskOutcome.APPROVED,
    order_side=OrderSide.BUY,
    order_quantity=Decimal("10"),
    reasons=(),
):
    return SimpleNamespace(
        outcome=outcome,
        requested_exposure=Decimal("0.5"),
        approved_exposure=Decimal("0.5"),
        equity=Decimal("10000"),
        target_quantity=Decimal("10"),
        order_side=order_side,
        order_quantity=order_quantity,
        reasons=reasons,
    )


def make_order(
    context,
    *,
    status=OrderStatus.PENDING,
    filled_quantity=Decimal("0"),
):
    return Order(
        account_id=context.account_id,
        instrument_id=context.instrument.instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        filled_quantity=filled_quantity,
        strategy_version_id=context.signal.strategy_version_id,
        signal_id=context.signal.signal_id,
        created_at=NOW,
        updated_at=NOW,
        status=status,
    )


def make_attempt(
    *,
    outcome=ExecutionAttemptOutcome.FILLED,
    status=ExecutionAttemptStatus.COMPLETED,
    attempt_id=None,
    order_id=None,
    reason=None,
):
    return SimpleNamespace(
        attempt_id=attempt_id or uuid4(),
        order_id=order_id or uuid4(),
        status=status,
        outcome=outcome,
        reason=reason,
    )


def make_prepared_result(order, *, account_revision=8, attempt=None):
    return SimpleNamespace(
        order=order,
        attempt=attempt or make_attempt(
            status=ExecutionAttemptStatus.PREPARED,
            outcome=None,
        ),
        account_revision=account_revision,
    )


def make_completed_result(
    order,
    *,
    outcome=ExecutionAttemptOutcome.FILLED,
    account_revision=9,
    portfolio=None,
    attempt=None,
    idempotent=False,
):
    return SimpleNamespace(
        order=order,
        attempt=attempt or make_attempt(outcome=outcome),
        portfolio=portfolio or SimpleNamespace(updated=True),
        account_revision=account_revision,
        idempotent=idempotent,
    )


def make_workflow(*, active_order=None):
    account_id = uuid4()
    signal = make_signal()
    instrument = make_instrument(signal.instrument_id)
    quote = make_quote()
    portfolio = SimpleNamespace(initial=True)

    state = SimpleNamespace(
        account_revision=7,
        portfolio=portfolio,
        active_order=active_order,
    )

    repository = Mock()
    repository.get_account_state = AsyncMock(return_value=state)
    repository.get_prepared_execution_attempts = AsyncMock(return_value=[])
    repository.get_order_by_signal = AsyncMock(return_value=None)
    repository.get_order = AsyncMock(return_value=None)
    repository.get_latest_execution_attempt = AsyncMock(return_value=None)
    repository.create_order_with_prepared_attempt = AsyncMock()
    repository.create_retry_execution_attempt = AsyncMock()
    repository.complete_execution_attempt = AsyncMock()

    engine = Mock()
    workflow = PaperTradingWorkflow(repository, engine)

    return SimpleNamespace(
        account_id=account_id,
        signal=signal,
        instrument=instrument,
        quote=quote,
        portfolio=portfolio,
        state=state,
        repository=repository,
        engine=engine,
        workflow=workflow,
    )


def run_process(context, *, signal=None, quote=None, executed_at=NOW):
    return asyncio.run(
        context.workflow.process_signal(
            account_id=context.account_id,
            signal=signal or context.signal,
            instrument=context.instrument,
            quote=quote or context.quote,
            risk_policy=object(),
            executed_at=executed_at,
        )
    )


def test_process_signal_creates_order_and_prepared_attempt_atomically(
    monkeypatch,
):
    context = make_workflow()
    decision = make_decision()
    order = make_order(context)
    prepared = make_prepared_result(order)
    completed = make_completed_result(order)

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        lambda *args, **kwargs: decision,
    )

    context.repository.create_order_with_prepared_attempt.return_value = prepared
    context.repository.complete_execution_attempt.return_value = completed

    result = run_process(context)

    assert result.outcome == WorkflowOutcome.EXECUTED
    assert result.signal_id == context.signal.signal_id
    assert result.account_revision == 9
    assert result.order is order
    assert result.attempt is completed.attempt
    assert result.portfolio is completed.portfolio
    assert result.risk_decision is decision

    context.repository.get_account_state.assert_awaited_once_with(
        context.account_id
    )
    context.repository.get_prepared_execution_attempts.assert_awaited_once_with(
        context.account_id
    )
    context.repository.create_order_with_prepared_attempt.assert_awaited_once()

    args, kwargs = (
        context.repository.create_order_with_prepared_attempt.call_args
    )
    created_order = args[0]
    assert created_order.account_id == context.account_id
    assert created_order.instrument_id == context.instrument.instrument_id
    assert created_order.signal_id == context.signal.signal_id
    assert created_order.strategy_version_id == context.signal.strategy_version_id
    assert created_order.side == OrderSide.BUY
    assert created_order.quantity == Decimal("10")
    assert kwargs["expected_account_revision"] == 7
    assert args[1].instrument is context.instrument
    assert args[1].quote is context.quote
    assert args[1].executed_at == NOW

    context.repository.complete_execution_attempt.assert_awaited_once_with(
        prepared.attempt.attempt_id,
        simulator=context.engine,
    )

    # The durable workflow must not use the legacy execution path.
    assert not hasattr(context.repository, "record_execution_result") or (
        not isinstance(
            context.repository.record_execution_result,
            AsyncMock,
        )
        or not context.repository.record_execution_result.await_count
    )


def test_process_signal_recovers_prepared_attempt_without_using_new_quote(
    monkeypatch,
):
    context = make_workflow()
    old_order = make_order(context)
    prepared_attempt = make_attempt(
        status=ExecutionAttemptStatus.PREPARED,
        outcome=None,
        order_id=old_order.order_id,
    )
    completed = make_completed_result(old_order)

    context.repository.get_prepared_execution_attempts.return_value = [
        prepared_attempt
    ]
    context.repository.get_order.return_value = old_order
    context.repository.complete_execution_attempt.return_value = completed

    def unexpected_risk_evaluation(*args, **kwargs):
        raise AssertionError("Recovery must not evaluate a new signal")

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        unexpected_risk_evaluation,
    )

    newer_quote = make_quote(timestamp=NOW + timedelta(minutes=5))
    result = run_process(
        context,
        quote=newer_quote,
        executed_at=NOW + timedelta(minutes=5),
    )

    assert result.order is old_order
    assert result.attempt is completed.attempt
    context.repository.complete_execution_attempt.assert_awaited_once_with(
        prepared_attempt.attempt_id,
        simulator=context.engine,
    )
    context.repository.create_order_with_prepared_attempt.assert_not_awaited()
    context.repository.create_retry_execution_attempt.assert_not_awaited()


def test_process_signal_retries_no_liquidity_for_same_signal():
    context = make_workflow()
    order = make_order(context, status=OrderStatus.ACCEPTED)
    previous_attempt = make_attempt(
        outcome=ExecutionAttemptOutcome.NO_LIQUIDITY,
    )
    prepared = make_prepared_result(order)
    completed = make_completed_result(
        order,
        outcome=ExecutionAttemptOutcome.FILLED,
    )

    context.state.active_order = order
    context.repository.get_order_by_signal.return_value = order
    context.repository.get_latest_execution_attempt.return_value = (
        previous_attempt
    )
    context.repository.create_retry_execution_attempt.return_value = (
        prepared.attempt
    )
    context.repository.complete_execution_attempt.return_value = completed

    retry_time = NOW + timedelta(minutes=1)
    retry_quote = make_quote(timestamp=retry_time)

    result = run_process(
        context,
        quote=retry_quote,
        executed_at=retry_time,
    )

    assert result.outcome == WorkflowOutcome.EXECUTED
    context.repository.create_retry_execution_attempt.assert_awaited_once()
    args, kwargs = context.repository.create_retry_execution_attempt.call_args
    assert args[0] == context.account_id
    assert args[1] == order.order_id
    assert args[2].quote is retry_quote
    assert kwargs["expected_account_revision"] == 7
    context.repository.create_order_with_prepared_attempt.assert_not_awaited()


def test_process_signal_retries_partial_fill_for_same_signal():
    context = make_workflow()
    order = make_order(
        context,
        status=OrderStatus.PARTIALLY_FILLED,
        filled_quantity=Decimal("2"),
    )
    previous_attempt = make_attempt(
        outcome=ExecutionAttemptOutcome.PARTIALLY_FILLED,
    )
    prepared = make_prepared_result(order)
    completed = make_completed_result(
        order,
        outcome=ExecutionAttemptOutcome.PARTIALLY_FILLED,
    )

    context.state.active_order = order
    context.repository.get_order_by_signal.return_value = order
    context.repository.get_latest_execution_attempt.return_value = (
        previous_attempt
    )
    context.repository.create_retry_execution_attempt.return_value = (
        prepared.attempt
    )
    context.repository.complete_execution_attempt.return_value = completed

    retry_time = NOW + timedelta(minutes=1)
    result = run_process(
        context,
        quote=make_quote(timestamp=retry_time),
        executed_at=retry_time,
    )

    assert result.outcome == WorkflowOutcome.PARTIALLY_EXECUTED
    context.repository.create_retry_execution_attempt.assert_awaited_once()
    context.repository.complete_execution_attempt.assert_awaited_once()


def test_unrelated_signal_is_blocked_by_active_order(monkeypatch):
    context = make_workflow()
    active_order = make_order(context)
    active_order = active_order
    unrelated_signal = make_signal(
        instrument_id=context.instrument.instrument_id
    )

    context.state.active_order = active_order
    context.repository.get_order_by_signal.return_value = None

    def unexpected_risk_evaluation(*args, **kwargs):
        raise AssertionError("Risk evaluation must not run")

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        unexpected_risk_evaluation,
    )

    result = run_process(context, signal=unrelated_signal)

    assert result.outcome == WorkflowOutcome.BLOCKED_ACTIVE_ORDER
    context.repository.create_order_with_prepared_attempt.assert_not_awaited()
    context.repository.create_retry_execution_attempt.assert_not_awaited()


def test_rejected_attempt_is_not_retried():
    context = make_workflow()
    order = make_order(context, status=OrderStatus.ACCEPTED)
    rejected_attempt = make_attempt(
        outcome=ExecutionAttemptOutcome.REJECTED,
    )

    context.state.active_order = order
    context.repository.get_order_by_signal.return_value = order
    context.repository.get_latest_execution_attempt.return_value = (
        rejected_attempt
    )

    result = run_process(
        context,
        quote=make_quote(timestamp=NOW + timedelta(minutes=1)),
        executed_at=NOW + timedelta(minutes=1),
    )

    assert result.outcome == WorkflowOutcome.EXECUTION_REJECTED
    context.repository.create_retry_execution_attempt.assert_not_awaited()
    context.repository.complete_execution_attempt.assert_not_awaited()


def test_risk_rejection_does_not_create_order_or_attempt(monkeypatch):
    context = make_workflow()
    decision = make_decision(
        outcome=RiskOutcome.REJECTED,
        order_side=None,
        order_quantity=Decimal("0"),
        reasons=("Maximum order exposure exceeded",),
    )

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        lambda *args, **kwargs: decision,
    )

    result = run_process(context)

    assert result.outcome == WorkflowOutcome.RISK_REJECTED
    assert result.risk_decision is decision
    assert result.reason == "Maximum order exposure exceeded"
    assert result.order is None

    context.repository.create_order_with_prepared_attempt.assert_not_awaited()
    context.repository.complete_execution_attempt.assert_not_awaited()


def test_no_order_required_does_not_create_attempt(monkeypatch):
    context = make_workflow()
    decision = make_decision(
        order_side=None,
        order_quantity=Decimal("0"),
    )

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        lambda *args, **kwargs: decision,
    )

    result = run_process(context)

    assert result.outcome == WorkflowOutcome.NO_ORDER_REQUIRED
    assert result.risk_decision is decision
    assert result.account_revision == 7
    assert result.order is None

    context.repository.create_order_with_prepared_attempt.assert_not_awaited()
    context.repository.complete_execution_attempt.assert_not_awaited()


def test_execution_before_signal_effective_time_is_rejected():
    context = make_workflow()

    with pytest.raises(
        ValueError,
        match="before the signal effective time",
    ):
        run_process(
            context,
            executed_at=NOW - timedelta(seconds=1),
        )

    context.repository.get_account_state.assert_not_awaited()


def test_stale_account_revision_is_propagated(monkeypatch):
    context = make_workflow()

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        lambda *args, **kwargs: make_decision(),
    )

    context.repository.create_order_with_prepared_attempt.side_effect = (
        StaleAccountStateError("Account state changed")
    )

    with pytest.raises(StaleAccountStateError):
        run_process(context)

    context.repository.create_order_with_prepared_attempt.assert_awaited_once()
    context.repository.complete_execution_attempt.assert_not_awaited()


def test_missing_account_is_rejected():
    context = make_workflow()
    context.repository.get_account_state.return_value = None

    with pytest.raises(
        LookupError,
        match="Paper-trading account not found",
    ):
        run_process(context)

    context.repository.get_prepared_execution_attempts.assert_not_awaited()
    context.repository.create_order_with_prepared_attempt.assert_not_awaited()


def test_simulator_interruption_leaves_prepared_attempt_for_recovery(
    monkeypatch,
):
    context = make_workflow()
    order = make_order(context)
    prepared = make_prepared_result(order)

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        lambda *args, **kwargs: make_decision(),
    )

    context.repository.create_order_with_prepared_attempt.return_value = (
        prepared
    )
    context.repository.complete_execution_attempt.side_effect = RuntimeError(
        "Simulated execution interrupted"
    )

    with pytest.raises(
        RuntimeError,
        match="Simulated execution interrupted",
    ):
        run_process(context)

    context.repository.create_order_with_prepared_attempt.assert_awaited_once()
    context.repository.complete_execution_attempt.assert_awaited_once_with(
        prepared.attempt.attempt_id,
        simulator=context.engine,
    )


def test_active_order_without_prepared_attempt_is_blocked(monkeypatch):
    context = make_workflow()
    active_order = make_order(context)
    context.state.active_order = active_order
    context.repository.get_order_by_signal.return_value = None

    def unexpected_risk_evaluation(*args, **kwargs):
        raise AssertionError("Risk evaluation must not run")

    monkeypatch.setattr(
        workflow_module,
        "evaluate_portfolio_risk",
        unexpected_risk_evaluation,
    )

    result = run_process(context)

    assert result.outcome == WorkflowOutcome.BLOCKED_ACTIVE_ORDER
    context.repository.create_order_with_prepared_attempt.assert_not_awaited()
    context.repository.create_retry_execution_attempt.assert_not_awaited()

def test_completed_signal_is_returned_idempotently():
    context = make_workflow()
    completed_order = make_order(
        context,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("10"),
    )
    completed_attempt = make_attempt(
        outcome=ExecutionAttemptOutcome.FILLED,
    )

    context.repository.get_order_by_signal.return_value = completed_order
    context.repository.get_latest_execution_attempt.return_value = (
        completed_attempt
    )

    def unexpected_risk_evaluation(*args, **kwargs):
        raise AssertionError("A completed signal must not be evaluated again")

    original_evaluator = workflow_module.evaluate_portfolio_risk
    workflow_module.evaluate_portfolio_risk = unexpected_risk_evaluation
    try:
        result = run_process(context)
    finally:
        workflow_module.evaluate_portfolio_risk = original_evaluator

    assert result.outcome == WorkflowOutcome.EXECUTED
    assert result.idempotent is True
    assert result.signal_id == context.signal.signal_id
    assert result.order is completed_order
    assert result.attempt is completed_attempt
    assert result.account_revision == context.state.account_revision

    context.repository.get_order_by_signal.assert_awaited_once_with(
        context.account_id,
        context.signal.signal_id,
    )
    context.repository.get_latest_execution_attempt.assert_awaited_once_with(
        completed_order.order_id
    )
    context.repository.create_order_with_prepared_attempt.assert_not_awaited()
    context.repository.create_retry_execution_attempt.assert_not_awaited()
    context.repository.complete_execution_attempt.assert_not_awaited()
