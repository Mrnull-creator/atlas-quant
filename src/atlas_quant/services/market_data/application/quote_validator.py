"""Incremental validation for bid/ask market quotes."""

from collections.abc import Iterable

from atlas_quant.services.market_data.application.incremental_validation import (
    IncrementalTimestampIndex,
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
    QualityFindingAccumulator,
)


class QuoteBatchValidator:
    """Validate consistency across bid/ask quote observations."""

    def __init__(self, *, max_findings: int = 1_000) -> None:
        self._max_findings = max_findings

    def start_session(self) -> "_QuoteValidationSession":
        """Start an incremental validation session."""
        return _QuoteValidationSession(
            max_findings=self._max_findings
        )

    def validate(
        self,
        quotes: Iterable[BidAskQuote],
    ) -> DataQualityReport:
        """Validate an iterable without materializing its observations."""

        if isinstance(quotes, (str, bytes)):
            raise TypeError("quotes must contain BidAskQuote objects.")

        try:
            observations = iter(quotes)
        except TypeError as exc:
            raise TypeError(
                "quotes must be an iterable of BidAskQuote objects."
            ) from exc

        with self.start_session() as session:
            for quote in observations:
                session.add(quote)

            return session.finish()


class _QuoteValidationSession:
    """Incrementally validate bid/ask quote observations."""

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

    def add(self, quote: BidAskQuote) -> bool:
        """Validate one observation and return whether it has an error."""

        self._ensure_open()

        if not isinstance(quote, BidAskQuote):
            raise TypeError(
                "Every observation must be a BidAskQuote."
            )

        record_index = self._checked_records
        self._checked_records += 1
        has_error = False

        # Assess the individual quote's bid/ask relationship.
        if quote.bid > quote.ask:
            self._findings.add(
                QualityFinding(
                    code="CROSSED_QUOTE",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Bid exceeds ask for {quote.symbol} "
                        f"at {quote.timestamp.isoformat()}."
                    ),
                    record_index=record_index,
                    field="bid/ask",
                    timestamp=quote.timestamp,
                )
            )
            has_error = True

        elif quote.bid == quote.ask:
            self._findings.add(
                QualityFinding(
                    code="ZERO_WIDTH_SPREAD",
                    severity=FindingSeverity.INFO,
                    message=(
                        f"Bid equals ask for {quote.symbol} "
                        f"at {quote.timestamp.isoformat()}."
                    ),
                    record_index=record_index,
                    field="bid/ask",
                    timestamp=quote.timestamp,
                )
            )

        # Check duplicate timestamps and chronological order.
        timestamp_check = self._timestamp_index.observe(
            quote.symbol,
            quote.timestamp,
        )

        if timestamp_check.is_duplicate:
            self._findings.add(
                QualityFinding(
                    code="DUPLICATE_QUOTE_TIMESTAMP",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"Duplicate quote timestamp for "
                        f"{quote.symbol} at "
                        f"{quote.timestamp.isoformat()}."
                    ),
                    record_index=record_index,
                    field="timestamp",
                    timestamp=quote.timestamp,
                )
            )
            has_error = True

        elif timestamp_check.is_out_of_order:
            self._findings.add(
                QualityFinding(
                    code="OUT_OF_ORDER_QUOTE",
                    severity=FindingSeverity.WARNING,
                    message=(
                        f"Quote timestamp for {quote.symbol} "
                        f"is earlier than a previously observed "
                        f"timestamp."
                    ),
                    record_index=record_index,
                    field="timestamp",
                    timestamp=quote.timestamp,
                )
            )

        return has_error

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
                "The quote validation session is closed."
            )

    def __enter__(self) -> "_QuoteValidationSession":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> None:
        self.close()
