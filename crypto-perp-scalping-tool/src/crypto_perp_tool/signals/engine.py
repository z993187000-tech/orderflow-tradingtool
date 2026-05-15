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


def _estimated_round_trip_cost(entry_price: float, taker_fee_rate: float = 0.0004) -> float:
    """Estimate round-trip cost: 2x taker fee + spread + slippage (~10 bps)."""
    return entry_price * (2.0 * taker_fee_rate + 0.0002)

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

_HIGH_EXTENSION_SETUPS = frozenset({
    "vah_breakout_lvn_pullback_aggression",
    "val_breakdown_lvn_pullback_aggression",
})

_FAILED_AUCTION_SETUPS = frozenset({
    "cvd_divergence_failed_breakout",
    "cvd_divergence_failed_breakdown",
    "hvn_vah_failed_breakout",
    "hvn_val_failed_breakdown",
})

_LOW_EXTENSION_SETUPS = frozenset({
    "lvn_break_acceptance",
    "lvn_breakdown_acceptance",
})


class SignalEngine:
    def __init__(self, min_reward_risk: float = 1.2, max_data_lag_ms: int = 2000,
                 session_gating_enabled: bool = True,
                 reward_risk: float = 5.0,
                 dynamic_reward_risk_enabled: bool = True,
                 reward_risk_min: float = 3.0,
                 reward_risk_max: float = 10.0,
                 atr_stop_mult: float = 0.35,
                 min_stop_cost_mult: float = 3.0,
                 min_target_cost_mult: float = 8.0,
                 taker_fee_rate: float = 0.0004,
                 execution_window: str = "execution_30m",
                 micro_window: str = "micro_15m",
                 context_window: str = "context_60m",
                 ) -> None:
        self.min_reward_risk = min_reward_risk
        self.max_data_lag_ms = max_data_lag_ms
        self.session_gating_enabled = session_gating_enabled
        self.reward_risk = reward_risk
        self.dynamic_reward_risk_enabled = dynamic_reward_risk_enabled
        self.reward_risk_min = reward_risk_min
        self.reward_risk_max = reward_risk_max
        self.atr_stop_mult = atr_stop_mult
        self.min_stop_cost_mult = min_stop_cost_mult
        self.min_target_cost_mult = min_target_cost_mult
        self._taker_fee_rate = taker_fee_rate
        self.execution_window = execution_window
        self.micro_window = micro_window
        self.context_window = context_window
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

        if not self._profile_levels_for_window(snapshot, self.execution_window):
            self.last_reject_reasons = ("execution_profile_insufficient",)
            return None

        gated = self._session_allows_setup
        candidate = (
            (self._setup_vah_breakout_lvn_pullback_aggression(snapshot, windows) if gated(snapshot, "vah_breakout_lvn_pullback_aggression") else None)
            or (self._setup_val_breakdown_lvn_pullback_aggression(snapshot, windows) if gated(snapshot, "val_breakdown_lvn_pullback_aggression") else None)
            or (self._setup_cvd_divergence_failed_breakout(snapshot, windows) if gated(snapshot, "cvd_divergence_failed_breakout") else None)
            or (self._setup_cvd_divergence_failed_breakdown(snapshot, windows) if gated(snapshot, "cvd_divergence_failed_breakdown") else None)
            or (self._setup_lvn_acceptance(snapshot, windows) if gated(snapshot, "lvn_acceptance") else None)
            or (self._setup_lvn_breakdown(snapshot, windows) if gated(snapshot, "lvn_breakdown") else None)
            or (self._setup_hvn_val_failed_breakdown(snapshot, windows) if gated(snapshot, "hvn_val_failed_breakdown") else None)
            or (self._setup_hvn_vah_failed_breakout(snapshot, windows) if gated(snapshot, "hvn_vah_failed_breakout") else None)
        )
        if candidate is None:
            self.last_reject_reasons = ("no_setup",)
            return None

        micro_reject = self._micro_confirm_reject_reason(snapshot, candidate)
        if micro_reject:
            self.last_reject_reasons = (micro_reject,)
            return None

        context_adjusted = self._apply_context_obstacle(snapshot, candidate)
        if context_adjusted is None:
            self.last_reject_reasons = ("context_reward_risk_too_low",)
            return None

        self.last_reject_reasons = ()
        return context_adjusted

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

        setup = "lvn_break_acceptance"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, lvn.lower_bound, SignalSide.LONG, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-long",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.65,
            reasons=self._with_target_reason(("price accepted above LVN", "delta_30s positive"), target_r),
            invalidation_rules=("price falls back below LVN", "delta flips negative"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
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

        setup = "lvn_breakdown_acceptance"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, lvn.upper_bound, SignalSide.SHORT, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-short",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.65,
            reasons=self._with_target_reason(("price accepted below LVN", "delta_30s negative"), target_r),
            invalidation_rules=("price reclaims LVN", "delta flips positive"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
        )

    # --- Setup B: HVN/VAL failed breakdown recovery (Long) ---

    def _setup_hvn_val_failed_breakdown(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
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

        setup = "hvn_val_failed_breakdown"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, level.lower_bound, SignalSide.LONG, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-long-b",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.55,
            reasons=self._with_target_reason(("price recovered after failed breakdown", "delta flipped positive"), target_r),
            invalidation_rules=("price falls back below level", "delta flips negative"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
        )

    # --- Setup B: HVN/VAH failed breakout recovery (Short) ---

    def _setup_hvn_vah_failed_breakout(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
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

        setup = "hvn_vah_failed_breakout"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, level.upper_bound, SignalSide.SHORT, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-short-b",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.55,
            reasons=self._with_target_reason(("price failed after false breakout", "delta flipped negative"), target_r),
            invalidation_rules=("price reclaims level", "delta flips positive"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
        )

    # --- Setup C: VAH breakout -> LVN pullback -> buy aggression bubble ---

    def _setup_vah_breakout_lvn_pullback_aggression(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
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

        setup = "vah_breakout_lvn_pullback_aggression"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, lvn.lower_bound, SignalSide.LONG, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-vah-lvn-bubble-long",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.72,
            reasons=self._with_target_reason(("VAH breakout accepted", "LVN pullback", "buy aggression bubble"), target_r),
            invalidation_rules=("price falls back below LVN", "buy aggression disappears"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
        )

    # --- Setup C: VAL breakdown -> LVN pullback -> sell aggression bubble ---

    def _setup_val_breakdown_lvn_pullback_aggression(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
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

        setup = "val_breakdown_lvn_pullback_aggression"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, lvn.upper_bound, SignalSide.SHORT, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-val-lvn-bubble-short",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.72,
            reasons=self._with_target_reason(("VAL breakdown accepted", "LVN pullback", "sell aggression bubble"), target_r),
            invalidation_rules=("price reclaims LVN", "sell aggression disappears"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
        )

    # --- Setup D: bearish CVD divergence failed breakout ---

    def _setup_cvd_divergence_failed_breakout(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
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

        high = max(price for _, price, _ in recent)
        structure_stop = max(vah.upper_bound, high)
        setup = "cvd_divergence_failed_breakout"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, structure_stop, SignalSide.SHORT, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.SHORT) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-cvd-failed-breakout-short",
            symbol=snapshot.symbol,
            side=SignalSide.SHORT,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.68,
            reasons=self._with_target_reason(("bearish CVD divergence", "failed breakout back inside value"), target_r),
            invalidation_rules=("price reclaims breakout high", "CVD makes a new high"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
        )

    # --- Setup D: bullish CVD divergence failed breakdown ---

    def _setup_cvd_divergence_failed_breakdown(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> TradeSignal | None:
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

        low = min(price for _, price, _ in recent)
        structure_stop = min(val.lower_bound, low)
        setup = "cvd_divergence_failed_breakdown"
        stop, target, target_r = self._adjust_stop_and_target(
            snapshot.last_price, structure_stop, SignalSide.LONG, snapshot, setup, windows,
        )
        if self._reward_risk(snapshot.last_price, stop, target, SignalSide.LONG) < self.min_reward_risk:
            return None

        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-cvd-failed-breakdown-long",
            symbol=snapshot.symbol,
            side=SignalSide.LONG,
            setup=setup,
            entry_price=snapshot.last_price,
            stop_price=stop,
            target_price=target,
            confidence=0.68,
            reasons=self._with_target_reason(("bullish CVD divergence", "failed breakdown back inside value"), target_r),
            invalidation_rules=("price loses breakdown low", "CVD makes a new low"),
            created_at=snapshot.local_time,
            target_r_multiple=target_r,
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
        candidates = [level for level in self._profile_levels_for_window(snapshot, self.execution_window) if level.type == ProfileLevelType.POC]
        if above:
            candidates = [level for level in candidates if level.price > snapshot.last_price]
            return min(candidates, key=lambda level: level.price, default=None)
        candidates = [level for level in candidates if level.price < snapshot.last_price]
        return max(candidates, key=lambda level: level.price, default=None)

    def _nearest_level(self, snapshot: MarketSnapshot, level_type: ProfileLevelType) -> ProfileLevel | None:
        levels = [level for level in self._profile_levels_for_window(snapshot, self.execution_window) if level.type == level_type]
        if not levels:
            return None
        return min(levels, key=lambda level: abs(snapshot.last_price - level.price))

    def _nearest_level_of_types(self, snapshot: MarketSnapshot, types: set[ProfileLevelType]) -> ProfileLevel | None:
        levels = [level for level in self._profile_levels_for_window(snapshot, self.execution_window) if level.type in types]
        if not levels:
            return None
        return min(levels, key=lambda level: abs(snapshot.last_price - level.price))

    def _target_level(self, snapshot: MarketSnapshot, above: bool) -> ProfileLevel | None:
        target_types = {ProfileLevelType.HVN, ProfileLevelType.POC, ProfileLevelType.VAH, ProfileLevelType.VAL}
        candidates = [level for level in self._profile_levels_for_window(snapshot, self.execution_window) if level.type in target_types]
        if above:
            candidates = [level for level in candidates if level.price > snapshot.last_price]
            return min(candidates, key=lambda level: level.price, default=None)
        candidates = [level for level in candidates if level.price < snapshot.last_price]
        return max(candidates, key=lambda level: level.price, default=None)

    def _profile_levels_for_window(self, snapshot: MarketSnapshot, window: str) -> list[ProfileLevel]:
        levels = [level for level in snapshot.profile_levels if level.window == window]
        if not levels and window == self.execution_window:
            levels = [level for level in snapshot.profile_levels if level.window == "rolling_4h"]
        return levels

    def _micro_confirm_reject_reason(self, snapshot: MarketSnapshot, signal: TradeSignal) -> str | None:
        micro_levels = [
            level for level in self._profile_levels_for_window(snapshot, self.micro_window)
            if level.type in {ProfileLevelType.VAH, ProfileLevelType.VAL, ProfileLevelType.HVN, ProfileLevelType.LVN}
        ]
        if not micro_levels:
            return "micro_profile_insufficient"
        if signal.side == SignalSide.LONG and snapshot.delta_15s <= 0:
            return "micro_delta_not_confirmed"
        if signal.side == SignalSide.SHORT and snapshot.delta_15s >= 0:
            return "micro_delta_not_confirmed"
        if not any(self._price_near_level(snapshot.last_price, level, snapshot) for level in micro_levels):
            return "micro_level_not_confirmed"
        return None

    def _price_near_level(self, price: float, level: ProfileLevel, snapshot: MarketSnapshot) -> bool:
        width = max(abs(level.upper_bound - level.lower_bound), 1e-8)
        tolerance = max(width, snapshot.atr_1m_14 * 0.25, price * 0.0002)
        return level.lower_bound - tolerance <= price <= level.upper_bound + tolerance

    def _with_target_reason(self, reasons: tuple[str, ...], target_r: float) -> tuple[str, ...]:
        target_reason = f"target {target_r:.1f}R"
        updated: list[str] = []
        replaced = False
        for reason in reasons:
            if reason.startswith("target ") and reason.endswith("R"):
                updated.append(target_reason)
                replaced = True
            else:
                updated.append(reason)
        if not replaced:
            updated.append(target_reason)
        return tuple(updated)

    def _clamp_reward_risk(self, value: float, upper_bound: float | None = None) -> float:
        lower = min(self.reward_risk_min, self.reward_risk_max)
        upper = max(self.reward_risk_min, self.reward_risk_max)
        if upper_bound is not None:
            upper = min(upper, max(lower, upper_bound))
        return min(max(value, lower), upper)

    def _setup_reward_risk_cap(self, setup: str) -> float:
        if setup in _HIGH_EXTENSION_SETUPS:
            return self.reward_risk_max
        if setup in _FAILED_AUCTION_SETUPS:
            return min(self.reward_risk_max, 7.0)
        if setup in _LOW_EXTENSION_SETUPS:
            return min(self.reward_risk_max, 6.0)
        return min(self.reward_risk_max, 6.0)

    def _entry_reward_risk(
        self,
        snapshot: MarketSnapshot,
        side: SignalSide,
        setup: str,
        windows: HistoricalWindows | None,
    ) -> float:
        if not self.dynamic_reward_risk_enabled:
            return self._clamp_reward_risk(self.reward_risk)

        score = 0.0
        if setup in _HIGH_EXTENSION_SETUPS:
            score += 0.35
        elif setup in _FAILED_AUCTION_SETUPS:
            score += 0.15

        if windows is not None:
            directional_delta = snapshot.delta_30s if side == SignalSide.LONG else -snapshot.delta_30s
            mean_delta = windows.mean_delta_30s()
            directional_mean = mean_delta if side == SignalSide.LONG else -mean_delta
            if directional_delta > 0 and directional_mean > 0:
                delta_ratio = directional_delta / max(abs(directional_mean), 1e-8)
                if delta_ratio >= 3.0:
                    score += 0.25
                elif delta_ratio >= 1.8:
                    score += 0.15

            mean_volume = windows.mean_volume_30s()
            if mean_volume > 0:
                volume_ratio = snapshot.volume_30s / mean_volume
                if volume_ratio >= 3.0:
                    score += 0.15
                elif volume_ratio >= 2.0:
                    score += 0.10

            median_spread = windows.median_spread_5min()
            if median_spread > 0:
                spread_ratio = snapshot.spread_bps / median_spread
                if spread_ratio <= 1.0:
                    score += 0.10
                elif spread_ratio > 1.5:
                    score -= 0.10

            mean_amplitude = windows.mean_amplitude_1m()
            if mean_amplitude > 0:
                atr_ratio = snapshot.atr_1m_14 / mean_amplitude
                if atr_ratio <= 1.5:
                    score += 0.05
                elif atr_ratio > 2.5:
                    score -= 0.15

        expected_bubble_side = "buy" if side == SignalSide.LONG else "sell"
        if snapshot.aggression_bubble_side == expected_bubble_side:
            if snapshot.aggression_bubble_tier == "block":
                score += 0.20
            elif snapshot.aggression_bubble_tier == "large":
                score += 0.12
        elif setup in _HIGH_EXTENSION_SETUPS:
            score -= 0.05

        try:
            session = Session(snapshot.session)
        except ValueError:
            session = None
        if session in (Session.LONDON, Session.NY) and setup in _HIGH_EXTENSION_SETUPS:
            score += 0.10
        elif session in (Session.ASIA, Session.DEAD) and setup in _HIGH_EXTENSION_SETUPS:
            score -= 0.10

        score = min(max(score, 0.0), 1.0)
        setup_cap = self._setup_reward_risk_cap(setup)
        return self._clamp_reward_risk(self.reward_risk_min + (setup_cap - self.reward_risk_min) * score, setup_cap)

    def _apply_context_obstacle(self, snapshot: MarketSnapshot, signal: TradeSignal) -> TradeSignal | None:
        obstacle = self._nearest_context_obstacle(snapshot, signal.side)
        if obstacle is None:
            return signal

        if signal.side == SignalSide.LONG:
            adjusted_target = min(signal.target_price, obstacle.lower_bound)
        else:
            adjusted_target = max(signal.target_price, obstacle.upper_bound)

        if self._reward_risk(signal.entry_price, signal.stop_price, adjusted_target, signal.side) < self.min_reward_risk:
            return None

        if adjusted_target == signal.target_price:
            return signal

        target_r = self._reward_risk(signal.entry_price, signal.stop_price, adjusted_target, signal.side)

        return TradeSignal(
            id=signal.id,
            symbol=signal.symbol,
            side=signal.side,
            setup=signal.setup,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=adjusted_target,
            confidence=round(signal.confidence * 0.9, 4),
            reasons=(*self._with_target_reason(signal.reasons, target_r), "context_60m obstacle target"),
            invalidation_rules=signal.invalidation_rules,
            created_at=signal.created_at,
            target_r_multiple=round(target_r, 4),
        )

    def _nearest_context_obstacle(self, snapshot: MarketSnapshot, side: SignalSide) -> ProfileLevel | None:
        context_types = {ProfileLevelType.POC, ProfileLevelType.HVN, ProfileLevelType.VAH, ProfileLevelType.VAL}
        levels = [
            level for level in self._profile_levels_for_window(snapshot, self.context_window)
            if level.type in context_types
        ]
        if side == SignalSide.LONG:
            candidates = [level for level in levels if level.lower_bound > snapshot.last_price]
            return min(candidates, key=lambda level: level.lower_bound, default=None)
        candidates = [level for level in levels if level.upper_bound < snapshot.last_price]
        return max(candidates, key=lambda level: level.upper_bound, default=None)

    def _adjust_stop_and_target(
        self,
        entry_price: float,
        structure_stop: float,
        side: SignalSide,
        snapshot: MarketSnapshot,
        setup: str,
        windows: HistoricalWindows | None,
    ) -> tuple[float, float, float]:
        atr = max(snapshot.atr_1m_14, snapshot.atr_3m_14)
        if atr <= 0:
            atr = entry_price * 0.002
        atr_buffer = self.atr_stop_mult * atr

        if side == SignalSide.LONG:
            adjusted_stop = min(structure_stop, entry_price - atr_buffer)
        else:
            adjusted_stop = max(structure_stop, entry_price + atr_buffer)

        stop_distance = abs(entry_price - adjusted_stop)
        cost = _estimated_round_trip_cost(entry_price, self._taker_fee_rate)
        min_stop = cost * self.min_stop_cost_mult

        if stop_distance < min_stop:
            if side == SignalSide.LONG:
                adjusted_stop = entry_price - min_stop
            else:
                adjusted_stop = entry_price + min_stop
            stop_distance = min_stop

        target_r = self._entry_reward_risk(snapshot, side, setup, windows)
        target_distance = stop_distance * target_r
        min_target = cost * self.min_target_cost_mult
        if target_distance < min_target:
            target_distance = min_target

        if side == SignalSide.LONG:
            target = entry_price + target_distance
        else:
            target = entry_price - target_distance

        actual_r = round(self._reward_risk(entry_price, adjusted_stop, target, side), 4)
        return adjusted_stop, target, actual_r

    def _reward_risk(self, entry: float, stop: float, target: float, side: SignalSide) -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0
        reward = target - entry if side == SignalSide.LONG else entry - target
        return reward / risk
