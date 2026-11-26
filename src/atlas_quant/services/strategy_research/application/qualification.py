"""Deterministic, advisory qualification of evaluated strategy candidates."""

from dataclasses import dataclass
import math
import re
from typing import Sequence
from uuid import UUID

import pandas as pd

from atlas_quant.services.strategy_research.application.evaluation import (
    EvaluationConfig,
    EvaluationResult,
    InSampleOutOfSampleResult,
    WalkForwardFold,
)
from atlas_quant.services.strategy_research.domain.performance import (
    PerformanceMetrics,
)
from atlas_quant.services.strategy_research.domain.qualification import (
    CriterionStatus,
    QualificationCriterion,
    QualificationDecision,
    QualificationPolicy,
)


@dataclass(frozen=True, slots=True)
class CandidateQualification:
    """Auditable qualification outcome; this does not promote a strategy."""

    strategy_id: UUID
    strategy_version_id: UUID
    version_number: int
    implementation_key: str
    implementation_version: str
    parameters_json: str
    instrument_id: UUID

    decision: QualificationDecision
    policy: QualificationPolicy
    criteria: tuple[QualificationCriterion, ...]

    in_sample_data_fingerprint: str
    out_of_sample_data_fingerprint: str
    walk_forward_data_fingerprints: tuple[str, ...]

    evaluation_config: EvaluationConfig
    out_of_sample_metrics: PerformanceMetrics


_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value: object, name: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise ValueError(f"{name} must be a valid timezone-aware timestamp.")
    return result.tz_convert("UTC")


