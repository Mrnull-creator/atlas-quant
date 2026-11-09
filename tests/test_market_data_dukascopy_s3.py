import asyncio
from datetime import date, datetime, timezone

import pytest

from atlas_quant.services.market_data.infrastructure.dukascopy_s3 import (
    DUKASCOPY_S3_BUCKET,
    DukascopyDownloadError,
    DukascopyS3Downloader,
    build_daily_object_key,
    create_dukascopy_s3_client,
)


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.response


class FakeS3Error(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


def test_build_daily_object_key_uses_zero_indexed_month():
    assert (
        build_daily_object_key("eurusd", date(2024, 1, 15))
        == "EURUSD/2024/00/15_ticks.bi5"
    )


def test_build_daily_object_key_handles_december():
    assert (
        build_daily_object_key("GBPUSD", date(2024, 12, 9))
        == "GBPUSD/2024/11/09_ticks.bi5"
    )


@pytest.mark.parametrize(
    "symbol",
    ["", "../EURUSD", "EUR/USD", "EUR USD", "EURUSD/"],
)
def test_build_daily_object_key_rejects_invalid_symbols(symbol):
    with pytest.raises(ValueError):
        build_daily_object_key(symbol, date(2024, 1, 1))


def test_build_daily_object_key_rejects_non_date():
    with pytest.raises(TypeError):
        build_daily_object_key("EURUSD", datetime(2024, 1, 1))


def test_download_daily_returns_original_payload_and_metadata():
    payload = b"original-compressed-bi5-payload"
    body = FakeBody(payload)
    client = FakeS3Client(response={"Body": body})
    downloaded_at = datetime(2026, 11, 9, 12, 30, tzinfo=timezone.utc)

    downloader = DukascopyS3Downloader(
        client,
        clock=lambda: downloaded_at,
    )

    result = asyncio.run(
        downloader.download_daily("eurusd", date(2024, 1, 15))
    )

    assert result is not None
    assert result.symbol == "EURUSD"
    assert result.trading_date == date(2024, 1, 15)
    assert result.bucket == DUKASCOPY_S3_BUCKET
    assert result.object_key == "EURUSD/2024/00/15_ticks.bi5"
    assert result.payload is payload
    assert result.downloaded_at == downloaded_at
    assert body.closed is True

    assert client.calls == [
        {
            "Bucket": DUKASCOPY_S3_BUCKET,
            "Key": "EURUSD/2024/00/15_ticks.bi5",
            "RequestPayer": "requester",
        }
    ]


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("NoSuchKey", 404),
        ("NotFound", 404),
        ("404", 404),
    ],
)
def test_download_daily_returns_none_for_missing_object(code, status):
    client = FakeS3Client(error=FakeS3Error(code, status))
    downloader = DukascopyS3Downloader(client)

    result = asyncio.run(
        downloader.download_daily("EURUSD", date(2024, 1, 6))
    )

    assert result is None


def test_download_daily_does_not_treat_missing_bucket_as_no_data():
    client = FakeS3Client(error=FakeS3Error("NoSuchBucket", 404))
    downloader = DukascopyS3Downloader(client)

    with pytest.raises(DukascopyDownloadError):
        asyncio.run(
            downloader.download_daily("EURUSD", date(2024, 1, 6))
        )


def test_download_daily_wraps_access_denied():
    client = FakeS3Client(error=FakeS3Error("AccessDenied", 403))
    downloader = DukascopyS3Downloader(client)

    with pytest.raises(DukascopyDownloadError):
        asyncio.run(
            downloader.download_daily("EURUSD", date(2024, 1, 6))
        )


def test_download_daily_rejects_missing_body():
    client = FakeS3Client(response={})
    downloader = DukascopyS3Downloader(client)

    with pytest.raises(DukascopyDownloadError):
        asyncio.run(
            downloader.download_daily("EURUSD", date(2024, 1, 6))
        )


def test_download_daily_rejects_empty_object():
    body = FakeBody(b"")
    client = FakeS3Client(response={"Body": body})
    downloader = DukascopyS3Downloader(client)

    with pytest.raises(DukascopyDownloadError):
        asyncio.run(
            downloader.download_daily("EURUSD", date(2024, 1, 6))
        )

    assert body.closed is True


def test_download_daily_closes_body_when_read_fails():
    class BrokenBody(FakeBody):
        def read(self):
            raise OSError("connection interrupted")

    body = BrokenBody(b"")
    client = FakeS3Client(response={"Body": body})
    downloader = DukascopyS3Downloader(client)

    with pytest.raises(DukascopyDownloadError):
        asyncio.run(
            downloader.download_daily("EURUSD", date(2024, 1, 6))
        )

    assert body.closed is True


def test_download_result_requires_timezone_aware_clock():
    client = FakeS3Client(response={"Body": FakeBody(b"payload")})
    downloader = DukascopyS3Downloader(
        client,
        clock=lambda: datetime(2026, 11, 9, 12, 30),
    )

    with pytest.raises(DukascopyDownloadError):
        asyncio.run(
            downloader.download_daily("EURUSD", date(2024, 1, 6))
        )


def test_create_dukascopy_s3_client_uses_expected_configuration(
    monkeypatch,
):
    import boto3

    captured = {}

    def fake_client(service_name, *, config):
        captured["service_name"] = service_name
        captured["config"] = config
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)

    client = create_dukascopy_s3_client()

    assert client is not None
    assert captured["service_name"] == "s3"

    config = captured["config"]

    assert config.region_name == "eu-west-1"
    assert config.connect_timeout == 5
    assert config.read_timeout == 60
    assert config.max_pool_connections == 10
    assert config.retries == {
        "mode": "standard",
        "max_attempts": 3,
    }
