from __future__ import annotations

from collections import deque

from crypto_perp_tool.session import Session
from crypto_perp_tool.types import (
    HistoricalWindows,
    MarketDataHealth,
    MarketSnapshot,
    ProfileLevel,
    ProfileLevelType,
    SignalSide,
    TradeSignal,
)

_TREND_SETUPS = frozenset({
    "vah_breakout_lvn_pullback_aggression",
    "val_breakdown_lvn_pullback_aggression",
    "cvd_divergence_failed_breakout",
    "cvd_divergence_failed_breakdown",
    "hvn_vah_failed_breakout",
    "hvn_val_failed_breakdown",
})

_MEAN_REVERSION_SETUPS = frozenset({
    "lvn_acceptance",
    "lvn_breakdown",
})


class SignalEngine:
    def __init__(self, min_reward_risk: float = 1.2, max_data_lag_ms: int = 2000,
                 session_gating_enabled: bool = True) -> None:
        self.min_reward_risk = min_reward_risk
        self.max_data_lag_ms = max_data_lag_ms
        self.session_gating_enabled = session_gating_enabled
        self._price_memory: deque[tuple[int, float] | tuple[int, float, float]] = deque(maxlen=120)
        self.last_reject_reasons: tuple[str, ...] = ()

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
            self.last_reject_reasons = tuple(forbidden)
            return None

        self._price_memory.append((snapshot.local_time, snapshot.last_price, snapshot.cumulative_delta))

        gated = self._session_allows_setup
        signal = (
            (self._setup_vah_breakout_lvn_pullback_aggression(snapshot) if gated(snapshot, "vah_breakout_lvn_pullback_aggression") else None)
            or (self._setup_val_breakdown_lvn_pullback_aggression(snapshot) if gated(snapshot, "val_breakdown_lvn_pullback_aggression") else None)
            or (self._setup_cvd_divergence_failed_breakout(snapshot) if gated(snapshot, "cvd_divergence_failed_breakout") else None)
            or (self._setup_cvd_divergence_failed_breakdown(snapshot) if gated(snapshot, "cvd_divergence_failed_breakdown") else None)
            or (self._setup_lvn_acceptance(snapshot, windows) if gated(snapshot, "lvn_acceptance") else None)
            or (self._setup_lvn_breakdown(snapshot, windows) if gated(snapshot, "lvn_breakdown") else None)
            or (self._setup_hvn_val_failed_breakdown(snapshot) if gated(snapshot, "hvn_val_failed_breakdown") else None)
            or (self._setup_hvn_vah_failed_breakout(snapshot) if gated(snapshot, "hvn_vah_failed_breakout") else None)
        )
        self.last_reject_reasons = () if signal is not None else ("no_setup",)
        return signal

    def _session_allows_setup(self, snapshot: MarketSnapshot, setup_name: str) -> bool:
        if not self.session_gating_enabled:
            return True
        try:
            session = Session(snapshot.session)
        except ValueError:
            return True
        if setup_name in _TREND_SETUPS and session in (Session.ASIA, Session.DEAD):
            return False
        if setup_name in _MEAN_REVERSION_SETUPS and session == Session.NY:
            return False
        return True

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

        if snapshot.exchange_lag_ms > self.max_data_lag_ms:
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
        recent = [(ts, p) for ts, p, _ in self._memory_items() if ts >= cutoff_ms]
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
        recent = [(ts, p) for ts, p, _ in self._memory_items() if ts >= cutoff_ms]
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

    # --- Setup C: VAH breakout -> LVN pullback -> buy aggression bubble ---

    def _setup_vah_breakout_lvn_pullback_aggression(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        vah = self._nearest_level(snapshot, ProfileLevelType.VAH)
        lvn = self._nearest_level(snapshot, ProfileLevelType.LVN)
        if vah is None or lvn is None:
            return None
        if lvn.price <= vah.price:
            return None
        if not (lvn.lower_bound <= snapshot.last_price <= lvn.upper_bound):
            return None
        if not self._recent_price_crossed(snapshot, vah.upper_bound, above=True):
            return None
        if snapshot.aggression_bubble_side != "buy" or snapshot.aggression_bubble_tier is None:
            return None
        if snapshot.delta_30s <= 0:
            return None

        target = self._target_level(snapshot, above=True)
        if target is None:
            return None

        atr = self._dynamic_atr(snapshot)
        bubble_price = snapshot.aggression_bubble_price or snapshot.last_price
        stop = min(lvn.lower_bound, bubble_price - 0.25 * atr)
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-vah-lvn-bubble-long",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup="vah_breakout_lvn_pullback_aggression",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.72,
            reasons=("VAH breakout accepted", "LVN pullback", "buy aggression bubble", "ATR dynamic stop"),
            invalidation_rules=("price falls back below LVN", "buy aggression disappears"),
            created_at=snapshot.local_time,
        )

    # --- Setup C: VAL breakdown -> LVN pullback -> sell aggression bubble ---

    def _setup_val_breakdown_lvn_pullback_aggression(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        val = self._nearest_level(snapshot, ProfileLevelType.VAL)
        lvn = self._nearest_level(snapshot, ProfileLevelType.LVN)
        if val is None or lvn is None:
            return None
        if lvn.price >= val.price:
            return None
        if not (lvn.lower_bound <= snapshot.last_price <= lvn.upper_bound):
            return None
        if not self._recent_price_crossed(snapshot, val.lower_bound, above=False):
            return None
        if snapshot.aggression_bubble_side != "sell" or snapshot.aggression_bubble_tier is None:
            return None
        if snapshot.delta_30s >= 0:
            return None

        target = self._target_level(snapshot, above=False)
        if target is None:
            return None

        atr = self._dynamic_atr(snapshot)
        bubble_price = snapshot.aggression_bubble_price or snapshot.last_price
        stop = max(lvn.upper_bound, bubble_price + 0.25 * atr)
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-val-lvn-bubble-short",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup="val_breakdown_lvn_pullback_aggression",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.72,
            reasons=("VAL breakdown accepted", "LVN pullback", "sell aggression bubble", "ATR dynamic stop"),
            invalidation_rules=("price reclaims LVN", "sell aggression disappears"),
            created_at=snapshot.local_time,
        )

    # --- Setup D: bearish CVD divergence failed breakout ---

    def _setup_cvd_divergence_failed_breakout(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        vah = self._nearest_level(snapshot, ProfileLevelType.VAH)
        if vah is None:
            return None
        if snapshot.last_price >= vah.price:
            return None
        recent = self._recent_memory(snapshot, 90_000)
        if len(recent) < 3:
            return None
        if not self._has_bearish_cvd_divergence(recent, vah.upper_bound):
            return None
        if snapshot.delta_30s >= 0:
            return None

        target = self._poc_target(snapshot, above=False) or self._target_level(snapshot, above=False)
        if target is None:
            return None

        atr = self._dynamic_atr(snapshot)
        high = max(price for _, price, _ in recent)
        stop = max(vah.upper_bound, high + 0.25 * atr)
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-cvd-failed-breakout-short",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup="cvd_divergence_failed_breakout",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.68,
            reasons=("bearish CVD divergence", "failed breakout back inside value", f"target at {target.type.value}"),
            invalidation_rules=("price reclaims breakout high", "CVD makes a new high"),
            created_at=snapshot.local_time,
        )

    # --- Setup D: bullish CVD divergence failed breakdown ---

    def _setup_cvd_divergence_failed_breakdown(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        val = self._nearest_level(snapshot, ProfileLevelType.VAL)
        if val is None:
            return None
        if snapshot.last_price <= val.price:
            return None
        recent = self._recent_memory(snapshot, 90_000)
        if len(recent) < 3:
            return None
        if not self._has_bullish_cvd_divergence(recent, val.lower_bound):
            return None
        if snapshot.delta_30s <= 0:
            return None

        target = self._poc_target(snapshot, above=True) or self._target_level(snapshot, above=True)
        if target is None:
            return None

        atr = self._dynamic_atr(snapshot)
        low = min(price for _, price, _ in recent)
        stop = min(val.lower_bound, low - 0.25 * atr)
        if self._reward_risk(snapshot.last_price, stop, target.price, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-cvd-failed-breakdown-long",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup="cvd_divergence_failed_breakdown",
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target.price,
            confidence=0.68,
            reasons=("bullish CVD divergence", "failed breakdown back inside value", f"target at {target.type.value}"),
            invalidation_rules=("price loses breakdown low", "CVD makes a new low"),
            created_at=snapshot.local_time,
        )

    # --- helpers ---

    def _memory_items(self) -> list[tuple[int, float, float]]:
        items: list[tuple[int, float, float]] = []
        for item in self._price_memory:
            if len(item) == 2:
                timestamp, price = item
                items.append((timestamp, price, 0.0))
            else:
                timestamp, price, cumulative_delta = item
                items.append((timestamp, price, cumulative_delta))
        return items

    def _recent_memory(self, snapshot: MarketSnapshot, window_ms: int) -> list[tuple[int, float, float]]:
        cutoff_ms = snapshot.local_time - window_ms
        return [(ts, price, cvd) for ts, price, cvd in self._memory_items() if ts >= cutoff_ms]

    def _recent_price_crossed(self, snapshot: MarketSnapshot, level: float, above: bool) -> bool:
        recent = self._recent_memory(snapshot, 90_000)
        if above:
            return any(price > level for _, price, _ in recent)
        return any(price < level for _, price, _ in recent)

    def _has_bearish_cvd_divergence(self, recent: list[tuple[int, float, float]], break_level: float) -> bool:
        above = [(price, cvd) for _, price, cvd in recent if price > break_level]
        if len(above) < 2:
            return False
        high_index = max(range(len(above)), key=lambda index: above[index][0])
        if high_index == 0:
            return False
        prior_cvd_high = max(cvd for _, cvd in above[:high_index])
        return above[high_index][1] <= prior_cvd_high

    def _has_bullish_cvd_divergence(self, recent: list[tuple[int, float, float]], break_level: float) -> bool:
        below = [(price, cvd) for _, price, cvd in recent if price < break_level]
        if len(below) < 2:
            return False
        low_index = min(range(len(below)), key=lambda index: below[index][0])
        if low_index == 0:
            return False
        prior_cvd_low = min(cvd for _, cvd in below[:low_index])
        return below[low_index][1] >= prior_cvd_low

    def _dynamic_atr(self, snapshot: MarketSnapshot) -> float:
        values = [value for value in (snapshot.atr_1m_14, snapshot.atr_3m_14) if value > 0]
        if values:
            return sum(values) / len(values)
        return max(snapshot.last_price * 0.0015, 1e-8)

    def _poc_target(self, snapshot: MarketSnapshot, above: bool) -> ProfileLevel | None:
        candidates = [level for level in snapshot.profile_levels if level.type == ProfileLevelType.POC]
        if above:
            candidates = [level for level in candidates if level.price > snapshot.last_price]
            return min(candidates, key=lambda level: level.price, default=None)
        candidates = [level for level in candidates if level.price < snapshot.last_price]
        return max(candidates, key=lambda level: level.price, default=None)

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
