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
