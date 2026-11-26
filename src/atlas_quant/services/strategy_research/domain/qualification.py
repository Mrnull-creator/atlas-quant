"""Domain contracts for deterministic strategy-candidate qualification."""

from dataclasses import dataclass
from enum import StrEnum
import math
from numbers import Integral, Real


class QualificationDecision(StrEnum):
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CriterionStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    """Explicit thresholds; no universal performance thresholds are assumed."""

    minimum_oos_observations: int
    minimum_oos_trades: int
    minimum_sharpe_ratio: float | None
    minimum_profit_factor: float | None
    maximum_drawdown: float
    minimum_walk_forward_folds: int
    minimum_profitable_fold_fraction: float

    def __post_init__(self) -> None:
        integer_fields = (
            "minimum_oos_observations",
            "minimum_oos_trades",
            "minimum_walk_forward_folds",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, Integral)
                or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer.")

        optional_numeric_fields = (
            "minimum_sharpe_ratio",
            "minimum_profit_factor",
        )
        for name in optional_numeric_fields:
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} must be None or a finite number.")

        if (
            self.minimum_profit_factor is not None
            and self.minimum_profit_factor < 0
        ):
            raise ValueError("minimum_profit_factor cannot be negative.")

        if (
            isinstance(self.maximum_drawdown, bool)
            or not isinstance(self.maximum_drawdown, Real)
            or not math.isfinite(float(self.maximum_drawdown))
            or not 0 <= self.maximum_drawdown <= 1
        ):
            raise ValueError("maximum_drawdown must be between 0 and 1.")

        fraction = self.minimum_profitable_fold_fraction
        if (
            isinstance(fraction, bool)
            or not isinstance(fraction, Real)
            or not math.isfinite(float(fraction))
            or not 0 <= fraction <= 1
        ):
            raise ValueError(
                "minimum_profitable_fold_fraction must be between 0 and 1."
            )


@dataclass(frozen=True, slots=True)
class QualificationCriterion:
    name: str
    status: CriterionStatus
    observed_value: int | float | None
    required_value: int | float | None
    explanation: str
