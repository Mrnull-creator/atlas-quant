"""Decoder for Dukascopy BI5 compressed tick data."""

from __future__ import annotations

import lzma
import math
import struct
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atlas_quant.services.market_data.domain.quotes import BidAskQuote


_RECORD = struct.Struct(">IIIff")
_RECORD_SIZE = _RECORD.size
_MILLISECONDS = timedelta(milliseconds=1)


class BI5DecodeError(ValueError):
    """Raised when a Dukascopy BI5 payload cannot be decoded safely."""


def decode_bi5(
    payload: bytes | bytearray | memoryview,
    *,
    symbol: str,
    period_start: datetime,
    period_duration: timedelta,
    price_divisor: int,
) -> tuple[BidAskQuote, ...]:
    """Decode a compressed BI5 payload into canonical bid/ask observations.

    BI5 records contain:
        milliseconds since period start (uint32)
        ask price (uint32)
        bid price (uint32)
        ask volume (float32)
        bid volume (float32)

    The caller must provide the period start, its duration, and the
    instrument-specific price divisor. This supports both daily and
    legacy hourly files without guessing their timestamp convention.

    Volume values are preserved as decoded. Their provider-specific
    units must be interpreted by the caller.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise BI5DecodeError("BI5 payload must be bytes-like.")

    if not isinstance(period_start, datetime):
        raise BI5DecodeError("period_start must be a datetime.")

    if period_start.tzinfo is None or period_start.utcoffset() is None:
        raise BI5DecodeError("period_start must be timezone-aware.")

    if period_start.microsecond != 0:
        raise BI5DecodeError(
            "period_start must be aligned to a whole second."
        )

    if not isinstance(period_duration, timedelta):
        raise BI5DecodeError("period_duration must be a timedelta.")

    if period_duration <= timedelta(0):
        raise BI5DecodeError("period_duration must be positive.")

    duration_ms = period_duration // _MILLISECONDS

    if period_duration != timedelta(milliseconds=duration_ms):
        raise BI5DecodeError(
            "period_duration must have millisecond precision."
        )

    if isinstance(price_divisor, bool) or not isinstance(price_divisor, int):
        raise BI5DecodeError("price_divisor must be a positive integer.")

    if price_divisor <= 0:
        raise BI5DecodeError("price_divisor must be a positive integer.")

    if not payload:
        return ()

    try:
        decompressed = lzma.decompress(bytes(payload))
    except (lzma.LZMAError, EOFError, ValueError) as exc:
        raise BI5DecodeError("Invalid or corrupted BI5 LZMA payload.") from exc

    if len(decompressed) % _RECORD_SIZE != 0:
        raise BI5DecodeError(
            "Decompressed BI5 payload contains an incomplete record."
        )

    period_start_utc = period_start.astimezone(timezone.utc)
    divisor = Decimal(price_divisor)
    observations: list[BidAskQuote] = []

    for record_index, offset in enumerate(
        range(0, len(decompressed), _RECORD_SIZE)
    ):
        (
            milliseconds,
            ask_raw,
            bid_raw,
            ask_volume_raw,
            bid_volume_raw,
        ) = _RECORD.unpack_from(decompressed, offset)

        if milliseconds >= duration_ms:
            raise BI5DecodeError(
                f"Record {record_index} timestamp is outside its period."
            )

        if not math.isfinite(ask_volume_raw):
            raise BI5DecodeError(
                f"Record {record_index} has a non-finite ask volume."
            )

        if not math.isfinite(bid_volume_raw):
            raise BI5DecodeError(
                f"Record {record_index} has a non-finite bid volume."
            )

        if ask_volume_raw < 0 or bid_volume_raw < 0:
            raise BI5DecodeError(
                f"Record {record_index} has a negative volume."
            )

        timestamp = period_start_utc + timedelta(milliseconds=milliseconds)

        try:
            observation = BidAskQuote(
                symbol=symbol,
                timestamp=timestamp,
                bid=Decimal(bid_raw) / divisor,
                ask=Decimal(ask_raw) / divisor,
                bid_size=Decimal(str(bid_volume_raw)),
                ask_size=Decimal(str(ask_volume_raw)),
            )
        except (TypeError, ValueError) as exc:
            raise BI5DecodeError(
                f"Invalid market observation in BI5 record {record_index}."
            ) from exc

        observations.append(observation)

    return tuple(observations)
