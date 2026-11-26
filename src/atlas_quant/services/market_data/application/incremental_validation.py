"""Disk-backed state for incremental market data validation."""

import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_DAY = 86_400 * _MICROSECONDS_PER_SECOND
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class TimestampCheck:
    """Validation results for one observed timestamp."""

    is_duplicate: bool
    is_out_of_order: bool


class IncrementalTimestampIndex:
    """Track duplicate and out-of-order timestamps using SQLite.

    Timestamp history is stored on disk rather than retained in Python
    collections. Each index instance represents one validation session.

    A series key identifies an independent chronological series, such
    as an instrument/timeframe combination.

    The connection permits cross-thread use because storage writers may
    consume observation iterators in a worker thread. A lock ensures
    that SQLite operations remain serialized.
    """

    def __init__(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="atlas-quant-validation-"
        )
        self._database_path = (
            Path(self._temporary_directory.name)
            / "timestamps.sqlite3"
        )
        self._lock = threading.RLock()
        self._closed = False

        try:
            self._connection = sqlite3.connect(
                self._database_path,
                check_same_thread=False,
            )

            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA temp_store = FILE")
            self._connection.execute("PRAGMA cache_size = -2048")

            self._connection.executescript(
                """
                CREATE TABLE seen_timestamps (
                    series_key TEXT NOT NULL,
                    timestamp_us INTEGER NOT NULL,
                    PRIMARY KEY (series_key, timestamp_us)
                ) WITHOUT ROWID;

                CREATE TABLE latest_timestamps (
                    series_key TEXT PRIMARY KEY,
                    timestamp_us INTEGER NOT NULL
                ) WITHOUT ROWID;
                """
            )

        except BaseException:
            if hasattr(self, "_connection"):
                self._connection.close()

            self._temporary_directory.cleanup()
            raise

    @property
    def database_path(self) -> Path:
        """Return the temporary database path for diagnostics."""

        return self._database_path

    def observe(
        self,
        series_key: str,
        timestamp: datetime,
    ) -> TimestampCheck:
        """Record a timestamp and detect duplicates or ordering defects.

        A duplicate does not also produce an out-of-order result.
        The latest timestamp advances only when a new timestamp is
        later than the previously observed latest timestamp.
        """

        if not isinstance(series_key, str) or not series_key:
            raise ValueError("series_key must be a non-empty string.")

        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp must be a datetime value.")

        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")

        timestamp_us = self._to_utc_microseconds(timestamp)

        with self._lock:
            self._ensure_open()

            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO seen_timestamps (
                    series_key,
                    timestamp_us
                )
                VALUES (?, ?)
                """,
                (series_key, timestamp_us),
            )

            if cursor.rowcount == 0:
                return TimestampCheck(
                    is_duplicate=True,
                    is_out_of_order=False,
                )

            latest = self._connection.execute(
                """
                SELECT timestamp_us
                FROM latest_timestamps
                WHERE series_key = ?
                """,
                (series_key,),
            ).fetchone()

            if latest is None:
                self._connection.execute(
                    """
                    INSERT INTO latest_timestamps (
                        series_key,
                        timestamp_us
                    )
                    VALUES (?, ?)
                    """,
                    (series_key, timestamp_us),
                )

                return TimestampCheck(
                    is_duplicate=False,
                    is_out_of_order=False,
                )

            latest_timestamp_us = latest[0]
            is_out_of_order = timestamp_us < latest_timestamp_us

            if timestamp_us > latest_timestamp_us:
                self._connection.execute(
                    """
                    UPDATE latest_timestamps
                    SET timestamp_us = ?
                    WHERE series_key = ?
                    """,
                    (timestamp_us, series_key),
                )

            return TimestampCheck(
                is_duplicate=False,
                is_out_of_order=is_out_of_order,
            )

    @staticmethod
    def _to_utc_microseconds(timestamp: datetime) -> int:
        """Convert an aware timestamp to an exact UTC integer."""

        normalized = timestamp.astimezone(timezone.utc)
        difference = normalized - _UNIX_EPOCH

        return (
            difference.days * _MICROSECONDS_PER_DAY
            + difference.seconds * _MICROSECONDS_PER_SECOND
            + difference.microseconds
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "The incremental timestamp index is closed."
            )

    def close(self) -> None:
        """Close the database and remove its temporary files."""

        with self._lock:
            if self._closed:
                return

            self._closed = True

            try:
                self._connection.close()
            finally:
                self._temporary_directory.cleanup()

    def __enter__(self) -> "IncrementalTimestampIndex":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> None:
        self.close()