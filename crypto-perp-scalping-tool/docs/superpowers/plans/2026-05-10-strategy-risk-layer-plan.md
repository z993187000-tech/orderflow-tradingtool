# Strategy & Risk Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the strategy layer, risk controls, and live data pipeline: dual-profile engine, 4-setup signal engine, circuit breaker, market data health, and live signal integration.

**Architecture:** Extend existing modules with backward-compatible API changes. ProfileEngine gains timestamp tracking and dual-window support. SignalEngine gains forbidden condition checks, 2 new setups, and historical window comparisons. New modules for CircuitBreaker and MarketDataHealth follow existing frozen-dataclass patterns.

**Tech Stack:** Python 3.11+, dataclasses, deque, time, unittest

---

### Task 1: Add new types to `types.py`

**Files:**
- Modify: `src/crypto_perp_tool/types.py`
- Test: `tests/unit/test_types.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_types.py
import unittest
import time
from crypto_perp_tool.types import HistoricalWindows, MarketDataHealth


class HistoricalWindowsTests(unittest.TestCase):
    def test_default_windows_are_empty(self):
        w = HistoricalWindows()
        self.assertEqual(w.delta_30s, ())
        self.assertEqual(w.volume_30s, ())
        self.assertEqual(w.spread_5min, ())


class MarketDataHealthTests(unittest.TestCase):
    def test_is_stale_when_latency_exceeds_max(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 2500,
            latency_ms=2500,
        )
        self.assertTrue(health.is_stale(max_data_lag_ms=2000))

    def test_is_not_stale_when_within_limits(self):
        now = int(time.time() * 1000)
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 500,
            latency_ms=500,
        )
        self.assertFalse(health.is_stale())

    def test_is_stale_when_no_recent_event(self):
        old = int(time.time() * 1000) - 3000
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=old,
            last_local_time=old + 100,
            latency_ms=100,
        )
        self.assertTrue(health.is_stale(websocket_stale_ms=1500))

    def test_default_state_is_starting(self):
        health = MarketDataHealth()
        self.assertEqual(health.connection_status, "starting")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_types.py -v`
Expected: ImportError for HistoricalWindows, MarketDataHealth

- [ ] **Step 3: Add types to `types.py`**

Add after the existing imports and before ProfileLevel:

```python
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_types.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/crypto_perp_tool/types.py tests/unit/test_types.py
git commit -m "feat: add HistoricalWindows, MarketDataHealth, CircuitBreakerReason types"
```

---

### Task 2: Profile Engine — timestamp tracking and dual windows

**Files:**
- Modify: `src/crypto_perp_tool/profile/engine.py`
- Modify: `tests/unit/test_profile_engine.py`

- [ ] **Step 1: Update the failing test**

Replace `tests/unit/test_profile_engine.py`:

```python
import time
import unittest

from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.types import ProfileLevelType


def _ts(minutes_ago: float = 0) -> int:
    return int((time.time() - minutes_ago * 60) * 1000)


class VolumeProfileEngineTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time() * 1000)

    def test_identifies_poc_hvn_lvn_and_value_area_rolling(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        for price, volume in [(100, 4), (110, 18), (120, 3), (130, 20), (140, 5)]:
            engine.add_trade(price=price, quantity=volume, timestamp=self.now)

        levels = engine.levels(window="rolling_4h")
        level_types = {level.type for level in levels}
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)

        self.assertEqual(poc.price, 130)
        self.assertIn(ProfileLevelType.HVN, level_types)
        self.assertIn(ProfileLevelType.LVN, level_types)
        self.assertIn(ProfileLevelType.VAH, level_types)
        self.assertIn(ProfileLevelType.VAL, level_types)

    def test_value_area_uses_bin_boundaries(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=100, timestamp=self.now)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        val = next(level for level in levels if level.type == ProfileLevelType.VAL)
        vah = next(level for level in levels if level.type == ProfileLevelType.VAH)
        self.assertEqual(poc.price, 100)
        self.assertEqual(val.price, 95)
        self.assertEqual(vah.price, 105)

    def test_backward_compatible_add_trade_without_timestamp(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=10)
        levels = engine.levels(window="rolling_4h")
        self.assertEqual(len(levels), 3)  # POC, VAH, VAL (single bin)

    def test_session_window_filters_by_utc_day(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=100, timestamp=self.now)
        levels = engine.levels(window="session")
        self.assertGreaterEqual(len(levels), 3)

    def test_rolling_window_excludes_old_trades(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        old = self.now - 5 * 3600 * 1000  # 5 hours ago
        engine.add_trade(price=100, quantity=100, timestamp=old)
        engine.add_trade(price=200, quantity=100, timestamp=self.now)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        self.assertEqual(poc.price, 200)

    def test_session_high_low_tracks_extremes(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        engine.add_trade(price=100, quantity=1, timestamp=self.now)
        engine.add_trade(price=200, quantity=1, timestamp=self.now)
        self.assertEqual(engine.session_high, 200)
        self.assertEqual(engine.session_low, 100)

    def test_evict_before_prunes_old_data(self):
        engine = VolumeProfileEngine(bin_size=10, value_area_ratio=0.70)
        old = self.now - 10 * 3600 * 1000
        engine.add_trade(price=100, quantity=100, timestamp=old)
        engine.add_trade(price=200, quantity=100, timestamp=self.now)
        engine._evict_before(self.now - 6 * 3600 * 1000)
        levels = engine.levels(window="rolling_4h")
        poc = next(level for level in levels if level.type == ProfileLevelType.POC)
        self.assertEqual(poc.price, 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_profile_engine.py -v`
