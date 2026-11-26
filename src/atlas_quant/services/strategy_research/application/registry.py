"""Strategy implementation contracts and in-memory registry."""

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

import pandas as pd

from atlas_quant.services.strategy_research.domain.strategies import (
    StrategySignal,
    StrategyVersion,
)


class StrategyImplementationError(Exception):
    """Base exception for strategy implementation registry errors."""


class StrategyImplementationAlreadyRegisteredError(
    StrategyImplementationError
):
    """Raised when an implementation key and version are already registered."""


class StrategyImplementationNotFoundError(StrategyImplementationError):
    """Raised when an exact implementation version cannot be resolved."""


class StrategyImplementation(Protocol):
    """Contract implemented by an executable strategy."""

    @property
    def implementation_key(self) -> str:
        """Stable implementation identifier."""
        ...

    @property
    def implementation_version(self) -> str:
        """Exact implementation version."""
        ...

    def validate_parameters(
        self,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Validate and normalize strategy-specific parameters."""
        ...

    def generate_signals(
        self,
        *,
        instrument_id: UUID,
        strategy_version: StrategyVersion,
        market_data: pd.DataFrame,
    ) -> Sequence[StrategySignal]:
        """Generate timestamped target-exposure signals.

        Market data must be chronological and must contain only data
        available at the time each signal is generated. Implementations
        must respect the execution timing recorded on the strategy version.
        """
        ...


class StrategyImplementationRegistry(Protocol):
    """Application contract for resolving exact strategy implementations."""

    def resolve(
        self,
        implementation_key: str,
        implementation_version: str,
    ) -> StrategyImplementation:
        """Resolve one exact implementation version."""
        ...


class InMemoryStrategyImplementationRegistry:
    """Simple registry for the initial research and backtesting workflow.

    Implementations are identified by the pair:
        (implementation_key, implementation_version)

    Registering a second implementation under the same pair is rejected.
    Resolution never falls back to another version.
    """

    def __init__(self) -> None:
        self._implementations: dict[
            tuple[str, str],
            StrategyImplementation,
        ] = {}

    def register(
        self,
        implementation: StrategyImplementation,
    ) -> None:
        key = implementation.implementation_key
        version = implementation.implementation_version

        if not isinstance(key, str) or not key.strip():
            raise ValueError("implementation_key cannot be empty.")

        if not isinstance(version, str) or not version.strip():
            raise ValueError("implementation_version cannot be empty.")

        identity = (key.strip(), version.strip())

        if identity in self._implementations:
            raise StrategyImplementationAlreadyRegisteredError(
                f"Strategy implementation {identity!r} is already registered."
            )

        self._implementations[identity] = implementation

    def resolve(
        self,
        implementation_key: str,
        implementation_version: str,
    ) -> StrategyImplementation:
        if not isinstance(implementation_key, str) or not implementation_key.strip():
            raise ValueError("implementation_key cannot be empty.")

        if (
            not isinstance(implementation_version, str)
            or not implementation_version.strip()
        ):
            raise ValueError("implementation_version cannot be empty.")

        identity = (
            implementation_key.strip(),
            implementation_version.strip(),
        )

        try:
            return self._implementations[identity]
        except KeyError as exc:
            raise StrategyImplementationNotFoundError(
                "No strategy implementation is registered for "
                f"{identity!r}."
            ) from exc
