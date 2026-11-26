from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    AccountStatus,
    LedgerEntryType,
    OrderSide,
    OrderStatus,
)
from atlas_quant.services.execution.domain.fills import Fill
from atlas_quant.services.execution.domain.ledger import CashLedgerEntry
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.execution.domain.positions import Position, PositionMark


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    account: PaperTradingAccount
    cash_balance: Decimal
    positions: tuple[Position, ...] = ()
    ledger_entries: tuple[CashLedgerEntry, ...] = ()
    processed_fill_ids: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.account, PaperTradingAccount):
            raise TypeError("account must be a PaperTradingAccount.")

        if not isinstance(self.cash_balance, Decimal):
            raise TypeError("cash_balance must use Decimal.")

        if not self.cash_balance.is_finite():
            raise ValueError("cash_balance must be finite.")

        if not isinstance(self.positions, tuple) or any(
            not isinstance(item, Position) for item in self.positions
        ):
            raise TypeError("positions must be a tuple of Position objects.")

        if not isinstance(self.ledger_entries, tuple) or any(
            not isinstance(item, CashLedgerEntry)
            for item in self.ledger_entries
        ):
            raise TypeError(
                "ledger_entries must be a tuple of CashLedgerEntry objects."
            )

        if not isinstance(self.processed_fill_ids, frozenset) or any(
            not isinstance(item, UUID) for item in self.processed_fill_ids
        ):
            raise TypeError("processed_fill_ids must be a frozenset of UUIDs.")

        position_ids = [item.instrument_id for item in self.positions]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("Only one position per instrument is allowed.")

        entry_ids = [item.entry_id for item in self.ledger_entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("Ledger entry IDs must be unique.")

        if any(item.account_id != self.account.account_id for item in self.ledger_entries):
            raise ValueError("Every ledger entry must belong to this account.")

        if any(item.currency != self.account.base_currency for item in self.ledger_entries):
            raise ValueError("Every ledger entry must use the account currency.")

        ledger_total = sum(
            (item.amount for item in self.ledger_entries),
            ZERO,
        )
        if ledger_total != self.cash_balance:
            raise ValueError("cash_balance does not reconcile with the cash ledger.")

        trade_fill_ids = [
            item.reference_id
            for item in self.ledger_entries
            if item.entry_type == LedgerEntryType.TRADE
        ]
        commission_fill_ids = [
            item.reference_id
            for item in self.ledger_entries
            if item.entry_type == LedgerEntryType.COMMISSION
        ]

        if (
            len(trade_fill_ids) != len(set(trade_fill_ids))
            or len(commission_fill_ids) != len(set(commission_fill_ids))
        ):
            raise ValueError("Each fill must have one trade and one commission ledger entry.")

        if (
            set(trade_fill_ids) != self.processed_fill_ids
            or set(commission_fill_ids) != self.processed_fill_ids
        ):
            raise ValueError(
                "Processed fill IDs must reconcile with trade and commission ledger entries."
            )


@dataclass(frozen=True, slots=True)
class FillApplicationResult:
    order: Order
    portfolio: PortfolioState


@dataclass(frozen=True, slots=True)
class PositionValuation:
    instrument_id: UUID
    quantity: Decimal
    mark_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    account_id: UUID
    currency: str
    cash_balance: Decimal
    position_market_value: Decimal
    equity: Decimal
    initial_capital: Decimal
    net_profit_loss: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    commission_cost: Decimal
    positions: tuple[PositionValuation, ...]
    valued_at: datetime


def create_portfolio(
    account: PaperTradingAccount,
) -> PortfolioState:
    """Create a portfolio and record its initial capital in the cash ledger."""

    if not isinstance(account, PaperTradingAccount):
        raise TypeError("account must be a PaperTradingAccount.")

    entry = CashLedgerEntry(
        account_id=account.account_id,
        entry_type=LedgerEntryType.ADJUSTMENT,
        amount=account.initial_capital,
        currency=account.base_currency,
        reference_id=account.account_id,
        description="Initial account capital",
        occurred_at=account.created_at,
    )

    return PortfolioState(
        account=account,
        cash_balance=account.initial_capital,
        ledger_entries=(entry,),
    )


def apply_fill(
    portfolio: PortfolioState,
    order: Order,
    fill: Fill,
    quote_currency: str,
    contract_size: Decimal | None = None,
) -> FillApplicationResult:
    """Apply a fill atomically to an order, position, and cash ledger."""

    if not isinstance(portfolio, PortfolioState):
        raise TypeError("portfolio must be a PortfolioState.")

    if not isinstance(order, Order):
        raise TypeError("order must be an Order.")

    if not isinstance(fill, Fill):
        raise TypeError("fill must be a Fill.")

    account = portfolio.account

    if account.status != AccountStatus.ACTIVE:
        raise ValueError("Cannot apply fills to a disabled account.")

    if order.account_id != account.account_id or fill.account_id != account.account_id:
        raise ValueError("Order and fill must belong to the portfolio account.")

    if fill.order_id != order.order_id:
        raise ValueError("Fill does not belong to the supplied order.")

    if fill.instrument_id != order.instrument_id:
        raise ValueError("Fill instrument does not match the order.")

    if order.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
        raise ValueError("Only active orders can receive fills.")

    if fill.fill_id in portfolio.processed_fill_ids:
        raise ValueError("This fill has already been applied.")

    if fill.executed_at < order.created_at:
        raise ValueError("Fill cannot precede order creation.")

    currency = _normalize_currency(quote_currency, "quote_currency")
    if currency != account.base_currency:
        raise ValueError(
            "Instrument quote currency must match account currency; "
            "FX conversion is not supported."
        )

    if fill.commission_currency != account.base_currency:
        raise ValueError(
            "Commission currency must match account currency; "
            "FX conversion is not supported."
        )

    multiplier = contract_size if contract_size is not None else ONE
    if not isinstance(multiplier, Decimal):
        raise TypeError("contract_size must use Decimal.")
    if not multiplier.is_finite() or multiplier <= ZERO:
        raise ValueError("contract_size must be finite and positive.")

    updated_order = order.apply_fill(fill.quantity, fill.executed_at)

    positions = {item.instrument_id: item for item in portfolio.positions}
    current = positions.get(order.instrument_id)

    if current is None:
        current = Position(
            instrument_id=order.instrument_id,
            quote_currency=currency,
            quantity=ZERO,
            average_entry_price=ZERO,
            contract_size=multiplier,
        )
    else:
        if current.quote_currency != currency:
            raise ValueError("Position quote currency cannot change.")
        if current.contract_size != multiplier:
            raise ValueError("Position contract size cannot change while accounting.")

    signed_fill_quantity = (
        fill.quantity if order.side == OrderSide.BUY else -fill.quantity
    )

    new_position = _apply_position_fill(
        current,
        signed_fill_quantity,
        fill.price,
    )

    trade_cash_movement = (
        -fill.quantity * fill.price * multiplier
        if order.side == OrderSide.BUY
        else fill.quantity * fill.price * multiplier
    )

    trade_entry = CashLedgerEntry(
        account_id=account.account_id,
        entry_type=LedgerEntryType.TRADE,
        amount=trade_cash_movement,
        currency=account.base_currency,
        reference_id=fill.fill_id,
        description=f"{order.side.value.title()} {fill.quantity} {order.instrument_id}",
        occurred_at=fill.executed_at,
    )

    commission_entry = CashLedgerEntry(
        account_id=account.account_id,
        entry_type=LedgerEntryType.COMMISSION,
        amount=-fill.commission,
        currency=account.base_currency,
        reference_id=fill.fill_id,
        description="Execution commission",
        occurred_at=fill.executed_at,
    )

    updated_positions = dict(positions)
    updated_positions[order.instrument_id] = new_position

    new_entries = portfolio.ledger_entries + (trade_entry, commission_entry)
    new_cash = portfolio.cash_balance + trade_cash_movement - fill.commission

    updated_portfolio = PortfolioState(
        account=account,
        cash_balance=new_cash,
        positions=tuple(
            sorted(updated_positions.values(), key=lambda item: str(item.instrument_id))
        ),
        ledger_entries=new_entries,
        processed_fill_ids=portfolio.processed_fill_ids | {fill.fill_id},
    )

    return FillApplicationResult(
        order=updated_order,
        portfolio=updated_portfolio,
    )


def value_portfolio(
    portfolio: PortfolioState,
    marks: tuple[PositionMark, ...],
    *,
    as_of: datetime | None = None,
    max_mark_age: timedelta | None = None,
) -> PortfolioSnapshot:
    """Value positions at executable liquidation prices and reconcile equity."""

    if not isinstance(portfolio, PortfolioState):
        raise TypeError("portfolio must be a PortfolioState.")

    if not isinstance(marks, tuple) or any(
        not isinstance(mark, PositionMark) for mark in marks
    ):
        raise TypeError("marks must be a tuple of PositionMark objects.")

    valuation_time = as_of if as_of is not None else datetime.now(timezone.utc)
    if not isinstance(valuation_time, datetime):
        raise TypeError("as_of must be a datetime or None.")
    if valuation_time.tzinfo is None or valuation_time.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware.")
    valuation_time = valuation_time.astimezone(timezone.utc)

    if max_mark_age is not None:
        if not isinstance(max_mark_age, timedelta):
            raise TypeError("max_mark_age must be a timedelta or None.")
        if max_mark_age < timedelta(0):
            raise ValueError("max_mark_age cannot be negative.")

    mark_map = {mark.instrument_id: mark for mark in marks}
    if len(mark_map) != len(marks):
        raise ValueError("Duplicate instrument marks are not allowed.")

    valuations: list[PositionValuation] = []
    total_market_value = ZERO
    total_unrealized = ZERO
    total_realized = ZERO

    for position in portfolio.positions:
        if position.instrument_id not in mark_map:
            raise ValueError(f"Missing mark for {position.instrument_id}.")

        mark = mark_map[position.instrument_id]

        if mark.timestamp > valuation_time:
            raise ValueError("A position mark cannot be later than the valuation time.")

        if (
            max_mark_age is not None
            and valuation_time - mark.timestamp > max_mark_age
        ):
            raise ValueError("A position mark is older than max_mark_age.")

        liquidation_price = mark.bid if position.quantity > ZERO else mark.ask

        market_value = (
            position.quantity * liquidation_price * position.contract_size
        )
        unrealized = (
            position.quantity
            * (liquidation_price - position.average_entry_price)
            * position.contract_size
        )

        valuations.append(
            PositionValuation(
                instrument_id=position.instrument_id,
                quantity=position.quantity,
                mark_price=liquidation_price,
                market_value=market_value,
                unrealized_pnl=unrealized,
                realized_pnl=position.realized_pnl,
            )
        )

        total_market_value += market_value
        total_unrealized += unrealized
        total_realized += position.realized_pnl

    equity = portfolio.cash_balance + total_market_value
    net_profit_loss = equity - portfolio.account.initial_capital

    commission_cost = -sum(
        (
            entry.amount
            for entry in portfolio.ledger_entries
            if entry.entry_type == LedgerEntryType.COMMISSION
        ),
        ZERO,
    )

    return PortfolioSnapshot(
        account_id=portfolio.account.account_id,
        currency=portfolio.account.base_currency,
        cash_balance=portfolio.cash_balance,
        position_market_value=total_market_value,
        equity=equity,
        initial_capital=portfolio.account.initial_capital,
        net_profit_loss=net_profit_loss,
        realized_pnl=total_realized,
        unrealized_pnl=total_unrealized,
        commission_cost=commission_cost,
        positions=tuple(valuations),
        valued_at=valuation_time,
    )


def _apply_position_fill(
    current: Position,
    signed_fill_quantity: Decimal,
    fill_price: Decimal,
) -> Position:
    """Apply a signed fill using average-cost accounting."""

    old_quantity = current.quantity
    new_quantity = old_quantity + signed_fill_quantity
    realized = current.realized_pnl

    same_direction = (
        old_quantity == ZERO
        or (old_quantity > ZERO and signed_fill_quantity > ZERO)
        or (old_quantity < ZERO and signed_fill_quantity < ZERO)
    )

    if same_direction:
        old_notional = abs(old_quantity) * current.average_entry_price
        added_notional = abs(signed_fill_quantity) * fill_price
        total_quantity = abs(new_quantity)
        average = (
            (old_notional + added_notional) / total_quantity
            if total_quantity != ZERO
            else ZERO
        )
    else:
        closed_quantity = min(abs(old_quantity), abs(signed_fill_quantity))
        direction = ONE if old_quantity > ZERO else -ONE

        realized += (
            closed_quantity
            * (fill_price - current.average_entry_price)
            * direction
            * current.contract_size
        )

        if new_quantity == ZERO:
            average = ZERO
        elif (new_quantity > ZERO) == (old_quantity > ZERO):
            average = current.average_entry_price
        else:
            average = fill_price

    return Position(
        instrument_id=current.instrument_id,
        quote_currency=current.quote_currency,
        quantity=new_quantity,
        average_entry_price=average,
        realized_pnl=realized,
        contract_size=current.contract_size,
    )


def _normalize_currency(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string.")

    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(f"{name} must be a three-letter currency code.")

    return currency