"""Core domain models for strategy identity, versioning and signals."""

import json
import math
import re

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class ExecutionTiming(StrEnum):
    """When a completed-candle strategy signal becomes executable."""

    NEXT_BAR_OPEN = "next_bar_open"


@dataclass(frozen=True, slots=True)
class Strategy:
    """Permanent identity of a research strategy.

    Strategy identity remains stable across all versions.
    """

    name: str
    strategy_key: str

    strategy_id: UUID = field(default_factory=uuid4)
    description: str | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, UUID):
            raise TypeError("strategy_id must be a UUID.")

        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Strategy name cannot be empty.")

        object.__setattr__(self, "name", self.name.strip())

        if not isinstance(self.strategy_key, str):
            raise TypeError("strategy_key must be a string.")

        key = self.strategy_key.strip().lower()

        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError("strategy_key has an invalid format.")

        object.__setattr__(self, "strategy_key", key)

        if self.description is not None:
            if not isinstance(self.description, str):
                raise TypeError("description must be a string or None.")

            description = self.description.strip()

            if not description:
                raise ValueError("description cannot be empty.")

            object.__setattr__(self, "description", description)

        _normalize_timestamp("created_at", self.created_at, self)


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    """Immutable, independently reproducible strategy definition."""

    strategy_id: UUID
    version_number: int

    implementation_key: str
    implementation_version: str

    parameters_json: str = "{}"
    execution_timing: ExecutionTiming = ExecutionTiming.NEXT_BAR_OPEN

    strategy_version_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, UUID):
            raise TypeError("strategy_id must be a UUID.")

        if not isinstance(self.strategy_version_id, UUID):
            raise TypeError("strategy_version_id must be a UUID.")

        if (
            isinstance(self.version_number, bool)
            or not isinstance(self.version_number, int)
            or self.version_number < 1
        ):
            raise ValueError("version_number must be a positive integer.")

        for name, value in (
            ("implementation_key", self.implementation_key),
            ("implementation_version", self.implementation_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be empty.")

        object.__setattr__(
            self, "implementation_key", self.implementation_key.strip()
        )
        object.__setattr__(
            self,
            "implementation_version",
            self.implementation_version.strip(),
        )

        if not isinstance(self.execution_timing, ExecutionTiming):
            raise TypeError("execution_timing must be an ExecutionTiming.")

        if not isinstance(self.parameters_json, str):
            raise TypeError("parameters_json must be a string.")

        try:
            parameters = json.loads(self.parameters_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("parameters_json must contain valid JSON.") from exc

        if not isinstance(parameters, dict):
            raise ValueError("Strategy parameters must be a JSON object.")

        _validate_json_values(parameters)

        canonical = json.dumps(
            parameters,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        object.__setattr__(self, "parameters_json", canonical)

        _normalize_timestamp("created_at", self.created_at, self)

    @property
    def parameters(self) -> dict:
        """Return a fresh copy of the immutable version configuration."""

        return json.loads(self.parameters_json)


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """A strategy's desired normalized exposure for one instrument.

    This is a research intent, not an executable broker order.

    target_exposure:
        -1 = fully short
         0 = flat
         1 = fully long

    Intermediate decimal values represent partial target exposure.
    """

    strategy_version_id: UUID
    instrument_id: UUID

    generated_at: datetime
    effective_at: datetime

    target_exposure: Decimal

    signal_id: UUID = field(default_factory=uuid4)
    rationale: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "strategy_version_id",
            "instrument_id",
            "signal_id",
        ):
            if not isinstance(getattr(self, name), UUID):
                raise TypeError(f"{name} must be a UUID.")

        _normalize_timestamp("generated_at", self.generated_at, self)
        _normalize_timestamp("effective_at", self.effective_at, self)

        if self.effective_at < self.generated_at:
            raise ValueError(
                "effective_at cannot precede generated_at."
            )

        if not isinstance(self.target_exposure, Decimal):
            raise TypeError("target_exposure must use Decimal.")

        if not self.target_exposure.is_finite():
            raise ValueError("target_exposure must be finite.")

        if not Decimal("-1") <= self.target_exposure <= Decimal("1"):
            raise ValueError(
                "target_exposure must be between -1 and 1."
            )

        if self.rationale is not None:
            if not isinstance(self.rationale, str):
                raise TypeError("rationale must be a string or None.")

            rationale = self.rationale.strip()

            if not rationale:
                raise ValueError("rationale cannot be empty.")

            object.__setattr__(self, "rationale", rationale)


def _normalize_timestamp(
    field_name: str,
    value: datetime,
    instance: object,
) -> None:
    """Require timezone-aware timestamps and normalize them to UTC."""

    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime.")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")

    object.__setattr__(
        instance,
        field_name,
        value.astimezone(timezone.utc),
    )


def _validate_json_values(value: object) -> None:
    """Reject non-JSON values and non-finite floating-point values."""

    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Strategy parameters cannot contain NaN or infinity.")
        return

    if isinstance(value, list):
        for item in value:
            _validate_json_values(item)
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Strategy parameter keys must be strings.")
            _validate_json_values(item)
        return

    raise ValueError(
        f"Unsupported strategy parameter type: {type(value).__name__}"
    )
