from dataclasses import replace
from datetime import timezone
from uuid import uuid4

import pandas as pd
import pytest

from atlas_quant.services.strategy_research.application.evaluation import (
    EvaluationConfig,
    evaluate_in_sample_out_of_sample,
    evaluate_walk_forward,
)
from atlas_quant.services.strategy_research.application.qualification import (
    qualify_candidate,
)
from atlas_quant.services.strategy_research.application.registry import (
    InMemoryStrategyImplementationRegistry,
)
from atlas_quant.services.strategy_research.domain.qualification import (
    CriterionStatus,
    QualificationDecision,
    QualificationPolicy,
)
from atlas_quant.services.strategy_research.domain.strategies import (
    Strategy,
    StrategyVersion,
)
from atlas_quant.services.strategy_research.infrastructure.momentum import (
    MomentumStrategyImplementation,
)


UTC = timezone.utc


def _market_data(periods=60):
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    index = pd.date_range(start, periods=periods, freq="min")
    prices = [
        (1.0, 1.1, 1.2, 1.1, 1.0, 0.9, 1.0, 1.1)[i % 8]
        for i in range(periods)
    ]
    midpoint = pd.Series(prices, index=index, dtype="float64")
    return pd.DataFrame(
        {
            "bid_open": midpoint - 0.0001,
            "ask_open": midpoint + 0.0001,
            "bid_close": midpoint - 0.0001,
            "ask_close": midpoint + 0.0001,
            "bar_close_time": index + pd.Timedelta(minutes=1),
        },
        index=index,
    )


def _evaluations():
    strategy = Strategy(name="Qualification test", strategy_key="qualification_test")
    version = StrategyVersion(
        strategy_id=strategy.strategy_id,
        version_number=1,
        implementation_key="momentum",
        implementation_version="1.0.0",
        parameters_json='{"fast_period": 2, "slow_period": 3}',
    )
    registry = InMemoryStrategyImplementationRegistry()
    registry.register(MomentumStrategyImplementation())

    data = _market_data()
    instrument_id = uuid4()
    config = EvaluationConfig()

    split = data.index[30]
    is_oos = evaluate_in_sample_out_of_sample(
        registry=registry,
        strategy_version=version,
        instrument_id=instrument_id,
        market_data=data,
        split_at=split,
        config=config,
    )
    folds = evaluate_walk_forward(
        registry=registry,
        strategy_version=version,
        instrument_id=instrument_id,
        market_data=data,
        training_bars=8,
        test_bars=4,
        step_bars=4,
        config=config,
    )
    return is_oos, folds


def _controlled_evidence(
    evaluation,
    folds,
    *,
    oos_changes=None,
    fold_profits=None,
    in_sample_changes=None,
):
    oos_changes = oos_changes or {}
    in_sample_changes = in_sample_changes or {}

    def changed_metrics(result, changes):
        return replace(result, metrics=replace(result.metrics, **changes))

    new_evaluation = replace(
        evaluation,
        in_sample=changed_metrics(evaluation.in_sample, in_sample_changes),
        out_of_sample=changed_metrics(
            evaluation.out_of_sample, oos_changes
        ),
    )

    new_folds = []
    for index, fold in enumerate(folds):
        changes = {}
        if fold_profits is not None:
            changes["net_profit_loss"] = fold_profits[index]
        new_folds.append(
            replace(
                fold,
                evaluation=changed_metrics(fold.evaluation, changes),
            )
        )

    return new_evaluation, tuple(new_folds)


def _policy(**changes):
    values = {
        "minimum_oos_observations": 5,
        "minimum_oos_trades": 1,
        "minimum_sharpe_ratio": 1.0,
        "minimum_profit_factor": 1.1,
        "maximum_drawdown": 0.20,
        "minimum_walk_forward_folds": 2,
        "minimum_profitable_fold_fraction": 0.5,
    }
    values.update(changes)
    return QualificationPolicy(**values)


def _good_metrics():
    return {
        "observations": 20,
        "trade_count": 8,
        "sharpe_ratio": 1.5,
        "profit_factor": 1.4,
        "max_drawdown": 0.10,
        "net_profit_loss": 100.0,
    }


def test_candidate_qualifies_when_all_oos_and_walk_forward_gates_pass():
    evaluation, folds = _evaluations()
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=_good_metrics(),
        fold_profits=[10.0] * len(folds),
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.QUALIFIED
    assert result.instrument_id == evaluation.out_of_sample.instrument_id
    assert result.strategy_version_id == evaluation.out_of_sample.strategy_version_id
    assert len(result.walk_forward_data_fingerprints) == len(folds)
    assert all(item.status == CriterionStatus.PASS for item in result.criteria)


def test_in_sample_performance_does_not_affect_qualification():
    evaluation, folds = _evaluations()
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=_good_metrics(),
        fold_profits=[10.0] * len(folds),
        in_sample_changes={
            "net_profit_loss": -100_000.0,
            "sharpe_ratio": -10.0,
            "max_drawdown": 0.95,
        },
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.QUALIFIED


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("sharpe_ratio", 0.2),
        ("profit_factor", 0.5),
        ("max_drawdown", 0.5),
    ],
)
def test_measured_oos_threshold_failure_rejects_candidate(metric, value):
    evaluation, folds = _evaluations()
    changes = _good_metrics()
    changes[metric] = value
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=changes,
        fold_profits=[10.0] * len(folds),
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.NOT_QUALIFIED
    assert any(
        item.status == CriterionStatus.FAIL
        for item in result.criteria
    )


