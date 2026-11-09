"""Dukascopy implementation of the market-data provider adapter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from typing import Protocol
from uuid import UUID

from atlas_quant.services.market_data.application.provider_adapters import (
    ProviderDataRequest,
    ProviderDownload,
    ProviderObservation,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.infrastructure.dukascopy_bi5 import (
    decode_bi5,
)
from atlas_quant.services.market_data.infrastructure.dukascopy_s3 import (
    DukascopyDailyDownload,
)


DUKASCOPY_PROVIDER_ID = "dukascopy"
DUKASCOPY_BI5_SCHEMA_VERSION = "dukascopy-bi5-daily-v1"


class DukascopyNoDataError(LookupError):
    """Raised when Dukascopy has no daily data for the requested date."""

    def __init__(self, trading_date: date) -> None:
        self.trading_date = trading_date
        super().__init__(
            f"Dukascopy has no BI5 data for {trading_date.isoformat()}."
        )


class DukascopyDailyDownloader(Protocol):
    """Contract required from the injected daily S3 downloader."""

    async def download_daily(
        self,
        symbol: str,
        trading_date: date,
    ) -> DukascopyDailyDownload | None:
        """Download one provider-symbol daily BI5 object."""
        ...


class DukascopyProviderAdapter:
    """Download and normalize Dukascopy daily bid/ask tick data.

    Price divisors are explicit provider-specific configuration, keyed by
    Atlas Quant's immutable canonical instrument UUID.

    Each request must fit within one UTC calendar day. The ingestion
    orchestrator is responsible for splitting larger requests.
    """

    def __init__(
        self,
        downloader: DukascopyDailyDownloader,
        *,
        price_divisors: Mapping[UUID, int],
    ) -> None:
        if not isinstance(price_divisors, Mapping):
            raise TypeError("price_divisors must be a mapping.")

        normalized_divisors: dict[UUID, int] = {}

        for instrument_id, divisor in price_divisors.items():
            if not isinstance(instrument_id, UUID):
                raise TypeError(
                    "Every price divisor key must be an instrument UUID."
                )

            if (
                isinstance(divisor, bool)
                or not isinstance(divisor, int)
                or divisor <= 0
            ):
                raise ValueError(
                    "Every price divisor must be a positive integer."
                )

            normalized_divisors[instrument_id] = divisor

        self._downloader = downloader
        self._price_divisors = normalized_divisors

    async def download(
        self,
        request: ProviderDataRequest,
    ) -> ProviderDownload:
        """Download the original BI5 payload for one UTC day."""

        self._validate_request(request)

        instrument_id = request.instrument.instrument_id

        if instrument_id not in self._price_divisors:
            raise ValueError(
                "No Dukascopy price divisor is configured for "
                f"instrument {instrument_id}."
            )

        trading_date = request.start.date()

        result = await self._downloader.download_daily(
            request.mapping.provider_symbol,
            trading_date,
        )

        if result is None:
            raise DukascopyNoDataError(trading_date)

        if not isinstance(result, DukascopyDailyDownload):
            raise TypeError(
                "The Dukascopy downloader must return a "
                "DukascopyDailyDownload or None."
            )

        if result.trading_date != trading_date:
            raise ValueError(
                "The downloader returned data for a different trading date."
            )

        if result.symbol != request.mapping.provider_symbol.strip().upper():
            raise ValueError(
                "The downloader returned data for a different provider symbol."
            )

        # Retain the original compressed provider bytes. Normalization is
        # deliberately separate and happens only after this payload can
        # be preserved by the ingestion workflow.
        return ProviderDownload(
            payload=BytesIO(result.payload),
            content_type="application/octet-stream",
            provider_schema_version=DUKASCOPY_BI5_SCHEMA_VERSION,
            downloaded_at=result.downloaded_at,
        )

    def normalize(
        self,
        download: ProviderDownload,
        request: ProviderDataRequest,
    ) -> Iterator[ProviderObservation]:
        """Decode BI5 ticks and retain only the requested time interval."""

        self._validate_request(request)

        try:
            price_divisor = self._price_divisors[
                request.instrument.instrument_id
            ]
        except KeyError as error:
            raise ValueError(
                "No Dukascopy price divisor is configured for "
                f"instrument {request.instrument.instrument_id}."
            ) from error

        if not isinstance(download, ProviderDownload):
            raise TypeError("download must be a ProviderDownload.")

        # The raw storage workflow may have consumed the stream already.
        # BI5 decoding starts from the beginning and leaves it reusable.
        download.payload.seek(0)

        try:
            payload = download.payload.read()
        finally:
            download.payload.seek(0)

        if not isinstance(payload, bytes):
            raise TypeError("BI5 payload must be a binary stream.")

        period_start = datetime.combine(
            request.start.date(),
            time.min,
            tzinfo=timezone.utc,
        )

        observations = decode_bi5(
            payload,
            symbol=request.instrument.symbol,
            period_start=period_start,
            period_duration=timedelta(days=1),
            price_divisor=price_divisor,
        )

        for observation in observations:
            if request.start <= observation.timestamp < request.end:
                yield observation

    @staticmethod
    def _validate_request(request: ProviderDataRequest) -> None:
        if not isinstance(request, ProviderDataRequest):
            raise TypeError("request must be a ProviderDataRequest.")

        if request.provider.provider_id != DUKASCOPY_PROVIDER_ID:
            raise ValueError(
                "DukascopyProviderAdapter only supports the "
                "'dukascopy' provider."
            )

        if request.data_kind != DatasetKind.BID_ASK:
            raise ValueError(
                "Dukascopy BI5 adapter currently supports bid/ask data only."
            )

        final_included_instant = request.end - timedelta(microseconds=1)

        if request.start.date() != final_included_instant.date():
            raise ValueError(
                "A Dukascopy adapter request must fit within one UTC "
                "calendar day. Split multi-day requests before downloading."
            )