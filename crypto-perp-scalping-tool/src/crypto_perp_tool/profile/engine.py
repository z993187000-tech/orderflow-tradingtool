from __future__ import annotations

import math
import time
from collections import defaultdict

from crypto_perp_tool.types import ProfileLevel, ProfileLevelType


def _utc_midnight_ms() -> int:
    now_s = time.time()
    midnight_s = now_s - (now_s % 86400)
    return int(midnight_s * 1000)


class VolumeProfileEngine:
    def __init__(self, bin_size: float, value_area_ratio: float = 0.70) -> None:
        if bin_size <= 0:
            raise ValueError("bin_size must be positive")
        if not 0 < value_area_ratio <= 1:
            raise ValueError("value_area_ratio must be in (0, 1]")
        self.bin_size = bin_size
        self.value_area_ratio = value_area_ratio
        self._trades: list[tuple[float, float, int]] = []
        self.session_high: float | None = None
        self.session_low: float | None = None
        self._reference_ms: int | None = None

    def add_trade(self, price: float, quantity: float, timestamp: int = 0) -> None:
        if quantity <= 0:
            return
        if timestamp == 0:
            timestamp = int(time.time() * 1000)
        self._trades.append((price, quantity, timestamp))
        self._reference_ms = max(self._reference_ms or 0, timestamp)
        if self.session_high is None or price > self.session_high:
            self.session_high = price
        if self.session_low is None or price < self.session_low:
            self.session_low = price

    def prune(self, cutoff_ms: int) -> None:
        """Remove trades older than cutoff_ms and recalculate extremes."""
        self._trades = [(p, q, ts) for p, q, ts in self._trades if ts >= cutoff_ms]
        if self._trades:
            prices = [p for p, _, _ in self._trades]
            self.session_high = max(prices)
            self.session_low = min(prices)
        else:
            self.session_high = None
            self.session_low = None

    def _evict_before(self, cutoff_ms: int) -> None:
        self._trades = [(p, q, ts) for p, q, ts in self._trades if ts >= cutoff_ms]
        if self._trades:
            prices = [p for p, _, _ in self._trades]
            self.session_high = max(prices)
            self.session_low = min(prices)
        else:
            self.session_high = None
            self.session_low = None

    def _window_cutoff(self, window: str) -> int:
        # Use reference timestamp when available (backtest mode), else wall clock
        now_ms = self._reference_ms or int(time.time() * 1000)
        if window == "session":
            return _utc_midnight_ms()
        if window == "rolling_4h":
            return now_ms - 4 * 3600 * 1000
        return 0

    def _volume_by_bin(self, window: str) -> dict[float, float]:
        cutoff = self._window_cutoff(window)
        bins: dict[float, float] = defaultdict(float)
        for price, quantity, ts in self._trades:
            if ts >= cutoff:
                bins[self._bin_price(price)] += quantity
        return bins

    def levels(self, window: str = "rolling_4h") -> tuple[ProfileLevel, ...]:
        volumes = self._volume_by_bin(window)
        if not volumes:
            return ()

        bins = sorted(volumes)
        total_volume = sum(volumes.values())
        average_volume = total_volume / len(volumes)
        poc_price = max(bins, key=lambda price: volumes[price])
        levels = [
            self._level(ProfileLevelType.POC, poc_price, volumes[poc_price] / average_volume, window)
        ]

        val_bin, vah_bin = self._value_area_bounds(bins, volumes, poc_price, total_volume)
        levels.append(self._boundary_level(ProfileLevelType.VAL, val_bin, "lower", volumes[val_bin] / average_volume, window))
        levels.append(self._boundary_level(ProfileLevelType.VAH, vah_bin, "upper", volumes[vah_bin] / average_volume, window))

        for index, price in enumerate(bins):
            left = volumes[bins[index - 1]] if index > 0 else None
            right = volumes[bins[index + 1]] if index < len(bins) - 1 else None
            if left is None or right is None:
                continue
            volume = volumes[price]
            ratio = volume / average_volume
            if volume > left and volume > right and ratio >= 1.25 and price != poc_price:
                levels.append(self._level(ProfileLevelType.HVN, price, ratio, window))
            if volume < left and volume < right and ratio <= 0.55:
                levels.append(self._level(ProfileLevelType.LVN, price, ratio, window))

        return tuple(levels)

    def _bin_price(self, price: float) -> float:
        return math.floor(price / self.bin_size) * self.bin_size

    def _level(self, level_type: ProfileLevelType, price: float, strength: float, window: str) -> ProfileLevel:
        return ProfileLevel(
            type=level_type,
            price=price,
            lower_bound=price,
            upper_bound=price + self.bin_size,
            strength=strength,
            window=window,
        )

    def _boundary_level(self, level_type: ProfileLevelType, bin_price: float, side: str, strength: float, window: str) -> ProfileLevel:
        price = bin_price if side == "lower" else bin_price + self.bin_size
        return ProfileLevel(
            type=level_type,
            price=price,
            lower_bound=bin_price,
            upper_bound=bin_price + self.bin_size,
            strength=strength,
            window=window,
        )

    def _value_area_bounds(self, bins: list[float], volumes: dict[float, float], poc_price: float, total_volume: float) -> tuple[float, float]:
        target_volume = total_volume * self.value_area_ratio
        included = {poc_price}
        included_volume = volumes[poc_price]
        poc_index = bins.index(poc_price)
        lower_index = poc_index
        upper_index = poc_index
        while included_volume < target_volume and (lower_index > 0 or upper_index < len(bins) - 1):
            lower_candidate = bins[lower_index - 1] if lower_index > 0 else None
            upper_candidate = bins[upper_index + 1] if upper_index < len(bins) - 1 else None
            lower_volume = volumes[lower_candidate] if lower_candidate is not None else -1
            upper_volume = volumes[upper_candidate] if upper_candidate is not None else -1
            if upper_volume >= lower_volume:
                upper_index += 1
                price = bins[upper_index]
            else:
                lower_index -= 1
                price = bins[lower_index]
            included.add(price)
            included_volume += volumes[price]
        return min(included), max(included)
