from __future__ import annotations

from collections import deque


class FlashCrashDetector:
    def __init__(
        self,
        price_window_ms: int = 2_000,
        liquidation_window_ms: int = 1_000,
        max_price_points: int = 10,
        max_liquidation_points: int = 50,
        atr_multiplier: float = 5.0,
        min_liquidation_count: int = 3,
        pct_threshold: float = 0.01,
    ) -> None:
        self.price_window_ms = price_window_ms
        self.liquidation_window_ms = liquidation_window_ms
        self.atr_multiplier = atr_multiplier
        self.min_liquidation_count = min_liquidation_count
        self.pct_threshold = pct_threshold
        self._prices: deque[tuple[int, float]] = deque(maxlen=max_price_points)
        self._liquidations: deque[tuple[int, float]] = deque(maxlen=max_liquidation_points)
        self._latest_price: float | None = None

    def add_price(self, timestamp_ms: int, price: float) -> None:
        self._prices.append((timestamp_ms, price))
        self._latest_price = price

    def add_liquidation(self, timestamp_ms: int, quantity: float) -> None:
        self._liquidations.append((timestamp_ms, quantity))

    def detect(self, now_ms: int, atr_1m: float) -> bool:
        if self._latest_price is None or self._latest_price <= 0:
            return False
        if atr_1m <= 0:
            atr_1m = self._latest_price * 0.001

        change_1s = self._price_change_over(now_ms, 1_000)
        liq_count_1s = self._liquidation_count_over(now_ms, 1_000)

        if abs(change_1s) / atr_1m > self.atr_multiplier and liq_count_1s > self.min_liquidation_count:
            return True
        if abs(change_1s) > self._latest_price * self.pct_threshold:
            return True
        return False

    def _price_change_over(self, now_ms: int, window_ms: int) -> float:
        cutoff = now_ms - window_ms
        oldest = None
        newest = None
        for ts, price in self._prices:
            if ts >= cutoff:
                if oldest is None:
                    oldest = price
                newest = price
        if oldest is not None and newest is not None:
            return newest - oldest
        return 0.0

    def _liquidation_count_over(self, now_ms: int, window_ms: int) -> int:
        cutoff = now_ms - window_ms
        return sum(1 for ts, _ in self._liquidations if ts >= cutoff)
