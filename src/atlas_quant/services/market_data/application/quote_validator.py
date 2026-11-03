"""Batch validation for bid/ask market quotes."""

from collections.abc import Iterable

from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
)


class QuoteBatchValidator:
    """Validate consistency across a batch of bid/ask quotes."""

    def validate(
        self,
        quotes: Iterable[BidAskQuote],
    ) -> DataQualityReport:
        """Detect crossed, zero-width, duplicate, and unordered quotes.

        Quotes are never sorted, modified, or discarded.

        Record indexes refer to the original input order.
        Validation is performed independently for each instrument.
        """

        if isinstance(quotes, (str, bytes)):
            raise TypeError("quotes must contain BidAskQuote objects.")

        try:
            observations = tuple(quotes)
        except TypeError as exc:
            raise TypeError(
                "quotes must be an iterable of BidAskQuote objects."
            ) from exc

        for quote in observations:
            if not isinstance(quote, BidAskQuote):
                raise TypeError(
                    "Every observation must be a BidAskQuote."
                )

        findings: list[QualityFinding] = []

        seen_timestamps: dict[str, set] = {}
        latest_timestamps: dict[str, object] = {}

        for record_index, quote in enumerate(observations):
            # Assess the individual quote's bid/ask relationship.
            if quote.bid > quote.ask:
                findings.append(
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

            elif quote.bid == quote.ask:
                findings.append(
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

            # Check duplicate timestamps within each instrument.
            timestamps = seen_timestamps.setdefault(
                quote.symbol,
                set(),
            )

            if quote.timestamp in timestamps:
                findings.append(
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

                # Do not also report an ordering warning for a duplicate.
                continue

            timestamps.add(quote.timestamp)

            # Check chronological order independently per instrument.
            latest_timestamp = latest_timestamps.get(quote.symbol)

            if (
                latest_timestamp is not None
                and quote.timestamp < latest_timestamp
            ):
                findings.append(
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

            if (
                latest_timestamp is None
                or quote.timestamp > latest_timestamp
            ):
                latest_timestamps[quote.symbol] = quote.timestamp

        return DataQualityReport(
            checked_records=len(observations),
            findings=tuple(findings),
        )