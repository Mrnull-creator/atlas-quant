"""Opt-in PostgreSQL integration tests for execution persistence."""

import asyncio
import os
import selectors
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.database import (
    create_async_database_engine,
    create_async_session_factory,
)
from atlas_quant.services.execution.application.simulated_execution import (
    ExecutionOutcome,
    SimulatedExecutionConfig,
    SimulatedExecutionEngine,
)
from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.execution_attempts import (
    ExecutionAttemptInputs,
)
from atlas_quant.services.execution.domain.enums import (
    AccountStatus,
    OrderSide,
    OrderStatus,
)
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.execution.infrastructure.persistence.execution_attempt import (
    ExecutionAttemptORM,
)
from atlas_quant.services.execution.infrastructure.persistence.cash_ledger import (
    CashLedgerEntryORM,
)
from atlas_quant.services.execution.infrastructure.persistence.execution_repository import (
    ActiveOrderExistsError,
    DuplicateSignalOrderError,
    SQLAlchemyExecutionRepository,
    StaleAccountStateError,
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
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") != "1",
    reason=(
        "Set ATLAS_RUN_POSTGRES_INTEGRATION=1 "
        "to run PostgreSQL integration tests."
    ),
)


def test_execution_repository_transactional_round_trip(monkeypatch):
    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        account_id = uuid4()
        instrument_id = uuid4()
        token = uuid4().hex

        now = datetime.now(timezone.utc).replace(microsecond=0)

        account = PaperTradingAccount(
            account_id=account_id,
            name=f"Execution integration {token}",
            base_currency="USD",
            initial_capital=Decimal("100000"),
            created_at=now,
        )

        instrument = Instrument(
            instrument_id=instrument_id,
            symbol=f"TEST{token[:6]}",
            asset_class=AssetClass.FOREX,
            base_currency="EUR",
            quote_currency="USD",
            price_precision=5,
            quantity_precision=2,
            tick_size=Decimal("0.00001"),
            contract_size=Decimal("1"),
        )

        simulator = SimulatedExecutionEngine()
        repository = SQLAlchemyExecutionRepository(sessions)

        config = SimulatedExecutionConfig(
            commission_per_1000_units=Decimal("10"),
            max_quote_age=timedelta(seconds=5),
        )

        def make_quote(
            at,
            *,
            bid=Decimal("1.10000"),
            ask=Decimal("1.10020"),
            bid_size=Decimal("1000"),
            ask_size=Decimal("1000"),
        ):
            return BidAskQuote(
                symbol=instrument.symbol,
                timestamp=at,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
            )

        def make_order(
            quantity,
            at,
            side=OrderSide.BUY,
            signal_id=None,
        ):
            return Order(
                account_id=account_id,
                instrument_id=instrument_id,
                side=side,
                quantity=Decimal(quantity),
                signal_id=signal_id,
                created_at=at,
                updated_at=at,
            )

        async def count_rows(model, account_column):
            async with sessions() as session:
                return await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(account_column == account_id)
                )

        try:
            # 1. Account creation and opening cash ledger.
            stored_account = await repository.create_account(account)
            assert stored_account.account_id == account_id

            retrieved_account = await repository.get_account(account_id)
            assert retrieved_account is not None
            assert retrieved_account.initial_capital == Decimal("100000")
            assert retrieved_account.status is AccountStatus.ACTIVE

            portfolio = await repository.get_portfolio(account_id)
            assert portfolio is not None
            assert portfolio.cash_balance == Decimal("100000")
            assert len(portfolio.ledger_entries) == 1

            # 2. Account snapshot, revision fencing, and order persistence.
            initial_state = await repository.get_account_state(account_id)
            assert initial_state is not None
            assert initial_state.account_revision == 0
            assert initial_state.active_order is None

            signal_id = uuid4()
            order = make_order("100", now, signal_id=signal_id)
            await repository.create_order(
                order,
                expected_account_revision=initial_state.account_revision,
            )

            created_state = await repository.get_account_state(account_id)
            assert created_state is not None
            assert created_state.account_revision == 1
            assert created_state.active_order == order

            # A stale risk snapshot must not create another order.
            stale_order = make_order(
                "10",
                now + timedelta(milliseconds=1),
            )
            with pytest.raises(StaleAccountStateError):
                await repository.create_order(
                    stale_order,
                    expected_account_revision=initial_state.account_revision,
                )

            # A current snapshot still cannot create a second active order.
            competing_order = make_order(
                "10",
                now + timedelta(milliseconds=2),
            )
            with pytest.raises(ActiveOrderExistsError) as active_error:
                await repository.create_order(
                    competing_order,
                    expected_account_revision=created_state.account_revision,
                )
            assert active_error.value.order.order_id == order.order_id

            retrieved_order = await repository.get_order(
                account_id,
                order.order_id,
            )
            assert retrieved_order == order

            # 3. Real simulator execution and atomic persistence.
            execution_time = now + timedelta(seconds=1)

            result = simulator.execute(
                order,
                account,
                instrument,
                make_quote(execution_time, ask_size=Decimal("100")),
                execution_time,
                config,
            )

            assert result.outcome is ExecutionOutcome.FILLED
            assert len(result.fills) == 1

            persisted = await repository.record_execution_result(
                result,
                quote_currency="USD",
                contract_size=Decimal("1"),
            )

            assert persisted.order.status is OrderStatus.FILLED
            assert persisted.order.filled_quantity == Decimal("100")
            assert persisted.portfolio.cash_balance == Decimal("99888.980")
            assert len(persisted.portfolio.positions) == 1
            assert persisted.portfolio.positions[0].quantity == Decimal("100")

            filled_state = await repository.get_account_state(account_id)
            assert filled_state is not None
            assert filled_state.account_revision == 2
            assert filled_state.active_order is None

            # A signal remains idempotent even after its order is complete.
            duplicate_signal_order = make_order(
                "100",
                now + timedelta(seconds=1, milliseconds=1),
                signal_id=signal_id,
            )
            with pytest.raises(DuplicateSignalOrderError) as signal_error:
                await repository.create_order(
                    duplicate_signal_order,
                    expected_account_revision=filled_state.account_revision,
                )
            assert signal_error.value.order.order_id == order.order_id
            signal_order = await repository.get_order_by_signal(
                account_id,
                signal_id,
            )
            assert signal_order is not None
            assert signal_order.order_id == order.order_id
            assert signal_order.signal_id == signal_id
            assert signal_order.status is OrderStatus.FILLED

            # The fill's accounting metadata must be persisted explicitly.
            async with sessions() as session:
                stored_fill = await session.get(
                    FillORM,
                    result.fills[0].fill_id,
                )
                assert stored_fill is not None
                assert stored_fill.contract_size == Decimal("1")
                assert stored_fill.quote_currency == "USD"
                assert stored_fill.accounting_metadata_version == 1

            # A clean account must reconcile immediately after its first fill.
            clean_report = await repository.reconcile_account(account_id)
            assert clean_report is not None
            assert clean_report.is_consistent is True, "\n" + "\n".join(issue.message for issue in clean_report.issues)
            assert clean_report.is_fully_verifiable is True
            assert clean_report.replayed_cash == Decimal("99888.980")
            assert clean_report.issues == ()

            assert await count_rows(FillORM, FillORM.account_id) == 1
            assert await count_rows(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id,
            ) == 3
            assert await count_rows(
                PositionORM,
                PositionORM.account_id,
            ) == 1

            # 4. Duplicate fill retry must not create additional movements.
            duplicate = await repository.record_execution_result(
                result,
                quote_currency="USD",
                contract_size=Decimal("1"),
            )

            assert duplicate.idempotent is True
            assert duplicate.order.status is OrderStatus.FILLED
            assert duplicate.portfolio.cash_balance == Decimal("99888.980")

            # An identical fill retry must not advance the account revision.
            duplicate_state = await repository.get_account_state(account_id)
            assert duplicate_state is not None
            assert duplicate_state.account_revision == 2

            assert await count_rows(FillORM, FillORM.account_id) == 1
            assert await count_rows(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id,
            ) == 3

            # Concurrent retries must also remain idempotent.
            concurrent_results = await asyncio.gather(
                *[
                    repository.record_execution_result(
                        result,
                        quote_currency="USD",
                        contract_size=Decimal("1"),
                    )
                    for _ in range(2)
                ]
            )

            assert all(item.idempotent for item in concurrent_results)

            concurrent_retry_state = await repository.get_account_state(
                account_id
            )
            assert concurrent_retry_state is not None
            assert concurrent_retry_state.account_revision == 2

            assert await count_rows(FillORM, FillORM.account_id) == 1
            assert await count_rows(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id,
            ) == 3

            # Reusing a fill ID with altered execution details must fail.
            altered_fill = replace(
                result.fills[0],
                quantity=Decimal("99"),
            )
            altered_result = replace(
                result,
                fills=(altered_fill,),
            )

            with pytest.raises(ValueError, match="different execution details"):
                await repository.record_execution_result(
                    altered_result,
                    quote_currency="USD",
                    contract_size=Decimal("1"),
                )

            # 5. Partial fill followed by completion.
            partial_order = make_order(
                "100",
                now + timedelta(seconds=2),
            )
            await repository.create_order(partial_order)

            first_time = now + timedelta(seconds=3)
            first_result = simulator.execute(
                partial_order,
                account,
                instrument,
                make_quote(
                    first_time,
                    ask_size=Decimal("40"),
                ),
                first_time,
                config,
            )

            assert first_result.outcome is ExecutionOutcome.PARTIALLY_FILLED
            assert first_result.fills[0].quantity == Decimal("40")

            first_persisted = await repository.record_execution_result(
                first_result,
                quote_currency="USD",
                contract_size=Decimal("1"),
            )

            assert (
                first_persisted.order.status
                is OrderStatus.PARTIALLY_FILLED
            )
            assert first_persisted.order.filled_quantity == Decimal("40")

            second_time = now + timedelta(seconds=4)
            second_result = simulator.execute(
                first_persisted.order,
                account,
                instrument,
                make_quote(
                    second_time,
                    bid=Decimal("1.10000"),
                    ask=Decimal("1.10100"),
                    ask_size=Decimal("60"),
                ),
                second_time,
                config,
            )

            assert second_result.outcome is ExecutionOutcome.FILLED
            assert second_result.fills[0].quantity == Decimal("60")

            second_persisted = await repository.record_execution_result(
                second_result,
                quote_currency="USD",
                contract_size=Decimal("1"),
            )

            assert second_persisted.order.status is OrderStatus.FILLED
            assert second_persisted.order.filled_quantity == Decimal("100")

            # 6. Rollback after fill and ledger rows have been staged.
            rollback_order = make_order(
                "10",
                now + timedelta(seconds=5),
            )
            await repository.create_order(rollback_order)

            rollback_time = now + timedelta(seconds=6)
            rollback_result = simulator.execute(
                rollback_order,
                account,
                instrument,
                make_quote(
                    rollback_time,
                    ask_size=Decimal("10"),
                ),
                rollback_time,
                config,
            )

            assert rollback_result.outcome is ExecutionOutcome.FILLED

            cash_before_failure = (
                await repository.get_portfolio(account_id)
            ).cash_balance

            fill_count_before_failure = await count_rows(
                FillORM,
                FillORM.account_id,
            )
            ledger_count_before_failure = await count_rows(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id,
            )

            async def fail_position_save(session, **kwargs):
                raise RuntimeError("Intentional persistence failure.")

            monkeypatch.setattr(
                repository,
                "_save_position",
                fail_position_save,
            )

            with pytest.raises(
                RuntimeError,
                match="Intentional persistence failure",
            ):
                await repository.record_execution_result(
                    rollback_result,
                    quote_currency="USD",
                    contract_size=Decimal("1"),
                )

            # Restore the real method and inspect committed database state.
            monkeypatch.undo()

            unchanged_order = await repository.get_order(
                account_id,
                rollback_order.order_id,
            )
            unchanged_portfolio = await repository.get_portfolio(account_id)

            assert unchanged_order is not None
            assert unchanged_order.status is OrderStatus.PENDING
            assert unchanged_order.filled_quantity == Decimal("0")

            assert unchanged_portfolio is not None
            assert unchanged_portfolio.cash_balance == cash_before_failure

            assert await count_rows(FillORM, FillORM.account_id) == (
                fill_count_before_failure
            )
            assert await count_rows(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id,
            ) == ledger_count_before_failure

            # The same execution can be safely retried after rollback.
            recovered = await repository.record_execution_result(
                rollback_result,
                quote_currency="USD",
                contract_size=Decimal("1"),
            )

            assert recovered.order.status is OrderStatus.FILLED
            assert recovered.portfolio.cash_balance < cash_before_failure

            # 7. No-liquidity outcome persists acceptance without a fill.
            no_liquidity_order = make_order(
                "10",
                now + timedelta(seconds=7),
            )
            await repository.create_order(no_liquidity_order)

            no_liquidity_time = now + timedelta(seconds=8)
            no_liquidity_result = simulator.execute(
                no_liquidity_order,
                account,
                instrument,
                make_quote(
                    no_liquidity_time,
                    ask_size=Decimal("0"),
                ),
                no_liquidity_time,
                config,
            )

            assert (
                no_liquidity_result.outcome
                is ExecutionOutcome.NO_LIQUIDITY
            )
            assert no_liquidity_result.fills == ()

            no_liquidity_persisted = (
                await repository.record_execution_result(
                    no_liquidity_result,
                    quote_currency="USD",
                    contract_size=Decimal("1"),
                )
            )

            assert (
                no_liquidity_persisted.order.status
                is OrderStatus.ACCEPTED
            )

            # The accepted order remains active until it is completed.
            active_state = await repository.get_account_state(account_id)
            assert active_state is not None
            assert active_state.active_order == no_liquidity_persisted.order

            # Supply liquidity on a later quote to complete it before
            # creating the next order for this account.
            completion_time = now + timedelta(seconds=9)
            completion_result = simulator.execute(
                no_liquidity_persisted.order,
                account,
                instrument,
                make_quote(
                    completion_time,
                    ask_size=Decimal("10"),
                ),
                completion_time,
                config,
            )
            assert completion_result.outcome is ExecutionOutcome.FILLED

            completion_persisted = (
                await repository.record_execution_result(
                    completion_result,
                    quote_currency="USD",
                    contract_size=Decimal("1"),
                )
            )
            assert completion_persisted.order.status is OrderStatus.FILLED

            completed_state = await repository.get_account_state(account_id)
            assert completed_state is not None
            assert completed_state.active_order is None

            # A stale quote rejects a pending order without a fill.
            rejected_order = make_order(
                "10",
                now + timedelta(seconds=9),
            )
            await repository.create_order(rejected_order)

            rejected_time = now + timedelta(seconds=10)
            stale_quote = make_quote(
                rejected_time - timedelta(seconds=10),
            )

            rejected_result = simulator.execute(
                rejected_order,
                account,
                instrument,
                stale_quote,
                rejected_time,
                config,
            )

            assert rejected_result.outcome is ExecutionOutcome.REJECTED
            assert rejected_result.fills == ()

            rejected_persisted = await repository.record_execution_result(
                rejected_result,
                quote_currency="USD",
                contract_size=Decimal("1"),
            )

            assert rejected_persisted.order.status is OrderStatus.REJECTED

            # PT-4C: a clean multi-order account must reconcile.
            clean_report = await repository.reconcile_account(account_id)
            assert clean_report is not None
            assert clean_report.is_consistent is True
            assert clean_report.is_fully_verifiable is True
            assert clean_report.replayed_cash == clean_report.persisted_cash
            assert clean_report.issues == ()

            # Corrupt a position directly. Reconciliation must detect it
            # and must not silently repair the persisted row.
            async with sessions() as session:
                async with session.begin():
                    position_row = await session.get(
                        PositionORM,
                        (account_id, instrument_id),
                    )
                    assert position_row is not None
                    original_realized_pnl = position_row.realized_pnl
                    position_row.realized_pnl += Decimal("1")

            position_report = await repository.reconcile_account(account_id)
            assert position_report is not None
            assert position_report.is_consistent is False
            assert any(
                issue.code == "POSITION_MISMATCH"
                for issue in position_report.issues
            )

            async with sessions() as session:
                position_row = await session.get(
                    PositionORM,
                    (account_id, instrument_id),
                )
                assert position_row is not None
                assert position_row.realized_pnl == (
                    original_realized_pnl + Decimal("1")
                )

            # Restore the position before testing the ledger.
            async with sessions() as session:
                async with session.begin():
                    position_row = await session.get(
                        PositionORM,
                        (account_id, instrument_id),
                    )
                    assert position_row is not None
                    position_row.realized_pnl = original_realized_pnl

            # Corrupt a ledger amount. The report must identify both the
            # event mismatch and the resulting cash reconciliation failure.
            async with sessions() as session:
                async with session.begin():
                    trade_entry = await session.scalar(
                        select(CashLedgerEntryORM).where(
                            CashLedgerEntryORM.account_id == account_id,
                            CashLedgerEntryORM.entry_type == "trade",
                            CashLedgerEntryORM.reference_id == result.fills[0].fill_id,
                        )
                    )
                    assert trade_entry is not None
                    original_trade_amount = trade_entry.amount
                    trade_entry.amount += Decimal("1")

            ledger_report = await repository.reconcile_account(account_id)
            assert ledger_report is not None
            assert ledger_report.is_consistent is False
            ledger_codes = {issue.code for issue in ledger_report.issues}
            assert "LEDGER_ENTRY_MISMATCH" in ledger_codes
            assert "ACCOUNT_LEDGER_CASH_MISMATCH" in ledger_codes

            async with sessions() as session:
                trade_entry = await session.scalar(
                    select(CashLedgerEntryORM).where(
                        CashLedgerEntryORM.account_id == account_id,
                        CashLedgerEntryORM.entry_type == "trade",
                        CashLedgerEntryORM.reference_id == result.fills[0].fill_id,
                    )
                )
                assert trade_entry is not None
                assert trade_entry.amount == (
                    original_trade_amount + Decimal("1")
                )

            # Restore the ledger before testing order-state corruption.
            async with sessions() as session:
                async with session.begin():
                    trade_entry = await session.scalar(
                        select(CashLedgerEntryORM).where(
                            CashLedgerEntryORM.account_id == account_id,
                            CashLedgerEntryORM.entry_type == "trade",
                            CashLedgerEntryORM.reference_id == result.fills[0].fill_id,
                        )
                    )
                    assert trade_entry is not None
                    trade_entry.amount = original_trade_amount

            # Corrupt a fully filled order to a valid-but-inconsistent
            # partial state. Database constraints allow this state, but
            # reconciliation must compare it against the actual fills.
            async with sessions() as session:
                async with session.begin():
                    order_row = await session.get(
                        OrderORM,
                        order.order_id,
                    )
                    assert order_row is not None
                    original_order_status = order_row.status
                    original_filled_quantity = order_row.filled_quantity
                    order_row.status = "partially_filled"
                    order_row.filled_quantity = Decimal("99")

            order_report = await repository.reconcile_account(account_id)
            assert order_report is not None
            assert order_report.is_consistent is False
            order_codes = {issue.code for issue in order_report.issues}
            assert "ORDER_FILLED_QUANTITY_MISMATCH" in order_codes

            # Reconciliation must not have repaired the order either.
            async with sessions() as session:
                order_row = await session.get(OrderORM, order.order_id)
                assert order_row is not None
                assert order_row.status == "partially_filled"
                assert order_row.filled_quantity == Decimal("99")

            async with sessions() as session:
                async with session.begin():
                    order_row = await session.get(OrderORM, order.order_id)
                    assert order_row is not None
                    order_row.status = original_order_status
                    order_row.filled_quantity = original_filled_quantity

            # Legacy accounting metadata must be reported as unverifiable.
            async with sessions() as session:
                async with session.begin():
                    stored_fill = await session.get(
                        FillORM,
                        result.fills[0].fill_id,
                    )
                    assert stored_fill is not None
                    stored_fill.accounting_metadata_version = 0

            legacy_report = await repository.reconcile_account(account_id)
            assert legacy_report is not None
            assert legacy_report.is_consistent is False
            assert legacy_report.is_fully_verifiable is False
            assert legacy_report.replayed_cash is None
            assert any(
                issue.code == "LEGACY_ACCOUNTING_METADATA"
                for issue in legacy_report.issues
            )

            # Restore metadata and require a clean final report.
            async with sessions() as session:
                async with session.begin():
                    stored_fill = await session.get(
                        FillORM,
                        result.fills[0].fill_id,
                    )
                    assert stored_fill is not None
                    stored_fill.accounting_metadata_version = 1

            restored_report = await repository.reconcile_account(account_id)
            assert restored_report is not None
            assert restored_report.is_consistent is True
            assert restored_report.is_fully_verifiable is True

            # Final persisted accounting must reconcile.
            final_portfolio = await repository.get_portfolio(account_id)
            assert final_portfolio is not None

            async with sessions() as session:
                stored_account_row = await session.get(
                    PaperAccountORM,
                    account_id,
                )
                assert stored_account_row is not None
                assert (
                    stored_account_row.current_cash
                    == final_portfolio.cash_balance
                )

                ledger_total = await session.scalar(
                    select(func.sum(CashLedgerEntryORM.amount)).where(
                        CashLedgerEntryORM.account_id == account_id
                    )
                )

                assert ledger_total == final_portfolio.cash_balance

        finally:
            # Delete only records created by this test.
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(FillORM).where(
                            FillORM.account_id == account_id
                        )
                    )
                    await session.execute(
                        delete(CashLedgerEntryORM).where(
                            CashLedgerEntryORM.account_id == account_id
                        )
                    )
                    await session.execute(
                        delete(PositionORM).where(
                            PositionORM.account_id == account_id
                        )
                    )
                    await session.execute(
                        delete(OrderORM).where(
                            OrderORM.account_id == account_id
                        )
                    )
                    await session.execute(
                        delete(PaperAccountORM).where(
                            PaperAccountORM.account_id == account_id
                        )
                    )

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