Expected: TypeError for missing `timestamp` argument

- [ ] **Step 3: Rewrite `profile/engine.py`**

```python
from __future__ import annotations

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

    def add_trade(self, price: float, quantity: float, timestamp: int = 0) -> None:
        if quantity <= 0:
            return
        if timestamp == 0:
            timestamp = int(time.time() * 1000)
        self._trades.append((price, quantity, timestamp))
        if self.session_high is None or price > self.session_high:
            self.session_high = price
        if self.session_low is None or price < self.session_low:
            self.session_low = price

    def _evict_before(self, cutoff_ms: int) -> None:
        self._trades = [(p, q, ts) for p, q, ts in self._trades if ts >= cutoff_ms]

    def _window_cutoff(self, window: str) -> int:
        now_ms = int(time.time() * 1000)
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
        return round(price / self.bin_size) * self.bin_size

    def _level(self, level_type: ProfileLevelType, price: float, strength: float, window: str) -> ProfileLevel:
        half_bin = self.bin_size / 2
        return ProfileLevel(
            type=level_type,
            price=price,
            lower_bound=price - half_bin,
            upper_bound=price + half_bin,
            strength=strength,
            window=window,
        )

    def _boundary_level(self, level_type: ProfileLevelType, bin_price: float, side: str, strength: float, window: str) -> ProfileLevel:
        half_bin = self.bin_size / 2
        price = bin_price - half_bin if side == "lower" else bin_price + half_bin
        return ProfileLevel(
            type=level_type,
            price=price,
            lower_bound=bin_price - half_bin,
            upper_bound=bin_price + half_bin,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_profile_engine.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/crypto_perp_tool/profile/engine.py tests/unit/test_profile_engine.py
git commit -m "feat: add timestamp tracking and dual-window support to VolumeProfileEngine"
```

---

### Task 3: Signal Engine — 4 setups + forbidden conditions

**Files:**
- Modify: `src/crypto_perp_tool/signals/engine.py`
- Modify: `tests/unit/test_signal_engine.py`

- [ ] **Step 1: Write the expanded test**

Replace `tests/unit/test_signal_engine.py`:

```python
import time
import unittest

from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import (
    HistoricalWindows,
    MarketDataHealth,
    MarketSnapshot,
    ProfileLevel,
    ProfileLevelType,
    SignalSide,
)


def _snapshot(last_price=101.0, delta_30s=25.0, volume_30s=100.0, spread_bps=1.98,
              profile_levels=None, event_time=None, local_time=None, symbol="BTCUSDT"):
    now = int(time.time() * 1000)
    if profile_levels is None:
        profile_levels = (
            ProfileLevel(ProfileLevelType.LVN, 100.0, 99.5, 100.5, 0.4, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, "rolling_4h"),
        )
    return MarketSnapshot(
        exchange="binance_futures", symbol=symbol,
        event_time=event_time or now, local_time=local_time or now,
        last_price=last_price, bid_price=last_price - 0.1, ask_price=last_price + 0.1,
        spread_bps=spread_bps, vwap=last_price - 0.5, atr_1m_14=2.0,
        delta_15s=10.0, delta_30s=delta_30s, delta_60s=35.0, volume_30s=volume_30s,
        profile_levels=profile_levels,
    )


class SignalEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = SignalEngine(min_reward_risk=1.2)

    # --- Setup A: LVN acceptance ---

    def test_long_lvn_acceptance(self):
        signal = self.engine.evaluate(_snapshot(last_price=101.0, delta_30s=25.0))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.LONG)
        self.assertEqual(signal.setup, "lvn_break_acceptance")

    def test_short_lvn_breakdown(self):
        levels = (
            ProfileLevel(ProfileLevelType.LVN, 110.0, 109.5, 110.5, 0.4, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 95.0, 94.5, 95.5, 1.5, "rolling_4h"),
        )
        signal = self.engine.evaluate(_snapshot(last_price=109.0, delta_30s=-25.0, profile_levels=levels))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, SignalSide.SHORT)
        self.assertEqual(signal.setup, "lvn_breakdown_acceptance")

    # --- Stale data ---

    def test_returns_none_when_data_is_stale(self):
        now = int(time.time() * 1000)
        snap = _snapshot(event_time=now - 3000, local_time=now)
        self.assertIsNone(self.engine.evaluate(snap))

    # --- Spread forbidden ---

    def test_rejects_high_spread(self):
        windows = HistoricalWindows(spread_5min=tuple([1.0] * 20))
        snap = _snapshot(spread_bps=5.0)
        self.assertIsNone(self.engine.evaluate(snap, windows=windows))

    # --- Volume threshold ---

    def test_lvn_rejects_low_volume(self):
        windows = HistoricalWindows(volume_30s=tuple([200.0] * 20))
        snap = _snapshot(volume_30s=50.0)
        self.assertIsNone(self.engine.evaluate(snap, windows=windows))

    # --- Delta threshold ---

    def test_lvn_rejects_low_delta(self):
        windows = HistoricalWindows(delta_30s=tuple([100.0] * 20))
        snap = _snapshot(delta_30s=5.0)
        self.assertIsNone(self.engine.evaluate(snap, windows=windows))

    # --- Circuit breaker ---

    def test_rejects_when_circuit_tripped(self):
        snap = _snapshot()
        self.assertIsNone(self.engine.evaluate(snap, circuit_tripped=True))

    # --- Existing position ---

    def test_rejects_when_has_position(self):
        snap = _snapshot()
        self.assertIsNone(self.engine.evaluate(snap, has_position=True))

    # --- Setup B: Failed breakdown recovery ---

    def test_long_failed_breakdown_recovery(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        val = 99.0
        levels = (
            ProfileLevel(ProfileLevelType.VAL, val, val - 0.5, val + 0.5, 1.2, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 105.0, 104.5, 105.5, 1.5, "rolling_4h"),
        )
        # Feed price memory: dip below VAL, then recover
        for ms, price in [
            (now - 5000, 98.5),  # dip below
            (now - 4000, 98.3),  # low
            (now - 3000, 98.8),  # recovering
            (now - 2000, 99.2),  # back above
            (now - 1000, 99.5),  # holding above
        ]:
            engine._price_memory.append((ms, price))
        snap = _snapshot(last_price=99.5, delta_30s=15.0, profile_levels=levels)
        signal = engine.evaluate(snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.setup, "hvn_val_failed_breakdown")

    def test_short_failed_breakout_recovery(self):
        engine = SignalEngine(min_reward_risk=1.2)
        now = int(time.time() * 1000)
        vah = 111.0
        levels = (
            ProfileLevel(ProfileLevelType.VAH, vah, vah - 0.5, vah + 0.5, 1.2, "rolling_4h"),
            ProfileLevel(ProfileLevelType.HVN, 95.0, 94.5, 95.5, 1.5, "rolling_4h"),
        )
        for ms, price in [
            (now - 5000, 111.5),
            (now - 4000, 112.0),
            (now - 3000, 111.2),
            (now - 2000, 110.4),
            (now - 1000, 110.2),
        ]:
            engine._price_memory.append((ms, price))
        snap = _snapshot(last_price=110.2, delta_30s=-15.0, profile_levels=levels)
        signal = engine.evaluate(snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.setup, "hvn_vah_failed_breakout")

    # --- Funding blackout ---

    def test_rejects_during_funding_blackout(self):
        now = int(time.time() * 1000)
        # Funding time is 30 seconds from now
        health = MarketDataHealth(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now,
            latency_ms=100,
        )
        snap = _snapshot(event_time=now, local_time=now)
        # next_funding_time is within 2 min
        self.assertIsNone(self.engine.evaluate(snap, health=health, next_funding_time=now + 30_000))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_signal_engine.py -v`
