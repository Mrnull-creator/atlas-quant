from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.application.quote_validator import (
    QuoteBatchValidator,
)
from atlas_quant.services.market_data.domain.quality import (
    FindingSeverity,
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


def create_quote(**overrides):
    values = {
        "symbol": "EURUSD",
        "timestamp": datetime(
            2026, 1, 1, 10, 0, tzinfo=timezone.utc
        ),
        "bid": Decimal("1.10000"),
        "ask": Decimal("1.10020"),
        "bid_size": Decimal("100"),
        "ask_size": Decimal("150"),
    }

    values.update(overrides)
    return BidAskQuote(**values)


def create_validator():
    return QuoteBatchValidator()


def test_valid_quote_batch_has_no_findings():
    quotes = [
        create_quote(),
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 1, tzinfo=timezone.utc
            )
        ),
    ]

    report = create_validator().validate(quotes)

    assert report.checked_records == 2
    assert report.findings == ()
    assert report.is_valid
    assert not report.has_warnings


def test_detects_crossed_quote():
    quote = create_quote(
        bid=Decimal("1.10100"),
        ask=Decimal("1.10000"),
    )

    report = create_validator().validate([quote])

    assert report.error_count == 1
    assert report.findings[0].code == "CROSSED_QUOTE"
    assert report.findings[0].severity == FindingSeverity.ERROR
    assert report.findings[0].record_index == 0
    assert report.findings[0].field == "bid/ask"
    assert not report.is_valid


def test_detects_zero_width_spread_as_information():
    quote = create_quote(
        bid=Decimal("1.10000"),
        ask=Decimal("1.10000"),
    )

    report = create_validator().validate([quote])

    assert report.error_count == 0
    assert report.info_count == 1
    assert report.findings[0].code == "ZERO_WIDTH_SPREAD"
    assert report.findings[0].severity == FindingSeverity.INFO
    assert report.is_valid


def test_detects_duplicate_quote_timestamp():
    timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )

    quotes = [
        create_quote(timestamp=timestamp),
        create_quote(
            timestamp=timestamp,
            bid=Decimal("1.10010"),
            ask=Decimal("1.10030"),
        ),
    ]

    report = create_validator().validate(quotes)

    assert report.error_count == 1
    assert report.findings[0].code == "DUPLICATE_QUOTE_TIMESTAMP"
    assert report.findings[0].record_index == 1
    assert not report.is_valid


def test_detects_out_of_order_quote():
    quotes = [
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 2, tzinfo=timezone.utc
            )
        ),
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 1, tzinfo=timezone.utc
            )
        ),
    ]

    report = create_validator().validate(quotes)

    assert report.warning_count == 1
    assert report.findings[0].code == "OUT_OF_ORDER_QUOTE"
    assert report.findings[0].severity == FindingSeverity.WARNING
    assert report.findings[0].record_index == 1
    assert report.is_valid
    assert report.has_warnings


def test_duplicate_does_not_also_create_ordering_warning():
    timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )

    quotes = [
        create_quote(timestamp=timestamp),
        create_quote(timestamp=timestamp),
    ]

    report = create_validator().validate(quotes)

    assert [finding.code for finding in report.findings] == [
        "DUPLICATE_QUOTE_TIMESTAMP"
    ]


def test_crossed_duplicate_reports_both_independent_findings():
    timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )

    quotes = [
        create_quote(timestamp=timestamp),
        create_quote(
            timestamp=timestamp,
            bid=Decimal("1.10100"),
            ask=Decimal("1.10000"),
        ),
    ]

    report = create_validator().validate(quotes)

    assert [finding.code for finding in report.findings] == [
        "CROSSED_QUOTE",
        "DUPLICATE_QUOTE_TIMESTAMP",
    ]


def test_validation_is_independent_per_instrument():
    quotes = [
        create_quote(
            symbol="EURUSD",
            timestamp=datetime(
                2026, 1, 1, 10, 2, tzinfo=timezone.utc
            ),
        ),
        create_quote(
            symbol="GBPUSD",
            timestamp=datetime(
                2026, 1, 1, 10, 1, tzinfo=timezone.utc
            ),
        ),
    ]

    report = create_validator().validate(quotes)

    assert report.findings == ()


