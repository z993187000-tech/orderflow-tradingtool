from __future__ import annotations

from collections import deque
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class TimeWindowBuffer(Generic[T]):
    def __init__(self, max_window_ms: int) -> None:
        if max_window_ms <= 0:
            raise ValueError("max_window_ms must be positive")
        self.max_window_ms = max_window_ms
        self._items: deque[tuple[int, T]] = deque()
        self.latest_timestamp = 0

    def append(self, timestamp: int, item: T) -> None:
        self.latest_timestamp = max(self.latest_timestamp, int(timestamp))
        self._items.append((int(timestamp), item))
        self._evict(self.latest_timestamp)

    def items(self) -> list[T]:
        return [item for _, item in self._items]

    def timed_items(self) -> list[tuple[int, T]]:
        return list(self._items)

    def items_since(self, now_ms: int, window_ms: int) -> list[T]:
        cutoff = int(now_ms) - int(window_ms)
        return [item for timestamp, item in self._items if timestamp >= cutoff and timestamp <= int(now_ms)]

    def count_since(self, now_ms: int, window_ms: int) -> int:
        return len(self.items_since(now_ms, window_ms))

    def sum_since(self, now_ms: int, window_ms: int, selector: Callable[[T], float]) -> float:
        return sum(selector(item) for item in self.items_since(now_ms, window_ms))

    def _evict(self, now_ms: int) -> None:
        cutoff = int(now_ms) - self.max_window_ms
        if not self._items:
            return
        self._items = deque((timestamp, item) for timestamp, item in self._items if timestamp >= cutoff)