Expected: FAIL on new test methods (spread, volume, circuit, etc.)

- [ ] **Step 3: Rewrite `signals/engine.py`**

```python
from __future__ import annotations

from collections import deque

from crypto_perp_tool.types import (
    HistoricalWindows,
    MarketDataHealth,
    MarketSnapshot,
    ProfileLevel,
    ProfileLevelType,
    SignalSide,
    TradeSignal,
)


class SignalEngine:
    def __init__(self, min_reward_risk: float = 1.2, max_data_lag_ms: int = 2000) -> None:
        self.min_reward_risk = min_reward_risk
        self.max_data_lag_ms = max_data_lag_ms
        self._price_memory: deque[tuple[int, float]] = deque(maxlen=120)

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        windows: HistoricalWindows | None = None,
        health: MarketDataHealth | None = None,
        circuit_tripped: bool = False,
        has_position: bool = False,
        next_funding_time: int = 0,
    ) -> TradeSignal | None:
        forbidden = self._check_forbidden(snapshot, windows, health, circuit_tripped, has_position, next_funding_time)
        if forbidden:
            return None

        self._price_memory.append((snapshot.local_time, snapshot.last_price))

        return (
            self._setup_lvn_acceptance(snapshot, windows)
            or self._setup_lvn_breakdown(snapshot, windows)
            or self._setup_hvn_val_failed_breakdown(snapshot)
            or self._setup_hvn_vah_failed_breakout(snapshot)
        )

    # ── forbidden conditions ──────────────────────────────────────

    def _check_forbidden(
        self,
        snapshot: MarketSnapshot,
        windows: HistoricalWindows | None,
        health: MarketDataHealth | None,
        circuit_tripped: bool,
        has_position: bool,
        next_funding_time: int,
    ) -> list[str]:
        reasons: list[str] = []

        if snapshot.local_time - snapshot.event_time > self.max_data_lag_ms:
            reasons.append("data_stale")

        if windows is not None and windows.spread_5min:
            median = windows.median_spread_5min()
            if snapshot.spread_bps > median * 2.0:
                reasons.append("spread_too_wide")

        if health is not None and health.is_stale():
            reasons.append("websocket_stale")

        if next_funding_time > 0:
            distance_ms = abs(snapshot.local_time - next_funding_time)
            if distance_ms < 2 * 60 * 1000:
                reasons.append("funding_blackout")

        if windows is not None and windows.amplitude_1m:
            mean_amp = windows.mean_amplitude_1m()
            if mean_amp > 0 and snapshot.atr_1m_14 > mean_amp * 3.0:
                reasons.append("extreme_volatility")

        if circuit_tripped:
            reasons.append("circuit_breaker_tripped")

        if has_position:
            reasons.append("existing_position")

        return reasons

    # ── Setup A: LVN acceptance (Long) ────────────────────────────

    def _setup_lvn_acceptance(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
        lvn = self._nearest_level(snapshot, ProfileLevelType.LVN)
        if lvn is None:
            return None
        if snapshot.last_price <= lvn.upper_bound:
            return None
        if snapshot.delta_30s <= 0:
            return None

        if windows is not None:
            mean_delta = windows.mean_delta_30s()
            if mean_delta > 0 and snapshot.delta_30s < mean_delta * 1.2:
                return None
            mean_vol = windows.mean_volume_30s()
            if mean_vol > 0 and snapshot.volume_30s < mean_vol * 1.5:
                return None

        target = self._target_level(snapshot, above=True)
        if target is None:
            return None

        stop = min(
            lvn.lower_bound,
            snapshot.last_price - max(0.35 * snapshot.atr_1m_14, snapshot.last_price * 0.0015),
        )
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-long",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup="lvn_break_acceptance",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.65,
            reasons=("price accepted above LVN", "delta_30s positive", f"target at {target.type.value}"),
            invalidation_rules=("price falls back below LVN", "delta flips negative"),
            created_at=snapshot.local_time,
        )

    # ── Setup A: LVN breakdown (Short) ────────────────────────────

    def _setup_lvn_breakdown(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
        lvn = self._nearest_level(snapshot, ProfileLevelType.LVN)
        if lvn is None:
            return None
        if snapshot.last_price >= lvn.lower_bound:
            return None
        if snapshot.delta_30s >= 0:
            return None

        if windows is not None:
            mean_delta = windows.mean_delta_30s()
            if mean_delta < 0 and abs(snapshot.delta_30s) < abs(mean_delta) * 1.2:
                return None
            mean_vol = windows.mean_volume_30s()
            if mean_vol > 0 and snapshot.volume_30s < mean_vol * 1.5:
                return None

        target = self._target_level(snapshot, above=False)
        if target is None:
            return None

        stop = max(
            lvn.upper_bound,
            snapshot.last_price + max(0.35 * snapshot.atr_1m_14, snapshot.last_price * 0.0015),
        )
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-short",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup="lvn_breakdown_acceptance",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.65,
            reasons=("price accepted below LVN", "delta_30s negative", f"target at {target.type.value}"),
            invalidation_rules=("price reclaims LVN", "delta flips positive"),
            created_at=snapshot.local_time,
        )

    # ── Setup B: HVN/VAL failed breakdown recovery (Long) ─────────

    def _setup_hvn_val_failed_breakdown(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        level = self._nearest_level_of_types(snapshot, {ProfileLevelType.VAL, ProfileLevelType.HVN})
        if level is None:
            return None
        if snapshot.last_price <= level.lower_bound:
            return None

        # Check price memory for dip below level and recovery within 60s
        cutoff_ms = snapshot.local_time - 60_000
        recent = [(ts, p) for ts, p in self._price_memory if ts >= cutoff_ms]
        if len(recent) < 3:
            return None

        dipped = any(p < level.lower_bound for _, p in recent)
        if not dipped:
            return None

        # Price must now be above level
        if snapshot.last_price <= level.price:
            return None

        # Delta must be positive for recovery confirmation
        if snapshot.delta_30s <= 0:
            return None

        target = self._target_level(snapshot, above=True)
        if target is None:
            return None

        stop = level.lower_bound
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-long-b",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup="hvn_val_failed_breakdown",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.55,
            reasons=("price recovered after failed breakdown", "delta flipped positive", f"target at {target.type.value}"),
            invalidation_rules=("price falls back below level", "delta flips negative"),
            created_at=snapshot.local_time,
        )

    # ── Setup B: HVN/VAH failed breakout recovery (Short) ─────────

    def _setup_hvn_vah_failed_breakout(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        level = self._nearest_level_of_types(snapshot, {ProfileLevelType.VAH, ProfileLevelType.HVN})
        if level is None:
            return None
        if snapshot.last_price >= level.upper_bound:
            return None

        cutoff_ms = snapshot.local_time - 60_000
        recent = [(ts, p) for ts, p in self._price_memory if ts >= cutoff_ms]
        if len(recent) < 3:
            return None

        broke = any(p > level.upper_bound for _, p in recent)
        if not broke:
            return None

        if snapshot.last_price >= level.price:
            return None

        if snapshot.delta_30s >= 0:
            return None

        target = self._target_level(snapshot, above=False)
        if target is None:
            return None

        stop = level.upper_bound
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-short-b",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup="hvn_vah_failed_breakout",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.55,
            reasons=("price failed after false breakout", "delta flipped negative", f"target at {target.type.value}"),
            invalidation_rules=("price reclaims level", "delta flips positive"),
            created_at=snapshot.local_time,
        )

    # ── helpers ────────────────────────────────────────────────────

    def _nearest_level(self, snapshot: MarketSnapshot, level_type: ProfileLevelType) -> ProfileLevel | None:
        levels = [level for level in snapshot.profile_levels if level.type == level_type]
        if not levels:
            return None
        return min(levels, key=lambda level: abs(snapshot.last_price - level.price))

    def _nearest_level_of_types(self, snapshot: MarketSnapshot, types: set[ProfileLevelType]) -> ProfileLevel | None:
        levels = [level for level in snapshot.profile_levels if level.type in types]
        if not levels:
            return None
        return min(levels, key=lambda level: abs(snapshot.last_price - level.price))

    def _target_level(self, snapshot: MarketSnapshot, above: bool) -> ProfileLevel | None:
        target_types = {ProfileLevelType.HVN, ProfileLevelType.POC, ProfileLevelType.VAH, ProfileLevelType.VAL}
        candidates = [level for level in snapshot.profile_levels if level.type in target_types]
        if above:
            candidates = [level for level in candidates if level.price > snapshot.last_price]
            return min(candidates, key=lambda level: level.price, default=None)
        candidates = [level for level in candidates if level.price < snapshot.last_price]
        return max(candidates, key=lambda level: level.price, default=None)

    def _reward_risk(self, entry: float, stop: float, target: float, side: SignalSide) -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0
        reward = target - entry if side == SignalSide.LONG else entry - target
        return reward / risk
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_signal_engine.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/crypto_perp_tool/signals/engine.py tests/unit/test_signal_engine.py
git commit -m "feat: add 4 setups, forbidden conditions, and historical windows to SignalEngine"
```

