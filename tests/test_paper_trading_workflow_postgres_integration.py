"""Opt-in PostgreSQL end-to-end tests for the paper-trading workflow."""

import asyncio
import os
import selectors
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import create_async_database_engine
from atlas_quant.services.execution.application.paper_trading_workflow import (
    PaperTradingWorkflow,
    WorkflowOutcome,
)
from atlas_quant.services.execution.application.simulated_execution import (
    SimulatedExecutionConfig,
    SimulatedExecutionEngine,
)
from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.execution_attempts import (
    ExecutionAttemptStatus,
)
from atlas_quant.services.execution.domain.risk import RiskPolicy
from atlas_quant.services.execution.infrastructure.persistence.execution_attempt import (
    ExecutionAttemptORM,
)
from atlas_quant.services.execution.infrastructure.persistence.execution_repository import (
    SQLAlchemyExecutionRepository,
)
from atlas_quant.services.execution.infrastructure.persistence.fill import FillORM
from atlas_quant.services.execution.infrastructure.persistence.order import OrderORM
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.strategy_research.domain.strategies import StrategySignal


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "Set ATLAS_RUN_POSTGRES_INTEGRATION=1 "
        "to run PostgreSQL integration tests."
    ),
)


class InterruptedSimulator:
    """Simulate a process interruption after the prepared attempt commits."""

    def execute(self, *args, **kwargs):
        raise RuntimeError("Intentional simulator interruption.")


