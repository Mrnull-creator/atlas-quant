"""Tests for market data ingestion execution."""

import asyncio
import inspect
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from dataclasses import replace

import pytest

from atlas_quant.services.market_data.application.ingestion_orchestrator import (
    IngestionOrchestrator,
)
from atlas_quant.services.market_data.application.dataset_publication import (
    DatasetPublicationRejectedError,
)
from atlas_quant.services.market_data.application.provider_adapters import (
    ProviderDownload,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
)
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
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
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


class FakeJobRepository:
    def __init__(self):
        self.jobs = {}
        self.updates = []
        self.claimed = set()
    async def add(self, job):
        self.jobs[job.job_id] = job

    async def get_by_id(self, job_id):
        return self.jobs.get(job_id)

    async def claim_execution(self, job_id):
        job = self.jobs.get(job_id)

        if (
            job is None
            or job.status != IngestionJobStatus.RUNNING
            or job_id in self.claimed
        ):
            return False

        self.claimed.add(job_id)
        return True

    async def update(self, job):
        assert job.job_id in self.jobs
        self.jobs[job.job_id] = job
        self.updates.append(job)

class FakeAdapterRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get_adapter(self, provider_id):
        assert provider_id == "dukascopy"
        return self.adapter


class FakeAdapter:
    def __init__(
        self,
        *,
        observations=None,
        download_error=None,
        normalize_error=None,
    ):
        self.observations = (
            tuple(observations)
            if observations is not None
            else (object(), object())
        )
        self.download_error = download_error
        self.normalize_error = normalize_error
        self.download_result = None
        self.normalized_payload = None

    async def download(self, request):
        if self.download_error is not None:
            raise self.download_error

        self.download_result = ProviderDownload(
            payload=BytesIO(b"provider-native-payload"),
            content_type="application/octet-stream",
            provider_schema_version="1",
            storage_format="bi5",
        )
        return self.download_result

    def normalize(self, download, request):
        if self.normalize_error is not None:
            raise self.normalize_error

        self.normalized_payload = download.payload.read()
        return iter(self.observations)


class FakeDatasetRepository:
    def __init__(self):
        self.datasets = []

    async def get_or_create(self, dataset):
        self.datasets.append(dataset)
        return dataset, True


class FakeRawPublicationService:
    def __init__(self, *, error=None):
        self.error = error
        self.datasets = []
        self.payloads = []
        self.result = SimpleNamespace(
            dataset_version=SimpleNamespace(
                dataset_version_id=uuid4(),
                record_count=None,
            )
        )

    async def publish(self, dataset, source, *, storage_format):
        if self.error is not None:
            raise self.error

        self.datasets.append(dataset)
        self.payloads.append(source.read())

        assert storage_format == "bi5"

        return self.result


class FakeDatasetPublicationService:
    def __init__(self, *, error=None):
        self.error = error
        self.ohlcv_calls = []
        self.quote_calls = []
        self.received_streams = []
        self.result = SimpleNamespace(
            dataset_version=SimpleNamespace(
                dataset_version_id=uuid4(),
                record_count=2,
            ),
            quality_report=None,
        )

    async def publish_ohlcv(self, dataset_id, observations):
        self.received_streams.append(observations)
        materialized = tuple(observations)

        if self.error is not None:
            raise self.error

        self.ohlcv_calls.append((dataset_id, materialized))
        return self.result

    async def publish_quotes(self, dataset_id, observations):
        self.received_streams.append(observations)
        materialized = tuple(observations)

        if self.error is not None:
            raise self.error

        self.quote_calls.append((dataset_id, materialized))
        return self.result


class FakeLineageRepository:
    def __init__(self, *, fail_on_call=None):
        self.fail_on_call = fail_on_call
        self.lineages = []

    async def add(self, lineage):
        call_number = len(self.lineages) + 1

        if self.fail_on_call == call_number:
            raise RuntimeError("Simulated lineage persistence failure.")

        self.lineages.append(lineage)


