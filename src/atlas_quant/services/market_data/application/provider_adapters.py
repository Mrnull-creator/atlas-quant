"""Contracts for external market data provider adapters."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import BinaryIO, Protocol

from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


_STORAGE_FORMAT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


@dataclass(frozen=True, slots=True)
class ProviderDataRequest:
    """Describe a canonical market data request to a provider adapter."""

    provider: MarketDataProvider
    instrument: Instrument
    mapping: ProviderInstrumentMapping

    data_kind: DatasetKind
    start: datetime
    end: datetime

    timeframe: Timeframe | None = None

    def __post_init__(self) -> None:
        """Validate provider capabilities and canonical instrument identity."""

        if not isinstance(self.provider, MarketDataProvider):
            raise TypeError("provider must be a MarketDataProvider.")

        if not isinstance(self.instrument, Instrument):
            raise TypeError("instrument must be an Instrument.")

        if not isinstance(self.mapping, ProviderInstrumentMapping):
            raise TypeError("mapping must be a ProviderInstrumentMapping.")

        if not isinstance(self.data_kind, DatasetKind):
            raise TypeError("data_kind must be a DatasetKind value.")

        if self.data_kind not in (DatasetKind.OHLCV, DatasetKind.BID_ASK):
            raise ValueError(
                "Provider requests must contain OHLCV or BID_ASK data."
            )

        if not self.provider.is_enabled:
            raise ValueError("The requested provider is disabled.")

        if not self.instrument.is_active:
            raise ValueError("The requested instrument is inactive.")

        if not self.mapping.is_active:
            raise ValueError("The provider instrument mapping is inactive.")

        if self.mapping.provider_id != self.provider.provider_id:
            raise ValueError(
                "The provider mapping belongs to a different provider."
            )

        if self.mapping.instrument_id != self.instrument.instrument_id:
            raise ValueError(
                "The provider mapping belongs to a different instrument."
            )

        if self.instrument.asset_class not in self.provider.supported_asset_classes:
            raise ValueError(
                "The provider does not support this instrument's asset class."
            )

        if self.data_kind == DatasetKind.OHLCV:
            if not self.provider.supports_historical_data:
                raise ValueError(
                    "The provider does not support historical data."
                )

            if not isinstance(self.timeframe, Timeframe):
                raise TypeError("OHLCV requests require a Timeframe value.")

            if self.timeframe not in self.provider.supported_timeframes:
                raise ValueError(
                    "The provider does not support the requested timeframe."
                )
        else:
            if not self.provider.supports_quote_data:
                raise ValueError(
                    "The provider does not support bid/ask quote data."
                )

            if self.timeframe is not None:
                raise ValueError(
                    "Bid/ask requests must not specify a timeframe."
                )

        self._normalize_timestamp("start", self.start)
        self._normalize_timestamp("end", self.end)

        if self.start >= self.end:
            raise ValueError("Request start must be before request end.")

        object.__setattr__(
            self, "start", self.start.astimezone(timezone.utc)
        )
        object.__setattr__(
            self, "end", self.end.astimezone(timezone.utc)
        )

    @staticmethod
    def _normalize_timestamp(field_name: str, value: datetime) -> None:
        if not isinstance(value, datetime):
            raise TypeError(f"{field_name} must be a datetime.")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{field_name} must include timezone information."
            )


@dataclass(frozen=True, slots=True)
class ProviderDownload:
    """Original provider payload returned by an adapter.

    The payload remains in its original provider format. The caller owns
    the stream and must close it after raw-data preservation and parsing.

    Streams must be readable and seekable so the same original payload
    can be preserved and subsequently parsed.
    """

    payload: BinaryIO
    content_type: str = "application/octet-stream"
    provider_schema_version: str | None = None
    storage_format: str = "provider-native"
    downloaded_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        """Validate the original payload and its descriptive metadata."""

        if not callable(getattr(self.payload, "read", None)):
            raise TypeError("payload must be a readable binary stream.")

        if not callable(getattr(self.payload, "seek", None)):
            raise TypeError("payload must be seekable.")

        if not callable(getattr(self.payload, "readable", None)):
            raise TypeError("payload must expose readable().")

        if not callable(getattr(self.payload, "seekable", None)):
            raise TypeError("payload must expose seekable().")

        if not self.payload.readable():
            raise ValueError("payload must be readable.")

        if not self.payload.seekable():
            raise ValueError("payload must be seekable.")

        if not isinstance(self.payload.read(0), bytes):
            raise TypeError("payload must be a binary stream.")

        if not isinstance(self.content_type, str):
            raise TypeError("content_type must be a string.")

        content_type = self.content_type.strip()

        if not content_type:
            raise ValueError("content_type cannot be empty.")

        object.__setattr__(self, "content_type", content_type)

        if self.provider_schema_version is not None:
            if not isinstance(self.provider_schema_version, str):
                raise TypeError(
                    "provider_schema_version must be a string or None."
                )

            schema_version = self.provider_schema_version.strip()

            if not schema_version:
                raise ValueError(
                    "provider_schema_version cannot be empty."
                )

            object.__setattr__(
                self, "provider_schema_version", schema_version
            )

        if not isinstance(self.storage_format, str):
            raise TypeError("storage_format must be a string.")

        storage_format = self.storage_format.strip().lower()

        if not _STORAGE_FORMAT_PATTERN.fullmatch(storage_format):
            raise ValueError("storage_format has an invalid format.")

        object.__setattr__(self, "storage_format", storage_format)

        if not isinstance(self.downloaded_at, datetime):
            raise TypeError("downloaded_at must be a datetime.")

        if (
            self.downloaded_at.tzinfo is None
            or self.downloaded_at.utcoffset() is None
        ):
            raise ValueError(
                "downloaded_at must include timezone information."
            )

        object.__setattr__(
            self,
            "downloaded_at",
            self.downloaded_at.astimezone(timezone.utc),
        )

        self.payload.seek(0)

    def close(self) -> None:
        """Close the provider payload stream."""

        self.payload.close()

    def __enter__(self) -> ProviderDownload:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()


ProviderObservation = OHLCVBar | BidAskQuote


class ProviderAdapter(Protocol):
    """Technology-independent interface implemented by provider adapters."""

    async def download(
        self,
        request: ProviderDataRequest,
    ) -> ProviderDownload:
        """Download and return the original provider payload."""
        ...

    def normalize(
        self,
        download: ProviderDownload,
        request: ProviderDataRequest,
    ) -> Iterator[ProviderObservation]:
        """Stream canonical observations from the original provider data."""
        ...


class ProviderAdapterRegistry(Protocol):
    """Resolve an adapter for a registered provider."""

    def get_adapter(self, provider_id: str) -> ProviderAdapter:
        """Return the adapter registered for the provider identifier."""
        ...
