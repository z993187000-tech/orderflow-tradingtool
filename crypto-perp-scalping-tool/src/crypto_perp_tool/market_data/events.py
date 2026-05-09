from dataclasses import dataclass


@dataclass(frozen=True)
class TradeEvent:
    timestamp: int
    symbol: str
    price: float
    quantity: float
    is_buyer_maker: bool

    @property
    def delta(self) -> float:
        return -self.quantity if self.is_buyer_maker else self.quantity


@dataclass(frozen=True)
class QuoteEvent:
    timestamp: int
    symbol: str
    bid_price: float
    ask_price: float

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2