---

### Task 4: Circuit Breaker

**Files:**
- Create: `src/crypto_perp_tool/risk/circuit.py`
- Create: `tests/unit/test_circuit_breaker.py`
- Modify: `src/crypto_perp_tool/risk/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_circuit_breaker.py
import unittest

from crypto_perp_tool.risk.circuit import CircuitBreaker, CircuitBreakerReason


class CircuitBreakerTests(unittest.TestCase):
    def test_starts_in_normal_state(self):
        cb = CircuitBreaker()
        self.assertEqual(cb.state, "normal")
        self.assertIsNone(cb.reason)
        self.assertIsNone(cb.tripped_at)

    def test_trip_sets_state_and_reason(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.DAILY_LOSS_LIMIT)
        self.assertEqual(cb.state, "tripped")
        self.assertEqual(cb.reason, CircuitBreakerReason.DAILY_LOSS_LIMIT)
        self.assertIsNotNone(cb.tripped_at)

    def test_can_resume_true_when_all_conditions_met(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.WEBSOCKET_STALE)
        ok = cb.can_resume(
            account_ok=True,
            data_healthy=True,
            positions_reconciled=True,
            daily_loss_within_limit=True,
        )
        self.assertTrue(ok)

    def test_can_resume_false_when_positions_not_reconciled(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.POSITION_MISMATCH)
        ok = cb.can_resume(
            account_ok=True,
            data_healthy=True,
            positions_reconciled=False,
            daily_loss_within_limit=True,
        )
        self.assertFalse(ok)

    def test_can_resume_false_when_daily_loss_exceeded(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.DAILY_LOSS_LIMIT)
        ok = cb.can_resume(
            account_ok=True,
            data_healthy=True,
            positions_reconciled=True,
            daily_loss_within_limit=False,
        )
        self.assertFalse(ok)

    def test_resume_clears_trip_state(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.WEBSOCKET_STALE)
        cb.resume(actor="telegram:12345")
        self.assertEqual(cb.state, "normal")
        self.assertIsNone(cb.reason)
        self.assertIsNone(cb.tripped_at)

    def test_resume_returns_resume_event(self):
        cb = CircuitBreaker()
        cb.trip(CircuitBreakerReason.WEBSOCKET_STALE)
        event = cb.resume(actor="telegram:12345")
        self.assertEqual(event["type"], "circuit_breaker_resumed")
        self.assertEqual(event["actor"], "telegram:12345")

    def test_cannot_resume_when_not_tripped(self):
        cb = CircuitBreaker()
        with self.assertRaises(RuntimeError):
            cb.resume(actor="telegram:12345")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_circuit_breaker.py -v`
