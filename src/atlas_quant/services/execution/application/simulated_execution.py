"""Deterministic, quote-driven simulated execution for paper orders."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from enum import StrEnum
from uuid import UUID, uuid4

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import (
    AccountStatus,
    OrderSide,
    OrderStatus,
)
from atlas_quant.services.execution.domain.fills import Fill
from atlas_quant.services.execution.domain.orders import Order
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


class MissingLiquidityPolicy(StrEnum):
    """Policy used when a quote does not report available size."""

    REJECT = "reject"
    ASSUME_FULL_FILL = "assume_full_fill"


class ExecutionOutcome(StrEnum):
    """Outcome of one simulated execution attempt."""

    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    NO_LIQUIDITY = "no_liquidity"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SimulatedExecutionConfig:
    """Explicit assumptions and costs for simulated market execution.

    commission_per_1000_units is denominated in the paper account currency
    and is charged for each 1,000 units filled, on each fill.

    slippage_ticks is an adverse whole-number tick adjustment. The spread is
    already represented by executing buys at ask and sells at bid.
    """

    commission_per_1000_units: Decimal = Decimal("0")
    slippage_ticks: int = 0
    max_quote_age: timedelta | None = timedelta(seconds=5)
    missing_liquidity_policy: MissingLiquidityPolicy = (
        MissingLiquidityPolicy.REJECT
    )

    def __post_init__(self) -> None:
        if not isinstance(self.commission_per_1000_units, Decimal):
            raise TypeError(
                "commission_per_1000_units must be a Decimal."
            )
        if (
            not self.commission_per_1000_units.is_finite()
            or self.commission_per_1000_units < Decimal("0")
        ):
            raise ValueError(
                "commission_per_1000_units must be finite and nonnegative."
            )

        if (
            isinstance(self.slippage_ticks, bool)
            or not isinstance(self.slippage_ticks, int)
        ):
            raise TypeError("slippage_ticks must be an integer.")
        if self.slippage_ticks < 0:
            raise ValueError("slippage_ticks cannot be negative.")

        if self.max_quote_age is not None:
            if not isinstance(self.max_quote_age, timedelta):
                raise TypeError("max_quote_age must be a timedelta or None.")
            if self.max_quote_age <= timedelta(0):
                raise ValueError("max_quote_age must be positive.")

        if not isinstance(
            self.missing_liquidity_policy,
            MissingLiquidityPolicy,
        ):
            raise TypeError(
                "missing_liquidity_policy must be a MissingLiquidityPolicy."
            )


@dataclass(frozen=True, slots=True)
class SimulatedExecutionResult:
    """Immutable audit result for one execution attempt."""

    order: Order
    fills: tuple[Fill, ...]
    outcome: ExecutionOutcome
    reason: str | None
    quote_timestamp: datetime
    reference_price: Decimal | None = None
    execution_price: Decimal | None = None
    slippage_cost: Decimal = Decimal("0")
    liquidity_assumed: bool = False


class SimulatedExecutionEngine:
    """Execute market orders against supplied bid/ask quotes.

    The engine does not mutate accounts or portfolios. It returns an updated
    immutable order and zero or one immutable fill. Portfolio accounting
    remains the responsibility of the PT-1 domain.
    """

    def execute(
        self,
        order: Order,
        account: PaperTradingAccount,
        instrument: Instrument,
        quote: BidAskQuote,
        executed_at: datetime,
        config: SimulatedExecutionConfig | None = None,
        fill_id: UUID | None = None,
    ) -> SimulatedExecutionResult:
        """Attempt one fill using the supplied quote and execution time."""

        settings = config or SimulatedExecutionConfig()
        at = self._normalize_time(executed_at, "executed_at")

        if at < order.updated_at:
            raise ValueError(
                "executed_at cannot be earlier than the order's updated_at."
            )

        if order.status not in {
            OrderStatus.PENDING,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            return self._result(
                order=order,
                quote=quote,
                outcome=ExecutionOutcome.REJECTED,
                reason="Order is not in an executable state.",
            )

        rejection = self._validate_execution(
            order=order,
            account=account,
            instrument=instrument,
            quote=quote,
            at=at,
            config=settings,
        )
        if rejection is not None:
            rejected_order = (
                order.reject(at)
                if order.status is OrderStatus.PENDING
                else order
            )
            return self._result(
                order=rejected_order,
                quote=quote,
                outcome=ExecutionOutcome.REJECTED,
                reason=rejection,
            )

        working_order = (
            order.accept(at)
            if order.status is OrderStatus.PENDING
            else order
        )

        remaining = working_order.quantity - working_order.filled_quantity
        quote_size = (
            quote.ask_size
            if working_order.side is OrderSide.BUY
            else quote.bid_size
        )

        liquidity_assumed = False
        if quote_size is None:
            if (
                settings.missing_liquidity_policy
                is MissingLiquidityPolicy.REJECT
            ):
                return self._result(
                    order=working_order,
                    quote=quote,
                    outcome=ExecutionOutcome.NO_LIQUIDITY,
                    reason=(
                        "Quote size is unavailable; no liquidity assumption "
                        "was authorized."
                    ),
                )
            available = remaining
            liquidity_assumed = True
        else:
            available = min(remaining, quote_size)

        fill_quantity = self._round_quantity_down(
            available,
            instrument.quantity_precision,
        )

        if fill_quantity <= Decimal("0"):
            return self._result(
                order=working_order,
                quote=quote,
                outcome=ExecutionOutcome.NO_LIQUIDITY,
                reason="The quote reports no executable quantity.",
                liquidity_assumed=liquidity_assumed,
            )

        reference_price = (
            quote.ask
            if working_order.side is OrderSide.BUY
            else quote.bid
        )
        execution_price = self._calculate_execution_price(
            reference_price=reference_price,
            side=working_order.side,
            instrument=instrument,
            slippage_ticks=settings.slippage_ticks,
        )

        if execution_price <= Decimal("0"):
            return self._reject_result(
                order=working_order,
                quote=quote,
                at=at,
                reason="Adverse slippage produced a nonpositive price.",
            )

        commission = (
            fill_quantity
            * settings.commission_per_1000_units
            / Decimal("1000")
        )

        fill = Fill(
            order_id=working_order.order_id,
            account_id=working_order.account_id,
            instrument_id=working_order.instrument_id,
            quantity=fill_quantity,
            price=execution_price,
            commission=commission,
            commission_currency=account.base_currency,
            fill_id=fill_id if fill_id is not None else uuid4(),
            executed_at=at,
        )

        updated_order = working_order.apply_fill(fill_quantity, at)
        outcome = (
            ExecutionOutcome.FILLED
            if updated_order.status is OrderStatus.FILLED
            else ExecutionOutcome.PARTIALLY_FILLED
        )

        contract_size = (
            instrument.contract_size
            if instrument.contract_size is not None
            else Decimal("1")
        )
        slippage_cost = (
            abs(execution_price - reference_price)
            * fill_quantity
            * contract_size
        )

        return SimulatedExecutionResult(
            order=updated_order,
            fills=(fill,),
            outcome=outcome,
            reason=None,
            quote_timestamp=quote.timestamp,
            reference_price=reference_price,
            execution_price=execution_price,
            slippage_cost=slippage_cost,
            liquidity_assumed=liquidity_assumed,
        )

    @staticmethod
    def cancel(order: Order, cancelled_at: datetime) -> Order:
        """Cancel an accepted or partially filled order."""

        at = SimulatedExecutionEngine._normalize_time(
            cancelled_at,
            "cancelled_at",
        )
        return order.cancel(at)

    @staticmethod
    def _validate_execution(
        *,
        order: Order,
        account: PaperTradingAccount,
        instrument: Instrument,
        quote: BidAskQuote,
        at: datetime,
        config: SimulatedExecutionConfig,
    ) -> str | None:
        if account.status is not AccountStatus.ACTIVE:
            return "Paper account is not active."

        if order.account_id != account.account_id:
            return "Order account_id does not match the supplied account."

        if order.instrument_id != instrument.instrument_id:
            return "Order instrument_id does not match the supplied instrument."

        if not instrument.is_active:
            return "Instrument is inactive."

        if quote.symbol != instrument.symbol:
            return "Quote symbol does not match the instrument."

        if quote.ask < quote.bid:
            return "Crossed quote is not executable."

        if instrument.quote_currency is None:
            return "Instrument quote currency is not defined."

        if instrument.quote_currency != account.base_currency:
            return (
                "Instrument quote currency must match the paper account "
                "currency; automatic FX conversion is not supported."
            )

        if quote.timestamp > at:
            return "Quote timestamp is later than the execution time."

        if (
            config.max_quote_age is not None
            and at - quote.timestamp > config.max_quote_age
        ):
            return "Quote is older than the configured maximum quote age."

        if (
            order.quantity
            != order.quantity.quantize(
                Decimal("1").scaleb(-instrument.quantity_precision)
            )
        ):
            return "Order quantity exceeds instrument quantity precision."

        if config.slippage_ticks > 0:
            if instrument.tick_size is None:
                return (
                    "Slippage in ticks requires the instrument tick_size."
                )

            reference_price = (
                quote.ask if order.side is OrderSide.BUY else quote.bid
            )
            if reference_price % instrument.tick_size != Decimal("0"):
                return (
                    "Executable quote price is not aligned to the instrument "
                    "tick_size; tick-based slippage cannot be applied safely."
                )

        return None

    @staticmethod
    def _calculate_execution_price(
        *,
        reference_price: Decimal,
        side: OrderSide,
        instrument: Instrument,
        slippage_ticks: int,
    ) -> Decimal:
        # Zero slippage means the exact executable quote, not a rounded price.
        if slippage_ticks == 0:
            return reference_price

        # Tick availability and alignment are validated before this method.
        tick = instrument.tick_size
        slippage = tick * Decimal(slippage_ticks)

        if side is OrderSide.BUY:
            return reference_price + slippage

        return reference_price - slippage

    @staticmethod
    def _round_quantity_down(
        quantity: Decimal,
        precision: int,
    ) -> Decimal:
        quantum = Decimal("1").scaleb(-precision)
        return quantity.quantize(quantum, rounding=ROUND_DOWN)

    @staticmethod
    def _normalize_time(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime.")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _result(
        *,
        order: Order,
        quote: BidAskQuote,
        outcome: ExecutionOutcome,
        reason: str | None,
        liquidity_assumed: bool = False,
    ) -> SimulatedExecutionResult:
        return SimulatedExecutionResult(
            order=order,
            fills=(),
            outcome=outcome,
            reason=reason,
            quote_timestamp=quote.timestamp,
            liquidity_assumed=liquidity_assumed,
        )

    @staticmethod
    def _reject_result(
        *,
        order: Order,
        quote: BidAskQuote,
        at: datetime,
        reason: str,
    ) -> SimulatedExecutionResult:
        rejected_order = (
            order.reject(at)
            if order.status is OrderStatus.PENDING
            else order
        )
        return SimulatedExecutionEngine._result(
            order=rejected_order,
            quote=quote,
            outcome=ExecutionOutcome.REJECTED,
            reason=reason,
        )
