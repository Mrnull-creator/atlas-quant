from collections.abc import Mapping, Sequence
from uuid import UUID

import pandas as pd
import pytest

from atlas_quant.services.strategy_research.application.registry import (
    InMemoryStrategyImplementationRegistry,
    StrategyImplementationAlreadyRegisteredError,
    StrategyImplementationNotFoundError,
)
from atlas_quant.services.strategy_research.domain.strategies import (
    StrategySignal,
    StrategyVersion,
)


class FakeStrategyImplementation:
    def __init__(
        self,
        key: str = "momentum",
        version: str = "1.0.0",
    ):
        self.implementation_key = key
        self.implementation_version = version

    def validate_parameters(
        self,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        return dict(parameters)

    def generate_signals(
        self,
        *,
        instrument_id: UUID,
        strategy_version: StrategyVersion,
        market_data: pd.DataFrame,
    ) -> Sequence[StrategySignal]:
        return []


def test_registry_resolves_exact_implementation():
    registry = InMemoryStrategyImplementationRegistry()
    implementation = FakeStrategyImplementation()

    registry.register(implementation)

    resolved = registry.resolve("momentum", "1.0.0")

    assert resolved is implementation


def test_registry_keeps_different_versions_separate():
    registry = InMemoryStrategyImplementationRegistry()
    version_1 = FakeStrategyImplementation("momentum", "1.0.0")
    version_2 = FakeStrategyImplementation("momentum", "2.0.0")

    registry.register(version_1)
    registry.register(version_2)

    assert registry.resolve("momentum", "1.0.0") is version_1
    assert registry.resolve("momentum", "2.0.0") is version_2


def test_registry_rejects_duplicate_key_and_version():
    registry = InMemoryStrategyImplementationRegistry()

    registry.register(FakeStrategyImplementation())

    with pytest.raises(StrategyImplementationAlreadyRegisteredError):
        registry.register(FakeStrategyImplementation())


def test_registry_does_not_fall_back_to_another_version():
    registry = InMemoryStrategyImplementationRegistry()
    registry.register(FakeStrategyImplementation("momentum", "2.0.0"))

    with pytest.raises(StrategyImplementationNotFoundError):
        registry.resolve("momentum", "1.0.0")


def test_registry_rejects_unknown_implementation():
    registry = InMemoryStrategyImplementationRegistry()

    with pytest.raises(StrategyImplementationNotFoundError):
        registry.resolve("breakout", "1.0.0")


@pytest.mark.parametrize(
    ("key", "version"),
    [
        ("", "1.0.0"),
        ("momentum", ""),
        (" ", "1.0.0"),
        ("momentum", " "),
    ],
)
def test_registry_rejects_empty_resolution_identity(key, version):
    registry = InMemoryStrategyImplementationRegistry()

    with pytest.raises(ValueError):
        registry.resolve(key, version)


@pytest.mark.parametrize(
    ("key", "version"),
    [
        ("", "1.0.0"),
        ("momentum", ""),
    ],
)
def test_registry_rejects_empty_registration_identity(key, version):
    registry = InMemoryStrategyImplementationRegistry()

    with pytest.raises(ValueError):
        registry.register(FakeStrategyImplementation(key, version))
