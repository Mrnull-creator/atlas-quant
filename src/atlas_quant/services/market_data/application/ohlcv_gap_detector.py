"""Expected-timestamp gap detection for historical OHLCV data."""

from collections.abc import Iterable
from datetime import datetime, timezone

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
)


class OHLCVGapDetector:
    """Detect expected OHLCV observations missing from a data batch."""

    def detect(
        self,
        bars: Iterable[OHLCVBar],
        expected_timestamps: Iterable[datetime],
    ) -> DataQualityReport:
        """Compare observed bars with explicitly expected timestamps.

        One instrument and timeframe are allowed per invocation.
        Expected timestamps must be timezone-aware.

        The caller is responsible for generating expected timestamps
        using the appropriate instrument and provider schedule.

        Observations are never sorted, modified, or discarded.
        Missing timestamps are reported in chronological order.
        """

        observations = self._normalize_bars(bars)
        expected = self._normalize_expected_timestamps(
            expected_timestamps
        )

        if observations:
            first_bar = observations[0]

            for bar in observations[1:]:
                if (
                    bar.symbol != first_bar.symbol
                    or bar.timeframe != first_bar.timeframe
                ):
                    raise ValueError(
                        "All bars must belong to the same instrument "
                        "and timeframe."
                    )

            symbol = first_bar.symbol
        else:
            symbol = "unknown instrument"

        observed_timestamps = {
            bar.timestamp for bar in observations
        }

        missing_timestamps = sorted(
            expected - observed_timestamps
        )

        findings = tuple(
            QualityFinding(
                code="MISSING_OHLCV_BAR",
                severity=FindingSeverity.WARNING,
                message=(
                    f"Expected OHLCV bar for {symbol} "
                    f"is missing at {timestamp.isoformat()}."
                ),
                field="timestamp",
                timestamp=timestamp,
            )
            for timestamp in missing_timestamps
        )

        return DataQualityReport(
            checked_records=len(observations),
            findings=findings,
        )

    @staticmethod
    def _normalize_bars(
        bars: Iterable[OHLCVBar],
    ) -> tuple[OHLCVBar, ...]:
        """Validate and materialize the observed bars."""

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

        return observations

    @staticmethod
    def _normalize_expected_timestamps(
        timestamps: Iterable[datetime],
    ) -> set[datetime]:
        """Validate, normalize, and deduplicate expected timestamps."""

        if isinstance(timestamps, (str, bytes)):
            raise TypeError(
                "expected_timestamps must contain datetime values."
            )

        try:
            values = tuple(timestamps)
        except TypeError as exc:
            raise TypeError(
                "expected_timestamps must be an iterable of datetimes."
            ) from exc

        normalized: set[datetime] = set()

        for timestamp in values:
            if not isinstance(timestamp, datetime):
                raise TypeError(
                    "Every expected timestamp must be a datetime."
                )

            if (
                timestamp.tzinfo is None
                or timestamp.utcoffset() is None
            ):
                raise ValueError(
                    "Expected timestamps must include timezone information."
                )

            utc_timestamp = timestamp.astimezone(timezone.utc)

            if utc_timestamp in normalized:
                raise ValueError(
                    "Expected timestamps cannot contain duplicates."
                )

            normalized.add(utc_timestamp)

        return normalized