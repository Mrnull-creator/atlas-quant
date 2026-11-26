"""Transactional persistence for paper-trading execution."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.execution.application.simulated_execution import (
    ExecutionOutcome,
    MissingLiquidityPolicy,
    SimulatedExecutionConfig,
    SimulatedExecutionEngine,
    SimulatedExecutionResult,
)
from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    AccountStatus,
    LedgerEntryType,
    OrderSide,
    OrderStatus,
    OrderType,
)
from atlas_quant.services.execution.domain.execution_attempts import (
    ExecutionAttempt,
    ExecutionAttemptInputs,
    ExecutionAttemptOutcome,
    ExecutionAttemptStatus,
)
from atlas_quant.services.execution.domain.fills import Fill
from atlas_quant.services.execution.domain.ledger import CashLedgerEntry
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.execution.domain.portfolio import (
    PortfolioState,
    apply_fill,
    create_portfolio,
)
from atlas_quant.services.execution.domain.positions import Position
from atlas_quant.services.execution.infrastructure.persistence.cash_ledger import (
    CashLedgerEntryORM,
)
from atlas_quant.services.execution.infrastructure.persistence.execution_attempt import (
    ExecutionAttemptORM,
)
from atlas_quant.services.execution.infrastructure.persistence.fill import (
    FillORM,
)
from atlas_quant.services.execution.infrastructure.persistence.order import (
    OrderORM,
)
from atlas_quant.services.execution.infrastructure.persistence.paper_account import (
    PaperAccountORM,
)
from atlas_quant.services.execution.infrastructure.persistence.position import (
    PositionORM,
)


@dataclass(frozen=True, slots=True)
class PersistedExecutionResult:
    """Result of recording a simulated execution."""

    order: Order
    portfolio: PortfolioState
    account_revision: int
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class PersistedPreparedExecution:
    """Order and first prepared attempt persisted atomically."""

    order: Order
    attempt: ExecutionAttempt
    account_revision: int


@dataclass(frozen=True, slots=True)
class PersistedAttemptExecutionResult:
    """Atomically completed durable execution attempt."""

    attempt: ExecutionAttempt
    order: Order
    portfolio: PortfolioState
    account_revision: int
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class AccountExecutionState:
    """Consistent account snapshot used for risk and order decisions."""

    account_revision: int
    portfolio: PortfolioState
    active_order: Order | None


class StaleAccountStateError(RuntimeError):
    """Raised when an order uses an outdated account snapshot."""


class ActiveOrderExistsError(RuntimeError):
    """Raised when an account already has an active order."""

    def __init__(self, order: Order) -> None:
        self.order = order
        super().__init__(
            f"Account already has active order {order.order_id}."
        )


class DuplicateSignalOrderError(RuntimeError):
    """Raised when a signal has already created an account order."""

    def __init__(self, order: Order) -> None:
        self.order = order
        super().__init__(
            f"Signal {order.signal_id} already created order {order.order_id}."
        )


class SQLAlchemyExecutionRepository:
    """Persist paper execution using atomic database transactions.

    An account row lock serializes portfolio mutations for that account.
    Financial calculations remain in the pure domain layer.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def create_account(
        self,
        account: PaperTradingAccount,
    ) -> PaperTradingAccount:
        """Create an account and its opening ledger entry atomically."""

        portfolio = create_portfolio(account)
        opening_entry = portfolio.ledger_entries[0]

        async with self._session_factory() as session:
            async with session.begin():
                session.add(
                    PaperAccountORM(
                        account_id=account.account_id,
                        name=account.name,
                        base_currency=account.base_currency,
                        initial_capital=account.initial_capital,
                        current_cash=portfolio.cash_balance,
                        status=account.status.value,
                        created_at=account.created_at,
                        updated_at=account.created_at,
                    )
                )

                # Ensure the account exists before inserting its ledger entry.
                await session.flush()

                session.add(self._ledger_to_orm(opening_entry))
                await session.flush()

        return account

    async def get_account(
        self,
        account_id: UUID,
    ) -> PaperTradingAccount | None:
        """Retrieve a persisted account."""

        async with self._session_factory() as session:
            row = await session.get(PaperAccountORM, account_id)

        return None if row is None else self._account_from_orm(row)

    async def create_order(
        self,
        order: Order,
        *,
        expected_account_revision: int | None = None,
    ) -> Order:
        """Persist a pending order if the account snapshot is still current."""

        if order.status is not OrderStatus.PENDING:
            raise ValueError("Only pending orders can be created.")

        async with self._session_factory() as session:
            async with session.begin():
                account = await session.scalar(
                    select(PaperAccountORM)
                    .where(PaperAccountORM.account_id == order.account_id)
                    .with_for_update()
                )

                if account is None:
                    raise LookupError("Paper-trading account was not found.")

                if account.status != AccountStatus.ACTIVE.value:
                    raise ValueError("Paper-trading account is not active.")

                if (
                    expected_account_revision is not None
                    and account.state_revision != expected_account_revision
                ):
                    raise StaleAccountStateError(
                        "Account state changed after the risk snapshot; "
                        "reload the account and evaluate risk again."
                    )

                if order.signal_id is not None:
                    existing_signal_order = await session.scalar(
                        select(OrderORM).where(
                            OrderORM.account_id == order.account_id,
                            OrderORM.signal_id == order.signal_id,
                        )
                    )
                    if existing_signal_order is not None:
                        raise DuplicateSignalOrderError(
                            self._order_from_orm(existing_signal_order)
                        )

                active_row = await session.scalar(
                    select(OrderORM)
                    .where(
                        OrderORM.account_id == order.account_id,
                        OrderORM.status.in_(
                            [
                                OrderStatus.PENDING.value,
                                OrderStatus.ACCEPTED.value,
                                OrderStatus.PARTIALLY_FILLED.value,
                            ]
                        ),
                    )
                    .order_by(OrderORM.created_at, OrderORM.order_id)
                    .limit(1)
                )
                if active_row is not None:
                    raise ActiveOrderExistsError(
                        self._order_from_orm(active_row)
                    )

                session.add(self._order_to_orm(order))
                account.state_revision += 1
                account.updated_at = max(
                    account.updated_at,
                    order.created_at,
                )
                await session.flush()

        return order

    async def create_order_with_prepared_attempt(
        self,
        order: Order,
        inputs: ExecutionAttemptInputs,
        *,
        expected_account_revision: int,
    ) -> PersistedPreparedExecution:
        """Create an order and its first PREPARED attempt atomically."""

        if order.status is not OrderStatus.PENDING:
            raise ValueError("Only pending orders can be created.")

        if not isinstance(inputs, ExecutionAttemptInputs):
            raise TypeError(
                "inputs must be an ExecutionAttemptInputs instance."
            )

        if order.instrument_id != inputs.instrument.instrument_id:
            raise ValueError(
                "Execution-attempt instrument does not match order."
            )

        if inputs.executed_at < order.created_at:
            raise ValueError(
                "Execution time cannot precede order creation."
            )

        async with self._session_factory() as session:
            async with session.begin():
                account = await session.scalar(
                    select(PaperAccountORM)
                    .where(
                        PaperAccountORM.account_id == order.account_id
                    )
                    .with_for_update()
                )

                if account is None:
                    raise LookupError(
                        "Paper-trading account was not found."
                    )

                if account.status != AccountStatus.ACTIVE.value:
                    raise ValueError(
                        "Paper-trading account is not active."
                    )

                if account.state_revision != expected_account_revision:
                    raise StaleAccountStateError(
                        "Account state changed after the risk snapshot; "
                        "reload the account and evaluate risk again."
                    )

                if order.signal_id is not None:
                    existing_signal_order = await session.scalar(
                        select(OrderORM).where(
                            OrderORM.account_id == order.account_id,
                            OrderORM.signal_id == order.signal_id,
                        )
                    )
                    if existing_signal_order is not None:
                        raise DuplicateSignalOrderError(
                            self._order_from_orm(existing_signal_order)
                        )

                active_row = await session.scalar(
                    select(OrderORM)
                    .where(
                        OrderORM.account_id == order.account_id,
                        OrderORM.status.in_(
                            [
                                OrderStatus.PENDING.value,
                                OrderStatus.ACCEPTED.value,
                                OrderStatus.PARTIALLY_FILLED.value,
                            ]
                        ),
                    )
                    .order_by(OrderORM.created_at, OrderORM.order_id)
                    .limit(1)
                )

                if active_row is not None:
                    raise ActiveOrderExistsError(
                        self._order_from_orm(active_row)
                    )

                attempt = ExecutionAttempt.prepare(
                    account_id=order.account_id,
                    order_id=order.order_id,
                    attempt_number=1,
                    inputs=inputs,
                    created_at=order.created_at,
                )

                # Persist the order before its dependent attempt.
                session.add(self._order_to_orm(order))
                await session.flush()

                session.add(self._execution_attempt_to_orm(attempt))

                # The complete creation operation increments revision once.
                account.state_revision += 1
                account.updated_at = max(
                    account.updated_at,
                    order.created_at,
                )

                await session.flush()

                persisted = PersistedPreparedExecution(
                    order=order,
                    attempt=attempt,
                    account_revision=account.state_revision,
                )

        return persisted

    async def create_retry_execution_attempt(
        self,
        account_id: UUID,
        order_id: UUID,
        inputs: ExecutionAttemptInputs,
        *,
        expected_account_revision: int,
    ) -> PersistedPreparedExecution:
        """Create a fresh attempt for an eligible, still-active order."""

        if not isinstance(inputs, ExecutionAttemptInputs):
            raise TypeError(
                "inputs must be an ExecutionAttemptInputs instance."
            )

        async with self._session_factory() as session:
            async with session.begin():
                # Keep the established account -> order -> attempt lock order.
                account = await session.scalar(
                    select(PaperAccountORM)
                    .where(PaperAccountORM.account_id == account_id)
                    .with_for_update()
                )

                if account is None:
                    raise LookupError(
                        "Paper-trading account was not found."
                    )

                if account.status != AccountStatus.ACTIVE.value:
                    raise ValueError(
                        "Paper-trading account is not active."
                    )

                if account.state_revision != expected_account_revision:
                    raise StaleAccountStateError(
                        "Account state changed before retry creation; "
                        "reload the account and retry."
                    )

                order_row = await session.scalar(
                    select(OrderORM)
                    .where(
                        OrderORM.account_id == account_id,
                        OrderORM.order_id == order_id,
                    )
                    .with_for_update()
                )

                if order_row is None:
                    raise LookupError(
                        "Paper-trading order was not found."
                    )

                order = self._order_from_orm(order_row)

                if order.status not in {
                    OrderStatus.ACCEPTED,
                    OrderStatus.PARTIALLY_FILLED,
                }:
                    raise ValueError(
                        "Only accepted or partially filled orders "
                        "can receive another execution attempt."
                    )

                if order.quantity - order.filled_quantity <= Decimal("0"):
                    raise ValueError(
                        "Order has no remaining quantity."
                    )

                if inputs.instrument.instrument_id != order.instrument_id:
                    raise ValueError(
                        "Retry instrument does not match the order."
                    )

                latest_row = await session.scalar(
                    select(ExecutionAttemptORM)
                    .where(ExecutionAttemptORM.order_id == order_id)
                    .order_by(ExecutionAttemptORM.attempt_number.desc())
                    .limit(1)
                    .with_for_update()
                )

                if latest_row is None:
                    raise ValueError(
                        "Order has no prior durable execution attempt."
                    )

                latest = self._execution_attempt_from_orm(latest_row)

                if latest.status is ExecutionAttemptStatus.PREPARED:
                    raise ValueError(
                        "The previous attempt is still prepared and "
                        "must be recovered before another attempt."
                    )

                if latest.status is ExecutionAttemptStatus.FAILED:
                    raise ValueError(
                        "A failed execution attempt cannot be retried "
                        "automatically."
                    )

                if latest.outcome not in {
                    ExecutionAttemptOutcome.NO_LIQUIDITY,
                    ExecutionAttemptOutcome.PARTIALLY_FILLED,
                }:
                    raise ValueError(
                        "A new attempt is permitted only after "
                        "NO_LIQUIDITY or PARTIALLY_FILLED."
                    )

                if inputs.executed_at <= latest.inputs.executed_at:
                    raise ValueError(
                        "Retry execution time must be later than "
                        "the previous attempt."
                    )

                if inputs.quote.timestamp <= latest.inputs.quote.timestamp:
                    raise ValueError(
                        "Retry quote must be newer than the previous "
                        "attempt's quote."
                    )

                attempt = ExecutionAttempt.prepare(
                    account_id=account_id,
                    order_id=order_id,
                    attempt_number=latest.attempt_number + 1,
                    inputs=inputs,
                    created_at=inputs.executed_at,
                )

                session.add(self._execution_attempt_to_orm(attempt))

                account.state_revision += 1
                account.updated_at = max(
                    account.updated_at,
                    inputs.executed_at,
                )

                await session.flush()

                persisted = PersistedPreparedExecution(
                    order=order,
                    attempt=attempt,
                    account_revision=account.state_revision,
                )

        return persisted

    async def get_execution_attempt(
        self,
        attempt_id: UUID,
    ) -> ExecutionAttempt | None:
        """Retrieve one durable execution attempt and its exact input snapshot."""

        async with self._session_factory() as session:
            row = await session.get(ExecutionAttemptORM, attempt_id)

        return (
            None
            if row is None
            else self._execution_attempt_from_orm(row)
        )

    async def get_latest_execution_attempt(
        self,
        order_id: UUID,
    ) -> ExecutionAttempt | None:
        """Retrieve the most recent durable attempt for an order."""

        async with self._session_factory() as session:
            row = await session.scalar(
                select(ExecutionAttemptORM)
                .where(ExecutionAttemptORM.order_id == order_id)
                .order_by(ExecutionAttemptORM.attempt_number.desc())
                .limit(1)
            )

        return (
            None
            if row is None
            else self._execution_attempt_from_orm(row)
        )
    async def get_prepared_execution_attempts(
        self,
        account_id: UUID,
    ) -> tuple[ExecutionAttempt, ...]:
        """Retrieve all unfinished attempts for an account in stable order."""

        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ExecutionAttemptORM)
                    .where(
                        ExecutionAttemptORM.account_id == account_id,
                        ExecutionAttemptORM.status
                        == ExecutionAttemptStatus.PREPARED.value,
                    )
                    .order_by(
                        ExecutionAttemptORM.created_at,
                        ExecutionAttemptORM.order_id,
                        ExecutionAttemptORM.attempt_number,
                    )
                )
            ).all()

        return tuple(
            self._execution_attempt_from_orm(row)
            for row in rows
        )

    async def get_order(
        self,
        account_id: UUID,
        order_id: UUID,
    ) -> Order | None:
        """Retrieve an order belonging to an account."""

        async with self._session_factory() as session:
            row = await session.scalar(
                select(OrderORM).where(
                    OrderORM.account_id == account_id,
                    OrderORM.order_id == order_id,
                )
            )

        return None if row is None else self._order_from_orm(row)

    async def get_order_by_signal(
        self,
        account_id: UUID,
        signal_id: UUID,
    ) -> Order | None:
        """Retrieve the order already created for an account signal."""

        async with self._session_factory() as session:
            row = await session.scalar(
                select(OrderORM).where(
                    OrderORM.account_id == account_id,
                    OrderORM.signal_id == signal_id,
                )
            )

        return None if row is None else self._order_from_orm(row)

    async def get_portfolio(
        self,
        account_id: UUID,
    ) -> PortfolioState | None:
        """Reconstruct portfolio state from persisted records."""

        async with self._session_factory() as session:
            account_row = await session.get(PaperAccountORM, account_id)

            if account_row is None:
                return None

            return await self._load_portfolio(session, account_row)

    async def get_account_state(
        self,
        account_id: UUID,
    ) -> AccountExecutionState | None:
        """Load revision, portfolio, and active order under one account lock."""

        async with self._session_factory() as session:
            async with session.begin():
                account_row = await session.scalar(
                    select(PaperAccountORM)
                    .where(PaperAccountORM.account_id == account_id)
                    .with_for_update()
                )

                if account_row is None:
                    return None

                portfolio = await self._load_portfolio(
                    session,
                    account_row,
                )

                active_rows = (
                    await session.scalars(
                        select(OrderORM)
                        .where(
                            OrderORM.account_id == account_id,
                            OrderORM.status.in_(
                                [
                                    OrderStatus.PENDING.value,
                                    OrderStatus.ACCEPTED.value,
                                    OrderStatus.PARTIALLY_FILLED.value,
                                ]
                            ),
                        )
                        .order_by(OrderORM.created_at, OrderORM.order_id)
                        .limit(2)
                    )
                ).all()

                if len(active_rows) > 1:
                    raise RuntimeError(
                        "Account contains multiple active orders; "
                        "execution state requires reconciliation."
                    )

                active_order = (
                    None
                    if not active_rows
                    else self._order_from_orm(active_rows[0])
                )

                return AccountExecutionState(
                    account_revision=account_row.state_revision,
                    portfolio=portfolio,
                    active_order=active_order,
                )

    async def reconcile_account(self, account_id: UUID):
        """Return a read-only reconciliation report for an account."""

        from atlas_quant.services.execution.infrastructure.persistence.reconciliation import (
            SQLAlchemyExecutionReconciler,
        )

        reconciler = SQLAlchemyExecutionReconciler(self._session_factory)
        return await reconciler.reconcile_account(account_id)

    async def record_execution_result(
        self,
        result: SimulatedExecutionResult,
        *,
        quote_currency: str,
        contract_size: Decimal,
    ) -> PersistedExecutionResult:
        """Persist a simulator result atomically.

        The simulator returns zero or one fill. The repository validates
        the proposed transition against the persisted order before saving.
        """

        if len(result.fills) > 1:
            raise ValueError(
                "A simulated execution result cannot contain more than "
                "one fill."
            )

        proposed_order = result.order
        fill = result.fills[0] if result.fills else None

        if fill is not None and (
            fill.account_id != proposed_order.account_id
            or fill.order_id != proposed_order.order_id
            or fill.instrument_id != proposed_order.instrument_id
        ):
            raise ValueError(
                "Fill ownership or instrument does not match the "
                "proposed order."
            )

        async with self._session_factory() as session:
            async with session.begin():
                account_row = await session.scalar(
                    select(PaperAccountORM)
                    .where(
                        PaperAccountORM.account_id
                        == proposed_order.account_id
                    )
                    .with_for_update()
                )

                if account_row is None:
                    raise LookupError("Paper-trading account was not found.")

                portfolio = await self._load_portfolio(
                    session,
                    account_row,
                )

                # Check duplicate fill IDs while holding the account lock.
                if fill is not None:
                    existing_fill = await session.get(
                        FillORM,
                        fill.fill_id,
                    )

                    if existing_fill is not None:
                        if not self._same_fill(
                            existing_fill,
                            fill,
                            quote_currency=quote_currency,
                            contract_size=contract_size,
                        ):
                            raise ValueError(
                                "Fill ID already exists with different "
                                "execution details."
                            )

                        if (
                            existing_fill.account_id
                            != proposed_order.account_id
                            or existing_fill.order_id
                            != proposed_order.order_id
                        ):
                            raise ValueError(
                                "Existing fill belongs to another "
                                "account or order."
                            )

                        stored_order = await session.scalar(
                            select(OrderORM).where(
                                OrderORM.account_id
                                == proposed_order.account_id,
                                OrderORM.order_id
                                == proposed_order.order_id,
                            )
                        )

                        if stored_order is None:
                            raise LookupError("Order was not found.")

                        return PersistedExecutionResult(
                            order=self._order_from_orm(stored_order),
                            portfolio=portfolio,
                            account_revision=account_row.state_revision,
                            idempotent=True,
                        )

                order_row = await session.scalar(
                    select(OrderORM)
                    .where(
                        OrderORM.account_id == proposed_order.account_id,
                        OrderORM.order_id == proposed_order.order_id,
                    )
                    .with_for_update()
                )

                if order_row is None:
                    raise LookupError("Order was not found.")

                persisted_order = self._order_from_orm(order_row)

                if (
                    persisted_order.instrument_id
                    != proposed_order.instrument_id
                ):
                    raise ValueError(
                        "Execution instrument does not match order."
                    )

                if account_row.status != AccountStatus.ACTIVE.value:
                    raise ValueError("Paper-trading account is not active.")

                if fill is not None:
                    expected_order = persisted_order

                    if expected_order.status is OrderStatus.PENDING:
                        expected_order = expected_order.accept(
                            fill.executed_at
                        )

                    application = apply_fill(
                        portfolio,
                        expected_order,
                        fill,
                        quote_currency=quote_currency,
                        contract_size=contract_size,
                    )

                    updated_order = application.order
                    updated_portfolio = application.portfolio

                    if updated_order != proposed_order:
                        raise ValueError(
                            "Simulator order state does not match the "
                            "persisted order transition."
                        )

                    expected_outcome = (
                        ExecutionOutcome.FILLED
                        if updated_order.status is OrderStatus.FILLED
                        else ExecutionOutcome.PARTIALLY_FILLED
                    )

                    if result.outcome is not expected_outcome:
                        raise ValueError(
                            "Execution outcome does not match the "
                            "resulting order state."
                        )

                    session.add(
                        self._fill_to_orm(
                            fill,
                            quote_currency=quote_currency,
                            contract_size=contract_size,
                        )
                    )

                    new_entries = updated_portfolio.ledger_entries[
                        len(portfolio.ledger_entries):
                    ]
                    session.add_all(
                        self._ledger_to_orm(entry)
                        for entry in new_entries
                    )

                    position = next(
                        (
                            item
                            for item in updated_portfolio.positions
                            if item.instrument_id == fill.instrument_id
                        ),
                        None,
                    )

                    if position is None:
                        raise RuntimeError(
                            "Fill accounting did not produce a position."
                        )

                    await self._save_position(
                        session,
                        account_id=fill.account_id,
                        position=position,
                        updated_at=fill.executed_at,
                    )

                    account_row.current_cash = (
                        updated_portfolio.cash_balance
                    )
                    account_row.updated_at = max(
                        account_row.updated_at,
                        fill.executed_at,
                    )

                else:
                    updated_order = self._validate_no_fill_transition(
                        persisted_order,
                        proposed_order,
                        result.outcome,
                    )
                    updated_portfolio = portfolio

                state_changed = (
                    fill is not None or updated_order != persisted_order
                )
                if state_changed:
                    account_row.state_revision += 1
                    account_row.updated_at = max(
                        account_row.updated_at,
                        updated_order.updated_at,
                    )

                order_row.status = updated_order.status.value
                order_row.filled_quantity = updated_order.filled_quantity
                order_row.updated_at = updated_order.updated_at

                await session.flush()

                return PersistedExecutionResult(
                    order=updated_order,
                    portfolio=updated_portfolio,
                    account_revision=account_row.state_revision,
                    idempotent=False,
                )

    async def complete_execution_attempt(
        self,
        attempt_id: UUID,
        *,
        simulator: SimulatedExecutionEngine | None = None,
    ) -> PersistedAttemptExecutionResult:
        """Replay and atomically complete a previously committed attempt.

        The simulator uses only the immutable inputs stored on the attempt.
        Account, order, and attempt are locked in that order. Attempt result,
        fill, ledger, position, order, and account revision are committed
        together.
        """

        engine = simulator or SimulatedExecutionEngine()

        # The initial read discovers the owning account and order. These
        # identifiers are revalidated after acquiring the transaction locks.
        async with self._session_factory() as lookup_session:
            seed = await lookup_session.get(
                ExecutionAttemptORM,
                attempt_id,
            )

        if seed is None:
            raise LookupError("Execution attempt was not found.")

        account_id = seed.account_id
        order_id = seed.order_id

        async with self._session_factory() as session:
            async with session.begin():
                # Keep the repository-wide lock order: account -> order
                # -> execution attempt.
                account_row = await session.scalar(
                    select(PaperAccountORM)
                    .where(PaperAccountORM.account_id == account_id)
                    .with_for_update()
                )
                if account_row is None:
                    raise LookupError("Paper-trading account was not found.")

                order_row = await session.scalar(
                    select(OrderORM)
                    .where(
                        OrderORM.account_id == account_id,
                        OrderORM.order_id == order_id,
                    )
                    .with_for_update()
                )
                if order_row is None:
                    raise LookupError("Order was not found.")

                attempt_row = await session.scalar(
                    select(ExecutionAttemptORM)
                    .where(
                        ExecutionAttemptORM.attempt_id == attempt_id,
                        ExecutionAttemptORM.account_id == account_id,
                        ExecutionAttemptORM.order_id == order_id,
                    )
                    .with_for_update()
                )
                if attempt_row is None:
                    raise LookupError(
                        "Execution attempt ownership changed or the "
                        "attempt was not found."
                    )

                attempt = self._execution_attempt_from_orm(attempt_row)
                persisted_order = self._order_from_orm(order_row)
                portfolio = await self._load_portfolio(
                    session,
                    account_row,
                )

                # A retry after a successful commit is read-only. It must
                # never apply the fill or ledger entries a second time.
                if attempt.status is ExecutionAttemptStatus.COMPLETED:
                    if (
                        attempt.outcome
                        in {
                            ExecutionAttemptOutcome.FILLED,
                            ExecutionAttemptOutcome.PARTIALLY_FILLED,
                        }
                    ):
                        existing_fill = await session.scalar(
                            select(FillORM).where(
                                FillORM.execution_attempt_id == attempt_id
                            )
                        )
                        if (
                            existing_fill is None
                            or existing_fill.fill_id != attempt.fill_id
                        ):
                            raise RuntimeError(
                                "Completed fill attempt has no matching "
                                "persisted fill; reconciliation is required."
                            )
                    else:
                        existing_fill = await session.scalar(
                            select(FillORM).where(
                                FillORM.execution_attempt_id == attempt_id
                            )
                        )
                        if existing_fill is not None:
                            raise RuntimeError(
                                "A no-fill attempt unexpectedly has a "
                                "persisted fill."
                            )

                    return PersistedAttemptExecutionResult(
                        attempt=attempt,
                        order=persisted_order,
                        portfolio=portfolio,
                        account_revision=account_row.state_revision,
                        idempotent=True,
                    )

                if attempt.status is ExecutionAttemptStatus.FAILED:
                    raise ValueError(
                        "A failed execution attempt cannot be completed."
                    )

                if account_row.status != AccountStatus.ACTIVE.value:
                    raise ValueError("Paper-trading account is not active.")

                if persisted_order.status not in {
                    OrderStatus.PENDING,
                    OrderStatus.ACCEPTED,
                    OrderStatus.PARTIALLY_FILLED,
                }:
                    raise ValueError(
                        "The order is no longer in an executable state."
                    )

                inputs = attempt.inputs
                instrument = inputs.instrument
                quote = inputs.quote

                config = SimulatedExecutionConfig(
                    commission_per_1000_units=(
                        inputs.commission_per_1000_units
                    ),
                    slippage_ticks=inputs.slippage_ticks,
                    max_quote_age=inputs.max_quote_age,
                    missing_liquidity_policy=MissingLiquidityPolicy(
                        inputs.missing_liquidity_policy
                    ),
                )

                # This is the deterministic replay: persisted inputs and
                # the attempt-specific fill ID are the only execution inputs.
                result = engine.execute(
                    order=persisted_order,
                    account=self._account_from_orm(account_row),
                    instrument=instrument,
                    quote=quote,
                    executed_at=inputs.executed_at,
                    config=config,
                    fill_id=attempt.fill_id,
                )

                if len(result.fills) > 1:
                    raise RuntimeError(
                        "A simulated execution attempt returned multiple fills."
                    )

                fill = result.fills[0] if result.fills else None
                if fill is not None and fill.fill_id != attempt.fill_id:
                    raise RuntimeError(
                        "Simulator returned a fill with an unexpected "
                        "deterministic identity."
                    )

                quote_currency = instrument.quote_currency
                if not quote_currency:
                    raise ValueError(
                        "Attempt instrument has no quote currency."
                    )

                contract_size = (
                    instrument.contract_size
                    if instrument.contract_size is not None
                    else Decimal("1")
                )

                if fill is not None:
                    if (
                        fill.account_id != account_id
                        or fill.order_id != order_id
                        or fill.instrument_id != instrument.instrument_id
                    ):
                        raise ValueError(
                            "Attempt fill ownership or instrument does "
                            "not match its persisted order."
                        )

                    expected_order = persisted_order
                    if expected_order.status is OrderStatus.PENDING:
                        expected_order = expected_order.accept(
                            fill.executed_at
                        )

                    application = apply_fill(
                        portfolio,
                        expected_order,
                        fill,
                        quote_currency=quote_currency,
                        contract_size=contract_size,
                    )
                    updated_order = application.order
                    updated_portfolio = application.portfolio

                    if updated_order != result.order:
                        raise ValueError(
                            "Simulator order state does not match the "
                            "persisted order transition."
                        )

                    expected_outcome = (
                        ExecutionOutcome.FILLED
                        if updated_order.status is OrderStatus.FILLED
                        else ExecutionOutcome.PARTIALLY_FILLED
                    )
                    if result.outcome is not expected_outcome:
                        raise ValueError(
                            "Execution outcome does not match the "
                            "resulting order state."
                        )

                    session.add(
                        self._fill_to_orm(
                            fill,
                            quote_currency=quote_currency,
                            contract_size=contract_size,
                            execution_attempt_id=attempt_id,
                        )
                    )

                    new_entries = updated_portfolio.ledger_entries[
                        len(portfolio.ledger_entries):
                    ]
                    session.add_all(
                        self._ledger_to_orm(entry)
                        for entry in new_entries
                    )

                    position = next(
                        (
                            item
                            for item in updated_portfolio.positions
                            if item.instrument_id == fill.instrument_id
                        ),
                        None,
                    )
                    if position is None:
                        raise RuntimeError(
                            "Fill accounting did not produce a position."
                        )

                    await self._save_position(
                        session,
                        account_id=account_id,
                        position=position,
                        updated_at=fill.executed_at,
                    )

                    account_row.current_cash = (
                        updated_portfolio.cash_balance
                    )
                    account_row.updated_at = max(
                        account_row.updated_at,
                        fill.executed_at,
                    )
                else:
                    # A rejection of an already accepted or partially filled
                    # order completes this attempt without rejecting the
                    # underlying order.
                    if (
                        result.outcome is ExecutionOutcome.REJECTED
                        and result.order == persisted_order
                        and persisted_order.status
                        in {
                            OrderStatus.ACCEPTED,
                            OrderStatus.PARTIALLY_FILLED,
                        }
                    ):
                        updated_order = persisted_order
                    else:
                        updated_order = self._validate_no_fill_transition(
                            persisted_order,
                            result.order,
                            result.outcome,
                        )
                    updated_portfolio = portfolio

                completed_at = max(
                    datetime.now(timezone.utc),
                    attempt.created_at,
                )
                completed_attempt = attempt.complete(
                    outcome=ExecutionAttemptOutcome(result.outcome.value),
                    completed_at=completed_at,
                    reason=result.reason,
                    reference_price=result.reference_price,
                    execution_price=result.execution_price,
                    slippage_cost=result.slippage_cost,
                    liquidity_assumed=result.liquidity_assumed,
                )

                # Only mutable result fields are updated. Database triggers
                # protect the original identity and input snapshot.
                attempt_row.status = completed_attempt.status.value
                attempt_row.outcome = completed_attempt.outcome.value
                attempt_row.reason = completed_attempt.reason
                attempt_row.reference_price = (
                    completed_attempt.reference_price
                )
                attempt_row.execution_price = (
                    completed_attempt.execution_price
                )
                attempt_row.slippage_cost = completed_attempt.slippage_cost
                attempt_row.liquidity_assumed = (
                    completed_attempt.liquidity_assumed
                )
                attempt_row.completed_at = completed_attempt.completed_at

                state_changed = (
                    fill is not None or updated_order != persisted_order
                )
                if state_changed:
                    account_row.state_revision += 1
                    account_row.updated_at = max(
                        account_row.updated_at,
                        updated_order.updated_at,
                    )

                order_row.status = updated_order.status.value
                order_row.filled_quantity = updated_order.filled_quantity
                order_row.updated_at = updated_order.updated_at

                await session.flush()

                return PersistedAttemptExecutionResult(
                    attempt=completed_attempt,
                    order=updated_order,
                    portfolio=updated_portfolio,
                    account_revision=account_row.state_revision,
                    idempotent=False,
                )

    async def _load_portfolio(
        self,
        session: AsyncSession,
        account_row: PaperAccountORM,
    ) -> PortfolioState:
        """Load and validate persisted accounting state."""

        account = self._account_from_orm(account_row)

        ledger_rows = (
            await session.scalars(
                select(CashLedgerEntryORM)
                .where(
                    CashLedgerEntryORM.account_id == account.account_id
                )
                .order_by(
                    CashLedgerEntryORM.occurred_at,
                    CashLedgerEntryORM.entry_id,
                )
            )
        ).all()

        position_rows = (
            await session.scalars(
                select(PositionORM).where(
                    PositionORM.account_id == account.account_id
                )
            )
        ).all()

        fill_rows = (
            await session.scalars(
                select(FillORM).where(
                    FillORM.account_id == account.account_id
                )
            )
        ).all()

        ledger_entries = tuple(
            self._ledger_from_orm(row) for row in ledger_rows
        )
        positions = tuple(
            self._position_from_orm(row) for row in position_rows
        )
        processed_fill_ids = frozenset(
            row.fill_id for row in fill_rows
        )

        ledger_total = sum(
            (entry.amount for entry in ledger_entries),
            Decimal("0"),
        )

        if ledger_total != account_row.current_cash:
            raise RuntimeError(
                "Persisted account cash does not reconcile with its "
                "cash ledger."
            )

        return PortfolioState(
            account=account,
            cash_balance=account_row.current_cash,
            positions=positions,
            ledger_entries=ledger_entries,
            processed_fill_ids=processed_fill_ids,
        )

    async def _save_position(
        self,
        session: AsyncSession,
        *,
        account_id: UUID,
        position: Position,
        updated_at,
    ) -> None:
        row = await session.get(
            PositionORM,
            (account_id, position.instrument_id),
        )

        if row is None:
            session.add(
                PositionORM(
                    account_id=account_id,
                    instrument_id=position.instrument_id,
                    quantity=position.quantity,
                    average_entry_price=position.average_entry_price,
                    realized_pnl=position.realized_pnl,
                    quote_currency=position.quote_currency,
                    contract_size=position.contract_size,
                    updated_at=updated_at,
                )
            )
            return

        row.quantity = position.quantity
        row.average_entry_price = position.average_entry_price
        row.realized_pnl = position.realized_pnl
        row.quote_currency = position.quote_currency
        row.contract_size = position.contract_size
        row.updated_at = max(row.updated_at, updated_at)

    @staticmethod
    def _validate_no_fill_transition(
        persisted: Order,
        proposed: Order,
        outcome: ExecutionOutcome,
    ) -> Order:
        if outcome not in {
            ExecutionOutcome.NO_LIQUIDITY,
            ExecutionOutcome.REJECTED,
        }:
            raise ValueError(
                "A result without a fill must be NO_LIQUIDITY or REJECTED."
            )

        if proposed == persisted:
            if (
                outcome is ExecutionOutcome.NO_LIQUIDITY
                and persisted.status
                not in {
                    OrderStatus.ACCEPTED,
                    OrderStatus.PARTIALLY_FILLED,
                }
            ):
                raise ValueError(
                    "NO_LIQUIDITY requires an active accepted order."
                )
            return persisted

        if persisted.status is not OrderStatus.PENDING:
            raise ValueError(
                "A no-fill result cannot change an existing "
                "non-pending order."
            )

        if (
            proposed.status is OrderStatus.ACCEPTED
            and outcome is ExecutionOutcome.NO_LIQUIDITY
        ):
            expected = persisted.accept(proposed.updated_at)
        elif (
            proposed.status is OrderStatus.REJECTED
            and outcome is ExecutionOutcome.REJECTED
        ):
            expected = persisted.reject(proposed.updated_at)
        else:
            raise ValueError("Invalid no-fill order transition.")

        if expected != proposed:
            raise ValueError(
                "Simulator order state does not match the expected "
                "no-fill transition."
            )

        return expected

    @staticmethod
    def _same_fill(
        row: FillORM,
        fill: Fill,
        *,
        quote_currency: str,
        contract_size: Decimal,
    ) -> bool:
        return (
            row.fill_id == fill.fill_id
            and row.account_id == fill.account_id
            and row.order_id == fill.order_id
            and row.instrument_id == fill.instrument_id
            and row.quantity == fill.quantity
            and row.price == fill.price
            and row.commission == fill.commission
            and row.commission_currency == fill.commission_currency
            and row.quote_currency == quote_currency.upper()
            and row.contract_size == contract_size
            and row.accounting_metadata_version == 1
            and row.executed_at == fill.executed_at
        )

    @staticmethod
    def _execution_attempt_from_orm(
        row: ExecutionAttemptORM,
    ) -> ExecutionAttempt:
        """Reconstruct an attempt exclusively from its persisted snapshot."""

        instrument = Instrument(
            instrument_id=row.instrument_id,
            symbol=row.instrument_symbol,
            asset_class=AssetClass(row.asset_class),
            exchange=row.exchange,
            base_currency=row.base_currency,
            quote_currency=row.quote_currency,
            price_precision=row.price_precision,
            quantity_precision=row.quantity_precision,
            tick_size=row.tick_size,
            contract_size=row.contract_size,
            is_active=row.instrument_is_active,
        )

        quote = BidAskQuote(
            symbol=row.quote_symbol,
            timestamp=row.quote_timestamp,
            bid=row.quote_bid,
            ask=row.quote_ask,
            bid_size=row.quote_bid_size,
            ask_size=row.quote_ask_size,
        )

        inputs = ExecutionAttemptInputs(
            instrument=instrument,
            quote=quote,
            executed_at=row.executed_at,
            commission_per_1000_units=row.commission_per_1000_units,
            slippage_ticks=row.slippage_ticks,
            max_quote_age=(
                None
                if row.max_quote_age_microseconds is None
                else timedelta(
                    microseconds=row.max_quote_age_microseconds
                )
            ),
            missing_liquidity_policy=row.missing_liquidity_policy,
        )

        return ExecutionAttempt(
            attempt_id=row.attempt_id,
            account_id=row.account_id,
            order_id=row.order_id,
            attempt_number=row.attempt_number,
            fill_id=row.fill_id,
            inputs=inputs,
            status=ExecutionAttemptStatus(row.status),
            outcome=(
                None
                if row.outcome is None
                else ExecutionAttemptOutcome(row.outcome)
            ),
            reason=row.reason,
            reference_price=row.reference_price,
            execution_price=row.execution_price,
            slippage_cost=row.slippage_cost,
            liquidity_assumed=row.liquidity_assumed,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _execution_attempt_to_orm(
        attempt: ExecutionAttempt,
    ) -> ExecutionAttemptORM:
        """Map an immutable domain attempt and its inputs to ORM storage."""

        inputs = attempt.inputs
        instrument = inputs.instrument
        quote = inputs.quote

        max_quote_age_microseconds = (
            None
            if inputs.max_quote_age is None
            else int(
                inputs.max_quote_age.total_seconds() * 1_000_000
            )
        )

        return ExecutionAttemptORM(
            attempt_id=attempt.attempt_id,
            account_id=attempt.account_id,
            order_id=attempt.order_id,
            attempt_number=attempt.attempt_number,
            fill_id=attempt.fill_id,
            status=attempt.status.value,
            quote_symbol=quote.symbol,
            quote_timestamp=quote.timestamp,
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            quote_bid_size=quote.bid_size,
            quote_ask_size=quote.ask_size,
            instrument_id=instrument.instrument_id,
            instrument_symbol=instrument.symbol,
            asset_class=instrument.asset_class.value,
            exchange=instrument.exchange,
            base_currency=instrument.base_currency,
            quote_currency=instrument.quote_currency,
            price_precision=instrument.price_precision,
            quantity_precision=instrument.quantity_precision,
            tick_size=instrument.tick_size,
            contract_size=instrument.contract_size,
            instrument_is_active=instrument.is_active,
            executed_at=inputs.executed_at,
            commission_per_1000_units=(
                inputs.commission_per_1000_units
            ),
            slippage_ticks=inputs.slippage_ticks,
            max_quote_age_microseconds=max_quote_age_microseconds,
            missing_liquidity_policy=(
                inputs.missing_liquidity_policy
            ),
            outcome=(
                None
                if attempt.outcome is None
                else attempt.outcome.value
            ),
            reason=attempt.reason,
            reference_price=attempt.reference_price,
            execution_price=attempt.execution_price,
            slippage_cost=attempt.slippage_cost,
            liquidity_assumed=attempt.liquidity_assumed,
            created_at=attempt.created_at,
            completed_at=attempt.completed_at,
        )

    @staticmethod
    def _account_from_orm(row: PaperAccountORM) -> PaperTradingAccount:
        return PaperTradingAccount(
            account_id=row.account_id,
            name=row.name,
            base_currency=row.base_currency,
            initial_capital=row.initial_capital,
            status=AccountStatus(row.status),
            created_at=row.created_at,
        )

    @staticmethod
    def _order_from_orm(row: OrderORM) -> Order:
        return Order(
            order_id=row.order_id,
            account_id=row.account_id,
            instrument_id=row.instrument_id,
            side=OrderSide(row.side),
            quantity=row.quantity,
            order_type=OrderType(row.order_type),
            status=OrderStatus(row.status),
            filled_quantity=row.filled_quantity,
            strategy_version_id=row.strategy_version_id,
            signal_id=row.signal_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _ledger_from_orm(row: CashLedgerEntryORM) -> CashLedgerEntry:
        return CashLedgerEntry(
            entry_id=row.entry_id,
            account_id=row.account_id,
            entry_type=LedgerEntryType(row.entry_type),
            amount=row.amount,
            currency=row.currency,
            reference_id=row.reference_id,
            description=row.description,
            occurred_at=row.occurred_at,
        )

    @staticmethod
    def _position_from_orm(row: PositionORM) -> Position:
        return Position(
            instrument_id=row.instrument_id,
            quote_currency=row.quote_currency,
            quantity=row.quantity,
            average_entry_price=row.average_entry_price,
            realized_pnl=row.realized_pnl,
            contract_size=row.contract_size,
        )

    @staticmethod
    def _order_to_orm(order: Order) -> OrderORM:
        return OrderORM(
            order_id=order.order_id,
            account_id=order.account_id,
            instrument_id=order.instrument_id,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=order.quantity,
            status=order.status.value,
            filled_quantity=order.filled_quantity,
            strategy_version_id=order.strategy_version_id,
            signal_id=order.signal_id,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )

    @staticmethod
    def _fill_to_orm(
        fill: Fill,
        *,
        quote_currency: str,
        contract_size: Decimal,
        execution_attempt_id: UUID | None = None,
    ) -> FillORM:
        return FillORM(
            fill_id=fill.fill_id,
            account_id=fill.account_id,
            order_id=fill.order_id,
            instrument_id=fill.instrument_id,
            quantity=fill.quantity,
            price=fill.price,
            commission=fill.commission,
            commission_currency=fill.commission_currency,
            contract_size=contract_size,
            quote_currency=quote_currency.upper(),
            accounting_metadata_version=1,
            execution_attempt_id=execution_attempt_id,
            executed_at=fill.executed_at,
        )

    @staticmethod
    def _ledger_to_orm(entry: CashLedgerEntry) -> CashLedgerEntryORM:
        return CashLedgerEntryORM(
            entry_id=entry.entry_id,
            account_id=entry.account_id,
            entry_type=entry.entry_type.value,
            amount=entry.amount,
            currency=entry.currency,
            reference_id=entry.reference_id,
            description=entry.description,
            occurred_at=entry.occurred_at,
        )
