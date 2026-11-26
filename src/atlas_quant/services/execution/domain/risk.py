"""Domain models for independent portfolio risk decisions."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from atlas_quant.services.execution.domain.enums import OrderSide


ZERO = Decimal("0")


class RiskOutcome(StrEnum):
    APPROVED = "approved"
    REDUCED = "reduced"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Exposure limits expressed as fractions of portfolio equity."""

    max_position_exposure: Decimal
    max_gross_portfolio_exposure: Decimal
    max_order_exposure: Decimal

    def __post_init__(self) -> None:
        for name in (
            "max_position_exposure",
            "max_gross_portfolio_exposure",
            "max_order_exposure",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must use Decimal.")
            if not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be finite and positive.")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Auditable result of evaluating one proposed instrument exposure."""

    outcome: RiskOutcome
    requested_exposure: Decimal
    approved_exposure: Decimal | None
    equity: Decimal | None
    target_quantity: Decimal
    order_side: OrderSide | None
    order_quantity: Decimal
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RiskOutcome):
            raise TypeError("outcome must be a RiskOutcome.")

        if not isinstance(self.requested_exposure, Decimal):
            raise TypeError("requested_exposure must use Decimal.")

        if self.approved_exposure is not None and not isinstance(
            self.approved_exposure, Decimal
        ):
            raise TypeError("approved_exposure must use Decimal or None.")

        if self.equity is not None and not isinstance(self.equity, Decimal):
            raise TypeError("equity must use Decimal or None.")

        for name in ("target_quantity", "order_quantity"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must use Decimal.")
            if not value.is_finite() or value < ZERO:
                if name == "target_quantity" and value.is_finite():
                    continue
                raise ValueError(f"{name} must be finite and nonnegative.")

        if self.approved_exposure is not None and not self.approved_exposure.is_finite():
            raise ValueError("approved_exposure must be finite.")

        if self.equity is not None and not self.equity.is_finite():
            raise ValueError("equity must be finite.")

        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) or not reason.strip()
            for reason in self.reasons
        ):
            raise TypeError("reasons must be a tuple of non-empty strings.")
