"""Application service for publishing immutable market data versions."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
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
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


class VerifiableOHLCVStorage(OHLCVStorage, Protocol):
    """OHLCV storage that supports independent object verification."""

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify the persisted object against its expected metadata."""
        ...


class VerifiableQuoteStorage(QuoteStorage, Protocol):
    """Quote storage that supports independent object verification."""

    async def verify(
        self,
        dataset_version_id: UUID,
        expected: StorageObjectMetadata,
    ) -> None:
        """Verify the persisted object against its expected metadata."""
        ...


class DatasetPublicationError(Exception):
    """Base exception for dataset publication failures."""


class DatasetPublicationDatasetKindError(DatasetPublicationError):
    """Raised when a dataset has the wrong kind for the requested data."""


class DatasetPublicationRejectedError(DatasetPublicationError):
    """Raised when pre-publication quality checks reject observations."""

    def __init__(self, report: DataQualityReport) -> None:
        self.report = report
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


class DatasetPublicationService:
    """Coordinate validation, storage, verification, and publication."""

    def __init__(
        self,
        dataset_repository: DatasetRepository,
        lifecycle_repository: DatasetLifecycleRepository,
        ohlcv_storage: VerifiableOHLCVStorage,
        quote_storage: VerifiableQuoteStorage,
        *,
        schema_version: str = "1",
        storage_format: str = "parquet",
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

        self._ohlcv_validator = OHLCVBatchValidator()
        self._quote_validator = QuoteBatchValidator()

    async def publish_ohlcv(
        self,
        dataset_id: UUID,
        bars: Iterable[OHLCVBar],
    ) -> DatasetPublicationResult:
        """Validate, store, verify, and publish OHLCV observations."""

        dataset = await self._get_dataset(dataset_id)

        if dataset.data_kind != DatasetKind.OHLCV:
            raise DatasetPublicationDatasetKindError(
                "The requested dataset is not an OHLCV dataset."
            )

        observations = self._materialize(bars, "bars")
        report = self._ohlcv_validator.validate(observations)

        identity_findings = []

        for index, bar in enumerate(observations):
            if bar.symbol != dataset.instrument_symbol:
                identity_findings.append(
                    QualityFinding(
                        code="DATASET_SYMBOL_MISMATCH",
                        severity=FindingSeverity.ERROR,
                        message=(
                            f"Observation symbol {bar.symbol} does not match "
                            f"dataset symbol {dataset.instrument_symbol}."
                        ),
                        record_index=index,
                        field="symbol",
                        timestamp=bar.timestamp,
                    )
                )

            if bar.timeframe != dataset.timeframe:
                identity_findings.append(
                    QualityFinding(
                        code="DATASET_TIMEFRAME_MISMATCH",
                        severity=FindingSeverity.ERROR,
                        message=(
                            f"Observation timeframe {bar.timeframe.value} "
                            f"does not match dataset timeframe "
                            f"{dataset.timeframe.value}."
                        ),
                        record_index=index,
                        field="timeframe",
                        timestamp=bar.timestamp,
                    )
                )

        report = self._add_identity_findings(
            report,
            identity_findings,
        )
        report = self._reject_empty_dataset(report, observations)

        if not report.is_valid:
            raise DatasetPublicationRejectedError(report)

        version = await self._lifecycle.create_next_staging_version(
            dataset_id,
            schema_version=self._schema_version,
            storage_format=self._storage_format,
        )

        return await self._write_verify_publish(
            version,
            observations,
            report,
            self._ohlcv_storage,
            "OHLCV",
        )

    async def publish_quotes(
        self,
        dataset_id: UUID,
        quotes: Iterable[BidAskQuote],
    ) -> DatasetPublicationResult:
        """Validate, store, verify, and publish bid/ask observations."""

        dataset = await self._get_dataset(dataset_id)

        if dataset.data_kind != DatasetKind.BID_ASK:
            raise DatasetPublicationDatasetKindError(
                "The requested dataset is not a bid/ask dataset."
            )

        observations = self._materialize(quotes, "quotes")
        report = self._quote_validator.validate(observations)

        report = self._add_identity_findings(
            report,
            (
                QualityFinding(
                    code="DATASET_SYMBOL_MISMATCH",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Observation symbol {quote.symbol} does not match "
                        f"dataset symbol {dataset.instrument_symbol}."
                    ),
                    record_index=index,
                    field="symbol",
                    timestamp=quote.timestamp,
                )
                for index, quote in enumerate(observations)
                if quote.symbol != dataset.instrument_symbol
            ),
        )

        report = self._reject_empty_dataset(report, observations)

        if not report.is_valid:
            raise DatasetPublicationRejectedError(report)

        version = await self._lifecycle.create_next_staging_version(
            dataset_id,
            schema_version=self._schema_version,
            storage_format=self._storage_format,
        )

        return await self._write_verify_publish(
            version,
            observations,
            report,
            self._quote_storage,
            "bid/ask",
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
    def _materialize(
        observations: Iterable,
        name: str,
    ) -> tuple:
        if isinstance(observations, (str, bytes)):
            raise TypeError(f"{name} must be an iterable of observations.")

        try:
            return tuple(observations)
        except TypeError as exc:
            raise TypeError(
                f"{name} must be an iterable of observations."
            ) from exc

    @staticmethod
    def _add_identity_findings(
        report: DataQualityReport,
        findings: Iterable[QualityFinding],
    ) -> DataQualityReport:
        additional_findings = tuple(findings)

        if not additional_findings:
            return report

        return DataQualityReport(
            checked_records=report.checked_records,
            findings=report.findings + additional_findings,
        )

    @staticmethod
    def _reject_empty_dataset(
        report: DataQualityReport,
        observations: tuple,
    ) -> DataQualityReport:
        if observations:
            return report

        return DatasetPublicationService._add_identity_findings(
            report,
            (
                QualityFinding(
                    code="EMPTY_DATASET",
                    severity=FindingSeverity.ERROR,
                    message="A published dataset version must contain records.",
                ),
            ),
        )

    async def _write_verify_publish(
        self,
        version: DatasetVersion,
        observations: tuple,
        report: DataQualityReport,
        storage: VerifiableOHLCVStorage | VerifiableQuoteStorage,
        description: str,
    ) -> DatasetPublicationResult:
        """Perform the storage and publication portion of the workflow."""

        try:
            metadata = await storage.write(
                version.dataset_version_id,
                observations,
            )

            self._validate_written_metadata(
                metadata,
                observations,
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
            )

            return DatasetPublicationResult(
                dataset_version=published_version,
                quality_report=report,
            )

        except Exception as operation_error:
            try:
                await self._lifecycle.fail_staging_version(
                    version.dataset_version_id
                )
            except Exception as failure_error:
                operation_error.add_note(
                    "Additionally, the staging version could not be "
                    f"marked FAILED: {failure_error!r}"
                )

            raise

    @staticmethod
    def _validate_written_metadata(
        metadata: StorageObjectMetadata,
        observations: tuple,
        description: str,
        expected_storage_format: str,
    ) -> None:
        if not isinstance(metadata, StorageObjectMetadata):
            raise DatasetPublicationStorageError(
                f"{description} storage returned invalid metadata."
            )

        if metadata.storage_format != expected_storage_format:
            raise DatasetPublicationStorageError(
                f"{description} storage format does not match the "
                "allocated dataset version."
            )

        timestamps = tuple(
            observation.timestamp
            for observation in observations
        )

        expected_first = min(timestamps)
        expected_last = max(timestamps)

        if metadata.record_count != len(observations):
            raise DatasetPublicationStorageError(
                f"{description} storage record count does not match "
                "the submitted observations."
            )

        if (
            metadata.first_timestamp != expected_first
            or metadata.last_timestamp != expected_last
        ):
            raise DatasetPublicationStorageError(
                f"{description} storage timestamp boundaries do not "
                "match the submitted observations."
            )