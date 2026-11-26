from datetime import datetime, timedelta, timezone

import pytest

from atlas_quant.services.market_data.application.incremental_validation import (
    IncrementalTimestampIndex,
    TimestampCheck,
)


BASE_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_first_timestamp_is_not_duplicate_or_out_of_order():
    with IncrementalTimestampIndex() as index:
        result = index.observe("EURUSD:M1", BASE_TIMESTAMP)

    assert result == TimestampCheck(
        is_duplicate=False,
        is_out_of_order=False,
    )


def test_duplicate_timestamp_is_detected():
    with IncrementalTimestampIndex() as index:
        index.observe("EURUSD:M1", BASE_TIMESTAMP)

        result = index.observe("EURUSD:M1", BASE_TIMESTAMP)

    assert result.is_duplicate
    assert not result.is_out_of_order


def test_out_of_order_timestamp_is_detected():
    with IncrementalTimestampIndex() as index:
        index.observe(
            "EURUSD:M1",
            BASE_TIMESTAMP + timedelta(minutes=2),
        )

        result = index.observe(
            "EURUSD:M1",
            BASE_TIMESTAMP + timedelta(minutes=1),
        )

    assert not result.is_duplicate
    assert result.is_out_of_order


def test_duplicate_detection_works_across_large_input_sequences():
    with IncrementalTimestampIndex() as index:
        for minute in range(20_000):
            result = index.observe(
                "EURUSD:M1",
                BASE_TIMESTAMP + timedelta(minutes=minute),
            )

            assert not result.is_duplicate
            assert not result.is_out_of_order

        duplicate = index.observe(
            "EURUSD:M1",
            BASE_TIMESTAMP + timedelta(minutes=12_345),
        )

    assert duplicate.is_duplicate
    assert not duplicate.is_out_of_order


def test_series_are_tracked_independently():
    with IncrementalTimestampIndex() as index:
        index.observe(
            "EURUSD:M1",
            BASE_TIMESTAMP + timedelta(minutes=2),
        )

        result = index.observe(
            "GBPUSD:M1",
            BASE_TIMESTAMP,
        )

    assert not result.is_duplicate
    assert not result.is_out_of_order


def test_same_symbol_with_different_timeframes_is_independent():
    with IncrementalTimestampIndex() as index:
        index.observe(
            "EURUSD:M1",
            BASE_TIMESTAMP + timedelta(minutes=2),
        )

        result = index.observe(
            "EURUSD:M5",
            BASE_TIMESTAMP,
        )

    assert not result.is_duplicate
    assert not result.is_out_of_order


def test_duplicate_does_not_advance_latest_timestamp():
    with IncrementalTimestampIndex() as index:
        later = BASE_TIMESTAMP + timedelta(minutes=2)

        index.observe("EURUSD:M1", later)
        index.observe("EURUSD:M1", later)

        result = index.observe(
            "EURUSD:M1",
            BASE_TIMESTAMP + timedelta(minutes=1),
        )

    assert result.is_out_of_order
    assert not result.is_duplicate


def test_timezone_equivalent_timestamps_are_duplicates():
    equivalent_timestamp = BASE_TIMESTAMP.astimezone(
    timezone(timedelta(hours=3))
)

    with IncrementalTimestampIndex() as index:
        index.observe("EURUSD:M1", BASE_TIMESTAMP)

        result = index.observe(
            "EURUSD:M1",
            equivalent_timestamp,
        )

    assert result.is_duplicate


def test_timestamp_precision_is_preserved():
    first = BASE_TIMESTAMP + timedelta(microseconds=1)
    second = BASE_TIMESTAMP + timedelta(microseconds=2)

    with IncrementalTimestampIndex() as index:
        index.observe("EURUSD:M1", first)
        result = index.observe("EURUSD:M1", second)

    assert not result.is_duplicate
    assert not result.is_out_of_order


def test_index_can_be_used_from_another_thread():
    from concurrent.futures import ThreadPoolExecutor

    index = IncrementalTimestampIndex()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                index.observe,
                "EURUSD:M1",
                BASE_TIMESTAMP,
            ).result()
    finally:
        index.close()

    assert not result.is_duplicate
    assert not result.is_out_of_order


def test_temporary_database_is_removed_when_closed():
    index = IncrementalTimestampIndex()
    database_path = index.database_path
    directory = database_path.parent

    assert database_path.exists()

    index.close()

    assert not database_path.exists()
    assert not directory.exists()


def test_close_is_idempotent():
    index = IncrementalTimestampIndex()

    index.close()
    index.close()


def test_observing_after_close_is_rejected():
    index = IncrementalTimestampIndex()
    index.close()

    with pytest.raises(RuntimeError, match="closed"):
        index.observe("EURUSD:M1", BASE_TIMESTAMP)


def test_rejects_empty_series_key():
    with IncrementalTimestampIndex() as index:
        with pytest.raises(ValueError, match="series_key"):
            index.observe("", BASE_TIMESTAMP)


def test_rejects_naive_timestamp():
    with IncrementalTimestampIndex() as index:
        with pytest.raises(ValueError, match="timezone-aware"):
            index.observe(
                "EURUSD:M1",
                datetime(2026, 1, 1),
            )


def test_rejects_invalid_timestamp_type():
    with IncrementalTimestampIndex() as index:
        with pytest.raises(TypeError, match="datetime"):
            index.observe("EURUSD:M1", "2026-01-01")

@pytest.mark.parametrize(
    ("module_name", "validator_name"),
    [
        ("ohlcv_validator", "OHLCVBatchValidator"),
        ("quote_validator", "QuoteBatchValidator"),
    ],
)
def test_validation_sessions_close_timestamp_index_when_accumulator_initialization_fails(
    monkeypatch,
    module_name,
    validator_name,
):
    from atlas_quant.services.market_data.application import (
        ohlcv_validator,
        quote_validator,
    )

    modules = {
        "ohlcv_validator": ohlcv_validator,
        "quote_validator": quote_validator,
    }
    module = modules[module_name]
    created_indexes = []

    class TrackingTimestampIndex:
        def __init__(self):
            self.closed = False
            created_indexes.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        module,
        "IncrementalTimestampIndex",
        TrackingTimestampIndex,
    )

    validator_type = getattr(module, validator_name)
    validator = validator_type(max_findings=0)

    with pytest.raises(ValueError):
        validator.start_session()

    assert len(created_indexes) == 1
    assert created_indexes[0].closed
