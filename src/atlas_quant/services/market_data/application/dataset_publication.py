"""Application service for publishing immutable market data versions."""

from collections.abc import Iterable, Iterator
from datetime import datetime
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar
from uuid import UUID

from atlas_quant.services.market_data.application.dataset_lifecycle import (
    DatasetLifecycleRepository,
)
from atlas_quant.services.market_data.application.dataset_lifecycle_errors import (
    DatasetLifecycleDatasetNotFoundError,
)
from atlas_quant.services.market_data.application.repositories import (
    DatasetRepository,
)
from atlas_quant.services.market_data.application.storage import (
    OHLCVStorage,
    QuoteStorage,
    StorageObjectMetadata,
)
from atlas_quant.services.market_data.application.ohlcv_validator import (
    OHLCVBatchValidator,
)
from atlas_quant.services.market_data.application.quote_validator import (
    QuoteBatchValidator,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
    DatasetVersion,
)
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
    QualityFindingAccumulator,
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.domain.ingestion_attempt import IngestionAttempt


ObservationT = TypeVar("ObservationT")


class _ValidationSession(Protocol, Generic[ObservationT]):
    """Common interface for incremental validation sessions."""

    def add(self, observation: ObservationT) -> bool:
        ...

    def finish(self) -> DataQualityReport:
        ...

    def close(self) -> None:
        ...


class VerifiableOHLCVStorage(OHLCVStorage, Protocol):
    """OHLCV storage that supports independent object verification."""

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        ...


class VerifiableQuoteStorage(QuoteStorage, Protocol):
    """Quote storage that supports independent object verification."""

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        ...


class DatasetPublicationError(Exception):
    """Base exception for dataset publication failures."""


class DatasetPublicationDatasetKindError(DatasetPublicationError):
    """Raised when a dataset has the wrong kind for the requested data."""


class DatasetPublicationRejectedError(DatasetPublicationError):
    """Raised when pre-publication quality checks reject observations."""

    def __init__(
        self,
        report: DataQualityReport,
        *,
        rejected_record_count: int = 0,
    ) -> None:
        if (
            isinstance(rejected_record_count, bool)
            or not isinstance(rejected_record_count, int)
            or rejected_record_count < 0
        ):
            raise ValueError(
                "rejected_record_count must be a non-negative integer."
            )

        self.report = report
        self.rejected_record_count = rejected_record_count

        super().__init__(
            "Dataset publication rejected: "
            f"{report.error_count} quality error(s) found."
        )


class DatasetPublicationStorageError(DatasetPublicationError):
    """Raised when stored metadata does not describe the input dataset."""


@dataclass(frozen=True, slots=True)
class DatasetPublicationResult:
    """Result of successfully publishing a dataset version."""

    dataset_version: DatasetVersion
    quality_report: DataQualityReport


@dataclass(slots=True)
class _StreamStatistics:
    """Constant-memory statistics collected during streaming."""

    record_count: int = 0
    rejected_record_count: int = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    def observe(
        self,
        timestamp: datetime,
        *,
        rejected: bool = False,
    ) -> None:
        self.record_count += 1

        if rejected:
            self.rejected_record_count += 1

        if self.first_timestamp is None or timestamp < self.first_timestamp:
            self.first_timestamp = timestamp

        if self.last_timestamp is None or timestamp > self.last_timestamp:
            self.last_timestamp = timestamp


