from __future__ import annotations

import math


class TradeSizeDistribution:
    def __init__(
        self,
        half_life_ms: int = 24 * 60 * 60 * 1000,
        bin_count: int = 30,
        min_edge: float = 0.001,
    ) -> None:
        self.half_life_ms = half_life_ms
        self._decay_lambda = math.log(2) / half_life_ms
        self._edges = [min_edge * (2 ** i) for i in range(bin_count)]
        self._bins = [0.0] * (bin_count - 1)
        self._total = 0.0
        self._last_update_ms: int | None = None
        self._observation_count = 0

    def add(self, quantity: float, timestamp_ms: int) -> None:
        if self._last_update_ms is not None:
            self._decay(timestamp_ms)
        self._last_update_ms = timestamp_ms
        idx = self._bin_index(quantity)
        if idx is not None:
            self._bins[idx] += 1.0
            self._total += 1.0
            self._observation_count += 1

    def percentile(self, p: float) -> float:
        if self._total <= 0:
            return 0.0
        target = max(1.0, math.floor(self._total * (1.0 - p)))
        cumulative = 0.0
        for i in range(len(self._bins) - 1, -1, -1):
            cumulative += self._bins[i]
            if cumulative >= target:
                return self._edges[i]
        return self._edges[0]

    def is_cold_start(self) -> bool:
        return self._observation_count < 100

    def _decay(self, now_ms: int) -> None:
        if self._last_update_ms is None:
            return
        dt = now_ms - self._last_update_ms
        if dt <= 0:
            return
        factor = math.exp(-self._decay_lambda * dt)
        for i in range(len(self._bins)):
            self._bins[i] *= factor
        self._total *= factor

    def _bin_index(self, quantity: float) -> int | None:
        if quantity < self._edges[0]:
            return None
        for i in range(len(self._edges) - 1):
            if self._edges[i] <= quantity < self._edges[i + 1]:
                return i
        return len(self._bins) - 1