def test_order_and_first_execution_attempt_are_atomic(monkeypatch):
    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)
    sessions = create_async_session_factory(engine)

    async def scenario():
        now = datetime.now(timezone.utc).replace(microsecond=0)
        token = uuid4().hex

        repository = SQLAlchemyExecutionRepository(sessions)

        instrument = Instrument(
            instrument_id=uuid4(),
            symbol=f"ATOMIC{token[:6]}",
            asset_class=AssetClass.FOREX,
            base_currency="EUR",
            quote_currency="USD",
            price_precision=5,
            quantity_precision=2,
            tick_size=Decimal("0.00001"),
            contract_size=Decimal("1"),
        )

        quote = BidAskQuote(
            symbol=instrument.symbol,
            timestamp=now,
            bid=Decimal("1.10000"),
            ask=Decimal("1.10020"),
            bid_size=Decimal("1000"),
            ask_size=Decimal("1000"),
        )

        inputs = ExecutionAttemptInputs(
            instrument=instrument,
            quote=quote,
            executed_at=now,
            commission_per_1000_units=Decimal("10"),
            slippage_ticks=0,
            max_quote_age=timedelta(seconds=5),
            missing_liquidity_policy="reject",
        )

        rollback_account_id = uuid4()
        success_account_id = uuid4()

        rollback_order = None
        success_order = None

        def make_account(account_id, name):
            return PaperTradingAccount(
                account_id=account_id,
                name=name,
                base_currency="USD",
                initial_capital=Decimal("100000"),
                created_at=now,
            )

        def make_order(account_id):
            return Order(
                account_id=account_id,
                instrument_id=instrument.instrument_id,
                side=OrderSide.BUY,
                quantity=Decimal("100"),
                created_at=now,
                updated_at=now,
            )

        async def count_attempts(account_id):
            async with sessions() as session:
                return await session.scalar(
                    select(func.count())
                    .select_from(ExecutionAttemptORM)
                    .where(
                        ExecutionAttemptORM.account_id == account_id
                    )
                )

        async def count_orders(account_id):
            async with sessions() as session:
                return await session.scalar(
                    select(func.count())
                    .select_from(OrderORM)
                    .where(OrderORM.account_id == account_id)
                )

        try:
            # Create the account used for the forced rollback.
            await repository.create_account(
                make_account(
                    rollback_account_id,
                    f"Atomic rollback {token}",
                )
            )

            rollback_state = await repository.get_account_state(
                rollback_account_id
            )
            assert rollback_state is not None
            assert rollback_state.account_revision == 0

            rollback_order = make_order(rollback_account_id)

            # Fail after the order has been flushed but before the
            # execution-attempt ORM object can be added.
            def fail_attempt_mapping(attempt):
                raise RuntimeError("Intentional attempt mapping failure.")

            monkeypatch.setattr(
                repository,
                "_execution_attempt_to_orm",
                fail_attempt_mapping,
            )

            with pytest.raises(
                RuntimeError,
                match="Intentional attempt mapping failure",
            ):
                await repository.create_order_with_prepared_attempt(
                    rollback_order,
                    inputs,
                    expected_account_revision=rollback_state.account_revision,
                )

            monkeypatch.undo()

            # The failed transaction must leave no order, no attempt,
            # and no account revision change.
            assert await count_orders(rollback_account_id) == 0
            assert await count_attempts(rollback_account_id) == 0

            rollback_state_after = await repository.get_account_state(
                rollback_account_id
            )
            assert rollback_state_after is not None
            assert rollback_state_after.account_revision == 0
            assert rollback_state_after.active_order is None

            # Now verify successful atomic creation for a separate account.
            await repository.create_account(
                make_account(
                    success_account_id,
                    f"Atomic success {token}",
                )
            )

            success_state = await repository.get_account_state(
                success_account_id
            )
            assert success_state is not None
            assert success_state.account_revision == 0

            success_order = make_order(success_account_id)

            persisted = await repository.create_order_with_prepared_attempt(
                success_order,
                inputs,
                expected_account_revision=success_state.account_revision,
            )

            # Recovery reads must reconstruct the exact immutable snapshot.
            recovered_attempt = await repository.get_execution_attempt(
                persisted.attempt.attempt_id
            )

            assert recovered_attempt == persisted.attempt
            assert recovered_attempt is not None
            assert recovered_attempt.inputs.instrument == instrument
            assert recovered_attempt.inputs.quote == quote
            assert recovered_attempt.inputs.executed_at == inputs.executed_at
            assert (
                recovered_attempt.inputs.commission_per_1000_units
                == inputs.commission_per_1000_units
            )
            assert recovered_attempt.inputs.slippage_ticks == inputs.slippage_ticks
            assert recovered_attempt.inputs.max_quote_age == inputs.max_quote_age
            assert (
                recovered_attempt.inputs.missing_liquidity_policy
                == inputs.missing_liquidity_policy
            )

            prepared_attempts = (
                await repository.get_prepared_execution_attempts(
                    success_account_id
                )
            )
            assert prepared_attempts == (persisted.attempt,)

            rollback_attempts = (
                await repository.get_prepared_execution_attempts(
                    rollback_account_id
                )
            )
            assert rollback_attempts == ()

            assert persisted.order == success_order
            assert persisted.attempt.account_id == success_account_id
            assert persisted.attempt.order_id == success_order.order_id
            assert persisted.attempt.attempt_number == 1
            assert persisted.attempt.status.value == "prepared"
            assert persisted.attempt.outcome is None
            assert persisted.attempt.fill_id is not None
            assert persisted.account_revision == 1

            # Confirm both records and the immutable input snapshot
            # are visible after the transaction has committed.
            async with sessions() as session:
                stored_order = await session.get(
                    OrderORM,
                    success_order.order_id,
                )
                stored_attempt = await session.get(
                    ExecutionAttemptORM,
                    persisted.attempt.attempt_id,
                )
                stored_account = await session.get(
                    PaperAccountORM,
                    success_account_id,
                )

                assert stored_order is not None
                assert stored_order.status == "pending"

                assert stored_attempt is not None
                assert stored_attempt.status == "prepared"
                assert stored_attempt.fill_id == persisted.attempt.fill_id
                assert stored_attempt.quote_symbol == instrument.symbol
                assert stored_attempt.quote_bid == Decimal("1.10000")
                assert stored_attempt.quote_ask == Decimal("1.10020")
                assert stored_attempt.instrument_id == instrument.instrument_id
                assert stored_attempt.instrument_symbol == instrument.symbol
                assert stored_attempt.executed_at == now
                assert (
                    stored_attempt.commission_per_1000_units
                    == Decimal("10")
                )
                assert stored_attempt.slippage_ticks == 0
                assert stored_attempt.max_quote_age_microseconds == 5_000_000
                assert stored_attempt.missing_liquidity_policy == "reject"

                assert stored_account is not None
                assert stored_account.state_revision == 1

            success_state_after = await repository.get_account_state(
                success_account_id
            )
            assert success_state_after is not None
            assert success_state_after.account_revision == 1
            assert success_state_after.active_order == success_order

        finally:
            monkeypatch.undo()

            # Delete only records created by this test. Attempts must
            # be deleted before their parent orders.
            async with sessions() as session:
                async with session.begin():
                    account_ids = [
                        rollback_account_id,
                        success_account_id,
                    ]

                    await session.execute(
                        delete(ExecutionAttemptORM).where(
                            ExecutionAttemptORM.account_id.in_(account_ids)
                        )
                    )
                    await session.execute(
                        delete(OrderORM).where(
                            OrderORM.account_id.in_(account_ids)
                        )
                    )
                    await session.execute(
                        delete(CashLedgerEntryORM).where(
                            CashLedgerEntryORM.account_id.in_(account_ids)
                        )
                    )
                    await session.execute(
                        delete(PaperAccountORM).where(
                            PaperAccountORM.account_id.in_(account_ids)
                        )
                    )

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

