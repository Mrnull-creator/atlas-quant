"""Orchestrate risk-checked, durable paper-trading execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Sequence
from uuid import UUID

from atlas_quant.services.execution.application.portfolio_risk import (
    evaluate_portfolio_risk,
)
from atlas_quant.services.execution.application.simulated_execution import (
    ExecutionOutcome,
    MissingLiquidityPolicy,
    SimulatedExecutionConfig,
    SimulatedExecutionEngine,
    SimulatedExecutionResult,
)
from atlas_quant.services.execution.domain.enums import OrderSide
from atlas_quant.services.execution.domain.execution_attempts import (
    ExecutionAttempt,
    ExecutionAttemptInputs,
    ExecutionAttemptOutcome,
    ExecutionAttemptStatus,
)
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.execution.domain.positions import PositionMark
from atlas_quant.services.execution.domain.risk import (
    RiskDecision,
    RiskOutcome,
    RiskPolicy,
)
from atlas_quant.services.execution.infrastructure.persistence.execution_repository import (
    SQLAlchemyExecutionRepository,
)
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.strategy_research.domain.strategies import StrategySignal


class WorkflowOutcome(StrEnum):
    """High-level result of processing a strategy signal."""

    BLOCKED_ACTIVE_ORDER = "blocked_active_order"
    RISK_REJECTED = "risk_rejected"
    NO_ORDER_REQUIRED = "no_order_required"
    EXECUTED = "executed"
    PARTIALLY_EXECUTED = "partially_executed"
    NO_LIQUIDITY = "no_liquidity"
    EXECUTION_REJECTED = "execution_rejected"


@dataclass(frozen=True, slots=True)
class PaperTradingWorkflowResult:
    """Result of processing one strategy signal."""

    outcome: WorkflowOutcome
    signal_id: UUID
    account_revision: int
    portfolio: object
    risk_decision: RiskDecision | None = None
    order: Order | None = None
    attempt: ExecutionAttempt | None = None
    execution: SimulatedExecutionResult | None = None
    reason: str | None = None
    idempotent: bool = False


class PaperTradingWorkflow:
    """Coordinate risk evaluation and durable simulated execution."""

    def __init__(
        self,
        repository: SQLAlchemyExecutionRepository,
        execution_engine: SimulatedExecutionEngine,
    ) -> None:
        self._repository = repository
        self._execution_engine = execution_engine

    async def process_signal(
        self,
        *,
        account_id: UUID,
        signal: StrategySignal,
        instrument: Instrument,
        quote: BidAskQuote,
        risk_policy: RiskPolicy,
        executed_at: datetime,
        position_marks: Sequence[PositionMark] = (),
        execution_config: SimulatedExecutionConfig | None = None,
        max_risk_quote_age: timedelta = timedelta(seconds=5),
    ) -> PaperTradingWorkflowResult:
        """Process a signal, recovering durable work before new execution."""

        execution_time = self._normalize_execution_time(executed_at)

        if signal.instrument_id != instrument.instrument_id:
            raise ValueError("Signal instrument does not match instrument")

        if execution_time < signal.effective_at:
            raise ValueError(
                "Execution cannot occur before the signal effective time"
            )

        state = await self._repository.get_account_state(account_id)

        if state is None:
            raise LookupError(
                f"Paper-trading account not found: {account_id}"
            )

        # Recovery always takes priority over a new signal. The persisted
        # attempt contains the authoritative quote, instrument and config.
        prepared_attempts = (
            await self._repository.get_prepared_execution_attempts(account_id)
        )

        if prepared_attempts:
            prepared = prepared_attempts[0]
            recovered = await self._repository.complete_execution_attempt(
                prepared.attempt_id,
                simulator=self._execution_engine,
            )
            recovered_order = await self._repository.get_order(
                account_id,
                prepared.order_id,
            )

            if recovered_order is None:
                raise RuntimeError(
                    "Prepared execution attempt references a missing order"
                )

            return self._result_from_attempt(
                persisted=recovered,
                signal_id=recovered_order.signal_id or signal.signal_id,
                risk_decision=None,
            )

        # Look up the signal independently of active-order state. This
        # prevents a repeated completed signal from creating another order.
        existing_order = await self._repository.get_order_by_signal(
            account_id,
            signal.signal_id,
        )

        active_order = state.active_order

        if active_order is not None:
            if active_order.signal_id != signal.signal_id:
                return self._blocked_result(
                    signal_id=signal.signal_id,
                    state=state,
                    reason=(
                        "An active order belonging to another signal "
                        "already exists for this paper-trading account"
                    ),
                )

            return await self._process_active_order_retry(
                account_id=account_id,
                signal_id=signal.signal_id,
                active_order=active_order,
                state=state,
                instrument=instrument,
                quote=quote,
                execution_time=execution_time,
                execution_config=execution_config,
            )

        if existing_order is not None:
            latest = await self._repository.get_latest_execution_attempt(
                existing_order.order_id
            )

            if latest is None:
                raise RuntimeError(
                    "An existing signal order has no durable execution attempt"
                )

            if latest.status == ExecutionAttemptStatus.PREPARED:
                # Defensive fallback. Normally handled by the account-wide
                # prepared-attempt recovery above.
                persisted = await self._repository.complete_execution_attempt(
                    latest.attempt_id,
                    simulator=self._execution_engine,
                )
                return self._result_from_attempt(
                    persisted=persisted,
                    signal_id=signal.signal_id,
                    risk_decision=None,
                )

            if latest.status == ExecutionAttemptStatus.COMPLETED:
                return self._existing_attempt_result(
                    latest=latest,
                    order=existing_order,
                    state=state,
                    signal_id=signal.signal_id,
                )

            return PaperTradingWorkflowResult(
                outcome=WorkflowOutcome.EXECUTION_REJECTED,
                signal_id=signal.signal_id,
                account_revision=state.account_revision,
                portfolio=state.portfolio,
                order=existing_order,
                attempt=latest,
                reason=latest.reason or "The execution attempt failed",
                idempotent=True,
            )

        risk_decision = evaluate_portfolio_risk(
            state.portfolio,
            instrument,
            quote,
            signal.target_exposure,
            risk_policy,
            evaluated_at=execution_time,
            position_marks=tuple(position_marks),
            max_quote_age=max_risk_quote_age,
        )

        if risk_decision.outcome == RiskOutcome.REJECTED:
            return PaperTradingWorkflowResult(
                outcome=WorkflowOutcome.RISK_REJECTED,
                signal_id=signal.signal_id,
                account_revision=state.account_revision,
                portfolio=state.portfolio,
                risk_decision=risk_decision,
                reason=(
                    "; ".join(risk_decision.reasons)
                    or "Risk policy rejected signal"
                ),
            )

        if (
            risk_decision.order_side is None
            or risk_decision.order_quantity <= Decimal("0")
        ):
            return PaperTradingWorkflowResult(
                outcome=WorkflowOutcome.NO_ORDER_REQUIRED,
                signal_id=signal.signal_id,
                account_revision=state.account_revision,
                portfolio=state.portfolio,
                risk_decision=risk_decision,
                reason="Risk evaluation produced no required order",
            )

        order = Order(
            account_id=account_id,
            instrument_id=instrument.instrument_id,
            side=risk_decision.order_side,
            quantity=risk_decision.order_quantity,
            strategy_version_id=signal.strategy_version_id,
            signal_id=signal.signal_id,
            created_at=execution_time,
            updated_at=execution_time,
        )

        inputs = self._build_attempt_inputs(
            instrument=instrument,
            quote=quote,
            executed_at=execution_time,
            execution_config=execution_config,
            order_updated_at=order.updated_at,
        )

        # The order and its first PREPARED attempt are committed together.
        prepared = await self._repository.create_order_with_prepared_attempt(
            order,
            inputs,
            expected_account_revision=state.account_revision,
        )

        # Simulation and financial completion use the persisted snapshot.
        prepared_attempt = getattr(prepared, "attempt", prepared)

        persisted = await self._repository.complete_execution_attempt(
            prepared_attempt.attempt_id,
            simulator=self._execution_engine,
        )

        return self._result_from_attempt(
            persisted=persisted,
            signal_id=signal.signal_id,
            risk_decision=risk_decision,
        )

    async def _process_active_order_retry(
        self,
        *,
        account_id: UUID,
        signal_id: UUID,
        active_order: Order,
        state: object,
        instrument: Instrument,
        quote: BidAskQuote,
        execution_time: datetime,
        execution_config: SimulatedExecutionConfig | None,
    ) -> PaperTradingWorkflowResult:
        """Retry an eligible active order using a new immutable snapshot."""

        latest = await self._repository.get_latest_execution_attempt(
            active_order.order_id
        )

        if latest is None:
            return self._blocked_result(
                signal_id=signal_id,
                state=state,
                reason="The active order has no recorded execution attempt",
            )

        if latest.status != ExecutionAttemptStatus.COMPLETED:
            return self._blocked_result(
                signal_id=signal_id,
                state=state,
                reason="The latest execution attempt is not retryable",
            )

        if latest.outcome not in (
            ExecutionAttemptOutcome.NO_LIQUIDITY,
            ExecutionAttemptOutcome.PARTIALLY_FILLED,
        ):
            return self._existing_attempt_result(
                latest=latest,
                order=active_order,
                state=state,
                signal_id=signal_id,
            )

        inputs = self._build_attempt_inputs(
            instrument=instrument,
            quote=quote,
            executed_at=execution_time,
            execution_config=execution_config,
            order_updated_at=active_order.updated_at,
        )

        prepared = await self._repository.create_retry_execution_attempt(
            account_id,
            active_order.order_id,
            inputs,
            expected_account_revision=state.account_revision,
        )

        prepared_attempt = getattr(prepared, "attempt", prepared)

        persisted = await self._repository.complete_execution_attempt(
            prepared_attempt.attempt_id,
            simulator=self._execution_engine,
        )

        return self._result_from_attempt(
            persisted=persisted,
            signal_id=signal_id,
            risk_decision=None,
        )

    @staticmethod
    def _normalize_execution_time(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("executed_at must be timezone-aware")

        return value.astimezone(timezone.utc)

    @staticmethod
    def _build_attempt_inputs(
        *,
        instrument: Instrument,
        quote: BidAskQuote,
        executed_at: datetime,
        execution_config: SimulatedExecutionConfig | None,
        order_updated_at: datetime,
    ) -> ExecutionAttemptInputs:
        """Validate and freeze the exact inputs for one execution attempt."""

        if quote.symbol != instrument.symbol:
            raise ValueError("Quote symbol does not match instrument")

        if quote.timestamp > executed_at:
            raise ValueError(
                "Quote timestamp cannot be later than execution time"
            )

        if executed_at < order_updated_at:
            raise ValueError(
                "Execution time cannot precede the order's latest update"
            )

        config = execution_config or SimulatedExecutionConfig()

        return ExecutionAttemptInputs(
            instrument=instrument,
            quote=quote,
            executed_at=executed_at,
            commission_per_1000_units=config.commission_per_1000_units,
            slippage_ticks=config.slippage_ticks,
            max_quote_age=config.max_quote_age,
            missing_liquidity_policy=str(
                config.missing_liquidity_policy
            ),
        )

    @staticmethod
    def _workflow_outcome(
        attempt: ExecutionAttempt,
    ) -> WorkflowOutcome:
        if attempt.status == ExecutionAttemptStatus.FAILED:
            return WorkflowOutcome.EXECUTION_REJECTED

        mapping = {
            ExecutionAttemptOutcome.FILLED: WorkflowOutcome.EXECUTED,
            ExecutionAttemptOutcome.PARTIALLY_FILLED: (
                WorkflowOutcome.PARTIALLY_EXECUTED
            ),
            ExecutionAttemptOutcome.NO_LIQUIDITY: WorkflowOutcome.NO_LIQUIDITY,
            ExecutionAttemptOutcome.REJECTED: (
                WorkflowOutcome.EXECUTION_REJECTED
            ),
        }

        return mapping.get(
            attempt.outcome,
            WorkflowOutcome.EXECUTION_REJECTED,
        )

    @classmethod
    def _result_from_attempt(
        cls,
        *,
        persisted: object,
        signal_id: UUID,
        risk_decision: RiskDecision | None,
    ) -> PaperTradingWorkflowResult:
        attempt = persisted.attempt

        return PaperTradingWorkflowResult(
            outcome=cls._workflow_outcome(attempt),
            signal_id=signal_id,
            account_revision=persisted.account_revision,
            portfolio=persisted.portfolio,
            risk_decision=risk_decision,
            order=persisted.order,
            attempt=attempt,
            reason=attempt.reason,
            idempotent=persisted.idempotent,
        )

    @classmethod
    def _existing_attempt_result(
        cls,
        *,
        latest: ExecutionAttempt,
        order: Order,
        state: object,
        signal_id: UUID,
    ) -> PaperTradingWorkflowResult:
        return PaperTradingWorkflowResult(
            outcome=cls._workflow_outcome(latest),
            signal_id=signal_id,
            account_revision=state.account_revision,
            portfolio=state.portfolio,
            order=order,
            attempt=latest,
            reason=latest.reason,
            idempotent=True,
        )

    @staticmethod
    def _blocked_result(
        *,
        signal_id: UUID,
        state: object,
        reason: str,
    ) -> PaperTradingWorkflowResult:
        return PaperTradingWorkflowResult(
            outcome=WorkflowOutcome.BLOCKED_ACTIVE_ORDER,
            signal_id=signal_id,
            account_revision=state.account_revision,
            portfolio=state.portfolio,
            reason=reason,
        )