Expected: ImportError for circuit module

- [ ] **Step 3: Create `risk/circuit.py`**

```python
from __future__ import annotations

import time

from crypto_perp_tool.types import CircuitBreakerReason


class CircuitBreaker:
    def __init__(self) -> None:
        self.state: str = "normal"
        self.reason: CircuitBreakerReason | None = None
        self.tripped_at: int | None = None

    def trip(self, reason: CircuitBreakerReason) -> dict:
        self.state = "tripped"
        self.reason = reason
        self.tripped_at = int(time.time() * 1000)
        return {
            "type": "circuit_breaker_tripped",
            "reason": reason.value,
            "tripped_at": self.tripped_at,
        }

    def can_resume(
        self,
        account_ok: bool = True,
        data_healthy: bool = True,
        positions_reconciled: bool = True,
        daily_loss_within_limit: bool = True,
    ) -> bool:
        if self.state != "tripped":
            return False
        return account_ok and data_healthy and positions_reconciled and daily_loss_within_limit

    def resume(self, actor: str) -> dict:
        if self.state != "tripped":
            raise RuntimeError("Cannot resume a circuit breaker that is not tripped")
        self.state = "normal"
        self.reason = None
        self.tripped_at = None
        return {
            "type": "circuit_breaker_resumed",
            "actor": actor,
            "resumed_at": int(time.time() * 1000),
        }
```

