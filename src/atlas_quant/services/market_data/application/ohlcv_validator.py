"""Incremental validation for historical OHLCV market data."""

import json
from collections.abc import Iterable

from atlas_quant.services.market_data.application.incremental_validation import (
    IncrementalTimestampIndex,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
    QualityFindingAccumulator,
)


class OHLCVBatchValidator:
    """Validate consistency across OHLCV observations."""

    def __init__(self, *, max_findings: int = 1_000) -> None:
        self._max_findings = max_findings

    def start_session(self) -> "_OHLCVValidationSession":
        """Start an incremental validation session."""
        return _OHLCVValidationSession(
            max_findings=self._max_findings
        )

    def validate(
        self,
        bars: Iterable[OHLCVBar],
    ) -> DataQualityReport:
        """Validate an iterable without materializing its observations."""

        if isinstance(bars, (str, bytes)):
            raise TypeError("bars must contain OHLCVBar objects.")

        try:
            observations = iter(bars)
        except TypeError as exc:
            raise TypeError(
                "bars must be an iterable of OHLCVBar objects."
            ) from exc

        with self.start_session() as session:
            for bar in observations:
                session.add(bar)

            return session.finish()


class _OHLCVValidationSession:
    """Incrementally validate OHLCV observations."""

    def __init__(self, *, max_findings: int = 1_000) -> None:
        self._timestamp_index = IncrementalTimestampIndex()

        try:
            self._findings = QualityFindingAccumulator(
                max_findings=max_findings
            )
        except BaseException:
            self._timestamp_index.close()
            raise
        self._checked_records = 0
        self._closed = False

    def add(self, bar: OHLCVBar) -> bool:
        """Validate one observation and return whether it has an error."""

        self._ensure_open()

        if not isinstance(bar, OHLCVBar):
            raise TypeError(
                "Every observation must be an OHLCVBar."
            )

        record_index = self._checked_records
        self._checked_records += 1

        series_key = json.dumps(
            (bar.symbol, bar.timeframe.value),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        timestamp_check = self._timestamp_index.observe(
            series_key,
            bar.timestamp,
        )

        if timestamp_check.is_duplicate:
            self._findings.add(
                QualityFinding(
                    code="DUPLICATE_BAR_TIMESTAMP",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Duplicate bar timestamp for "
                        f"{bar.symbol} at {bar.timestamp.isoformat()}."
                    ),
                    record_index=record_index,
                    field="timestamp",
                    timestamp=bar.timestamp,
                )
            )
            return True

        if timestamp_check.is_out_of_order:
            self._findings.add(
                QualityFinding(
                    code="OUT_OF_ORDER_BAR",
                    severity=FindingSeverity.WARNING,
                    message=(
                        f"Bar timestamp for {bar.symbol} is earlier "
                        f"than a previously observed timestamp."
                    ),
                    record_index=record_index,
                    field="timestamp",
                    timestamp=bar.timestamp,
                )
            )

        return False

    def finish(self) -> DataQualityReport:
        """Complete validation and return the quality report."""

        self._ensure_open()

        report = self._findings.build_report(
            checked_records=self._checked_records
        )

        self.close()
        return report

    def close(self) -> None:
        """Release temporary validation storage."""

        if self._closed:
            return

        self._closed = True
        self._timestamp_index.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "The OHLCV validation session is closed."
            )

    def __enter__(self) -> "_OHLCVValidationSession":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> None:
        self.close()