def make_execution_orchestrator(
    *,
    data_kind=DatasetKind.OHLCV,
    mapping_active=True,
    adapter=None,
    raw_publication=None,
    dataset_publication=None,
    dataset_repository=None,
    lineage_repository=None,
):
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

    adapter = adapter or FakeAdapter()
    jobs = FakeJobRepository()
    raw_publication = (
        raw_publication or FakeRawPublicationService()
    )
    dataset_publication = (
        dataset_publication or FakeDatasetPublicationService()
    )
    dataset_repository = (
        dataset_repository or FakeDatasetRepository()
    )
    lineage_repository = (
        lineage_repository or FakeLineageRepository()
    )

    orchestrator = IngestionOrchestrator(
        provider_registry=FakeProviderRegistry(provider),
        instrument_registry=FakeInstrumentRegistry(instrument),
        mapping_repository=FakeMappingRepository(mapping),
        adapter_registry=FakeAdapterRegistry(adapter),
        job_repository=jobs,
        dataset_repository=dataset_repository,
        raw_publication_service=raw_publication,
        dataset_publication_service=dataset_publication,
        lineage_repository=lineage_repository,
    )

    timeframe = (
        Timeframe.M1
        if data_kind == DatasetKind.OHLCV
        else None
    )

    prepared = run(
        orchestrator.prepare(
            provider_id="dukascopy",
            instrument_id=instrument.instrument_id,
            mapping_id=mapping.mapping_id,
            data_kind=data_kind,
            timeframe=timeframe,
            requested_start=datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
            requested_end=datetime(
                2026, 1, 2, tzinfo=timezone.utc
            ),
        )
    )

    return SimpleNamespace(
        orchestrator=orchestrator,
        instrument=instrument,
        provider=provider,
        mapping=mapping,
        jobs=jobs,
        adapter=adapter,
        raw_publication=raw_publication,
        dataset_publication=dataset_publication,
        dataset_repository=dataset_repository,
        lineage_repository=lineage_repository,
        prepared=prepared,
    )


@pytest.mark.parametrize(
    ("data_kind", "expected_publication_method"),
    [
        (DatasetKind.OHLCV, "ohlcv"),
        (DatasetKind.BID_ASK, "quotes"),
    ],
)
def test_execute_successfully_publishes_raw_and_normalized_data(
    data_kind,
    expected_publication_method,
):
    context = make_execution_orchestrator(data_kind=data_kind)

    result = run(
        context.orchestrator.execute(context.prepared)
    )

    assert result.status == IngestionJobStatus.SUCCEEDED
    assert result.records_downloaded == 2
    assert result.records_written == 2
    assert result.records_rejected == 0
    assert len(result.produced_dataset_version_ids) == 2

    assert context.raw_publication.payloads == [
        b"provider-native-payload"
    ]
    assert context.adapter.normalized_payload == (
        b"provider-native-payload"
    )
    assert context.adapter.download_result.payload.closed

    assert len(context.dataset_publication.received_streams) == 1
    assert inspect.isgenerator(
        context.dataset_publication.received_streams[0]
    )

    assert len(context.dataset_repository.datasets) == 1

    if expected_publication_method == "ohlcv":
        assert len(context.dataset_publication.ohlcv_calls) == 1
        assert context.dataset_publication.quote_calls == []
    else:
        assert len(context.dataset_publication.quote_calls) == 1
        assert context.dataset_publication.ohlcv_calls == []

    assert len(context.lineage_repository.lineages) == 2

    raw_lineage, normalized_lineage = (
        context.lineage_repository.lineages
    )

    assert (
        raw_lineage.source_type
        == DatasetLineageSourceType.INGESTION_JOB
    )
    assert (
        raw_lineage.relationship
        == DatasetLineageRelationship.INGESTED_FROM
    )
    assert raw_lineage.source_id == result.job_id
    assert (
        raw_lineage.target_dataset_version_id
        == result.produced_dataset_version_ids[0]
    )

    assert (
        normalized_lineage.source_type
        == DatasetLineageSourceType.DATASET_VERSION
    )
    assert (
        normalized_lineage.relationship
        == DatasetLineageRelationship.DERIVED_FROM
    )
    assert (
        normalized_lineage.source_id
        == result.produced_dataset_version_ids[0]
    )
    assert (
        normalized_lineage.target_dataset_version_id
        == result.produced_dataset_version_ids[1]
    )

    assert context.jobs.jobs[result.job_id] == result


def test_execute_marks_job_failed_when_raw_publication_fails():
    context = make_execution_orchestrator(
        raw_publication=FakeRawPublicationService(
            error=RuntimeError("Raw storage unavailable.")
        )
    )

    result = run(
        context.orchestrator.execute(context.prepared)
    )

    assert result.status == IngestionJobStatus.FAILED
    assert result.produced_dataset_version_ids == ()
    assert "Raw storage unavailable." in result.failure_message
    assert result.completed_at is not None



