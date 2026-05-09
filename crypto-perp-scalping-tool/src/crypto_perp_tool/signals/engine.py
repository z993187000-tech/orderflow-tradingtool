from __future__ import annotations

from crypto_perp_tool.types import MarketSnapshot, ProfileLevel, ProfileLevelType, SignalSide, TradeSignal


class SignalEngine:
    def __init__(self, min_reward_risk: float = 1.2, max_data_lag_ms: int = 2000) -> None:
        self.min_reward_risk = min_reward_risk
        self.max_data_lag_ms = max_data_lag_ms

    def evaluate(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        if snapshot.local_time - snapshot.event_time > self.max_data_lag_ms:
            return None
        if snapshot.spread_bps > 20:
            return None

        lvn = self._nearest_level(snapshot, ProfileLevelType.LVN)
        if lvn is None:
            return None

        target = self._target_level(snapshot, above=snapshot.last_price > lvn.upper_bound)
        if target is None:
            return None

        if snapshot.last_price > lvn.upper_bound and snapshot.delta_30s > 0:
            stop = min(lvn.lower_bound, snapshot.last_price - max(0.35 * snapshot.atr_1m_14, snapshot.last_price * 0.0015))
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
                confidence=0.62,
                reasons=("price accepted above LVN", "delta_30s positive", f"target at {target.type.value}"),
                invalidation_rules=("price falls back below LVN", "delta flips negative"),
                created_at=snapshot.local_time,
            )

        if snapshot.last_price < lvn.lower_bound and snapshot.delta_30s < 0:
            stop = max(lvn.upper_bound, snapshot.last_price + max(0.35 * snapshot.atr_1m_14, snapshot.last_price * 0.0015))
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
                confidence=0.62,
                reasons=("price accepted below LVN", "delta_30s negative", f"target at {target.type.value}"),
                invalidation_rules=("price reclaims LVN", "delta flips positive"),
                created_at=snapshot.local_time,
            )

        return None

    def _nearest_level(self, snapshot: MarketSnapshot, level_type: ProfileLevelType) -> ProfileLevel | None:
        levels = [level for level in snapshot.profile_levels if level.type == level_type]
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
