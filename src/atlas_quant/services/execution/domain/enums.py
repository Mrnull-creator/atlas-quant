from enum import StrEnum


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"


class OrderStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class LedgerEntryType(StrEnum):
    TRADE = "trade"
    COMMISSION = "commission"
    ADJUSTMENT = "adjustment"