"""Application orchestration for market data ingestion."""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from atlas_quant.services.market_data.application.dataset_publication import (
    DatasetPublicationRejectedError,
)
from atlas_quant.services.market_data.application.provider_adapters import (
    ProviderAdapter,
    ProviderDataRequest,
    ProviderDownload,
)
from atlas_quant.services.market_data.application.repository_errors import (
    IngestionJobRepositoryExecutionOwnershipError,
)
from atlas_quant.services.market_data.domain.dataset_lineage import (
    DatasetLineageRelationship,
    DatasetLineageSourceType,
    DatasetVersionLineage,
)
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)
from atlas_quant.services.market_data.domain.ingestion_attempt import (
    IngestionAttempt,
)


@dataclass(frozen=True, slots=True)
class PreparedIngestion:
    """Validated ingestion request prepared for execution."""

    job: IngestionJob
    request: ProviderDataRequest
    adapter: ProviderAdapter


class IngestionOrchestrator:
    """Coordinate provider downloads, publication, and ingestion ownership."""

    def __init__(
        self,
        provider_registry,
        instrument_registry,
        mapping_repository,
        adapter_registry,
        job_repository,
        dataset_repository=None,
        raw_publication_service=None,
        dataset_publication_service=None,
        lineage_repository=None,
        *,
        lease_duration: timedelta = timedelta(seconds=60),
        heartbeat_interval: float = 15.0,
        worker_id: str | None = None,
    ):
        self._provider_registry = provider_registry
        self._instrument_registry = instrument_registry
        self._mapping_repository = mapping_repository
        self._adapter_registry = adapter_registry
        self._job_repository = job_repository
        self._dataset_repository = dataset_repository
        self._raw_publication_service = raw_publication_service
        self._dataset_publication_service = dataset_publication_service
        self._lineage_repository = lineage_repository

        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive.")

        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive.")

        if heartbeat_interval >= lease_duration.total_seconds():
            raise ValueError(
                "heartbeat_interval must be shorter than lease_duration."
            )

        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._worker_id = worker_id or f"market-data-{uuid4()}"

    async def prepare(
        self,
        *,
        provider_id,
        instrument_id,
        mapping_id,
        data_kind,
        timeframe,
        requested_start,
        requested_end,
    ) -> PreparedIngestion:
        """Validate an ingestion request and persist its running job."""

        provider = await self._resolve(
            self._provider_registry,
            ("get_provider", "get_by_id"),
            provider_id,
        )
        if provider is None:
            raise ValueError(f"Provider '{provider_id}' was not found.")

        instrument = await self._resolve(
            self._instrument_registry,
            ("get_instrument", "get_by_id"),
            instrument_id,
        )
        if instrument is None:
            raise ValueError(f"Instrument '{instrument_id}' was not found.")

        mapping = await self._resolve(
            self._mapping_repository,
            ("get_by_id",),
            mapping_id,
        )
        if mapping is None:
            raise ValueError(f"Provider mapping '{mapping_id}' was not found.")

        if not mapping.is_active:
            raise ValueError("The provider instrument mapping is inactive.")

        if mapping.provider_id != provider.provider_id:
            raise ValueError(
                "The provider mapping belongs to a different provider."
            )

        if mapping.instrument_id != instrument.instrument_id:
            raise ValueError(
                "The provider mapping belongs to a different instrument."
            )

        request = ProviderDataRequest(
            provider=provider,
            instrument=instrument,
            mapping=mapping,
            data_kind=data_kind,
            start=requested_start,
            end=requested_end,
            timeframe=timeframe,
        )

        adapter = self._adapter_registry.get_adapter(provider.provider_id)

        now = datetime.now(timezone.utc)

        job = IngestionJob(
            provider_id=provider.provider_id,
            asset_class=instrument.asset_class,
            requested_start=request.start,
            requested_end=request.end,
            instrument_id=instrument.instrument_id,
            instrument_symbol=instrument.symbol,
            timeframe=timeframe,
            data_kind=data_kind,
            provider_mapping_id=mapping.mapping_id,
            status=IngestionJobStatus.RUNNING,
            created_at=now,
            started_at=now,
        )

        await self._job_repository.add(job)

        return PreparedIngestion(
            job=job,
            request=request,
            adapter=adapter,
        )

    async def execute(self, prepared: PreparedIngestion) -> IngestionJob:
        """Execute a prepared job under an authoritative execution attempt."""

        if not isinstance(prepared, PreparedIngestion):
            raise TypeError("prepared must be a PreparedIngestion value.")

        job = await self._job_repository.get_by_id(prepared.job.job_id)
        if job is None:
            raise ValueError("The persisted ingestion job was not found.")

        if job.status != IngestionJobStatus.RUNNING:
            raise ValueError(
                "Only a running ingestion job can be executed."
            )

        self._validate_prepared_request(job, prepared.request)

        # Resolve the adapter again from the persisted provider identity.
        # A caller-supplied adapter is not trusted as execution authority.
        adapter = self._adapter_registry.get_adapter(job.provider_id)

        attempt = None
        claim_attempt = getattr(self._job_repository, "claim_attempt", None)

        if callable(claim_attempt):
            attempt = await claim_attempt(
                job.job_id,
                self._worker_id,
                lease_duration=self._lease_duration,
            )
            if attempt is None:
                raise ValueError(
                    "The ingestion job is already claimed by another worker."
                )
        else:
            # Temporary compatibility for older test doubles and legacy
            # repository implementations. Production repositories must use
            # claim_attempt and update_owned.
            claimed = await self._job_repository.claim_execution(job.job_id)
            if not claimed:
                raise ValueError(
                    "The ingestion job is already claimed by another worker."
                )

        state = {
            "job": job,
            "records_downloaded": job.records_downloaded,
            "records_written": job.records_written,
            "records_rejected": job.records_rejected,
            "version_ids": list(job.produced_dataset_version_ids),
        }

        heartbeat_error = []
        heartbeat_task = None
        owner_task = asyncio.current_task()

        if attempt is not None:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(
                    job=job,
                    attempt=attempt,
                    heartbeat_error=heartbeat_error,
                    owner_task=owner_task,
                )
            )

        try:
            self._ensure_execution_dependencies()

            result = await self._execute_claimed(
                job=job,
                request=prepared.request,
                adapter=adapter,
                attempt=attempt,
                state=state,
            )

            if heartbeat_error:
                raise heartbeat_error[0]

            await self._persist_job(result, attempt)
            return result

        except asyncio.CancelledError:
            if heartbeat_error:
                raise heartbeat_error[0]

            cancelled = replace(
                state["job"],
                status=IngestionJobStatus.CANCELLED,
                records_downloaded=state["records_downloaded"],
                records_written=state["records_written"],
                records_rejected=state["records_rejected"],
                produced_dataset_version_ids=tuple(state["version_ids"]),
                completed_at=datetime.now(timezone.utc),
                failure_code=None,
                failure_message=None,
            )

            await self._persist_job(cancelled, attempt)
            raise

        except IngestionJobRepositoryExecutionOwnershipError:
            # A stale worker must never attempt to finalize a job it no
            # longer owns.
            raise

        except Exception as exc:
            if isinstance(exc, DatasetPublicationRejectedError):
                state["records_rejected"] += exc.rejected_record_count

            if heartbeat_error:
                raise heartbeat_error[0]

            has_output = bool(state["version_ids"])

            failed = replace(
                state["job"],
                status=(
                    IngestionJobStatus.PARTIALLY_SUCCEEDED
                    if has_output
                    else IngestionJobStatus.FAILED
                ),
                records_downloaded=state["records_downloaded"],
                records_written=state["records_written"],
                records_rejected=state["records_rejected"],
                produced_dataset_version_ids=tuple(state["version_ids"]),
                completed_at=datetime.now(timezone.utc),
                failure_code="execution_failed",
                failure_message=str(exc) or exc.__class__.__name__,
            )

            await self._persist_job(failed, attempt)
            return failed

        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_loop(
        self,
        *,
        job,
        attempt: IngestionAttempt,
        heartbeat_error: list,
        owner_task,
    ) -> None:
        """Renew the attempt lease while execution is in progress."""

        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)

                await self._job_repository.renew_attempt_lease(
                    job.job_id,
                    attempt.attempt_id,
                    attempt.fencing_token,
                    self._worker_id,
                    lease_duration=self._lease_duration,
                )

        except asyncio.CancelledError:
            raise

        except BaseException as exc:
            heartbeat_error.append(exc)
            if owner_task is not None and not owner_task.done():
                owner_task.cancel()

    async def _execute_claimed(
        self,
        *,
        job,
        request,
        adapter,
        attempt,
        state,
    ) -> IngestionJob:
        """Download, preserve, normalize, publish, and record lineage."""

        with (await adapter.download(request)) as download:
            if not isinstance(download, ProviderDownload):
                raise TypeError(
                    "Provider adapters must return a ProviderDownload."
                )

            raw_dataset = self._build_raw_dataset(request)

            raw_result = await self._publish_raw(
                raw_dataset,
                download,
                attempt,
            )

            raw_version = self._extract_dataset_version(raw_result)
            self._append_output_version(state, raw_version)

            await self._add_lineage(
                DatasetVersionLineage(
                    target_dataset_version_id=raw_version.dataset_version_id,
                    source_type=DatasetLineageSourceType.INGESTION_JOB,
                    source_id=job.job_id,
                    relationship=DatasetLineageRelationship.INGESTED_FROM,
                )
            )

            await self._persist_progress(state, attempt)

            # The raw publisher consumes the stream. Rewind it before
            # asking the provider adapter to normalize the same payload.
            download.payload.seek(0)

            normalized_dataset = self._build_normalized_dataset(request)
            normalized_dataset, _ = await self._dataset_repository.get_or_create(
                normalized_dataset
            )

            def counted_observations():
                for observation in adapter.normalize(download, request):
                    state["records_downloaded"] += 1
                    yield observation

            if request.data_kind == DatasetKind.OHLCV:
                publication_result = await self._publish_normalized(
                    "publish_ohlcv",
                    normalized_dataset.dataset_id,
                    counted_observations(),
                    attempt,
                )
            else:
                publication_result = await self._publish_normalized(
                    "publish_quotes",
                    normalized_dataset.dataset_id,
                    counted_observations(),
                    attempt,
                )

            normalized_version = self._extract_dataset_version(
                publication_result
            )
            self._append_output_version(state, normalized_version)

            written_count = normalized_version.record_count
            if written_count is None:
                written_count = state["records_downloaded"]

            state["records_written"] += written_count

            await self._add_lineage(
                DatasetVersionLineage(
                    target_dataset_version_id=(
                        normalized_version.dataset_version_id
                    ),
                    source_type=DatasetLineageSourceType.DATASET_VERSION,
                    source_id=raw_version.dataset_version_id,
                    relationship=DatasetLineageRelationship.DERIVED_FROM,
                )
            )

            await self._persist_progress(state, attempt)

        return replace(
            state["job"],
            status=IngestionJobStatus.SUCCEEDED,
            records_downloaded=state["records_downloaded"],
            records_written=state["records_written"],
            records_rejected=state["records_rejected"],
            produced_dataset_version_ids=tuple(state["version_ids"]),
            completed_at=datetime.now(timezone.utc),
            failure_code=None,
            failure_message=None,
        )

    async def _publish_raw(self, dataset, download, attempt):
        publisher = self._raw_publication_service.publish

        if attempt is None:
            return await publisher(
                dataset,
                download.payload,
                storage_format=download.storage_format,
            )

        return await publisher(
            dataset,
            download.payload,
            storage_format=download.storage_format,
            attempt=attempt,
        )

    async def _publish_normalized(
        self,
        method_name,
        dataset_id,
        observations,
        attempt,
    ):
        publisher = getattr(self._dataset_publication_service, method_name)

        if attempt is None:
            return await publisher(dataset_id, observations)

        return await publisher(
            dataset_id,
            observations,
            attempt=attempt,
        )

    async def _add_lineage(self, lineage):
        await self._lineage_repository.add(lineage)

    async def _persist_progress(self, state, attempt):
        updated = replace(
            state["job"],
            records_downloaded=state["records_downloaded"],
            records_written=state["records_written"],
            records_rejected=state["records_rejected"],
            produced_dataset_version_ids=tuple(state["version_ids"]),
        )

        await self._persist_job(updated, attempt)
        state["job"] = updated

    async def _persist_job(self, job, attempt):
        if attempt is None:
            await self._job_repository.update(job)
            return

        await self._job_repository.update_owned(
            job,
            attempt.attempt_id,
            attempt.fencing_token,
            self._worker_id,
        )

    def _ensure_execution_dependencies(self):
        missing = []

        if self._dataset_repository is None:
            missing.append("dataset repository")

        if self._raw_publication_service is None:
            missing.append("raw publication service")

        if self._dataset_publication_service is None:
            missing.append("normalized publication service")

        if self._lineage_repository is None:
            missing.append("lineage repository")

        if missing:
            raise ValueError(
                "Ingestion execution is not configured: "
                + ", ".join(missing)
            )

    @staticmethod
    def _extract_dataset_version(result):
        version = getattr(result, "dataset_version", result)

        if not hasattr(version, "dataset_version_id"):
            raise ValueError(
                "Publication did not return a dataset version identity."
            )

        return version

    @staticmethod
    def _append_output_version(state, version):
        version_id = version.dataset_version_id

        if version_id not in state["version_ids"]:
            state["version_ids"].append(version_id)

    @staticmethod
    def _build_raw_dataset(request):
        instrument = request.instrument
        provider = request.provider

        return Dataset(
            name=f"{provider.provider_id}:{instrument.symbol}:raw",
            data_kind=DatasetKind.RAW_PROVIDER,
            asset_class=instrument.asset_class,
            instrument_id=instrument.instrument_id,
            instrument_symbol=instrument.symbol,
            provider_id=provider.provider_id,
        )

    @staticmethod
    def _build_normalized_dataset(request):
        instrument = request.instrument

        if request.data_kind == DatasetKind.OHLCV:
            suffix = request.timeframe.value
        else:
            suffix = "quotes"

        return Dataset(
            name=f"{instrument.symbol}:{request.data_kind.value}:{suffix}",
            data_kind=request.data_kind,
            asset_class=instrument.asset_class,
            instrument_id=instrument.instrument_id,
            instrument_symbol=instrument.symbol,
            timeframe=request.timeframe,
        )

    @staticmethod
    async def _resolve(repository, method_names, identifier):
        for method_name in method_names:
            method = getattr(repository, method_name, None)
            if callable(method):
                return await method(identifier)

        raise AttributeError(
            f"Repository does not implement any of: {method_names}"
        )

    @staticmethod
    def _validate_prepared_request(job, request):
        if (
            request.provider.provider_id != job.provider_id
            or request.instrument.instrument_id != job.instrument_id
            or request.mapping.mapping_id != job.provider_mapping_id
            or request.data_kind != job.data_kind
            or request.timeframe != job.timeframe
            or request.start != job.requested_start
            or request.end != job.requested_end
        ):
            raise ValueError(
                "Prepared request does not match the persisted ingestion job."
            )