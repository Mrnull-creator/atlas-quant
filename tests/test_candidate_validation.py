
import pandas as pd

from atlas_quant.backtesting.validate_strategy_candidates import (
    DATA_END,
    HOLDOUT_START,
    TRAIN_START,
)


def test_research_and_holdout_periods_do_not_overlap():
    assert TRAIN_START == pd.Timestamp("2024-01-01", tz="UTC")
    assert HOLDOUT_START == pd.Timestamp("2026-01-01", tz="UTC")
    assert DATA_END == pd.Timestamp("2026-10-14", tz="UTC")
    assert TRAIN_START < HOLDOUT_START < DATA_END


def test_period_boundaries_assign_new_year_to_holdout():
    timestamps = pd.to_datetime(
        [
            "2025-12-31 23:59:00+00:00",
            "2026-01-01 00:00:00+00:00",
            "2026-10-13 23:59:00+00:00",
        ],
        utc=True,
    )

    research_mask = (timestamps >= TRAIN_START) & (
        timestamps < HOLDOUT_START
    )
    holdout_mask = (timestamps >= HOLDOUT_START) & (
        timestamps < DATA_END
    )

    assert research_mask.tolist() == [True, False, False]
    assert holdout_mask.tolist() == [False, True, True]
    assert not (research_mask & holdout_mask).any()