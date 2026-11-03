from enum import StrEnum


class AssetClass(StrEnum):
    """Supported financial asset classes."""

    FOREX = "forex"
    EQUITY = "equity"
    CRYPTO = "crypto"
    FUTURE = "future"
    OPTION = "option"
    BOND = "bond"
    INDEX = "index"
    COMMODITY = "commodity"
    OTHER = "other"


class Timeframe(StrEnum):
    """Supported historical market data timeframes."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"

    H1 = "1h"
    H4 = "4h"

    D1 = "1d"
    W1 = "1w"
    MN1 = "1mo"