def _validate_fingerprint(value: str, name: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest.")


def _identity(result: EvaluationResult) -> tuple[object, ...]:
    return (
        result.strategy_id,
        result.strategy_version_id,
        result.version_number,
        result.implementation_key,
        result.implementation_version,
        result.parameters_json,
        result.instrument_id,
        result.config,
    )


def _criterion(
    name: str,
    observed: int | float | None,
    required: int | float | None,
    *,
    direction: str,
    evidence_available: bool = True,
    below_is_insufficient: bool = False,
) -> QualificationCriterion:
    if required is None:
        return QualificationCriterion(
            name=name,
            status=CriterionStatus.NOT_APPLICABLE,
            observed_value=observed,
            required_value=None,
            explanation="This criterion is disabled by the qualification policy.",
        )

    if not evidence_available or observed is None:
        return QualificationCriterion(
            name=name,
            status=CriterionStatus.INSUFFICIENT,
            observed_value=observed,
            required_value=required,
            explanation="Required evaluation evidence is unavailable.",
        )

    if not math.isfinite(float(observed)):
        return QualificationCriterion(
            name=name,
            status=CriterionStatus.INSUFFICIENT,
            observed_value=None,
            required_value=required,
            explanation="The observed metric is not finite.",
        )

    passed = (
        observed >= required
        if direction == "minimum"
        else observed <= required
    )
    if not passed and below_is_insufficient:
        return QualificationCriterion(
            name=name,
            status=CriterionStatus.INSUFFICIENT,
            observed_value=observed,
            required_value=required,
            explanation="The evaluation sample is too small to establish sufficient evidence.",
        )

    return QualificationCriterion(
        name=name,
        status=CriterionStatus.PASS if passed else CriterionStatus.FAIL,
        observed_value=observed,
        required_value=required,
        explanation=(
            f"Observed {observed} meets the required threshold {required}."
            if passed
            else f"Observed {observed} does not meet the required threshold {required}."
        ),
    )


def _validate_evaluation(
    result: EvaluationResult,
    expected_identity: tuple[object, ...],
    label: str,
) -> None:
    if _identity(result) != expected_identity:
        raise ValueError(
            f"{label} does not match the candidate strategy, instrument, "
            "implementation, parameters, and evaluation configuration."
        )

    _validate_fingerprint(result.data_fingerprint, f"{label} data fingerprint")

    start = _timestamp(result.requested_start, f"{label} requested_start")
    end = _timestamp(
        result.requested_end_exclusive,
        f"{label} requested_end_exclusive",
    )
    actual_start = _timestamp(result.actual_start, f"{label} actual_start")
    actual_end = _timestamp(result.actual_end, f"{label} actual_end")

    if start >= end or actual_start > actual_end:
        raise ValueError(f"{label} contains an invalid evaluation time range.")


def qualify_candidate(
    *,
    evaluation: InSampleOutOfSampleResult,
    walk_forward_folds: Sequence[WalkForwardFold],
    policy: QualificationPolicy,
) -> CandidateQualification:
    """Evaluate OOS and walk-forward evidence against an explicit policy.

    In-sample performance is retained for provenance but is never used as
    a qualification gate. A measured failure takes precedence over missing
    evidence when determining the overall decision.
    """

    if not isinstance(policy, QualificationPolicy):
        raise TypeError("policy must be a QualificationPolicy.")

    if not isinstance(evaluation, InSampleOutOfSampleResult):
        raise TypeError("evaluation must be an InSampleOutOfSampleResult.")

    folds = tuple(walk_forward_folds)
    if any(not isinstance(fold, WalkForwardFold) for fold in folds):
        raise TypeError("Every walk-forward item must be a WalkForwardFold.")

    in_sample = evaluation.in_sample
    oos = evaluation.out_of_sample
    expected_identity = _identity(oos)

    _validate_evaluation(in_sample, expected_identity, "In-sample evaluation")
    _validate_evaluation(oos, expected_identity, "Out-of-sample evaluation")

    split = _timestamp(evaluation.split_at, "split_at")
    if (
        _timestamp(in_sample.requested_end_exclusive, "in-sample end") != split
        or _timestamp(oos.requested_start, "out-of-sample start") != split
    ):
        raise ValueError(
            "In-sample end, out-of-sample start, and split_at must agree."
        )

    previous_test_end: pd.Timestamp | None = None
    previous_fold_number = 0

    for fold in folds:
        if (
            isinstance(fold.fold_number, bool)
            or not isinstance(fold.fold_number, int)
            or fold.fold_number <= previous_fold_number
        ):
            raise ValueError(
                "Walk-forward fold numbers must be positive and strictly increasing."
            )
        previous_fold_number = fold.fold_number

        training_start = _timestamp(
            fold.training_start, "walk-forward training_start"
        )
        training_end = _timestamp(
            fold.training_end_exclusive,
            "walk-forward training_end_exclusive",
        )
        test_start = _timestamp(fold.test_start, "walk-forward test_start")
        test_end = _timestamp(
            fold.test_end_exclusive, "walk-forward test_end_exclusive"
        )

        if previous_test_end is not None and test_start < previous_test_end:
            raise ValueError("Walk-forward test windows must not overlap.")
        if not training_start < training_end <= test_start < test_end:
            raise ValueError(
                f"Walk-forward fold {fold.fold_number} has invalid time boundaries."
            )
        previous_test_end = test_end

        _validate_evaluation(
            fold.evaluation,
            expected_identity,
            f"Walk-forward fold {fold.fold_number}",
        )
        if (
            _timestamp(
                fold.evaluation.requested_start, "fold evaluation start"
            ) != test_start
            or _timestamp(
                fold.evaluation.requested_end_exclusive,
                "fold evaluation end",
            ) != test_end
        ):
            raise ValueError(
                f"Walk-forward fold {fold.fold_number} boundaries do not "
                "match its evaluation."
            )

    metrics = oos.metrics
    criteria: list[QualificationCriterion] = []

    criteria.append(
        _criterion(
            "minimum_oos_observations",
            metrics.observations,
            policy.minimum_oos_observations,
            direction="minimum",
            below_is_insufficient=True,
        )
    )
    criteria.append(
        _criterion(
            "minimum_oos_trades",
            metrics.trade_count,
            policy.minimum_oos_trades,
            direction="minimum",
            below_is_insufficient=True,
        )
    )
    criteria.append(
        _criterion(
            "minimum_oos_sharpe_ratio",
            metrics.sharpe_ratio,
            policy.minimum_sharpe_ratio,
            direction="minimum",
        )
    )
    criteria.append(
        _criterion(
            "minimum_oos_profit_factor",
            metrics.profit_factor,
            policy.minimum_profit_factor,
            direction="minimum",
        )
    )
    criteria.append(
        _criterion(
            "maximum_oos_drawdown",
            metrics.max_drawdown,
            policy.maximum_drawdown,
            direction="maximum",
        )
    )

    fold_count = len(folds)
    criteria.append(
        _criterion(
            "minimum_walk_forward_folds",
            fold_count,
            policy.minimum_walk_forward_folds,
            direction="minimum",
            below_is_insufficient=True,
        )
    )

    profitable_fraction = (
        sum(
            1
            for fold in folds
            if fold.evaluation.metrics.net_profit_loss > 0
        )
        / fold_count
        if fold_count
        else None
    )
    criteria.append(
        _criterion(
            "minimum_profitable_fold_fraction",
            profitable_fraction,
            policy.minimum_profitable_fold_fraction,
            direction="minimum",
            evidence_available=fold_count >= policy.minimum_walk_forward_folds,
        )
    )

    statuses = {criterion.status for criterion in criteria}
    if CriterionStatus.FAIL in statuses:
        decision = QualificationDecision.NOT_QUALIFIED
    elif CriterionStatus.INSUFFICIENT in statuses:
        decision = QualificationDecision.INSUFFICIENT_EVIDENCE
    else:
        decision = QualificationDecision.QUALIFIED

    _validate_fingerprint(
        in_sample.data_fingerprint, "in-sample data fingerprint"
    )

    return CandidateQualification(
        strategy_id=oos.strategy_id,
        strategy_version_id=oos.strategy_version_id,
        version_number=oos.version_number,
        implementation_key=oos.implementation_key,
        implementation_version=oos.implementation_version,
        parameters_json=oos.parameters_json,
        instrument_id=oos.instrument_id,
        decision=decision,
        policy=policy,
        criteria=tuple(criteria),
        in_sample_data_fingerprint=in_sample.data_fingerprint,
        out_of_sample_data_fingerprint=oos.data_fingerprint,
        walk_forward_data_fingerprints=tuple(
            fold.evaluation.data_fingerprint for fold in folds
        ),
        evaluation_config=oos.config,
        out_of_sample_metrics=metrics,
    )