class DatasetPublicationService:
    """Coordinate incremental validation, storage, and publication."""

    def __init__(
        self,
        dataset_repository: DatasetRepository,
        lifecycle_repository: DatasetLifecycleRepository,
        ohlcv_storage: VerifiableOHLCVStorage,
        quote_storage: VerifiableQuoteStorage,
        *,
        schema_version: str = "1",
        storage_format: str = "parquet",
        max_findings: int = 1_000,
    ) -> None:
        if not isinstance(schema_version, str) or not schema_version.strip():
            raise ValueError("schema_version must be a non-empty string.")

        if not isinstance(storage_format, str) or not storage_format.strip():
            raise ValueError("storage_format must be a non-empty string.")

        self._datasets = dataset_repository
        self._lifecycle = lifecycle_repository
        self._ohlcv_storage = ohlcv_storage
        self._quote_storage = quote_storage
        self._schema_version = schema_version.strip()
        self._storage_format = storage_format.strip().lower()
        self._max_findings = max_findings

        self._ohlcv_validator = OHLCVBatchValidator(
            max_findings=max_findings
        )
        self._quote_validator = QuoteBatchValidator(
            max_findings=max_findings
        )

    async def publish_ohlcv(
        self,
        dataset_id: UUID,
        bars: Iterable[OHLCVBar],
        *,
        attempt: IngestionAttempt | None = None,
    ) -> DatasetPublicationResult:
        """Incrementally validate, store, verify, and publish OHLCV data."""

        dataset = await self._get_dataset(dataset_id)

        if dataset.data_kind != DatasetKind.OHLCV:
            raise DatasetPublicationDatasetKindError(
                "The requested dataset is not an OHLCV dataset."
            )

        first_observation, observations = self._prepare_nonempty_iterator(
            bars,
            "bars",
        )
        self._validate_first_observation_identity(
            dataset,
            first_observation,
        )

        version = await self._lifecycle.create_next_staging_version(
            dataset_id,
            schema_version=self._schema_version,
            storage_format=self._storage_format,
            **({"attempt": attempt} if attempt is not None else {}),
        )

        try:
            session = self._ohlcv_validator.start_session()
        except Exception as operation_error:
            await self._mark_staging_failed(
                version, operation_error, attempt=attempt
            )
            raise

        statistics = _StreamStatistics()
        identity_findings = QualityFindingAccumulator(
            max_findings=self._max_findings
        )

        streamed_observations = self._stream_ohlcv(
            observations,
            dataset,
            session,
            statistics,
            identity_findings,
        )

        return await self._write_verify_publish(
            version,
            streamed_observations,
            session,
            statistics,
            identity_findings,
            self._ohlcv_storage,
            "OHLCV",
            attempt=attempt,
        )

    async def publish_quotes(
        self,
        dataset_id: UUID,
        quotes: Iterable[BidAskQuote],
        *,
        attempt: IngestionAttempt | None = None,
    ) -> DatasetPublicationResult:
        """Incrementally validate, store, verify, and publish quotes."""

        dataset = await self._get_dataset(dataset_id)

        if dataset.data_kind != DatasetKind.BID_ASK:
            raise DatasetPublicationDatasetKindError(
                "The requested dataset is not a bid/ask dataset."
            )

        first_observation, observations = self._prepare_nonempty_iterator(
            quotes,
            "quotes",
        )
        self._validate_first_observation_identity(
            dataset,
            first_observation,
        )

        version = await self._lifecycle.create_next_staging_version(
            dataset_id,
            schema_version=self._schema_version,
            storage_format=self._storage_format,
            **({"attempt": attempt} if attempt is not None else {}),
        )

        try:
            session = self._quote_validator.start_session()
        except Exception as operation_error:
            await self._mark_staging_failed(
                version, operation_error, attempt=attempt
            )
            raise

        statistics = _StreamStatistics()
        identity_findings = QualityFindingAccumulator(
            max_findings=self._max_findings
        )

        streamed_observations = self._stream_quotes(
            observations,
            dataset,
            session,
            statistics,
            identity_findings,
        )

        return await self._write_verify_publish(
            version,
            streamed_observations,
            session,
            statistics,
            identity_findings,
            self._quote_storage,
            "bid/ask",
            attempt=attempt,
        )

    @staticmethod
    def _validate_first_observation_identity(
        dataset: Dataset,
        observation: OHLCVBar | BidAskQuote,
    ) -> None:
        """Reject first-record identity mismatches before staging."""

        findings = []

        if observation.symbol != dataset.instrument_symbol:
            findings.append(
                QualityFinding(
                    code="DATASET_SYMBOL_MISMATCH",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Observation symbol {observation.symbol} does not "
                        f"match dataset symbol {dataset.instrument_symbol}."
                    ),
                    record_index=0,
                    field="symbol",
                    timestamp=observation.timestamp,
                )
            )

        if (
            isinstance(observation, OHLCVBar)
            and observation.timeframe != dataset.timeframe
        ):
            findings.append(
                QualityFinding(
                    code="DATASET_TIMEFRAME_MISMATCH",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Observation timeframe {observation.timeframe.value} "
                        f"does not match dataset timeframe "
                        f"{dataset.timeframe.value}."
                    ),
                    record_index=0,
                    field="timeframe",
                    timestamp=observation.timestamp,
                )
            )

        if findings:
            raise DatasetPublicationRejectedError(
                DataQualityReport(
                    checked_records=1,
                    findings=tuple(findings),
                ),
                rejected_record_count=1,
            )

    async def _get_dataset(self, dataset_id: UUID) -> Dataset:
        if not isinstance(dataset_id, UUID):
            raise TypeError("dataset_id must be a UUID.")

        dataset = await self._datasets.get_by_id(dataset_id)

        if dataset is None:
            raise DatasetLifecycleDatasetNotFoundError(
                f"Dataset {dataset_id} does not exist."
            )

        return dataset

    @staticmethod
    def _prepare_nonempty_iterator(
        observations: Iterable[ObservationT],
        name: str,
    ) -> tuple[ObservationT, Iterator[ObservationT]]:
        """Peek at the first observation and preserve the complete stream."""

        if isinstance(observations, (str, bytes)):
            raise TypeError(
                f"{name} must be an iterable of observations."
            )

        try:
            iterator = iter(observations)
        except TypeError as exc:
            raise TypeError(
                f"{name} must be an iterable of observations."
            ) from exc

        try:
            first_observation = next(iterator)
        except StopIteration:
            raise DatasetPublicationRejectedError(
                DataQualityReport(
                    checked_records=0,
                    findings=(
                        QualityFinding(
                            code="EMPTY_DATASET",
                            severity=FindingSeverity.ERROR,
                            message=(
                                "A published dataset version must contain "
                                "records."
                            ),
                        ),
                    ),
                )
            ) from None

        def replay_first() -> Iterator[ObservationT]:
            yield first_observation
            yield from iterator

        return first_observation, replay_first()

    @staticmethod
    def _stream_ohlcv(
        observations: Iterable[OHLCVBar],
        dataset: Dataset,
        session: _ValidationSession[OHLCVBar],
        statistics: _StreamStatistics,
        identity_findings: QualityFindingAccumulator,
    ) -> Iterator[OHLCVBar]:
        """Validate and yield OHLCV observations one at a time."""

        for record_index, bar in enumerate(observations):
            has_error = session.add(bar)
            identity_error = False

            if bar.symbol != dataset.instrument_symbol:
                identity_error = True
                identity_findings.add(
                    QualityFinding(
                        code="DATASET_SYMBOL_MISMATCH",
                        severity=FindingSeverity.ERROR,
                        message=(
                            f"Observation symbol {bar.symbol} does not match "
                            f"dataset symbol {dataset.instrument_symbol}."
                        ),
                        record_index=record_index,
                        field="symbol",
                        timestamp=bar.timestamp,
                    )
                )

            if bar.timeframe != dataset.timeframe:
                identity_error = True
                identity_findings.add(
                    QualityFinding(
                        code="DATASET_TIMEFRAME_MISMATCH",
                        severity=FindingSeverity.ERROR,
                        message=(
                            f"Observation timeframe {bar.timeframe.value} "
                            f"does not match dataset timeframe "
                            f"{dataset.timeframe.value}."
                        ),
                        record_index=record_index,
                        field="timeframe",
                        timestamp=bar.timestamp,
                    )
                )

            statistics.observe(
                bar.timestamp,
                rejected=has_error or identity_error,
            )
            yield bar

    @staticmethod
    def _stream_quotes(
        observations: Iterable[BidAskQuote],
        dataset: Dataset,
        session: _ValidationSession[BidAskQuote],
        statistics: _StreamStatistics,
        identity_findings: QualityFindingAccumulator,
    ) -> Iterator[BidAskQuote]:
        """Validate and yield quote observations one at a time."""

        for record_index, quote in enumerate(observations):
            has_error = session.add(quote)
            identity_error = False

            if quote.symbol != dataset.instrument_symbol:
                identity_error = True
                identity_findings.add(
                    QualityFinding(
                        code="DATASET_SYMBOL_MISMATCH",
                        severity=FindingSeverity.ERROR,
                        message=(
                            f"Observation symbol {quote.symbol} does not match "
                            f"dataset symbol {dataset.instrument_symbol}."
                        ),
                        record_index=record_index,
                        field="symbol",
                        timestamp=quote.timestamp,
                    )
                )

            statistics.observe(
                quote.timestamp,
                rejected=has_error or identity_error,
            )
            yield quote

    @staticmethod
    def _add_identity_findings(
        report: DataQualityReport,
        findings: QualityFindingAccumulator,
        *,
        max_findings: int,
    ) -> DataQualityReport:
        combined = QualityFindingAccumulator(
            max_findings=max_findings
        )
        combined.merge_report(report)
        combined.merge_report(
            findings.build_report(
                checked_records=report.checked_records
            )
        )
        return combined.build_report(
            checked_records=report.checked_records
        )

    async def _mark_staging_failed(
        self,
        version: DatasetVersion,
        operation_error: Exception,
        *,
        attempt: IngestionAttempt | None = None,
    ) -> None:
        """Mark a staging version FAILED without hiding the original error."""

        try:
            await self._lifecycle.fail_staging_version(
                version.dataset_version_id,
                **({"attempt": attempt} if attempt is not None else {}),
            )
        except Exception as failure_error:
            operation_error.add_note(
                "Additionally, the staging version could not be "
                f"marked FAILED: {failure_error!r}"
            )

    async def _write_verify_publish(
        self,
        version: DatasetVersion,
        observations: Iterable[ObservationT],
        session: _ValidationSession[ObservationT],
        statistics: _StreamStatistics,
        identity_findings: QualityFindingAccumulator,
        storage: VerifiableOHLCVStorage | VerifiableQuoteStorage,
        description: str,
        *,
        attempt: IngestionAttempt | None = None,
    ) -> DatasetPublicationResult:
        """Stream, validate, verify, and publish one dataset version."""

        try:
            metadata = await storage.write(
                version.dataset_version_id,
                observations,
            )

            report = session.finish()

            report = self._add_identity_findings(
                report,
                identity_findings,
                max_findings=self._max_findings,
            )

            if not report.is_valid:
                raise DatasetPublicationRejectedError(
                    report,
                    rejected_record_count=(
                        statistics.rejected_record_count
                    ),
                )

            self._validate_written_metadata(
                metadata,
                statistics,
                description,
                version.storage_format,
            )

            await storage.verify(
                version.dataset_version_id,
                metadata,
            )

            published_version = await self._lifecycle.publish_staging_version(
                version.dataset_version_id,
                metadata,
                **({"attempt": attempt} if attempt is not None else {}),
            )

            return DatasetPublicationResult(
                dataset_version=published_version,
                quality_report=report,
            )

        except Exception as operation_error:
            await self._mark_staging_failed(
                version, operation_error, attempt=attempt
            )
            raise

        finally:
            session.close()

    @staticmethod
    def _validate_written_metadata(
        metadata: StorageObjectMetadata,
        statistics: _StreamStatistics,
        description: str,
        expected_storage_format: str,
    ) -> None:
        """Compare stored metadata with streaming input statistics."""

        if not isinstance(metadata, StorageObjectMetadata):
            raise DatasetPublicationStorageError(
                f"{description} storage returned invalid metadata."
            )

        if metadata.storage_format != expected_storage_format:
            raise DatasetPublicationStorageError(
                f"{description} storage format does not match the "
                "allocated dataset version."
            )

        if metadata.record_count != statistics.record_count:
            raise DatasetPublicationStorageError(
                f"{description} storage record count does not match "
                "the submitted observations."
            )

        if (
            metadata.first_timestamp != statistics.first_timestamp
            or metadata.last_timestamp != statistics.last_timestamp
        ):
            raise DatasetPublicationStorageError(
                f"{description} storage timestamp boundaries do not "
                "match the submitted observations."
            )