def test_same_timestamp_for_different_instruments_is_not_duplicate():
    timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )

    quotes = [
        create_quote(symbol="EURUSD", timestamp=timestamp),
        create_quote(symbol="GBPUSD", timestamp=timestamp),
    ]

    report = create_validator().validate(quotes)

    assert report.findings == ()


def test_reports_original_input_indexes():
    quotes = [
        create_quote(),
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 2, tzinfo=timezone.utc
            )
        ),
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 1, tzinfo=timezone.utc
            )
        ),
    ]

    report = create_validator().validate(quotes)

    assert report.findings[0].record_index == 2


def test_does_not_modify_or_sort_input_quotes():
    quotes = [
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 2, tzinfo=timezone.utc
            )
        ),
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 1, tzinfo=timezone.utc
            )
        ),
    ]

    original_order = tuple(quotes)

    create_validator().validate(quotes)

    assert tuple(quotes) == original_order


def test_accepts_generator_input():
    quotes = (
        create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, minute, tzinfo=timezone.utc
            )
        )
        for minute in range(3)
    )

    report = create_validator().validate(quotes)

    assert report.checked_records == 3
    assert report.findings == ()


def test_empty_batch_is_valid():
    report = create_validator().validate([])

    assert report.checked_records == 0
    assert report.findings == ()
    assert report.is_valid


def test_rejects_non_iterable_input():
    with pytest.raises(TypeError, match="iterable"):
        create_validator().validate(None)


@pytest.mark.parametrize("invalid_input", ["quotes", b"quotes"])
def test_rejects_string_input(invalid_input):
    with pytest.raises(TypeError, match="BidAskQuote"):
        create_validator().validate(invalid_input)


def test_rejects_non_quote_observation():
    with pytest.raises(TypeError, match="BidAskQuote"):
        create_validator().validate([object()])


def test_normalizes_timezone_before_duplicate_detection():
    first_timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )
    second_timestamp = datetime(
        2026, 1, 1, 13, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    quotes = [
        create_quote(timestamp=first_timestamp),
        create_quote(timestamp=second_timestamp),
    ]

    report = create_validator().validate(quotes)

    assert report.error_count == 1
    assert report.findings[0].code == "DUPLICATE_QUOTE_TIMESTAMP"
def test_finding_details_are_bounded_but_aggregate_counts_are_preserved():
    validator = QuoteBatchValidator(max_findings=2)

    timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )

    quotes = [
        create_quote(
            timestamp=timestamp,
            bid=Decimal("1.10100"),
            ask=Decimal("1.10000"),
        )
        for _ in range(4)
    ]

    report = validator.validate(quotes)

    assert report.checked_records == 4
    assert len(report.findings) == 2
    assert report.retained_finding_count == 2
    assert report.total_findings == 7
    assert report.error_count == 7
    assert report.warning_count == 0
    assert report.info_count == 0
    assert report.findings_truncated is True
    assert report.is_valid is False


def test_session_add_counts_multiple_errors_as_one_observation():
    timestamp = datetime(
        2026, 1, 1, 10, 0, tzinfo=timezone.utc
    )

    session = QuoteBatchValidator().start_session()

    try:
        assert session.add(create_quote(timestamp=timestamp)) is False

        crossed_duplicate = create_quote(
            timestamp=timestamp,
            bid=Decimal("1.10100"),
            ask=Decimal("1.10000"),
        )

        assert session.add(crossed_duplicate) is True

        report = session.finish()

        assert report.error_count == 2
        assert report.checked_records == 2
    finally:
        session.close()


def test_session_add_does_not_count_warnings_or_information():
    session = QuoteBatchValidator().start_session()

    try:
        zero_width = create_quote(
            bid=Decimal("1.10000"),
            ask=Decimal("1.10000"),
        )

        later = create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 2, tzinfo=timezone.utc
            )
        )

        earlier = create_quote(
            timestamp=datetime(
                2026, 1, 1, 10, 1, tzinfo=timezone.utc
            )
        )

        assert session.add(zero_width) is False
        assert session.add(later) is False
        assert session.add(earlier) is False
    finally:
        session.close()
