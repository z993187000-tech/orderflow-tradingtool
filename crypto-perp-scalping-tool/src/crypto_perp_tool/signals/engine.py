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

    # --- forbidden conditions ---

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
            if median > 0 and snapshot.spread_bps > median * 2.0:
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

    # --- Setup A: LVN acceptance (Long) ---

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

    # --- Setup A: LVN breakdown (Short) ---

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

    # --- Setup B: HVN/VAL failed breakdown recovery (Long) ---

    def _setup_hvn_val_failed_breakdown(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        level = self._nearest_level_of_types(snapshot, {ProfileLevelType.VAL, ProfileLevelType.HVN})
        if level is None:
            return None
        if snapshot.last_price <= level.lower_bound:
            return None

        cutoff_ms = snapshot.local_time - 60_000
        recent = [(ts, p) for ts, p in self._price_memory if ts >= cutoff_ms]
        if len(recent) < 3:
            return None

        dipped = any(p < level.lower_bound for _, p in recent)
        if not dipped:
            return None

        if snapshot.last_price <= level.price:
            return None

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

    # --- Setup B: HVN/VAH failed breakout recovery (Short) ---

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

    # --- helpers ---

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
