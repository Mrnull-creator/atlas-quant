"""Pure application logic for independent portfolio risk evaluation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN

from atlas_quant.services.execution.domain.accounts import PaperTradingAccount
from atlas_quant.services.execution.domain.enums import AccountStatus, OrderSide
from atlas_quant.services.execution.domain.portfolio import PortfolioState
from atlas_quant.services.execution.domain.positions import Position, PositionMark
from atlas_quant.services.execution.domain.risk import (
    RiskDecision,
    RiskOutcome,
    RiskPolicy,
)
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


ZERO = Decimal("0")
ONE = Decimal("1")


def evaluate_portfolio_risk(
    portfolio: PortfolioState,
    instrument: Instrument,
    quote: BidAskQuote,
    requested_exposure: Decimal,
    policy: RiskPolicy,
    *,
    evaluated_at: datetime,
    position_marks: tuple[PositionMark, ...] = (),
    max_quote_age: timedelta = timedelta(seconds=5),
) -> RiskDecision:
    """Evaluate a target exposure without creating orders or fills.

    Exposure is measured against portfolio equity. Positive exposure is
    long and negative exposure is short. Existing positions are valued at
    executable liquidation prices: bid for longs and ask for shorts.

    The supplied quote is used for the requested instrument. Position marks
    must be supplied for every other open portfolio position.
    """

    if not isinstance(portfolio, PortfolioState):
        raise TypeError("portfolio must be a PortfolioState.")
    if not isinstance(instrument, Instrument):
        raise TypeError("instrument must be an Instrument.")
    if not isinstance(quote, BidAskQuote):
        raise TypeError("quote must be a BidAskQuote.")
    if not isinstance(requested_exposure, Decimal):
        raise TypeError("requested_exposure must use Decimal.")
    if not requested_exposure.is_finite() or not (
        Decimal("-1") <= requested_exposure <= ONE
    ):
        raise ValueError("requested_exposure must be finite and between -1 and 1.")
    if not isinstance(policy, RiskPolicy):
        raise TypeError("policy must be a RiskPolicy.")
    if not isinstance(evaluated_at, datetime):
        raise TypeError("evaluated_at must be a datetime.")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware.")
    if not isinstance(max_quote_age, timedelta) or max_quote_age < timedelta(0):
        raise ValueError("max_quote_age must be a nonnegative timedelta.")
    if not isinstance(position_marks, tuple) or any(
        not isinstance(mark, PositionMark) for mark in position_marks
    ):
        raise TypeError("position_marks must be a tuple of PositionMark objects.")

    now = evaluated_at.astimezone(timezone.utc)
    account = portfolio.account
    current_position = next(
        (
            position
            for position in portfolio.positions
            if position.instrument_id == instrument.instrument_id
        ),
        None,
    )
    current_quantity = (
        current_position.quantity if current_position is not None else ZERO
    )

    def reject(reason: str, equity: Decimal | None = None) -> RiskDecision:
        return RiskDecision(
            outcome=RiskOutcome.REJECTED,
            requested_exposure=requested_exposure,
            approved_exposure=None,
            equity=equity,
            target_quantity=current_quantity,
            order_side=None,
            order_quantity=ZERO,
            reasons=(reason,),
        )

    if account.status != AccountStatus.ACTIVE:
        return reject("The paper-trading account is not active.")

    if not instrument.is_active:
        return reject("The instrument is not active.")

    if quote.symbol != instrument.symbol:
        return reject("Quote symbol does not match the instrument.")

    if quote.ask < quote.bid:
        return reject("The quote is crossed.")

    if quote.timestamp > now:
        return reject("The quote timestamp is in the future.")

    if now - quote.timestamp > max_quote_age:
        return reject("The quote is older than the permitted maximum age.")

    if instrument.quote_currency is None:
        return reject("Instrument quote currency is not defined.")

    if instrument.quote_currency != account.base_currency:
        return reject(
            "Instrument quote currency must match account currency; "
            "FX conversion is not supported."
        )

    multiplier = (
        instrument.contract_size
        if instrument.contract_size is not None
        else ONE
    )

    if current_position is not None:
        if current_position.quote_currency != account.base_currency:
            return reject("Existing position currency does not match account currency.")
        if current_position.contract_size != multiplier:
            return reject("Instrument contract size differs from the existing position.")

    other_positions = {
        position.instrument_id: position
        for position in portfolio.positions
        if position.instrument_id != instrument.instrument_id
    }
    supplied_marks = {mark.instrument_id: mark for mark in position_marks}

    if len(supplied_marks) != len(position_marks):
        return reject("Duplicate position marks were supplied.")

    if instrument.instrument_id in supplied_marks:
        return reject(
            "Do not supply a separate mark for the requested instrument; "
            "its current quote is used."
        )

    if set(supplied_marks) != set(other_positions):
        return reject(
            "A current position mark is required for every other open position."
        )

    equity = portfolio.cash_balance
    gross_exposure = ZERO
    other_gross_exposure = ZERO

    for position in portfolio.positions:
        if position.quote_currency != account.base_currency:
            return reject(
                "Every open position must use the account currency."
            )

        if position.instrument_id == instrument.instrument_id:
            bid = quote.bid
            ask = quote.ask
        else:
            mark = supplied_marks[position.instrument_id]
            bid = mark.bid
            ask = mark.ask

            if mark.ask < mark.bid:
                return reject("An existing position mark is crossed.")
            if mark.timestamp > now:
                return reject("An existing position mark is in the future.")
            if now - mark.timestamp > max_quote_age:
                return reject("An existing position mark is stale.")

        liquidation_price = bid if position.quantity > ZERO else ask
        signed_value = (
            position.quantity * liquidation_price * position.contract_size
        )
        absolute_value = abs(signed_value)

        equity += signed_value
        gross_exposure += absolute_value

        if position.instrument_id != instrument.instrument_id:
            other_gross_exposure += absolute_value

    if not equity.is_finite() or equity <= ZERO:
        return reject("Portfolio equity must be positive.", equity)

    current_target_notional = gross_exposure - other_gross_exposure
    position_limit = policy.max_position_exposure * equity
    gross_limit = policy.max_gross_portfolio_exposure * equity
    order_limit = policy.max_order_exposure * equity

    current_breached = (
        current_target_notional > position_limit
        or gross_exposure > gross_limit
    )

    # A target long is sized using the ask; a target short uses the bid.
    entry_price = quote.ask if requested_exposure > ZERO else quote.bid
    requested_notional = abs(requested_exposure) * equity
    requested_quantity = _floor_quantity(
        requested_notional / (entry_price * multiplier),
        instrument.quantity_precision,
    )
    if requested_exposure < ZERO:
        requested_quantity = -requested_quantity

    reasons: list[str] = []
    target_quantity = requested_quantity

    if current_breached:
        # Existing breaches must not prevent a reduction. Do not cross through
        # zero into a new position while the portfolio is already over a limit.
        reduces_existing = (
            abs(requested_quantity) < abs(current_quantity)
            and (
                requested_quantity == ZERO
                or current_quantity == ZERO
                or (requested_quantity > ZERO) == (current_quantity > ZERO)
            )
        )

        if reduces_existing:
            target_quantity = requested_quantity
            reasons.append(
                "Risk-reducing exposure change permitted while an existing "
                "portfolio limit is breached."
            )
        elif requested_quantity == current_quantity:
            target_quantity = current_quantity
            reasons.append("No additional exposure is requested.")
        elif (
            current_quantity != ZERO
            and requested_quantity != ZERO
            and (requested_quantity > ZERO) != (current_quantity > ZERO)
        ):
            target_quantity = ZERO
            reasons.append(
                "Opposite-direction target capped at flat while an existing "
                "portfolio limit is breached."
            )
        else:
            target_quantity = current_quantity
            reasons.append(
                "Additional exposure is not permitted while an existing "
                "portfolio limit is breached."
            )
    else:
        # The final target position must satisfy both position and gross limits.
        gross_capacity = max(ZERO, gross_limit - other_gross_exposure)
        target_capacity = min(position_limit, gross_capacity)

        if requested_quantity != ZERO:
            liquidation_price = (
                quote.bid if requested_quantity > ZERO else quote.ask
            )
            maximum_target_quantity = _floor_quantity(
                target_capacity / (liquidation_price * multiplier),
                instrument.quantity_precision,
            )
            if abs(target_quantity) > maximum_target_quantity:
                target_quantity = (
                    maximum_target_quantity
                    if requested_quantity > ZERO
                    else -maximum_target_quantity
                )
                reasons.append(
                    "Target position reduced to comply with position and "
                    "gross portfolio exposure limits."
                )

        delta = target_quantity - current_quantity
        if delta != ZERO:
            order_side = OrderSide.BUY if delta > ZERO else OrderSide.SELL
            order_price = quote.ask if order_side == OrderSide.BUY else quote.bid
            maximum_order_quantity = _floor_quantity(
                order_limit / (order_price * multiplier),
                instrument.quantity_precision,
            )

            if abs(delta) > maximum_order_quantity:
                allowed_delta = (
                    maximum_order_quantity
                    if delta > ZERO
                    else -maximum_order_quantity
                )
                target_quantity = current_quantity + allowed_delta
                reasons.append(
                    "Order quantity reduced to comply with the individual "
                    "order exposure limit."
                )

    delta = target_quantity - current_quantity
    if delta > ZERO:
        order_side = OrderSide.BUY
    elif delta < ZERO:
        order_side = OrderSide.SELL
    else:
        order_side = None

    order_quantity = abs(delta)

    if target_quantity > ZERO:
        target_liquidation_price = quote.bid
    elif target_quantity < ZERO:
        target_liquidation_price = quote.ask
    else:
        target_liquidation_price = ZERO

    approved_exposure = (
        target_quantity
        * target_liquidation_price
        * multiplier
        / equity
    )

    outcome = (
        RiskOutcome.REDUCED
        if (
            target_quantity != requested_quantity
            or (current_breached and target_quantity != current_quantity)
        )
        else RiskOutcome.APPROVED
    )

    if outcome == RiskOutcome.APPROVED and not reasons:
        reasons.append("Requested exposure satisfies all configured risk limits.")

    return RiskDecision(
        outcome=outcome,
        requested_exposure=requested_exposure,
        approved_exposure=approved_exposure,
        equity=equity,
        target_quantity=target_quantity,
        order_side=order_side,
        order_quantity=order_quantity,
        reasons=tuple(reasons),
    )


def _floor_quantity(quantity: Decimal, precision: int) -> Decimal:
    """Round a nonnegative quantity down to instrument precision."""

    quantum = Decimal("1").scaleb(-precision)
    return quantity.quantize(quantum, rounding=ROUND_DOWN)