def test_paper_trading_workflow_end_to_end():
    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)

    async def scenario():
        connection = await engine.connect()
        outer_transaction = await connection.begin()

        # Repository commits become savepoints. The final rollback removes
        # all test records, including immutable completed attempts.
        sessions = async_sessionmaker(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        repository = SQLAlchemyExecutionRepository(sessions)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        token = uuid4().hex

        instrument = Instrument(
            instrument_id=uuid4(),
            symbol=f"FLOW{token[:6]}",
            asset_class=AssetClass.FOREX,
            base_currency="EUR",
            quote_currency="USD",
            price_precision=5,
            quantity_precision=2,
            tick_size=Decimal("0.00001"),
            contract_size=Decimal("1"),
        )

        policy = RiskPolicy(
            max_position_exposure=Decimal("0.50"),
            max_gross_portfolio_exposure=Decimal("1.00"),
            max_order_exposure=Decimal("0.25"),
        )

        config = SimulatedExecutionConfig(
            commission_per_1000_units=Decimal("10"),
            slippage_ticks=0,
            max_quote_age=timedelta(seconds=5),
        )

        def make_account(label):
            return PaperTradingAccount(
                account_id=uuid4(),
                name=f"Workflow {label} {token}",
                base_currency="USD",
                initial_capital=Decimal("100000"),
                created_at=now,
            )

        def make_quote(
            at,
            *,
            bid=Decimal("1.10000"),
            ask=Decimal("1.10020"),
            liquidity=Decimal("100000"),
        ):
            return BidAskQuote(
                symbol=instrument.symbol,
                timestamp=at,
                bid=bid,
                ask=ask,
                bid_size=liquidity,
                ask_size=liquidity,
            )

        def make_signal(at):
            return StrategySignal(
                strategy_version_id=uuid4(),
                instrument_id=instrument.instrument_id,
                generated_at=at,
                effective_at=at,
                target_exposure=Decimal("0.20"),
            )

        async def row_count(model, account_id):
            return await connection.scalar(
                select(func.count())
                .select_from(model)
                .where(model.account_id == account_id)
            )

        try:
            # ------------------------------------------------------
            # 1. Full execution and completed-signal idempotency
            # ------------------------------------------------------
            success_account = make_account("success")
            await repository.create_account(success_account)

            success_signal = make_signal(now)
            success_quote = make_quote(now)

            workflow = PaperTradingWorkflow(
                repository,
                SimulatedExecutionEngine(),
            )

            # A list is intentional: the workflow boundary must normalize
            # sequence inputs before calling the tuple-strict risk evaluator.
            success = await workflow.process_signal(
                account_id=success_account.account_id,
                signal=success_signal,
                instrument=instrument,
                quote=success_quote,
                risk_policy=policy,
                executed_at=now,
                position_marks=[],
                execution_config=config,
            )

            assert success.outcome is WorkflowOutcome.EXECUTED
            assert success.order is not None
            assert success.order.status.value == "filled"
            assert success.attempt is not None
            assert success.attempt.status is ExecutionAttemptStatus.COMPLETED
            assert success.attempt.outcome.value == "filled"
            assert success.attempt.inputs.quote == success_quote
            assert success.idempotent is False

            success_order_count = await row_count(
                OrderORM,
                success_account.account_id,
            )
            success_attempt_count = await row_count(
                ExecutionAttemptORM,
                success_account.account_id,
            )
            success_fill_count = await row_count(
                FillORM,
                success_account.account_id,
            )

            assert success_order_count == 1
            assert success_attempt_count == 1
            assert success_fill_count == 1

            duplicate = await workflow.process_signal(
                account_id=success_account.account_id,
                signal=success_signal,
                instrument=instrument,
                quote=make_quote(now + timedelta(seconds=1)),
                risk_policy=policy,
                executed_at=now + timedelta(seconds=1),
                execution_config=config,
            )

            assert duplicate.idempotent is True
            assert duplicate.order is not None
            assert duplicate.order.order_id == success.order.order_id
            assert duplicate.attempt is not None
            assert duplicate.attempt.attempt_id == success.attempt.attempt_id

            assert await row_count(
                OrderORM,
                success_account.account_id,
            ) == 1
            assert await row_count(
                ExecutionAttemptORM,
                success_account.account_id,
            ) == 1
            assert await row_count(
                FillORM,
                success_account.account_id,
            ) == 1

            # ------------------------------------------------------
            # 2. Partial fill followed by a sequential retry
            # ------------------------------------------------------
            partial_account = make_account("partial")
            await repository.create_account(partial_account)

            partial_signal = make_signal(now)
            partial_first_quote = make_quote(
                now,
                liquidity=Decimal("1000"),
            )

            partial_workflow = PaperTradingWorkflow(
                repository,
                SimulatedExecutionEngine(),
            )

            first_partial = await partial_workflow.process_signal(
                account_id=partial_account.account_id,
                signal=partial_signal,
                instrument=instrument,
                quote=partial_first_quote,
                risk_policy=policy,
                executed_at=now,
                execution_config=config,
            )

            assert first_partial.outcome is WorkflowOutcome.PARTIALLY_EXECUTED
            assert first_partial.order is not None
            assert first_partial.order.status.value == "partially_filled"
            assert first_partial.order.filled_quantity == Decimal("1000")
            assert first_partial.attempt is not None
            assert first_partial.attempt.attempt_number == 1
            assert first_partial.attempt.outcome.value == "partially_filled"

            retry_time = now + timedelta(seconds=2)
            retry_quote = make_quote(
                retry_time,
                bid=Decimal("1.10010"),
                ask=Decimal("1.10030"),
            )

            second_partial = await partial_workflow.process_signal(
                account_id=partial_account.account_id,
                signal=partial_signal,
                instrument=instrument,
                quote=retry_quote,
                risk_policy=policy,
                executed_at=retry_time,
                execution_config=config,
            )

            assert second_partial.outcome is WorkflowOutcome.EXECUTED
            assert second_partial.order is not None
            assert second_partial.order.order_id == first_partial.order.order_id
            assert second_partial.order.status.value == "filled"
            assert second_partial.order.filled_quantity == (
                second_partial.order.quantity
            )
            assert second_partial.attempt is not None
            assert second_partial.attempt.attempt_number == 2
            assert second_partial.attempt.status is ExecutionAttemptStatus.COMPLETED
            assert second_partial.attempt.outcome.value == "filled"
            assert second_partial.attempt.fill_id != first_partial.attempt.fill_id

            assert await row_count(
                OrderORM,
                partial_account.account_id,
            ) == 1
            assert await row_count(
                ExecutionAttemptORM,
                partial_account.account_id,
            ) == 2
            assert await row_count(
                FillORM,
                partial_account.account_id,
            ) == 2

            # ------------------------------------------------------
            # 3. Recover a PREPARED attempt using its original snapshot
            # ------------------------------------------------------
            recovery_account = make_account("recovery")
            await repository.create_account(recovery_account)

            recovery_time = now + timedelta(seconds=4)
            original_quote = make_quote(
                recovery_time,
                bid=Decimal("1.10000"),
                ask=Decimal("1.10020"),
            )
            recovery_signal = make_signal(recovery_time)

            interrupted_workflow = PaperTradingWorkflow(
                repository,
                InterruptedSimulator(),
            )

            with pytest.raises(
                RuntimeError,
                match="Intentional simulator interruption",
            ):
                await interrupted_workflow.process_signal(
                    account_id=recovery_account.account_id,
                    signal=recovery_signal,
                    instrument=instrument,
                    quote=original_quote,
                    risk_policy=policy,
                    executed_at=recovery_time,
                    execution_config=config,
                )

            prepared_attempts = (
                await repository.get_prepared_execution_attempts(
                    recovery_account.account_id
                )
            )
            assert len(prepared_attempts) == 1

            prepared = prepared_attempts[0]
            assert prepared.status is ExecutionAttemptStatus.PREPARED
            assert prepared.inputs.quote == original_quote

            # The new quote deliberately differs. Recovery must execute
            # from the persisted original quote, not this newer market data.
            recovery_retry_time = now + timedelta(seconds=5)
            newer_quote = make_quote(
                recovery_retry_time,
                bid=Decimal("1.20000"),
                ask=Decimal("1.20020"),
            )

            recovery_workflow = PaperTradingWorkflow(
                repository,
                SimulatedExecutionEngine(),
            )

            recovered = await recovery_workflow.process_signal(
                account_id=recovery_account.account_id,
                signal=recovery_signal,
                instrument=instrument,
                quote=newer_quote,
                risk_policy=policy,
                executed_at=recovery_retry_time,
                execution_config=config,
            )

            assert recovered.outcome is WorkflowOutcome.EXECUTED
            assert recovered.order is not None
            assert recovered.order.status.value == "filled"
            assert recovered.attempt is not None
            assert recovered.attempt.attempt_id == prepared.attempt_id
            assert recovered.attempt.status is ExecutionAttemptStatus.COMPLETED
            assert recovered.attempt.inputs.quote == original_quote
            assert recovered.attempt.execution_price == original_quote.ask

            assert await row_count(
                OrderORM,
                recovery_account.account_id,
            ) == 1
            assert await row_count(
                ExecutionAttemptORM,
                recovery_account.account_id,
            ) == 1
            assert await row_count(
                FillORM,
                recovery_account.account_id,
            ) == 1

            # ------------------------------------------------------
            # 4. Reconciliation of all end-to-end account histories
            # ------------------------------------------------------
            for account_id in (
                success_account.account_id,
                partial_account.account_id,
                recovery_account.account_id,
            ):
                report = await repository.reconcile_account(account_id)

                assert report is not None
                assert report.is_consistent is True, (
                    "\n" + "\n".join(issue.message for issue in report.issues)
                )
                assert report.is_fully_verifiable is True
                assert report.issues == ()

        finally:
            if outer_transaction.is_active:
                await outer_transaction.rollback()

            await connection.close()

    try:
        asyncio.run(
            scenario(),
            loop_factory=lambda: asyncio.SelectorEventLoop(
                selectors.SelectSelector()
            ),
        )
    finally:
        asyncio.run(
            engine.dispose(),
            loop_factory=lambda: asyncio.SelectorEventLoop(
                selectors.SelectSelector()
            ),
        )
