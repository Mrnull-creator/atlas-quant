"""Tests for ingestion job request identity."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.enums import AssetClass, Timeframe
from atlas_quant.services.market_data.domain.ingestion import IngestionJob


def make_job(**overrides):
    values = {
        "provider_id": "dukascopy",
        "asset_class": AssetClass.FOREX,
        "instrument_id": uuid4(),
        "instrument_symbol": "EUR/USD",
        "timeframe": Timeframe.M1,
        "requested_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "requested_end": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return IngestionJob(**values)


def test_historical_job_can_omit_request_identity():
    job = make_job(
        data_kind=None,
        provider_mapping_id=None,
    )

    assert job.data_kind is None
    assert job.provider_mapping_id is None


def test_ohlcv_request_identity_is_valid():
    mapping_id = uuid4()

    job = make_job(
        data_kind=DatasetKind.OHLCV,
        provider_mapping_id=mapping_id,
    )

    assert job.data_kind == DatasetKind.OHLCV
    assert job.provider_mapping_id == mapping_id


def test_bid_ask_request_identity_is_valid_without_timeframe():
    mapping_id = uuid4()

    job = make_job(
        data_kind=DatasetKind.BID_ASK,
        provider_mapping_id=mapping_id,
        timeframe=None,
    )

    assert job.data_kind == DatasetKind.BID_ASK
    assert job.timeframe is None


@pytest.mark.parametrize(
    ("data_kind", "provider_mapping_id"),
    [
        (DatasetKind.OHLCV, None),
        (None, uuid4()),
    ],
)
def test_request_identity_fields_must_be_provided_together(
    data_kind,
    provider_mapping_id,
):
    with pytest.raises(ValueError, match="provided together"):
        make_job(
            data_kind=data_kind,
            provider_mapping_id=provider_mapping_id,
        )


def test_raw_provider_is_not_a_valid_request_data_kind():
    with pytest.raises(ValueError, match="OHLCV or BID_ASK"):
        make_job(
            data_kind=DatasetKind.RAW_PROVIDER,
            provider_mapping_id=uuid4(),
        )


def test_request_identity_requires_canonical_instrument_id():
    with pytest.raises(ValueError, match="instrument_id"):
        make_job(
            instrument_id=None,
            data_kind=DatasetKind.OHLCV,
            provider_mapping_id=uuid4(),
        )


def test_ohlcv_request_requires_timeframe():
    with pytest.raises(ValueError, match="require a timeframe"):
        make_job(
            data_kind=DatasetKind.OHLCV,
            provider_mapping_id=uuid4(),
            timeframe=None,
        )


def test_bid_ask_request_rejects_timeframe():
    with pytest.raises(ValueError, match="must not have a timeframe"):
        make_job(
            data_kind=DatasetKind.BID_ASK,
            provider_mapping_id=uuid4(),
            timeframe=Timeframe.M1,
        )


def test_request_rejects_invalid_data_kind_type():
    with pytest.raises(TypeError, match="DatasetKind"):
        make_job(
            data_kind="ohlcv",
            provider_mapping_id=uuid4(),
        )


def test_request_rejects_invalid_provider_mapping_id():
    with pytest.raises(TypeError, match="provider_mapping_id"):
        make_job(
            data_kind=DatasetKind.OHLCV,
            provider_mapping_id="not-a-uuid",
        )