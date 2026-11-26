"""Tests for the market data ingestion orchestrator."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.ingestion_orchestrator import (
    IngestionOrchestrator,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJobStatus,
)
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)


def run(coroutine):
    """Run an asynchronous test operation."""
    return asyncio.run(coroutine)


class FakeProviderRegistry:
    def __init__(self, provider):
        self.provider = provider

    async def get_provider(self, provider_id):
        assert provider_id == self.provider.provider_id
        return self.provider


class FakeInstrumentRegistry:
    def __init__(self, instrument):
        self.instrument = instrument

    async def get_instrument(self, instrument_id):
        assert instrument_id == self.instrument.instrument_id
        return self.instrument


class FakeMappingRepository:
    def __init__(self, mapping):
        self.mapping = mapping

    async def get_by_id(self, mapping_id):
        if mapping_id != self.mapping.mapping_id:
            return None
        return self.mapping


class FakeAdapter:
    async def download(self, request):
        raise NotImplementedError

    def normalize(self, download, request):
        raise NotImplementedError


class FakeAdapterRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get_adapter(self, provider_id):
        assert provider_id == "dukascopy"
        return self.adapter


class FakeJobRepository:
    def __init__(self):
        self.jobs = {}

    async def add(self, job):
        self.jobs[job.job_id] = job

    async def update(self, job):
        assert job.job_id in self.jobs
        self.jobs[job.job_id] = job


def make_orchestrator(*, mapping_active=True):
    instrument = Instrument(
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
    )

    provider = MarketDataProvider(
        provider_id="dukascopy",
        name="Dukascopy",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(Timeframe.M1,),
        supports_historical_data=True,
        supports_quote_data=True,
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id="dukascopy",
        provider_symbol="EURUSD",
        is_active=mapping_active,
    )

    jobs = FakeJobRepository()
    adapter = FakeAdapter()

    orchestrator = IngestionOrchestrator(
        provider_registry=FakeProviderRegistry(provider),
        instrument_registry=FakeInstrumentRegistry(instrument),
        mapping_repository=FakeMappingRepository(mapping),
        adapter_registry=FakeAdapterRegistry(adapter),
        job_repository=jobs,
    )

    return orchestrator, instrument, mapping, jobs, adapter


def test_prepare_creates_and_starts_ingestion_job():
    orchestrator, instrument, mapping, jobs, adapter = (
        make_orchestrator()
    )

    result = run(
        orchestrator.prepare(
            provider_id="dukascopy",
            instrument_id=instrument.instrument_id,
            mapping_id=mapping.mapping_id,
            data_kind=DatasetKind.OHLCV,
            timeframe=Timeframe.M1,
            requested_start=datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
            requested_end=datetime(
                2026, 1, 2, tzinfo=timezone.utc
            ),
        )
    )

    assert result.job.status == IngestionJobStatus.RUNNING
    assert result.job.instrument_id == instrument.instrument_id
    assert result.job.provider_mapping_id == mapping.mapping_id
    assert result.job.data_kind == DatasetKind.OHLCV
    assert result.job.timeframe == Timeframe.M1

    assert result.request.instrument == instrument
    assert result.request.mapping == mapping
    assert result.adapter is adapter

    assert jobs.jobs[result.job.job_id] == result.job


def test_prepare_rejects_inactive_mapping_without_creating_job():
    orchestrator, instrument, mapping, jobs, _ = (
        make_orchestrator(mapping_active=False)
    )

    with pytest.raises(ValueError, match="inactive"):
        run(
            orchestrator.prepare(
                provider_id="dukascopy",
                instrument_id=instrument.instrument_id,
                mapping_id=mapping.mapping_id,
                data_kind=DatasetKind.OHLCV,
                timeframe=Timeframe.M1,
                requested_start=datetime(
                    2026, 1, 1, tzinfo=timezone.utc
                ),
                requested_end=datetime(
                    2026, 1, 2, tzinfo=timezone.utc
                ),
            )
        )

    assert jobs.jobs == {}


def test_prepare_rejects_missing_mapping_without_creating_job():
    orchestrator, instrument, _, jobs, _ = make_orchestrator()

    with pytest.raises(ValueError, match="was not found"):
        run(
            orchestrator.prepare(
                provider_id="dukascopy",
                instrument_id=instrument.instrument_id,
                mapping_id=uuid4(),
                data_kind=DatasetKind.OHLCV,
                timeframe=Timeframe.M1,
                requested_start=datetime(
                    2026, 1, 1, tzinfo=timezone.utc
                ),
                requested_end=datetime(
                    2026, 1, 2, tzinfo=timezone.utc
                ),
            )
        )

    assert jobs.jobs == {}