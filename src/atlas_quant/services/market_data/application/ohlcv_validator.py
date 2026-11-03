
"""Batch validation for historical OHLCV market data."""

from collections.abc import Iterable
from datetime import datetime

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
)
from atlas_quant.services.market_data.domain.enums import Timeframe


class OHLCVBatchValidator:
    """Validate consistency across a batch of OHLCV observations."""

    def validate(
        self,
        bars: Iterable[OHLCVBar],
    ) -> DataQualityReport:
        """Detect duplicate and out-of-order observations.

        Observations are never sorted, modified, or discarded.

        Record indexes refer to the original input order.
        Validation is performed independently for each
        instrument and timeframe.
        """

        if isinstance(bars, (str, bytes)):
            raise TypeError("bars must contain OHLCVBar objects.")

        try:
            observations = tuple(bars)
        except TypeError as exc:
            raise TypeError(
                "bars must be an iterable of OHLCVBar objects."
            ) from exc

        for bar in observations:
            if not isinstance(bar, OHLCVBar):
                raise TypeError(
                    "Every observation must be an OHLCVBar."
                )

        findings: list[QualityFinding] = []

        seen_timestamps: dict[
            tuple[str, Timeframe],
            set[datetime],
        ] = {}

        latest_timestamps: dict[
            tuple[str, Timeframe],
            datetime,
        ] = {}

        for record_index, bar in enumerate(observations):
            series_key = (bar.symbol, bar.timeframe)

            timestamps = seen_timestamps.setdefault(
                series_key,
                set(),
            )

            if bar.timestamp in timestamps:
                findings.append(
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

                # A duplicate already has a specific error.
                # Avoid reporting a second ordering warning.
                continue

            timestamps.add(bar.timestamp)

            latest_timestamp = latest_timestamps.get(series_key)

            if (
                latest_timestamp is not None
                and bar.timestamp < latest_timestamp
            ):
                findings.append(
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

            if (
                latest_timestamp is None
                or bar.timestamp > latest_timestamp
            ):
                latest_timestamps[series_key] = bar.timestamp

        return DataQualityReport(
            checked_records=len(observations),
            findings=tuple(findings),
        )