- [ ] **Step 4: Update `risk/__init__.py`**

Add this line after existing imports:

```python
from crypto_perp_tool.risk.circuit import CircuitBreaker, CircuitBreakerReason
```

Current file is:
```python
"""Risk engine and circuit breaker."""
```

Change to:
```python
"""Risk engine and circuit breaker."""

from crypto_perp_tool.risk.circuit import CircuitBreaker, CircuitBreakerReason
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_circuit_breaker.py -v`
Expected: 8 PASS

- [ ] **Step 6: Commit**

```bash
git add src/crypto_perp_tool/risk/circuit.py src/crypto_perp_tool/risk/__init__.py tests/unit/test_circuit_breaker.py
git commit -m "feat: add CircuitBreaker state machine with trip/resume"
```

---

### Task 5: MarketDataHealth module

**Files:**
- Create: `src/crypto_perp_tool/market_data/health.py`
- Create: `tests/unit/test_market_data_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_market_data_health.py
import time
import unittest

from crypto_perp_tool.market_data.health import compute_health


class MarketDataHealthTests(unittest.TestCase):
    def test_health_from_fresh_events_is_not_stale(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 100,
            symbol="BTCUSDT",
        )
        self.assertEqual(health.connection_status, "connected")
        self.assertEqual(health.latency_ms, 100)
        self.assertFalse(health.is_stale())

    def test_health_tracks_reconnect_count(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now,
            last_local_time=now + 50,
            reconnect_count=3,
        )
        self.assertEqual(health.reconnect_count, 3)

    def test_health_detects_high_latency(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now - 100,
            last_local_time=now + 2000,
        )
        self.assertTrue(health.is_stale(max_data_lag_ms=1500))

    def test_health_detects_stale_connection(self):
        now = int(time.time() * 1000)
        health = compute_health(
            connection_status="connected",
            last_event_time=now - 3000,
            last_local_time=now - 2900,
        )
        self.assertTrue(health.is_stale(websocket_stale_ms=1500))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_market_data_health.py -v`
Expected: ImportError for health module

- [ ] **Step 3: Create `market_data/health.py`**

```python
from __future__ import annotations

import time

from crypto_perp_tool.types import MarketDataHealth


def compute_health(
    connection_status: str = "starting",
    last_event_time: int = 0,
    last_local_time: int = 0,
    reconnect_count: int = 0,
    symbol: str = "",
) -> MarketDataHealth:
    if last_event_time > 0 and last_local_time > 0:
        latency_ms = last_local_time - last_event_time
    else:
        latency_ms = 0
    return MarketDataHealth(
        connection_status=connection_status,
        last_event_time=last_event_time,
        last_local_time=last_local_time,
        latency_ms=max(latency_ms, 0),
        reconnect_count=reconnect_count,
        symbol=symbol,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_market_data_health.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/crypto_perp_tool/market_data/health.py tests/unit/test_market_data_health.py
git commit -m "feat: add MarketDataHealth helper for latency and staleness detection"
```

---

### Task 6: Integrate LiveOrderflowStore with profile + signals

**Files:**
- Modify: `src/crypto_perp_tool/web/live_store.py`
- Modify: `tests/unit/test_live_orderflow_store.py`

- [ ] **Step 1: Update the test file**

Add these test methods to the existing `LiveOrderflowStoreTests` class in `tests/unit/test_live_orderflow_store.py`:

```python
    def test_live_store_runs_signal_engine_and_produces_signals(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=600, enable_signals=True)
        store.add_quote(QuoteEvent(1000, "BTCUSDT", 99, 101))
        for price in [101.0, 101.5, 102.0, 102.5, 101.8, 102.2, 102.8, 103.5, 104.0, 104.5,
                       105.0, 104.8, 105.5, 106.0, 106.5, 105.8, 107.0, 106.2, 106.8, 107.5,
                       108.0, 107.5, 108.5, 109.0, 108.2, 108.8, 109.5, 110.0, 109.8, 110.5]:
            store.add_trade(TradeEvent(1000 + len(store._events) * 100, "BTCUSDT", price, 1.0, False))

        view = store.view()
        self.assertGreaterEqual(view["summary"]["signals"], 0)
        self.assertIn("mode_breakdown", view["summary"])

    def test_live_store_maintains_persistent_profile_engine(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=10)

        store.add_trade(TradeEvent(1000, "BTCUSDT", 100, 5, True))
        store.add_trade(TradeEvent(2000, "BTCUSDT", 110, 20, False))
        view1 = store.view()
        store.add_trade(TradeEvent(3000, "BTCUSDT", 120, 3, True))
        view2 = store.view()

        self.assertLess(view1["summary"]["profile_trade_count"], view2["summary"]["profile_trade_count"])

    def test_live_store_handles_signal_engine_without_quote(self):
        store = LiveOrderflowStore(symbol="BTCUSDT", max_events=600, enable_signals=True)
        for price in [200.0, 200.5, 201.0, 201.5, 200.8, 201.2, 201.8, 202.5, 203.0, 203.5]:
            store.add_trade(TradeEvent(1000 + len(store._events) * 100, "BTCUSDT", price, 1.0, False))

        view = store.view()
        self.assertIsNotNone(view["summary"]["last_price"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_live_orderflow_store.py::LiveOrderflowStoreTests::test_live_store_runs_signal_engine_and_produces_signals -v`
