"""Application service for OHLCV market data coverage assessment."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from atlas_quant.services.market_data.application.calendar_schedule import (
    ResolvedSessionInterval,
)
from atlas_quant.services.market_data.application.expected_timestamps import (
    ExpectedTimestampGenerator,
)
from atlas_quant.services.market_data.application.ohlcv_gap_detector import (
    OHLCVGapDetector,
)
from atlas_quant.services.market_data.application.provider_availability_schedule import (
    ResolvedProviderAvailabilityInterval,
)
from atlas_quant.services.market_data.domain.bar_boundaries import (
    BarBoundaryPolicy,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.calendar import TradingCalendar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilitySchedule,
)
from atlas_quant.services.market_data.domain.quality import DataQualityReport


@dataclass(frozen=True, slots=True)
class OHLCVCoverageAssessment:
    """Immutable result of an OHLCV coverage assessment."""

    instrument_symbol: str
    timeframe: Timeframe
    expected_timestamps: tuple[datetime, ...]
    expected_count: int
    observed_expected_count: int
    missing_count: int
    coverage_ratio: float
    quality_report: DataQualityReport
    market_intervals: tuple[ResolvedSessionInterval, ...]
    provider_intervals: tuple[ResolvedProviderAvailabilityInterval, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_symbol, str):
            raise TypeError("instrument_symbol must be a string.")

        symbol = self.instrument_symbol.strip().upper()

        if not symbol:
            raise ValueError("instrument_symbol cannot be empty.")

        object.__setattr__(self, "instrument_symbol", symbol)

        if not isinstance(self.timeframe, Timeframe):
            raise TypeError("timeframe must be a Timeframe value.")

        if not isinstance(self.expected_timestamps, tuple):
            raise TypeError("expected_timestamps must be a tuple.")

        for timestamp in self.expected_timestamps:
            if not isinstance(timestamp, datetime):
                raise TypeError(
                    "Expected timestamps must contain datetime values."
                )

            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(
                    "Expected timestamps must be timezone-aware."
                )

        if tuple(sorted(set(self.expected_timestamps))) != (
            self.expected_timestamps
        ):
            raise ValueError(
                "Expected timestamps must be unique and chronological."
            )

        counts = (
            self.expected_count,
            self.observed_expected_count,
            self.missing_count,
        )

        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in counts
        ):
            raise TypeError("Coverage counts must be integers.")

        if any(value < 0 for value in counts):
            raise ValueError("Coverage counts cannot be negative.")

        if self.expected_count != len(self.expected_timestamps):
            raise ValueError(
                "expected_count must match expected_timestamps."
            )

        if (
            self.observed_expected_count + self.missing_count
            != self.expected_count
        ):
            raise ValueError(
                "Observed and missing counts must equal expected_count."
            )

        if (
            isinstance(self.coverage_ratio, bool)
            or not isinstance(self.coverage_ratio, (int, float))
        ):
            raise TypeError("coverage_ratio must be numeric.")

        if not isfinite(self.coverage_ratio) or not (
            0.0 <= self.coverage_ratio <= 1.0
        ):
            raise ValueError(
                "coverage_ratio must be finite and between 0 and 1."
            )

        expected_ratio = (
            self.observed_expected_count / self.expected_count
            if self.expected_count
            else 1.0
        )

        if abs(self.coverage_ratio - expected_ratio) > 1e-12:
            raise ValueError(
                "coverage_ratio does not match coverage counts."
            )

        if not isinstance(self.quality_report, DataQualityReport):
            raise TypeError(
                "quality_report must be a DataQualityReport."
            )

        if self.quality_report.checked_records < 0:
            raise ValueError("Invalid quality report record count.")

        missing_findings = sum(
            finding.code == "MISSING_OHLCV_BAR"
            for finding in self.quality_report.findings
        )

        if missing_findings != self.missing_count:
            raise ValueError(
                "Missing count must match missing-bar quality findings."
            )

        if not isinstance(self.market_intervals, tuple):
            raise TypeError("market_intervals must be a tuple.")

        if not isinstance(self.provider_intervals, tuple):
            raise TypeError("provider_intervals must be a tuple.")


class OHLCVCoverageAssessor:
    """Coordinate expected timestamp generation and gap detection."""

    def __init__(
        self,
        timestamp_generator: ExpectedTimestampGenerator | None = None,
        gap_detector: OHLCVGapDetector | None = None,
    ) -> None:
        self._timestamp_generator = (
            timestamp_generator or ExpectedTimestampGenerator()
        )
        self._gap_detector = gap_detector or OHLCVGapDetector()

    def assess(
        self,
        instrument: Instrument,
        calendar: TradingCalendar,
        provider_schedule: ProviderAvailabilitySchedule,
        policy: BarBoundaryPolicy,
        bars: Iterable[OHLCVBar],
        start: datetime,
        end: datetime,
    ) -> OHLCVCoverageAssessment:
        """Assess expected versus observed OHLCV coverage."""

        observations = self._normalize_bars(bars)

        seen_timestamps: set[datetime] = set()

        for bar in observations:
            if bar.symbol != instrument.symbol:
                raise ValueError(
                    "Every bar must belong to the requested instrument."
                )

            if bar.timeframe != policy.timeframe:
                raise ValueError(
                    "Every bar must match the requested timeframe."
                )

            if bar.timestamp in seen_timestamps:
                raise ValueError(
                    "Coverage assessment requires unique bar timestamps."
                )

            seen_timestamps.add(bar.timestamp)

        generated = self._timestamp_generator.generate(
            instrument=instrument,
            calendar=calendar,
            provider_schedule=provider_schedule,
            policy=policy,
            start=start,
            end=end,
        )

        report = self._gap_detector.detect(
            bars=observations,
            expected_timestamps=generated.expected_timestamps,
        )

        expected_set = set(generated.expected_timestamps)

        observed_expected_count = sum(
            bar.timestamp in expected_set
            for bar in observations
        )

        expected_count = len(generated.expected_timestamps)
        missing_count = sum(
            finding.code == "MISSING_OHLCV_BAR"
            for finding in report.findings
        )

        coverage_ratio = (
            observed_expected_count / expected_count
            if expected_count
            else 1.0
        )

        return OHLCVCoverageAssessment(
            instrument_symbol=instrument.symbol,
            timeframe=policy.timeframe,
            expected_timestamps=generated.expected_timestamps,
            expected_count=expected_count,
            observed_expected_count=observed_expected_count,
            missing_count=missing_count,
            coverage_ratio=coverage_ratio,
            quality_report=report,
            market_intervals=generated.market_intervals,
            provider_intervals=generated.provider_intervals,
        )

    @staticmethod
    def _normalize_bars(
        bars: Iterable[OHLCVBar],
    ) -> tuple[OHLCVBar, ...]:
        """Materialize and validate observed OHLCV bars."""

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