from __future__ import annotations

import threading
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
        self._lock = threading.Lock()

    def append(self, timestamp: int, item: T) -> list[T]:
        with self._lock:
            self.latest_timestamp = max(self.latest_timestamp, int(timestamp))
            self._items.append((int(timestamp), item))
            return self._evict(self.latest_timestamp)

    def items(self) -> list[T]:
        with self._lock:
            return [item for _, item in self._items]

    def timed_items(self) -> list[tuple[int, T]]:
        with self._lock:
            return list(self._items)

    def items_since(self, now_ms: int, window_ms: int) -> list[T]:
        with self._lock:
            cutoff = int(now_ms) - int(window_ms)
            return [item for timestamp, item in self._items if timestamp >= cutoff and timestamp <= int(now_ms)]

    def count_since(self, now_ms: int, window_ms: int) -> int:
        with self._lock:
            cutoff = int(now_ms) - int(window_ms)
            count = 0
            for timestamp, _ in reversed(self._items):
                if timestamp < cutoff:
                    break
                if timestamp <= int(now_ms):
                    count += 1
            return count

    def sum_since(self, now_ms: int, window_ms: int, selector: Callable[[T], float]) -> float:
        with self._lock:
            cutoff = int(now_ms) - int(window_ms)
            total = 0.0
            for timestamp, item in reversed(self._items):
                if timestamp < cutoff:
                    break
                if timestamp <= int(now_ms):
                    total += selector(item)
            return total

    def _evict(self, now_ms: int) -> list[T]:
        cutoff = int(now_ms) - self.max_window_ms
        evicted: list[T] = []
        if not self._items:
            return evicted
        while self._items and self._items[0][0] < cutoff:
            _, item = self._items.popleft()
            evicted.append(item)
        if self._items and self._items[-1][0] < cutoff:
            kept: deque[tuple[int, T]] = deque()
            for timestamp, item in self._items:
                if timestamp >= cutoff:
                    kept.append((timestamp, item))
                else:
                    evicted.append(item)
            self._items = kept
        return evicted