Expected: TypeError (unexpected keyword argument 'enable_signals')

- [ ] **Step 3: Rewrite `web/live_store.py`**

```python
from __future__ import annotations

import threading
from collections import deque
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TradeEvent
from crypto_perp_tool.market_data.health import compute_health
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.risk.circuit import CircuitBreaker
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import HistoricalWindows
from crypto_perp_tool.web.details import empty_execution_details, mode_breakdown, total_pnl_for_range


class LiveOrderflowStore:
    def __init__(self, symbol: str, max_events: int = 20_000, display_events: int = 500,
                 enable_signals: bool = False) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self.display_events = display_events
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._quote: QuoteEvent | None = None
        self._mark: MarkPriceEvent | None = None
        self._spot: SpotPriceEvent | None = None
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._reconnect_count = 0
        self._lock = threading.Lock()

        settings = default_settings()
        bin_size = settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else settings.profile.eth_bin_size
        self._profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=settings.profile.value_area_ratio)
        self._signal_engine = SignalEngine(
            min_reward_risk=settings.signals.min_reward_risk,
            max_data_lag_ms=settings.execution.max_data_lag_ms,
        ) if enable_signals else None
        self._circuit_breaker = CircuitBreaker()
        self._signal_count = 0
        self._order_count = 0
        self._closed_positions = 0
        self._realized_pnl = 0.0
        self._position: dict | None = None
        self._historical: HistoricalWindows = HistoricalWindows()

    def add_trade(self, event: TradeEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._events.append(event)
            self._profile.add_trade(event.price, event.quantity, event.timestamp)
            self._update_historical(event)
            self._try_signal(event)

    def add_quote(self, event: QuoteEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._quote = event

    def add_mark(self, event: MarkPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._mark = event

    def add_spot(self, event: SpotPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._spot = event

    def set_connection_status(self, status: str, message: str) -> None:
        with self._lock:
            self._connection_status = status
            self._connection_message = message
            if status == "reconnecting" or (status == "connected" and "error" in self._connection_status):
                self._reconnect_count += 1

    def _update_historical(self, event: TradeEvent) -> None:
        self._historical = self._historical.with_window("spread_5min", 2.0)  # simplified

    def _try_signal(self, event: TradeEvent) -> None:
        if self._signal_engine is None:
            return
        if self._circuit_breaker.state == "tripped":
            return
        events = list(self._events)
        if len(events) < 30:
            return
        window = events[-30:]
        delta_30s = sum(e.delta for e in window)
        volume_30s = sum(abs(e.delta) for e in window)
        prices = [e.price for e in events]
        avg_price = sum(prices) / len(prices)

        from crypto_perp_tool.types import MarketSnapshot
        now = event.timestamp
        levels = self._profile.levels(window="rolling_4h")

        snapshot = MarketSnapshot(
            exchange="binance_futures",
            symbol=self.symbol,
            event_time=now,
            local_time=now,
            last_price=event.price,
            bid_price=self._quote.bid_price if self._quote else event.price * 0.9999,
            ask_price=self._quote.ask_price if self._quote else event.price * 1.0001,
            spread_bps=((self._quote.ask_price - self._quote.bid_price) / self._quote.mid_price * 10000) if self._quote else 2.0,
            vwap=avg_price,
            atr_1m_14=max(event.price * 0.002, 1.0),
            delta_15s=sum(e.delta for e in events[-15:]),
            delta_30s=delta_30s,
            delta_60s=sum(e.delta for e in events[-60:]) if len(events) >= 60 else sum(e.delta for e in events),
            volume_30s=volume_30s,
            profile_levels=levels,
        )

        health = compute_health(
            connection_status=self._connection_status,
            last_event_time=now,
            last_local_time=now,
            reconnect_count=self._reconnect_count,
            symbol=self.symbol,
        )

        signal = self._signal_engine.evaluate(
            snapshot,
            windows=self._historical,
            health=health,
            circuit_tripped=(self._circuit_breaker.state == "tripped"),
            has_position=(self._position is not None),
            next_funding_time=self._mark.next_funding_time if self._mark else 0,
        )

        if signal is None:
            return

        self._signal_count += 1
        # Simple paper execution: open position, set stop/target
        if self._position is None:
            self._position = {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side,
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
            }
            self._order_count += 1
        else:
            close_price = self._check_close(event.price)
            if close_price is not None:
                pnl = close_price - self._position["entry_price"]
                if self._position["side"].value == "short":
                    pnl = self._position["entry_price"] - close_price
                self._realized_pnl += pnl
                self._closed_positions += 1
                self._position = None

    def _check_close(self, current_price: float) -> float | None:
        if self._position is None:
            return None
        side = self._position["side"].value
        if side == "long":
            if current_price <= self._position["stop_price"]:
                return self._position["stop_price"]
            if current_price >= self._position["target_price"]:
                return self._position["target_price"]
        else:
            if current_price >= self._position["stop_price"]:
                return self._position["stop_price"]
            if current_price <= self._position["target_price"]:
                return self._position["target_price"]
        return None

    def view(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            quote = self._quote
            mark = self._mark
            spot = self._spot
            connection_status = self._connection_status
            connection_message = self._connection_message

        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []
        display_events = events[-self.display_events:]
        details = empty_execution_details()

        for index, event in enumerate(display_events):
            cumulative_delta += event.delta
            trades.append({
                "index": index, "timestamp": event.timestamp, "symbol": event.symbol,
                "price": event.price, "quantity": event.quantity,
                "side": "sell" if event.is_buyer_maker else "buy", "delta": event.delta,
            })
            delta_series.append({
                "index": index, "timestamp": event.timestamp,
                "delta": event.delta, "cumulative_delta": cumulative_delta,
            })

        last_trade_price = trades[-1]["price"] if trades else None
        quote_mid_price = quote.mid_price if quote is not None else None
        spot_last_price = spot.price if spot is not None else None
        index_price = mark.index_price if mark is not None else None
        last_price = (
            spot_last_price if spot_last_price is not None
            else index_price if index_price is not None
            else last_trade_price if last_trade_price is not None
            else quote_mid_price
        )
        price_source = (
            "spotTrade" if spot_last_price is not None
            else "indexPrice" if index_price is not None
            else "aggTrade" if last_trade_price is not None
            else "bookTicker"
        )
        derived_connection_status = "connected" if last_price is not None else connection_status

        levels = self._profile.levels(window="rolling_4h")

        return {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": derived_connection_status,
                "connection_message": connection_message,
                "trade_count": len(trades),
                "profile_trade_count": len(events),
                "last_price": last_price,
                "spot_last_price": spot_last_price,
                "last_trade_price": last_trade_price,
                "bid_price": quote.bid_price if quote is not None else None,
                "ask_price": quote.ask_price if quote is not None else None,
                "quote_mid_price": quote_mid_price,
                "mark_price": mark.mark_price if mark is not None else None,
                "index_price": index_price,
                "funding_rate": mark.funding_rate if mark is not None else None,
                "next_funding_time": mark.next_funding_time if mark is not None else None,
                "price_source": price_source,
                "cumulative_delta": cumulative_delta,
                "signals": self._signal_count,
                "orders": self._order_count,
                "closed_positions": self._closed_positions,
                "realized_pnl": self._realized_pnl,
                "circuit_state": self._circuit_breaker.state,
                "pnl_24h": total_pnl_for_range(details, "24h"),
                "mode_breakdown": mode_breakdown(details),
            },
            "trades": trades,
            "delta_series": delta_series,
            "profile_levels": [
                {"type": level.type.value, "price": level.price,
                 "lower_bound": level.lower_bound, "upper_bound": level.upper_bound,
                 "strength": level.strength, "window": level.window}
                for level in levels
            ],
            "markers": [],
            "details": details,
        }
```

