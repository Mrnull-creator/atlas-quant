"""Unit tests for ingestion execution attempt lifecycle rules."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttempt,
    IngestionAttemptStatus,
    TERMINAL_STATUSES,
)


def make_attempt(
    status: IngestionAttemptStatus,
    *,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> IngestionAttempt:
    """Build a valid attempt for lifecycle testing."""

    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    heartbeat_at = started_at + timedelta(seconds=10)
    finished_at = (
        started_at + timedelta(seconds=20)
        if status != IngestionAttemptStatus.RUNNING
        else None
    )

    return IngestionAttempt(
        job_id=uuid4(),
        worker_id="test-worker",
        attempt_number=1,
        fencing_token=1,
        started_at=started_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=started_at + timedelta(seconds=60),
        status=status,
        finished_at=finished_at,
        failure_code=failure_code,
        failure_message=failure_message,
    )


@pytest.mark.parametrize(
    ("status", "failure_message"),
    [
        (IngestionAttemptStatus.SUCCEEDED, None),
        (
            IngestionAttemptStatus.PARTIALLY_SUCCEEDED,
            "Some observations were rejected.",
        ),
        (
            IngestionAttemptStatus.FAILED,
            "Provider request failed.",
        ),
        (IngestionAttemptStatus.CANCELLED, None),
        (IngestionAttemptStatus.LEASE_EXPIRED, None),
        (IngestionAttemptStatus.SUPERSEDED, None),
    ],
)
def test_terminal_attempt_statuses(
    status: IngestionAttemptStatus,
    failure_message: str | None,
):
    attempt = make_attempt(
        status,
        failure_message=failure_message,
    )

    assert attempt.status == status
    assert attempt.is_terminal
    assert attempt.finished_at is not None
    assert status in TERMINAL_STATUSES


def test_running_attempt_is_not_terminal():
    attempt = make_attempt(IngestionAttemptStatus.RUNNING)

    assert not attempt.is_terminal
    assert attempt.finished_at is None


def test_partially_succeeded_attempt_requires_failure_message():
    with pytest.raises(
        ValueError,
        match="require failure_message",
    ):
        make_attempt(IngestionAttemptStatus.PARTIALLY_SUCCEEDED)


def test_failed_attempt_requires_failure_message():
    with pytest.raises(
        ValueError,
        match="require failure_message",
    ):
        make_attempt(IngestionAttemptStatus.FAILED)


def test_cancelled_attempt_rejects_failure_details():
    with pytest.raises(
        ValueError,
        match="Cancelled attempts cannot have failure details",
    ):
        make_attempt(
            IngestionAttemptStatus.CANCELLED,
            failure_message="Cancellation is not a failure.",
        )


def test_cancelled_attempt_rejects_failure_code():
    with pytest.raises(
        ValueError,
        match="Cancelled attempts cannot have failure details",
    ):
        make_attempt(
            IngestionAttemptStatus.CANCELLED,
            failure_code="CANCELLED",
        )


def test_successful_attempt_rejects_failure_details():
    with pytest.raises(
        ValueError,
        match="Successful attempts cannot have failure details",
    ):
        make_attempt(
            IngestionAttemptStatus.SUCCEEDED,
            failure_message="Unexpected failure details.",
        )