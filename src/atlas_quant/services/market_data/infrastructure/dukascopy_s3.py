"""Dukascopy historical BI5 downloads from its Requester Pays S3 bucket."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from typing import Any, Protocol

from atlas_quant.services.market_data.infrastructure.parquet.async_io import (
    run_blocking_safely,
)


DUKASCOPY_S3_BUCKET = "cfg-public-proper-wallaby"
DUKASCOPY_S3_REGION = "eu-west-1"

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")


class DukascopyDownloadError(RuntimeError):
    """Raised when a Dukascopy object cannot be downloaded."""


class DukascopyS3Client(Protocol):
    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """Retrieve an S3 object."""


@dataclass(frozen=True, slots=True)
class DukascopyDailyDownload:
    """An unchanged compressed BI5 payload and its source metadata."""

    symbol: str
    trading_date: date
    bucket: str
    object_key: str
    payload: bytes
    downloaded_at: datetime

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError("Downloaded BI5 payload cannot be empty.")

        if (
            self.downloaded_at.tzinfo is None
            or self.downloaded_at.utcoffset() is None
        ):
            raise ValueError("downloaded_at must be timezone-aware.")

        object.__setattr__(
            self,
            "downloaded_at",
            self.downloaded_at.astimezone(timezone.utc),
        )


def build_daily_object_key(symbol: str, trading_date: date) -> str:
    """Build the current Dukascopy daily BI5 object key.

    Dukascopy uses a zero-indexed month in the S3 object path.
    """

    if not isinstance(symbol, str):
        raise TypeError("symbol must be a string.")

    normalized_symbol = symbol.strip().upper()

    if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("symbol contains invalid characters.")

    if not isinstance(trading_date, date) or isinstance(trading_date, datetime):
        raise TypeError("trading_date must be a date, not a datetime.")

    zero_indexed_month = trading_date.month - 1

    return (
        f"{normalized_symbol}/"
        f"{trading_date.year:04d}/"
        f"{zero_indexed_month:02d}/"
        f"{trading_date.day:02d}_ticks.bi5"
    )


def _error_details(error: Exception) -> tuple[str | None, int | None]:
    """Extract an AWS-style error code and HTTP status without importing boto3."""

    response = getattr(error, "response", None)

    if not isinstance(response, dict):
        return None, None

    error_details = response.get("Error", {})
    metadata = response.get("ResponseMetadata", {})

    code = (
        error_details.get("Code")
        if isinstance(error_details, dict)
        else None
    )
    status = (
        metadata.get("HTTPStatusCode")
        if isinstance(metadata, dict)
        else None
    )

    return (
        str(code) if code is not None else None,
        status if isinstance(status, int) else None,
    )


def _is_missing_object(error: Exception) -> bool:
    code, status = _error_details(error)

    if code in {"NoSuchKey", "NotFound", "404"}:
        return True

    return status == 404 and code != "NoSuchBucket"


class DukascopyS3Downloader:
    """Download individual daily BI5 objects from Dukascopy's S3 bucket.

    The S3 client is injected so the downloader can be tested without AWS
    credentials or network access.
    """

    def __init__(
        self,
        client: DukascopyS3Client,
        *,
        bucket: str = DUKASCOPY_S3_BUCKET,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not bucket or not bucket.strip():
            raise ValueError("bucket must not be empty.")

        self._client = client
        self._bucket = bucket.strip()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def download_daily(
        self,
        symbol: str,
        trading_date: date,
    ) -> DukascopyDailyDownload | None:
        """Download one daily BI5 object.

        Returns None when the object does not exist. This is a normal
        no-data outcome for weekends, holidays, or unavailable history.
        """

        object_key = build_daily_object_key(symbol, trading_date)

        return await run_blocking_safely(
            self._download_sync,
            symbol.strip().upper(),
            trading_date,
            object_key,
        )

    def _download_sync(
        self,
        symbol: str,
        trading_date: date,
        object_key: str,
    ) -> DukascopyDailyDownload | None:
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=object_key,
                RequestPayer="requester",
            )
        except Exception as error:
            if _is_missing_object(error):
                return None

            raise DukascopyDownloadError(
                f"Failed to download Dukascopy object {object_key!r}."
            ) from error

        body = response.get("Body")

        if body is None or not callable(getattr(body, "read", None)):
            raise DukascopyDownloadError(
                f"S3 returned no readable body for {object_key!r}."
            )

        try:
            payload = body.read()
        except Exception as error:
            raise DukascopyDownloadError(
                f"Failed while reading Dukascopy object {object_key!r}."
            ) from error
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

        if not isinstance(payload, bytes):
            raise DukascopyDownloadError(
                f"S3 returned a non-bytes payload for {object_key!r}."
            )

        if not payload:
            raise DukascopyDownloadError(
                f"S3 returned an empty object for {object_key!r}; "
                "a missing object should be represented by a 404 response."
            )

        downloaded_at = self._clock()

        if (
            downloaded_at.tzinfo is None
            or downloaded_at.utcoffset() is None
        ):
            raise DukascopyDownloadError(
                "The downloader clock must return a timezone-aware datetime."
            )

        return DukascopyDailyDownload(
            symbol=symbol,
            trading_date=trading_date,
            bucket=self._bucket,
            object_key=object_key,
            payload=payload,
            downloaded_at=downloaded_at,
        )


def create_dukascopy_s3_client() -> DukascopyS3Client:
    """Create a configured Boto3 S3 client.

    Boto3 is optional and is imported only when this factory is called.
    Credentials are resolved through the standard AWS credential chain.
    """

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise RuntimeError(
            "Dukascopy S3 support requires the optional Boto3 dependency. "
            "Install the project's data extra."
        ) from error

    config = Config(
        region_name=DUKASCOPY_S3_REGION,
        connect_timeout=5,
        read_timeout=60,
        max_pool_connections=10,
        retries={
            "mode": "standard",
            "max_attempts": 3,
        },
    )

    return boto3.client("s3", config=config)
