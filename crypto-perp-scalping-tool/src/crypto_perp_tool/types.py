import time
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


class CircuitBreakerReason(StrEnum):
    DAILY_LOSS_LIMIT = "daily_loss_limit_reached"
    MAX_CONSECUTIVE_LOSSES = "max_consecutive_losses_reached"
    WEBSOCKET_STALE = "websocket_stale"
    ORDER_PROTECTION_MISSING = "order_protection_missing"
    POSITION_MISMATCH = "position_mismatch"
    EXCHANGE_API_FAILURE = "exchange_api_failure"


@dataclass(frozen=True)
class HistoricalWindows:
    delta_15s: tuple[float, ...] = ()
    delta_30s: tuple[float, ...] = ()
    delta_60s: tuple[float, ...] = ()
    volume_30s: tuple[float, ...] = ()
    spread_5min: tuple[float, ...] = ()
    amplitude_1m: tuple[float, ...] = ()

    def mean_delta_30s(self) -> float:
        if not self.delta_30s:
            return 0.0
        return sum(self.delta_30s) / len(self.delta_30s)

    def mean_volume_30s(self) -> float:
        if not self.volume_30s:
            return 0.0
        return sum(self.volume_30s) / len(self.volume_30s)

    def median_spread_5min(self) -> float:
        if not self.spread_5min:
            return 0.0
        return sorted(self.spread_5min)[len(self.spread_5min) // 2]

    def mean_amplitude_1m(self) -> float:
        if not self.amplitude_1m:
            return 0.0
        return sum(self.amplitude_1m) / len(self.amplitude_1m)

    def with_window(self, field: str, value: float, max_len: int = 20) -> "HistoricalWindows":
        current = getattr(self, field)
        new_vals = (*current[-max_len + 1:], value) if len(current) >= max_len else (*current, value)
        return self._replace(**{field: new_vals})

    def _replace(self, **kwargs) -> "HistoricalWindows":
        return HistoricalWindows(
            delta_15s=kwargs.get("delta_15s", self.delta_15s),
            delta_30s=kwargs.get("delta_30s", self.delta_30s),
            delta_60s=kwargs.get("delta_60s", self.delta_60s),
            volume_30s=kwargs.get("volume_30s", self.volume_30s),
            spread_5min=kwargs.get("spread_5min", self.spread_5min),
            amplitude_1m=kwargs.get("amplitude_1m", self.amplitude_1m),
        )


@dataclass(frozen=True)
class MarketDataHealth:
    connection_status: str = "starting"
    last_event_time: int = 0
    last_local_time: int = 0
    latency_ms: int = 0
    reconnect_count: int = 0
    symbol: str = ""

    def is_stale(self, websocket_stale_ms: int = 1500, max_data_lag_ms: int = 2000) -> bool:
        now = int(time.time() * 1000)
        if self.last_event_time > 0 and now - self.last_event_time > websocket_stale_ms:
            return True
        if self.latency_ms > max_data_lag_ms:
            return True
        return False


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