def test_execute_counts_observations_emitted_before_normalization_fails():
    context = make_execution_orchestrator()
    first_observation = object()

    def normalize_one_then_fail(download, request):
        context.adapter.normalized_payload = download.payload.read()

        def observations():
            yield first_observation
            raise ValueError("Normalization failed after one observation.")

        return observations()

    context.adapter.normalize = normalize_one_then_fail

    result = run(context.orchestrator.execute(context.prepared))

    assert result.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert result.records_downloaded == 1
    assert result.records_written == 0
    assert len(result.produced_dataset_version_ids) == 1
    assert "after one observation" in result.failure_message

def test_execute_preserves_raw_output_when_normalization_fails():
    context = make_execution_orchestrator(
        adapter=FakeAdapter(
            normalize_error=ValueError("Invalid provider payload.")
        )
    )

    result = run(
        context.orchestrator.execute(context.prepared)
    )

    assert result.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert len(result.produced_dataset_version_ids) == 1
    assert result.records_downloaded == 0
    assert result.records_written == 0
    assert "Invalid provider payload." in result.failure_message

    assert len(context.raw_publication.payloads) == 1
    assert context.dataset_publication.ohlcv_calls == []


def test_execute_marks_raw_output_partial_when_normalized_publication_fails():
    context = make_execution_orchestrator(
        dataset_publication=FakeDatasetPublicationService(
            error=RuntimeError("Normalized publication failed.")
        )
    )

    result = run(
        context.orchestrator.execute(context.prepared)
    )

    assert result.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert len(result.produced_dataset_version_ids) == 1
    assert result.records_downloaded == 2
    assert result.records_written == 0
    assert result.records_rejected == 0
    assert "Normalized publication failed." in result.failure_message


def test_execute_preserves_outputs_when_lineage_persistence_fails():
    context = make_execution_orchestrator(
        lineage_repository=FakeLineageRepository(fail_on_call=1)
    )

    result = run(
        context.orchestrator.execute(context.prepared)
    )

    assert result.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert len(result.produced_dataset_version_ids) == 1
    assert "lineage persistence failure" in result.failure_message.lower()