def test_durable_attempt_completion_is_atomic_and_idempotent(monkeypatch):
    settings = get_settings()

    if settings.database_url is None:
        pytest.skip("No database URL is configured.")

    engine = create_async_database_engine(settings.database_url)

    async def scenario():
        connection = await engine.connect()
        outer_transaction = await connection.begin()

        # Repository commits become savepoints inside this outer
        # transaction. The final rollback removes all test records,
        # including immutable COMPLETED execution attempts.
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
            symbol=f"ATTEMPT{token[:6]}",
            asset_class=AssetClass.FOREX,
            base_currency="EUR",
            quote_currency="USD",
            price_precision=5,
            quantity_precision=2,
            tick_size=Decimal("0.00001"),
            contract_size=Decimal("1"),
        )

        def make_account(label):
            return PaperTradingAccount(
                account_id=uuid4(),
                name=f"Attempt {label} {token}",
                base_currency="USD",
                initial_capital=Decimal("100000"),
                created_at=now,
            )

        def make_inputs(at, *, liquidity=Decimal("1000")):
            quote = BidAskQuote(
                symbol=instrument.symbol,
                timestamp=at,
                bid=Decimal("1.10000"),
                ask=Decimal("1.10020"),
                bid_size=liquidity,
                ask_size=liquidity,
            )
            return ExecutionAttemptInputs(
                instrument=instrument,
                quote=quote,
                executed_at=at,
                commission_per_1000_units=Decimal("10"),
                slippage_ticks=0,
                max_quote_age=timedelta(seconds=5),
                missing_liquidity_policy="reject",
            )

        def make_order(account_id, at):
            return Order(
                account_id=account_id,
                instrument_id=instrument.instrument_id,
                side=OrderSide.BUY,
                quantity=Decimal("100"),
                created_at=at,
                updated_at=at,
            )

        async def row_count(model, condition):
            return await connection.scalar(
                select(func.count()).select_from(model).where(condition)
            )

        try:
            # ------------------------------------------------------
            # 1. Successful execution and atomic financial accounting
            # ------------------------------------------------------
            success_account = make_account("success")
            await repository.create_account(success_account)

            success_state = await repository.get_account_state(
                success_account.account_id
            )
            assert success_state is not None

            success_order = make_order(
                success_account.account_id,
                now,
            )
            success_inputs = make_inputs(now)

            prepared = await repository.create_order_with_prepared_attempt(
                success_order,
                success_inputs,
                expected_account_revision=success_state.account_revision,
            )

            assert prepared.account_revision == 1
            assert prepared.attempt.status.value == "prepared"

            completed = await repository.complete_execution_attempt(
                prepared.attempt.attempt_id
            )

            assert completed.idempotent is False
            assert completed.attempt.status.value == "completed"
            assert completed.attempt.outcome.value == "filled"
            assert completed.attempt.fill_id == prepared.attempt.fill_id
            assert completed.order.status is OrderStatus.FILLED
            assert completed.order.filled_quantity == Decimal("100")
            assert completed.account_revision == 2
            assert len(completed.portfolio.positions) == 1
            assert completed.portfolio.positions[0].quantity == Decimal("100")

            assert await row_count(
                FillORM,
                FillORM.account_id == success_account.account_id,
            ) == 1

            assert await row_count(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id == success_account.account_id,
            ) == 3

            assert await row_count(
                PositionORM,
                PositionORM.account_id == success_account.account_id,
            ) == 1

            async with sessions() as session:
                stored_fill = await session.scalar(
                    select(FillORM).where(
                        FillORM.execution_attempt_id
                        == prepared.attempt.attempt_id
                    )
                )
                assert stored_fill is not None
                assert stored_fill.fill_id == prepared.attempt.fill_id

            # ------------------------------------------------------
            # 2. Repeated completion must be strictly idempotent
            # ------------------------------------------------------
            repeated = await repository.complete_execution_attempt(
                prepared.attempt.attempt_id
            )

            assert repeated.idempotent is True
            assert repeated.attempt == completed.attempt
            assert repeated.order == completed.order
            assert repeated.account_revision == completed.account_revision
            assert repeated.portfolio.account == completed.portfolio.account
            assert repeated.portfolio.cash_balance == completed.portfolio.cash_balance
            assert repeated.portfolio.positions == completed.portfolio.positions
            assert (
                repeated.portfolio.processed_fill_ids
                == completed.portfolio.processed_fill_ids
            )

            # Database reads need not preserve ledger row ordering.
            # Compare complete immutable entries by their stable IDs.
            repeated_ledger = {
                entry.entry_id: entry
                for entry in repeated.portfolio.ledger_entries
            }
            completed_ledger = {
                entry.entry_id: entry
                for entry in completed.portfolio.ledger_entries
            }
            assert repeated_ledger == completed_ledger

            assert await row_count(
                FillORM,
                FillORM.account_id == success_account.account_id,
            ) == 1

            assert await row_count(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id == success_account.account_id,
            ) == 3

            success_state_after = await repository.get_account_state(
                success_account.account_id
            )
            assert success_state_after is not None
            assert success_state_after.account_revision == 2

            # ------------------------------------------------------
            # 3. NO_LIQUIDITY completes the attempt without a fill
            # ------------------------------------------------------
            no_liquidity_account = make_account("no-liquidity")
            await repository.create_account(no_liquidity_account)

            no_liquidity_state = await repository.get_account_state(
                no_liquidity_account.account_id
            )
            assert no_liquidity_state is not None

            no_liquidity_time = now + timedelta(seconds=1)
            no_liquidity_order = make_order(
                no_liquidity_account.account_id,
                no_liquidity_time,
            )

            no_liquidity_prepared = (
                await repository.create_order_with_prepared_attempt(
                    no_liquidity_order,
                    make_inputs(
                        no_liquidity_time,
                        liquidity=Decimal("0"),
                    ),
                    expected_account_revision=(
                        no_liquidity_state.account_revision
                    ),
                )
            )

            no_liquidity_completed = (
                await repository.complete_execution_attempt(
                    no_liquidity_prepared.attempt.attempt_id
                )
            )

            assert no_liquidity_completed.attempt.status.value == "completed"
            assert (
                no_liquidity_completed.attempt.outcome.value
                == "no_liquidity"
            )
            assert no_liquidity_completed.order.status is OrderStatus.ACCEPTED
            assert no_liquidity_completed.account_revision == 2

            assert await row_count(
                FillORM,
                FillORM.account_id == no_liquidity_account.account_id,
            ) == 0

            assert await row_count(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id
                == no_liquidity_account.account_id,
            ) == 1

            no_liquidity_retry = (
                await repository.complete_execution_attempt(
                    no_liquidity_prepared.attempt.attempt_id
                )
            )

            assert no_liquidity_retry.idempotent is True
            assert (
                no_liquidity_retry.account_revision
                == no_liquidity_completed.account_revision
            )

            assert await row_count(
                FillORM,
                FillORM.account_id == no_liquidity_account.account_id,
            ) == 0

            # ------------------------------------------------------
            # 4. Failure during persistence rolls back everything
            # ------------------------------------------------------
            rollback_account = make_account("rollback")
            await repository.create_account(rollback_account)

            rollback_state = await repository.get_account_state(
                rollback_account.account_id
            )
            assert rollback_state is not None

            rollback_time = now + timedelta(seconds=2)
            rollback_order = make_order(
                rollback_account.account_id,
                rollback_time,
            )

            rollback_prepared = (
                await repository.create_order_with_prepared_attempt(
                    rollback_order,
                    make_inputs(rollback_time),
                    expected_account_revision=rollback_state.account_revision,
                )
            )

            async def fail_position_save(session, **kwargs):
                raise RuntimeError("Intentional attempt accounting failure.")

            monkeypatch.setattr(
                repository,
                "_save_position",
                fail_position_save,
            )

            with pytest.raises(
                RuntimeError,
                match="Intentional attempt accounting failure",
            ):
                await repository.complete_execution_attempt(
                    rollback_prepared.attempt.attempt_id
                )

            monkeypatch.undo()

            # The failed completion must leave the original PREPARED
            # attempt and order untouched.
            recovered_attempt = await repository.get_execution_attempt(
                rollback_prepared.attempt.attempt_id
            )
            assert recovered_attempt is not None
            assert recovered_attempt.status.value == "prepared"
            assert recovered_attempt.outcome is None
            assert recovered_attempt.completed_at is None

            recovered_order = await repository.get_order(
                rollback_account.account_id,
                rollback_order.order_id,
            )
            assert recovered_order is not None
            assert recovered_order.status is OrderStatus.PENDING
            assert recovered_order.filled_quantity == Decimal("0")

            assert await row_count(
                FillORM,
                FillORM.account_id == rollback_account.account_id,
            ) == 0

            assert await row_count(
                CashLedgerEntryORM,
                CashLedgerEntryORM.account_id == rollback_account.account_id,
            ) == 1

            assert await row_count(
                PositionORM,
                PositionORM.account_id == rollback_account.account_id,
            ) == 0

            rollback_state_after = await repository.get_account_state(
                rollback_account.account_id
            )
            assert rollback_state_after is not None
            assert rollback_state_after.account_revision == 1
            assert rollback_state_after.active_order == rollback_order

            # All three accounts remain internally consistent.
            for account_id in (
                success_account.account_id,
                no_liquidity_account.account_id,
                rollback_account.account_id,
            ):
                report = await repository.reconcile_account(account_id)
                assert report is not None
                assert report.is_consistent is True, (
                    "\n" + "\n".join(issue.message for issue in report.issues)
                )
                assert report.is_fully_verifiable is True

        finally:
            monkeypatch.undo()

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