@pytest.mark.parametrize("metric", ["sharpe_ratio", "profit_factor"])
def test_missing_required_oos_metric_is_insufficient_evidence(metric):
    evaluation, folds = _evaluations()
    changes = _good_metrics()
    changes[metric] = None
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=changes,
        fold_profits=[10.0] * len(folds),
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.INSUFFICIENT_EVIDENCE
    assert any(
        item.name == f"minimum_oos_{metric}"
        and item.status == CriterionStatus.INSUFFICIENT
        for item in result.criteria
    )


def test_disabled_optional_metrics_are_not_gates():
    evaluation, folds = _evaluations()
    changes = _good_metrics()
    changes["sharpe_ratio"] = None
    changes["profit_factor"] = None
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=changes,
        fold_profits=[10.0] * len(folds),
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(
            minimum_sharpe_ratio=None,
            minimum_profit_factor=None,
        ),
    )

    assert result.decision == QualificationDecision.QUALIFIED
    assert sum(
        item.status == CriterionStatus.NOT_APPLICABLE
        for item in result.criteria
    ) == 2


def test_too_few_oos_observations_or_trades_is_insufficient():
    evaluation, folds = _evaluations()
    changes = _good_metrics()
    changes["observations"] = 2
    changes["trade_count"] = 0
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=changes,
        fold_profits=[10.0] * len(folds),
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.INSUFFICIENT_EVIDENCE
    assert {
        item.name
        for item in result.criteria
        if item.status == CriterionStatus.INSUFFICIENT
    } >= {"minimum_oos_observations", "minimum_oos_trades"}


def test_too_few_walk_forward_folds_is_insufficient():
    evaluation, folds = _evaluations()
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds[:1],
        oos_changes=_good_metrics(),
        fold_profits=[10.0],
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.INSUFFICIENT_EVIDENCE


def test_unprofitable_walk_forward_fraction_fails_candidate():
    evaluation, folds = _evaluations()
    profits = [10.0 if index == 0 else -10.0 for index in range(len(folds))]
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=_good_metrics(),
        fold_profits=profits,
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(minimum_profitable_fold_fraction=0.75),
    )

    assert result.decision == QualificationDecision.NOT_QUALIFIED
    assert any(
        item.name == "minimum_profitable_fold_fraction"
        and item.status == CriterionStatus.FAIL
        for item in result.criteria
    )


def test_measured_failure_takes_precedence_over_insufficient_evidence():
    evaluation, folds = _evaluations()
    changes = _good_metrics()
    changes["observations"] = 1
    changes["sharpe_ratio"] = -1.0
    evaluation, folds = _controlled_evidence(
        evaluation,
        folds,
        oos_changes=changes,
        fold_profits=[10.0] * len(folds),
    )

    result = qualify_candidate(
        evaluation=evaluation,
        walk_forward_folds=folds,
        policy=_policy(),
    )

    assert result.decision == QualificationDecision.NOT_QUALIFIED


def test_instrument_mismatch_is_rejected():
    evaluation, folds = _evaluations()
    wrong_instrument = replace(
        evaluation.out_of_sample,
        instrument_id=uuid4(),
    )
    evaluation = replace(evaluation, out_of_sample=wrong_instrument)

    with pytest.raises(ValueError, match="does not match"):
        qualify_candidate(
            evaluation=evaluation,
            walk_forward_folds=folds,
            policy=_policy(),
        )


def test_walk_forward_strategy_version_mismatch_is_rejected():
    evaluation, folds = _evaluations()
    wrong_result = replace(
        folds[0].evaluation,
        strategy_version_id=uuid4(),
    )
    wrong_fold = replace(folds[0], evaluation=wrong_result)

    with pytest.raises(ValueError, match="does not match"):
        qualify_candidate(
            evaluation=evaluation,
            walk_forward_folds=(wrong_fold, *folds[1:]),
            policy=_policy(),
        )


def test_overlapping_walk_forward_test_windows_are_rejected():
    evaluation, folds = _evaluations()
    overlapping = replace(
        folds[1],
        test_start=folds[0].test_start,
    )

    with pytest.raises(ValueError, match="must not overlap"):
        qualify_candidate(
            evaluation=evaluation,
            walk_forward_folds=(folds[0], overlapping, *folds[2:]),
            policy=_policy(),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"minimum_oos_observations": 0},
        {"minimum_oos_trades": 0},
        {"minimum_walk_forward_folds": 0},
        {"minimum_profit_factor": -0.1},
        {"maximum_drawdown": 1.1},
        {"minimum_profitable_fold_fraction": -0.1},
        {"minimum_sharpe_ratio": float("nan")},
    ],
)
def test_invalid_policy_is_rejected(overrides):
    with pytest.raises(ValueError):
        _policy(**overrides)


def test_invalid_data_fingerprint_is_rejected():
    evaluation, folds = _evaluations()
    bad_oos = replace(
        evaluation.out_of_sample,
        data_fingerprint="not-a-sha256-digest",
    )
    evaluation = replace(evaluation, out_of_sample=bad_oos)

    with pytest.raises(ValueError, match="SHA-256"):
        qualify_candidate(
            evaluation=evaluation,
            walk_forward_folds=folds,
            policy=_policy(),
        )