def test_execute_marks_job_failed_when_dependencies_are_missing():
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
    )
    jobs = FakeJobRepository()

    orchestrator = IngestionOrchestrator(
        provider_registry=FakeProviderRegistry(provider),
        instrument_registry=FakeInstrumentRegistry(instrument),
        mapping_repository=FakeMappingRepository(mapping),
        adapter_registry=FakeAdapterRegistry(FakeAdapter()),
        job_repository=jobs,
    )

    prepared = run(
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

    result = run(orchestrator.execute(prepared))

    assert result.status == IngestionJobStatus.FAILED
    assert "execution is not configured" in result.failure_message.lower()
    assert result.completed_at is not None


def test_execute_marks_job_cancelled_when_download_is_cancelled():
    context = make_execution_orchestrator(
        adapter=FakeAdapter(
            download_error=asyncio.CancelledError()
        )
    )

    with pytest.raises(asyncio.CancelledError):
        run(context.orchestrator.execute(context.prepared))

    stored_job = context.jobs.jobs[context.prepared.job.job_id]

    assert stored_job.status == IngestionJobStatus.CANCELLED
    assert stored_job.completed_at is not None
    assert stored_job.produced_dataset_version_ids == ()

def test_execute_preserves_both_outputs_when_second_lineage_write_fails():
    context = make_execution_orchestrator(
        lineage_repository=FakeLineageRepository(fail_on_call=2)
    )

    result = run(context.orchestrator.execute(context.prepared))

    assert result.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert len(result.produced_dataset_version_ids) == 2
    assert result.records_downloaded == 2
    assert result.records_written == 2
    assert result.records_rejected == 0
    assert "lineage persistence failure" in result.failure_message.lower()


def test_execute_preserves_raw_output_when_normalized_publication_is_cancelled():
    context = make_execution_orchestrator(
        dataset_publication=FakeDatasetPublicationService(
            error=asyncio.CancelledError()
        )
    )

    with pytest.raises(asyncio.CancelledError):
        run(context.orchestrator.execute(context.prepared))

    stored_job = context.jobs.jobs[context.prepared.job.job_id]

    assert stored_job.status == IngestionJobStatus.CANCELLED
    assert len(stored_job.produced_dataset_version_ids) == 1
    assert stored_job.records_downloaded == 2
    assert stored_job.records_written == 0
    assert stored_job.records_rejected == 0
    assert stored_job.completed_at is not None


def test_execute_preserves_both_outputs_when_second_lineage_write_is_cancelled():
    class CancellingLineageRepository:
        def __init__(self):
            self.calls = 0

        async def add(self, lineage):
            self.calls += 1
            if self.calls == 2:
                raise asyncio.CancelledError()

    context = make_execution_orchestrator(
        lineage_repository=CancellingLineageRepository()
    )

    with pytest.raises(asyncio.CancelledError):
        run(context.orchestrator.execute(context.prepared))

    stored_job = context.jobs.jobs[context.prepared.job.job_id]

    assert stored_job.status == IngestionJobStatus.CANCELLED
    assert len(stored_job.produced_dataset_version_ids) == 2
    assert stored_job.records_downloaded == 2
    assert stored_job.records_written == 2


def test_execute_rejects_replay_of_a_completed_job():
    context = make_execution_orchestrator()

    first_result = run(
        context.orchestrator.execute(context.prepared)
    )

    assert first_result.status == IngestionJobStatus.SUCCEEDED

    with pytest.raises(
        ValueError,
        match="Only a running ingestion job can be executed",
    ):
        run(context.orchestrator.execute(context.prepared))

    assert len(context.raw_publication.payloads) == 1
    assert len(context.dataset_publication.ohlcv_calls) == 1

def test_execute_rejects_job_claimed_by_another_worker():
    context = make_execution_orchestrator()
    job_id = context.prepared.job.job_id

    # Simulate another worker having already acquired the claim.
    context.jobs.claimed.add(job_id)

    with pytest.raises(ValueError, match="already claimed"):
        run(context.orchestrator.execute(context.prepared))

    assert context.jobs.jobs[job_id].status == IngestionJobStatus.RUNNING
    assert context.raw_publication.payloads == []
    assert context.adapter.download_result is None
def test_execute_rejects_request_that_does_not_match_persisted_job():
    context = make_execution_orchestrator()
    job_id = context.prepared.job.job_id

    tampered_request = replace(
        context.prepared.request,
        end=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    tampered_prepared = replace(
        context.prepared,
        request=tampered_request,
    )

    with pytest.raises(
        ValueError,
        match="does not match the persisted ingestion job",
    ):
        run(context.orchestrator.execute(tampered_prepared))

    assert context.jobs.jobs[job_id].status == IngestionJobStatus.RUNNING
    assert job_id not in context.jobs.claimed
    assert context.raw_publication.payloads == []
    assert context.adapter.download_result is None


def test_execute_resolves_adapter_from_persisted_provider_identity():
    context = make_execution_orchestrator()

    class UntrustedAdapter:
        def __init__(self):
            self.download_called = False

        async def download(self, request):
            self.download_called = True
            raise AssertionError(
                "The caller-supplied adapter must not be used."
            )

        def normalize(self, download, request):
            raise AssertionError(
                "The caller-supplied adapter must not be used."
            )

    untrusted_adapter = UntrustedAdapter()

    tampered_prepared = replace(
        context.prepared,
        adapter=untrusted_adapter,
    )

    result = run(
        context.orchestrator.execute(tampered_prepared)
    )

    assert result.status == IngestionJobStatus.SUCCEEDED
    assert untrusted_adapter.download_called is False
    assert context.adapter.download_result is not None

def test_execute_persists_rejected_record_count_from_publication_failure():
    rejection = DatasetPublicationRejectedError(
        DataQualityReport(
            checked_records=2,
            findings=(),
        ),
        rejected_record_count=1,
    )

    context = make_execution_orchestrator(
        dataset_publication=FakeDatasetPublicationService(
            error=rejection
        )
    )

    result = run(context.orchestrator.execute(context.prepared))

    assert result.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert result.records_downloaded == 2
    assert result.records_written == 0
    assert result.records_rejected == 1
    assert len(result.produced_dataset_version_ids) == 1
    assert context.jobs.jobs[result.job_id] == result
