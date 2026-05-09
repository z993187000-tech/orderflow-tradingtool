from dataclasses import dataclass
from enum import StrEnum


class ProfileLevelType(StrEnum):
    POC = "POC"
    HVN = "HVN"
    LVN = "LVN"
    VAH = "VAH"
    VAL = "VAL"


class ProfileWindow(StrEnum):
    SESSION = "session"
    ROLLING_4H = "rolling_4h"


class SignalSide(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


@dataclass(frozen=True)
class ProfileLevel:
    type: ProfileLevelType
    price: float
    lower_bound: float
    upper_bound: float
    strength: float
    window: str
    touched_at: int | None = None
    confluence: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketSnapshot:
    exchange: str
    symbol: str
    event_time: int
    local_time: int
    last_price: float
    bid_price: float
    ask_price: float
    spread_bps: float
    vwap: float
    atr_1m_14: float
    delta_15s: float
    delta_30s: float
    delta_60s: float
    volume_30s: float
    profile_levels: tuple[ProfileLevel, ...]
    session: str = "unknown"


@dataclass(frozen=True)
class TradeSignal:
    id: str
    symbol: str
    side: SignalSide
    setup: str
    entry_price: float
    stop_price: float
    target_price: float
    confidence: float
    reasons: tuple[str, ...]
    invalidation_rules: tuple[str, ...]
    created_at: int


@dataclass(frozen=True)
class RiskDecision:
    signal_id: str
    allowed: bool
    quantity: float
    max_slippage_bps: float
    remaining_daily_risk: float
    reject_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionOrder:
    client_order_id: str
    signal_id: str
    exchange: str
    symbol: str
    side: OrderSide
    type: OrderType
    quantity: float
    reduce_only: bool
    price: float | None = None
    stop_price: float | None = None
    time_in_force: str | None = None
