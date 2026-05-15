from dataclasses import dataclass

from crypto_perp_tool.market_data.latency import compute_exchange_lag_ms


@dataclass(frozen=True)
class TradeEvent:
    timestamp: int
    symbol: str
    price: float
    quantity: float
    is_buyer_maker: bool
    exchange_event_time: int | None = None

    @property
    def delta(self) -> float:
        return -self.quantity if self.is_buyer_maker else self.quantity

    @property
    def exchange_lag_ms(self) -> int:
        return compute_exchange_lag_ms(event_time=self.timestamp, exchange_event_time=self.exchange_event_time)


@dataclass(frozen=True)
class QuoteEvent:
    timestamp: int
    symbol: str
    bid_price: float
    ask_price: float
    bid_qty: float = 0.0
    ask_qty: float = 0.0

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2


@dataclass(frozen=True)
class MarkPriceEvent:
    timestamp: int
    symbol: str
    mark_price: float
    index_price: float
    funding_rate: float
    next_funding_time: int


@dataclass(frozen=True)
class SpotPriceEvent:
    timestamp: int
    symbol: str
    price: float


@dataclass(frozen=True)
class ForceOrderEvent:
    timestamp: int
    symbol: str
    price: float
    quantity: float
    side: str
    order_type: str = "LIQUIDATION"


@dataclass(frozen=True)
class KlineEvent:
    timestamp: int
    close_time: int
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    is_closed: bool
