from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    AccountStatus,
    LedgerEntryType,
    OrderSide,
    OrderStatus,
    OrderType,
)
from atlas_quant.services.execution.domain.fills import Fill
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


_STORAGE_QUANTUM = Decimal("0.000000000000000001")


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    code: str
    entity_type: str
    entity_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ExecutionReconciliationReport:
    account_id: UUID
    checked_at: datetime
    is_consistent: bool
    is_fully_verifiable: bool
    replayed_cash: Decimal | None
    persisted_cash: Decimal
    ledger_cash: Decimal
    replayed_positions: tuple[Position, ...]
    issues: tuple[ReconciliationIssue, ...]


class SQLAlchemyExecutionReconciler:
    """Read-only verification of persisted paper-trading accounting.

    The account row is locked while the snapshot is read. This prevents
    concurrent repository fill transactions from changing the account
    during reconciliation. No financial records are modified.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def reconcile_account(
        self,
        account_id: UUID,
    ) -> ExecutionReconciliationReport | None:
        checked_at = datetime.now(timezone.utc)
        issues: list[ReconciliationIssue] = []
        fully_verifiable = True

        async with self._session_factory() as session:
            async with session.begin():
                account_row = await session.scalar(
                    select(PaperAccountORM)
                    .where(PaperAccountORM.account_id == account_id)
                    .with_for_update()
                )

                if account_row is None:
                    return None

                account = PaperTradingAccount(
                    account_id=account_row.account_id,
                    name=account_row.name,
                    base_currency=account_row.base_currency,
                    initial_capital=account_row.initial_capital,
                    status=AccountStatus(account_row.status),
                    created_at=account_row.created_at,
                )

                order_rows = (
                    await session.scalars(
                        select(OrderORM)
                        .where(OrderORM.account_id == account_id)
                        .order_by(OrderORM.created_at, OrderORM.order_id)
                    )
                ).all()

                fill_rows = (
                    await session.scalars(
                        select(FillORM)
                        .where(FillORM.account_id == account_id)
                        .order_by(FillORM.executed_at, FillORM.fill_id)
                    )
                ).all()

                ledger_rows = (
                    await session.scalars(
                        select(CashLedgerEntryORM)
                        .where(CashLedgerEntryORM.account_id == account_id)
                        .order_by(
                            CashLedgerEntryORM.occurred_at,
                            CashLedgerEntryORM.entry_id,
                        )
                    )
                ).all()

                position_rows = (
                    await session.scalars(
                        select(PositionORM)
                        .where(PositionORM.account_id == account_id)
                    )
                ).all()

                with localcontext() as decimal_context:
                    decimal_context.prec = 60

                    replay_portfolio, replayed_orders, replayed_fill_ids = (
                        self._replay(
                            account,
                            order_rows,
                            fill_rows,
                            issues,
                        )
                    )

                    all_fill_ids = {row.fill_id for row in fill_rows}
                    replay_complete = replayed_fill_ids == all_fill_ids

                    if not replay_complete:
                        issues.append(
                            ReconciliationIssue(
                                code="FILL_REPLAY_INCOMPLETE",
                                entity_type="account",
                                entity_id=str(account_id),
                                message=(
                                    "One or more persisted fills could not "
                                    "be included in the accounting replay. "
                                    "Replay-derived totals are provisional."
                                ),
                            )
                        )

                    legacy_rows = [
                        row for row in fill_rows
                        if row.accounting_metadata_version == 0
                    ]

                    unsupported_rows = [
                        row for row in fill_rows
                        if row.accounting_metadata_version not in (0, 1)
                    ]

                    if legacy_rows:
                        for row in legacy_rows:
                            issues.append(
                                ReconciliationIssue(
                                    code="LEGACY_ACCOUNTING_METADATA",
                                    entity_type="fill",
                                    entity_id=str(row.fill_id),
                                    message=(
                                        "This fill uses legacy accounting "
                                        "metadata. Its stored contract size "
                                        "and quote currency cannot be "
                                        "independently verified."
                                    ),
                                )
                            )

                    if unsupported_rows:
                        for row in unsupported_rows:
                            issues.append(
                                ReconciliationIssue(
                                    code="UNSUPPORTED_FILL_METADATA",
                                    entity_type="fill",
                                    entity_id=str(row.fill_id),
                                    message=(
                                        "This fill uses an unsupported "
                                        "accounting metadata version."
                                    ),
                                )
                            )

                    replay_integrity_issue_codes = {
                        "ORDER_INVALID",
                        "FILL_ORDER_MISSING",
                        "FILL_ACCOUNT_MISMATCH",
                        "FILL_INSTRUMENT_MISMATCH",
                        "FILL_REPLAY_FAILED",
                    }
                    replay_has_integrity_issues = any(
                        issue.code in replay_integrity_issue_codes
                        for issue in issues
                    )

                    fully_verifiable = (
                        replay_complete
                        and not legacy_rows
                        and not unsupported_rows
                        and not replay_has_integrity_issues
                    )

                    # Account-versus-ledger cash remains independently
                    # verifiable even if fill replay is incomplete.
                    self._verify_cash(
                        account_row.current_cash,
                        ledger_rows,
                        (
                            replay_portfolio.cash_balance
                            if fully_verifiable
                            else None
                        ),
                        account_id,
                        issues,
                    )

                    if fully_verifiable:
                        self._verify_ledger(
                            account,
                            replay_portfolio,
                            ledger_rows,
                            issues,
                        )

                        self._verify_positions(
                            replay_portfolio.positions,
                            position_rows,
                            account_id,
                            issues,
                        )

                        self._verify_orders(
                            order_rows,
                            replayed_orders,
                            account_id,
                            issues,
                        )

        return ExecutionReconciliationReport(
            account_id=account_id,
            checked_at=checked_at,
            is_consistent=not issues,
            is_fully_verifiable=fully_verifiable,
            replayed_cash=(
                replay_portfolio.cash_balance
                if fully_verifiable
                else None
            ),
            persisted_cash=account_row.current_cash,
            ledger_cash=sum(
                (row.amount for row in ledger_rows),
                Decimal("0"),
            ),
            replayed_positions=(
                tuple(replay_portfolio.positions)
                if fully_verifiable
                else ()
            ),
            issues=tuple(issues),
        )

    def _replay(
        self,
        account: PaperTradingAccount,
        order_rows: list[OrderORM],
        fill_rows: list[FillORM],
        issues: list[ReconciliationIssue],
    ) -> tuple[PortfolioState, dict[UUID, Order], set[UUID]]:
        # Historical fills remain valid evidence even if the account
        # has subsequently been disabled.
        replay_account = replace(account, status=AccountStatus.ACTIVE)
        portfolio = create_portfolio(replay_account)

        original_orders: dict[UUID, Order] = {}
        replayed_orders: dict[UUID, Order] = {}

        for row in order_rows:
            try:
                order = Order(
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
                original_orders[row.order_id] = order

                replayed_orders[row.order_id] = replace(
                    order,
                    status=OrderStatus.PENDING,
                    filled_quantity=Decimal("0"),
                    updated_at=order.created_at,
                )
            except (TypeError, ValueError) as exc:
                issues.append(
                    ReconciliationIssue(
                        code="ORDER_INVALID",
                        entity_type="order",
                        entity_id=str(row.order_id),
                        message=f"Persisted order cannot be reconstructed: {exc}",
                    )
                )

        replayed_fill_ids: set[UUID] = set()

        # The SQL query already orders by (executed_at, fill_id).
        for row in fill_rows:
            order = replayed_orders.get(row.order_id)

            if order is None:
                issues.append(
                    ReconciliationIssue(
                        code="FILL_ORDER_MISSING",
                        entity_type="fill",
                        entity_id=str(row.fill_id),
                        message=(
                            "The fill references an order that could not "
                            "be reconstructed for this account."
                        ),
                    )
                )
                continue

            if row.account_id != account.account_id:
                issues.append(
                    ReconciliationIssue(
                        code="FILL_ACCOUNT_MISMATCH",
                        entity_type="fill",
                        entity_id=str(row.fill_id),
                        message="Fill account does not match the reconciled account.",
                    )
                )
                continue

            if row.instrument_id != order.instrument_id:
                issues.append(
                    ReconciliationIssue(
                        code="FILL_INSTRUMENT_MISMATCH",
                        entity_type="fill",
                        entity_id=str(row.fill_id),
                        message="Fill instrument does not match its order.",
                    )
                )
                continue

            if row.accounting_metadata_version not in (0, 1):
                issues.append(
                    ReconciliationIssue(
                        code="UNSUPPORTED_FILL_METADATA",
                        entity_type="fill",
                        entity_id=str(row.fill_id),
                        message="Fill accounting metadata version is unsupported.",
                    )
                )
                continue

            try:
                fill = Fill(
                    fill_id=row.fill_id,
                    order_id=row.order_id,
                    account_id=row.account_id,
                    instrument_id=row.instrument_id,
                    quantity=row.quantity,
                    price=row.price,
                    commission=row.commission,
                    commission_currency=row.commission_currency,
                    executed_at=row.executed_at,
                )

                if order.status is OrderStatus.PENDING:
                    order = order.accept(fill.executed_at)

                application = apply_fill(
                    portfolio,
                    order,
                    fill,
                    quote_currency=row.quote_currency,
                    contract_size=row.contract_size,
                )

                # Match the database round-trip after each fill.
                # The repository reloads Numeric(38,18) values before
                # applying the next fill, so replay must do the same.
                portfolio = _normalize_portfolio_for_storage(
                    application.portfolio
                )
                replayed_orders[row.order_id] = application.order
                replayed_fill_ids.add(row.fill_id)

            except (TypeError, ValueError, ArithmeticError) as exc:
                issues.append(
                    ReconciliationIssue(
                        code="FILL_REPLAY_FAILED",
                        entity_type="fill",
                        entity_id=str(row.fill_id),
                        message=f"Fill could not be replayed: {exc}",
                    )
                )

        return portfolio, replayed_orders, replayed_fill_ids

    def _verify_ledger(
        self,
        account: PaperTradingAccount,
        replay_portfolio: PortfolioState,
        ledger_rows: list[CashLedgerEntryORM],
        issues: list[ReconciliationIssue],
    ) -> None:
        expected: dict[tuple[str, UUID], list] = {}

        for entry in replay_portfolio.ledger_entries:
            key = (entry.entry_type.value, entry.reference_id)
            expected.setdefault(key, []).append(entry)

        actual: dict[tuple[str, UUID], list[CashLedgerEntryORM]] = {}

        for row in ledger_rows:
            key = (row.entry_type, row.reference_id)
            actual.setdefault(key, []).append(row)

        for key, expected_entries in expected.items():
            actual_entries = actual.pop(key, [])

            if not actual_entries:
                issues.append(
                    ReconciliationIssue(
                        code="LEDGER_ENTRY_MISSING",
                        entity_type="ledger_reference",
                        entity_id=str(key[1]),
                        message=f"Missing expected {key[0]} ledger entry.",
                    )
                )
                continue

            if len(actual_entries) != len(expected_entries):
                issues.append(
                    ReconciliationIssue(
                        code="LEDGER_ENTRY_COUNT_MISMATCH",
                        entity_type="ledger_reference",
                        entity_id=str(key[1]),
                        message=(
                            f"Expected {len(expected_entries)} {key[0]} "
                            f"entry/entries but found {len(actual_entries)}."
                        ),
                    )
                )

            for expected_entry, actual_entry in zip(
                expected_entries,
                actual_entries,
            ):
                if (
                    not _decimal_equal(
                        expected_entry.amount,
                        actual_entry.amount,
                    )
                    or expected_entry.currency != actual_entry.currency
                    or expected_entry.occurred_at != actual_entry.occurred_at
                    or actual_entry.account_id != account.account_id
                ):
                    issues.append(
                        ReconciliationIssue(
                            code="LEDGER_ENTRY_MISMATCH",
                            entity_type="ledger_entry",
                            entity_id=str(actual_entry.entry_id),
                            message=(
                                f"amount expected={expected_entry.amount!r}, actual={actual_entry.amount!r}; "
                                f"currency expected={expected_entry.currency!r}, actual={actual_entry.currency!r}; "
                                f"occurred_at expected={expected_entry.occurred_at!r}, actual={actual_entry.occurred_at!r}; "
                                f"description expected={expected_entry.description!r}, actual={actual_entry.description!r}; "
                                f"account_id expected={account.account_id!r}, actual={actual_entry.account_id!r}"
                            ),
                        )
                    )

        for key, rows in actual.items():
            for row in rows:
                issues.append(
                    ReconciliationIssue(
                        code="LEDGER_ENTRY_UNEXPECTED",
                        entity_type="ledger_entry",
                        entity_id=str(row.entry_id),
                        message=(
                            f"Unexpected {row.entry_type} ledger entry "
                            "or duplicate accounting reference."
                        ),
                    )
                )

    def _verify_cash(
        self,
        persisted_cash: Decimal,
        ledger_rows: list[CashLedgerEntryORM],
        replayed_cash: Decimal | None,
        account_id: UUID,
        issues: list[ReconciliationIssue],
    ) -> None:
        ledger_cash = sum(
            (row.amount for row in ledger_rows),
            Decimal("0"),
        )

        if not _decimal_equal(persisted_cash, ledger_cash):
            issues.append(
                ReconciliationIssue(
                    code="ACCOUNT_LEDGER_CASH_MISMATCH",
                    entity_type="account",
                    entity_id=str(account_id),
                    message=(
                        "Cached account cash differs from the sum "
                        "of persisted ledger entries."
                    ),
                )
            )

        if replayed_cash is not None:
            if not _decimal_equal(persisted_cash, replayed_cash):
                issues.append(
                    ReconciliationIssue(
                        code="ACCOUNT_REPLAY_CASH_MISMATCH",
                        entity_type="account",
                        entity_id=str(account_id),
                        message=(
                            "Cached account cash differs from deterministic "
                            "replay of the opening capital and fills."
                        ),
                    )
                )

            if not _decimal_equal(ledger_cash, replayed_cash):
                issues.append(
                    ReconciliationIssue(
                        code="LEDGER_REPLAY_CASH_MISMATCH",
                        entity_type="account",
                        entity_id=str(account_id),
                        message=(
                            "Ledger total differs from deterministic "
                            "replay of the opening capital and fills."
                        ),
                    )
                )

    def _verify_positions(
        self,
        replayed_positions: tuple[Position, ...],
        position_rows: list[PositionORM],
        account_id: UUID,
        issues: list[ReconciliationIssue],
    ) -> None:
        expected = {
            position.instrument_id: position
            for position in replayed_positions
        }
        actual = {
            row.instrument_id: row
            for row in position_rows
        }

        for instrument_id, position in expected.items():
            row = actual.pop(instrument_id, None)

            if row is None:
                issues.append(
                    ReconciliationIssue(
                        code="POSITION_MISSING",
                        entity_type="position",
                        entity_id=str(instrument_id),
                        message="No persisted position exists for replayed fills.",
                    )
                )
                continue

            if (
                not _decimal_equal(position.quantity, row.quantity)
                or not _decimal_equal(
                    position.average_entry_price,
                    row.average_entry_price,
                )
                or not _decimal_equal(position.realized_pnl, row.realized_pnl)
                or position.quote_currency != row.quote_currency
                or not _decimal_equal(position.contract_size, row.contract_size)
            ):
                issues.append(
                    ReconciliationIssue(
                        code="POSITION_MISMATCH",
                        entity_type="position",
                        entity_id=str(instrument_id),
                        message=(
                            "Persisted position differs from deterministic "
                            "replay of fills."
                        ),
                    )
                )

        for instrument_id in actual:
            issues.append(
                ReconciliationIssue(
                    code="POSITION_UNEXPECTED",
                    entity_type="position",
                    entity_id=str(instrument_id),
                    message=(
                        "A persisted position has no corresponding "
                        "replayed fill history."
                    ),
                )
            )

    def _verify_orders(
        self,
        order_rows: list[OrderORM],
        replayed_orders: dict[UUID, Order],
        account_id: UUID,
        issues: list[ReconciliationIssue],
    ) -> None:
        for row in order_rows:
            replayed = replayed_orders.get(row.order_id)

            if replayed is None:
                continue

            persisted_status = OrderStatus(row.status)
            replayed_quantity = replayed.filled_quantity

            if not _decimal_equal(row.filled_quantity, replayed_quantity):
                issues.append(
                    ReconciliationIssue(
                        code="ORDER_FILLED_QUANTITY_MISMATCH",
                        entity_type="order",
                        entity_id=str(row.order_id),
                        message=(
                            "Persisted filled quantity differs from "
                            "the sum of replayed fills."
                        ),
                    )
                )

            if not _status_is_compatible(
                persisted_status,
                replayed_quantity,
                row.quantity,
            ):
                issues.append(
                    ReconciliationIssue(
                        code="ORDER_STATUS_MISMATCH",
                        entity_type="order",
                        entity_id=str(row.order_id),
                        message=(
                            "Persisted order status is incompatible "
                            "with its replayed fill quantity."
                        ),
                    )
                )

            if (
                replayed_quantity > 0
                and replayed.updated_at > row.updated_at
            ):
                issues.append(
                    ReconciliationIssue(
                        code="ORDER_TIMESTAMP_MISMATCH",
                        entity_type="order",
                        entity_id=str(row.order_id),
                        message=(
                            "Persisted order update timestamp precedes "
                            "a replayed fill."
                        ),
                    )
                )


def _storage_decimal(value: Decimal) -> Decimal:
    # PostgreSQL NUMERIC rounds ties away from zero.
    return value.quantize(
        _STORAGE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _normalize_portfolio_for_storage(
    portfolio: PortfolioState,
) -> PortfolioState:
    ledger_entries = tuple(
        replace(entry, amount=_storage_decimal(entry.amount))
        for entry in portfolio.ledger_entries
    )

    positions = tuple(
        replace(
            position,
            quantity=_storage_decimal(position.quantity),
            average_entry_price=_storage_decimal(
                position.average_entry_price
            ),
            realized_pnl=_storage_decimal(position.realized_pnl),
            contract_size=_storage_decimal(position.contract_size),
        )
        for position in portfolio.positions
    )

    cash_balance = sum(
        (entry.amount for entry in ledger_entries),
        Decimal("0"),
    )

    return PortfolioState(
        account=portfolio.account,
        cash_balance=cash_balance,
        positions=positions,
        ledger_entries=ledger_entries,
        processed_fill_ids=portfolio.processed_fill_ids,
    )


def _decimal_equal(left: Decimal, right: Decimal) -> bool:
    return _storage_decimal(left) == _storage_decimal(right)


def _status_is_compatible(
    status: OrderStatus,
    filled_quantity: Decimal,
    order_quantity: Decimal,
) -> bool:
    if status is OrderStatus.FILLED:
        return filled_quantity == order_quantity

    if status is OrderStatus.PARTIALLY_FILLED:
        return Decimal("0") < filled_quantity < order_quantity

    if status in {
        OrderStatus.PENDING,
        OrderStatus.ACCEPTED,
        OrderStatus.REJECTED,
    }:
        return filled_quantity == Decimal("0")

    if status is OrderStatus.CANCELLED:
        return filled_quantity < order_quantity

    return False
