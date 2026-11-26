"""Durable execution-attempt domain models."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4, uuid5

from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


class ExecutionAttemptStatus(StrEnum):
    PREPARED = "prepared"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionAttemptOutcome(StrEnum):
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    NO_LIQUIDITY = "no_liquidity"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExecutionAttemptInputs:
    """Immutable inputs required to reproduce one simulated execution."""

    instrument: Instrument
    quote: BidAskQuote
    executed_at: datetime

    commission_per_1000_units: Decimal
    slippage_ticks: int
    max_quote_age: timedelta | None
    missing_liquidity_policy: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, Instrument):
            raise TypeError("instrument must be an Instrument.")

        if not isinstance(self.quote, BidAskQuote):
            raise TypeError("quote must be a BidAskQuote.")

        if not isinstance(self.executed_at, datetime):
            raise TypeError("executed_at must be a datetime.")

        if (
            self.executed_at.tzinfo is None
            or self.executed_at.utcoffset() is None
        ):
            raise ValueError("executed_at must be timezone-aware.")

        object.__setattr__(
            self,
            "executed_at",
            self.executed_at.astimezone(timezone.utc),
        )

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
                raise TypeError(
                    "max_quote_age must be a timedelta or None."
                )

            if self.max_quote_age <= timedelta(0):
                raise ValueError("max_quote_age must be positive.")

        policy = str(self.missing_liquidity_policy)

        if policy not in {"reject", "assume_full_fill"}:
            raise ValueError("Invalid missing_liquidity_policy.")

        object.__setattr__(
            self,
            "missing_liquidity_policy",
            policy,
        )


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """One durable attempt to execute a paper-trading order."""

    attempt_id: UUID
    account_id: UUID
    order_id: UUID
    attempt_number: int
    fill_id: UUID

    inputs: ExecutionAttemptInputs

    status: ExecutionAttemptStatus = ExecutionAttemptStatus.PREPARED

    outcome: ExecutionAttemptOutcome | None = None
    reason: str | None = None

    reference_price: Decimal | None = None
    execution_price: Decimal | None = None
    slippage_cost: Decimal = Decimal("0")
    liquidity_assumed: bool = False

    created_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "account_id",
            "order_id",
            "fill_id",
        ):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID.")

        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
        ):
            raise TypeError("attempt_number must be an integer.")

        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive.")

        if not isinstance(self.inputs, ExecutionAttemptInputs):
            raise TypeError("inputs must be ExecutionAttemptInputs.")

        if not isinstance(self.status, ExecutionAttemptStatus):
            object.__setattr__(
                self,
                "status",
                ExecutionAttemptStatus(self.status),
            )

        if self.outcome is not None and not isinstance(
            self.outcome,
            ExecutionAttemptOutcome,
        ):
            object.__setattr__(
                self,
                "outcome",
                ExecutionAttemptOutcome(self.outcome),
            )

        if self.reason is not None:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("reason must be a nonempty string or None.")

        for name in ("reference_price", "execution_price"):
            value = getattr(self, name)

            if value is not None:
                if not isinstance(value, Decimal):
                    raise TypeError(f"{name} must be a Decimal or None.")

                if not value.is_finite() or value <= Decimal("0"):
                    raise ValueError(
                        f"{name} must be finite and positive."
                    )

        if not isinstance(self.slippage_cost, Decimal):
            raise TypeError("slippage_cost must be a Decimal.")

        if (
            not self.slippage_cost.is_finite()
            or self.slippage_cost < Decimal("0")
        ):
            raise ValueError(
                "slippage_cost must be finite and nonnegative."
            )

        if not isinstance(self.liquidity_assumed, bool):
            raise TypeError("liquidity_assumed must be a boolean.")

        created_at = self.created_at or datetime.now(timezone.utc)
        object.__setattr__(
            self,
            "created_at",
            self._normalize_time(created_at, "created_at"),
        )

        if self.completed_at is not None:
            completed_at = self._normalize_time(
                self.completed_at,
                "completed_at",
            )
            object.__setattr__(self, "completed_at", completed_at)

            if completed_at < self.created_at:
                raise ValueError(
                    "completed_at cannot precede created_at."
                )

        if self.status is ExecutionAttemptStatus.PREPARED:
            if self.outcome is not None or self.completed_at is not None:
                raise ValueError(
                    "A prepared attempt cannot have a result."
                )

        elif self.status is ExecutionAttemptStatus.COMPLETED:
            if self.outcome is None or self.completed_at is None:
                raise ValueError(
                    "A completed attempt requires an outcome and "
                    "completed_at."
                )

        elif self.status is ExecutionAttemptStatus.FAILED:
            if self.outcome is not None or self.completed_at is None:
                raise ValueError(
                    "A failed attempt requires completed_at and "
                    "cannot have an execution outcome."
                )

            if self.reason is None:
                raise ValueError(
                    "A failed attempt requires a failure reason."
                )

    @classmethod
    def prepare(
        cls,
        *,
        account_id: UUID,
        order_id: UUID,
        attempt_number: int,
        inputs: ExecutionAttemptInputs,
        attempt_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> "ExecutionAttempt":
        """Create an attempt with a deterministic fill identity."""

        identifier = attempt_id or uuid4()

        return cls(
            attempt_id=identifier,
            account_id=account_id,
            order_id=order_id,
            attempt_number=attempt_number,
            fill_id=uuid5(identifier, "atlas-quant-paper-fill"),
            inputs=inputs,
            created_at=created_at,
        )

    def complete(
        self,
        *,
        outcome: ExecutionAttemptOutcome,
        completed_at: datetime,
        reason: str | None = None,
        reference_price: Decimal | None = None,
        execution_price: Decimal | None = None,
        slippage_cost: Decimal = Decimal("0"),
        liquidity_assumed: bool = False,
    ) -> "ExecutionAttempt":
        """Return a completed copy of this attempt."""

        if self.status is not ExecutionAttemptStatus.PREPARED:
            raise ValueError("Only a prepared attempt can be completed.")

        return replace(
            self,
            status=ExecutionAttemptStatus.COMPLETED,
            outcome=outcome,
            reason=reason,
            reference_price=reference_price,
            execution_price=execution_price,
            slippage_cost=slippage_cost,
            liquidity_assumed=liquidity_assumed,
            completed_at=completed_at,
        )

    def fail(
        self,
        *,
        reason: str,
        completed_at: datetime,
    ) -> "ExecutionAttempt":
        """Return a failed copy of this attempt."""

        if self.status is not ExecutionAttemptStatus.PREPARED:
            raise ValueError("Only a prepared attempt can fail.")

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A failure reason is required.")

        return replace(
            self,
            status=ExecutionAttemptStatus.FAILED,
            reason=reason.strip(),
            completed_at=completed_at,
        )

    @staticmethod
    def _normalize_time(
        value: datetime,
        field_name: str,
    ) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime.")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware.")

        return value.astimezone(timezone.utc)