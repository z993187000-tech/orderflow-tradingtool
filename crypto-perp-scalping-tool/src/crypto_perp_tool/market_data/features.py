from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from crypto_perp_tool.market_data.events import TradeEvent


@dataclass(frozen=True)
class AggressionBubble:
    timestamp: int
    symbol: str
    price: float
    quantity: float
    side: str
    tier: str

    @property
    def label(self) -> str:
        prefix = "BLOCK" if self.tier == "block" else "BIG"
        return f"{prefix} {self.side.upper()} {self.quantity:.2f}"


class AggressionBubbleDetector:
    def __init__(
        self,
        large_threshold: float = 20.0,
        block_threshold: float = 50.0,
        dynamic_enabled: bool = False,
        percentile_large: float = 0.95,
        percentile_block: float = 0.99,
        half_life_ms: int = 24 * 60 * 60 * 1000,
    ) -> None:
        if large_threshold <= 0:
            raise ValueError("large_threshold must be positive")
        if block_threshold < large_threshold:
            raise ValueError("block_threshold must be greater than or equal to large_threshold")
        self.large_threshold = float(large_threshold)
        self.block_threshold = float(block_threshold)
        self.dynamic_enabled = dynamic_enabled
        self._percentile_large = percentile_large
        self._percentile_block = percentile_block
        if dynamic_enabled:
            from crypto_perp_tool.market_data.distribution import TradeSizeDistribution
            self._distribution = TradeSizeDistribution(half_life_ms=half_life_ms)
        else:
            self._distribution = None

    def detect(self, event: TradeEvent) -> AggressionBubble | None:
        if self._distribution is not None:
            self._distribution.add(event.quantity, event.timestamp)
            large_t = self.large_threshold
            block_t = self.block_threshold
            if not self._distribution.is_cold_start():
                large_t = max(self._distribution.percentile(self._percentile_large), self.large_threshold)
                block_t = max(self._distribution.percentile(self._percentile_block), self.block_threshold)
        else:
            large_t = self.large_threshold
            block_t = self.block_threshold

        if event.quantity < large_t:
            return None
        side = "sell" if event.is_buyer_maker else "buy"
        tier = "block" if event.quantity >= block_t else "large"
        return AggressionBubble(
            timestamp=event.timestamp,
            symbol=event.symbol,
            price=event.price,
            quantity=event.quantity,
            side=side,
            tier=tier,
        )


@dataclass
class _OhlcBar:
    start_time: int
    high: float
    low: float
    close: float

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price


class AtrTracker:
    def __init__(self, bar_ms: int, period: int = 14) -> None:
        if bar_ms <= 0:
            raise ValueError("bar_ms must be positive")
        if period <= 0:
            raise ValueError("period must be positive")
        self.bar_ms = int(bar_ms)
        self.period = int(period)
        self._current_bar: _OhlcBar | None = None
        self._previous_close: float | None = None
        self._true_ranges: deque[float] = deque(maxlen=self.period)
        self.latest_atr = 0.0

    def update(self, event: TradeEvent) -> float:
        bar_start = int(event.timestamp) - (int(event.timestamp) % self.bar_ms)
        if self._current_bar is None:
            self._current_bar = _OhlcBar(bar_start, event.price, event.price, event.price)
            return self.latest_atr

        if bar_start == self._current_bar.start_time:
            self._current_bar.update(event.price)
            return self.latest_atr

        if bar_start > self._current_bar.start_time:
            self._finalize_current_bar()
            self._current_bar = _OhlcBar(bar_start, event.price, event.price, event.price)
            return self.latest_atr

        return self.latest_atr

    def _finalize_current_bar(self) -> None:
        if self._current_bar is None:
            return
        high_low = self._current_bar.high - self._current_bar.low
        if self._previous_close is None:
            true_range = high_low
        else:
            true_range = max(
                high_low,
                abs(self._current_bar.high - self._previous_close),
                abs(self._current_bar.low - self._previous_close),
            )
        self._true_ranges.append(true_range)
        self._previous_close = self._current_bar.close
        self.latest_atr = sum(self._true_ranges) / len(self._true_ranges)