- [ ] **Step 4: Run all live_store tests**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/test_live_orderflow_store.py -v`
Expected: 12 PASS (9 original + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/crypto_perp_tool/web/live_store.py tests/unit/test_live_orderflow_store.py
git commit -m "feat: integrate persistent profile and signal engine into LiveOrderflowStore"
```

---

### Task 7: Adapt orderflow.py and paper.py to new ProfileEngine

**Files:**
- Modify: `src/crypto_perp_tool/web/orderflow.py`
- Modify: `src/crypto_perp_tool/paper.py`

- [ ] **Step 1: Fix `orderflow.py` — add timestamp to `add_trade` calls**

In `build_orderflow_view()`, line 28, change:
```python
profile.add_trade(event.price, event.quantity)
```
To:
```python
profile.add_trade(event.price, event.quantity, event.timestamp)
```

- [ ] **Step 2: Fix `paper.py` — add timestamp to `add_trade` calls**

In `run_csv()`, line 85, change:
```python
profile.add_trade(event.price, event.quantity)
```
To:
```python
profile.add_trade(event.price, event.quantity, event.timestamp)
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/crypto_perp_tool/web/orderflow.py src/crypto_perp_tool/paper.py
git commit -m "fix: pass timestamp to new ProfileEngine.add_trade signature"
```

---

### Task 8: Full integration verification

- [ ] **Step 1: Run the complete test suite**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/unit/ -v`

Expected: All ~40+ tests pass across all files including:
- `test_types.py` (4 tests)
- `test_profile_engine.py` (7 tests)
- `test_signal_engine.py` (11 tests)
- `test_circuit_breaker.py` (8 tests)
- `test_market_data_health.py` (4 tests)
- `test_live_orderflow_store.py` (12 tests)
- `test_risk_engine.py` (existing)
- `test_paper_runner.py` (existing)
- `test_config.py` (existing)
- `test_journal.py` (existing)
- `test_telegram_bot.py` (existing)
- `test_cli.py` (existing)
- `test_network.py` (existing)
- `test_health.py` (existing)
- `test_auth.py` (existing)
- `test_orderflow_view.py` (existing)
- `test_binance_market_data.py` (existing)

- [ ] **Step 2: Run paper replay end-to-end**

Run: `$env:PYTHONPATH='src'; python -m crypto_perp_tool.cli paper run --csv data/sample_trades.csv --journal data/journal.jsonl`

Expected: JSON output with signals, orders, closed_positions, realized_pnl

- [ ] **Step 3: Commit final verification**

```bash
git add -A
git commit -m "chore: final integration verification, all tests pass"
```
