from __future__ import annotations

from crypto_perp_tool.types import BiasResult, MarketSnapshot, MarketStateResult, ProfileLevel, ProfileLevelType, SetupCandidate, SignalSide


class SetupCandidateEngine:
    def generate(
        self,
        snapshot: MarketSnapshot,
        market_state: MarketStateResult,
        bias: BiasResult,
    ) -> tuple[SetupCandidate, ...]:
        candidates: list[SetupCandidate] = []
        if bias.bias in {"long", "short"} and market_state.state in {"imbalanced_up", "imbalanced_down", "compression", "absorption"}:
            squeeze = self._squeeze_candidate(snapshot, bias.bias)
            if squeeze is not None:
                candidates.append(squeeze)
        if market_state.state == "failed_auction" and market_state.direction in {"long", "short"}:
            candidates.append(self._failed_auction_candidate(snapshot, market_state.direction))
        lvn = self._lvn_acceptance_candidate(snapshot)
        if lvn is not None:
            candidates.append(lvn)
        if market_state.state == "absorption" and market_state.direction in {"long", "short"}:
            candidates.append(self._absorption_candidate(snapshot, market_state.direction))
        return tuple(candidates)

    def _levels(self, snapshot: MarketSnapshot) -> list[ProfileLevel]:
        levels = [level for level in snapshot.profile_levels if level.window in {"execution_30m", "rolling_4h"}]
        return levels or list(snapshot.profile_levels)

    def _nearest(self, snapshot: MarketSnapshot, level_type: ProfileLevelType) -> ProfileLevel | None:
        levels = [level for level in self._levels(snapshot) if level.type == level_type]
        return min(levels, key=lambda level: abs(snapshot.last_price - level.price), default=None)

    def _target_for_side(self, snapshot: MarketSnapshot, side: SignalSide) -> float | None:
        target_types = {ProfileLevelType.POC, ProfileLevelType.HVN, ProfileLevelType.VAH, ProfileLevelType.VAL}
        levels = [level for level in self._levels(snapshot) if level.type in target_types]
        if side == SignalSide.LONG:
            candidates = [level.lower_bound for level in levels if level.lower_bound > snapshot.last_price]
            return min(candidates, default=None)
        candidates = [level.upper_bound for level in levels if level.upper_bound < snapshot.last_price]
        return max(candidates, default=None)

    def _squeeze_candidate(self, snapshot: MarketSnapshot, bias: str) -> SetupCandidate | None:
        if bias == "long":
            vah = self._nearest(snapshot, ProfileLevelType.VAH)
            if vah is None:
                return None
            return SetupCandidate(
                setup_model="squeeze_continuation",
                legacy_setup="vah_breakout_lvn_pullback_aggression",
                side=SignalSide.LONG,
                trigger_price=vah.upper_bound,
                location="above_value",
                reasons=("long bias after value acceptance",),
                invalidation_rules=("price reclaims trigger", "delta flips negative"),
                structure_stop=vah.lower_bound,
                structure_target=self._target_for_side(snapshot, SignalSide.LONG),
                trigger_time=snapshot.local_time,
            )
        val = self._nearest(snapshot, ProfileLevelType.VAL)
        if val is None:
            return None
        return SetupCandidate(
            setup_model="squeeze_continuation",
            legacy_setup="val_breakdown_lvn_pullback_aggression",
            side=SignalSide.SHORT,
            trigger_price=val.lower_bound,
            location="below_value",
            reasons=("short bias after value acceptance",),
            invalidation_rules=("price reclaims trigger", "delta flips positive"),
            structure_stop=val.upper_bound,
            structure_target=self._target_for_side(snapshot, SignalSide.SHORT),
            trigger_time=snapshot.local_time,
        )

    def _failed_auction_candidate(self, snapshot: MarketSnapshot, direction: str) -> SetupCandidate:
        side = SignalSide.LONG if direction == "long" else SignalSide.SHORT
        legacy = "cvd_divergence_failed_breakdown" if side == SignalSide.LONG else "cvd_divergence_failed_breakout"
        level = self._nearest(snapshot, ProfileLevelType.VAL if side == SignalSide.LONG else ProfileLevelType.VAH)
        structure_stop = level.lower_bound if side == SignalSide.LONG and level else level.upper_bound if level else None
        return SetupCandidate(
            setup_model="failed_auction_reversal",
            legacy_setup=legacy,
            side=side,
            trigger_price=snapshot.last_price,
            location="failed_auction",
            reasons=("failed auction recovered inside value",),
            invalidation_rules=("price retests failed auction extreme",),
            structure_stop=structure_stop,
            structure_target=None,
            trigger_time=snapshot.local_time,
        )

    def _lvn_acceptance_candidate(self, snapshot: MarketSnapshot) -> SetupCandidate | None:
        lvn = self._nearest(snapshot, ProfileLevelType.LVN)
        if lvn is None:
            return None
        if snapshot.last_price > lvn.upper_bound and snapshot.delta_30s > 0:
            return SetupCandidate(
                setup_model="lvn_acceptance",
                legacy_setup="lvn_break_acceptance",
                side=SignalSide.LONG,
                trigger_price=lvn.upper_bound,
                location="above_lvn",
                reasons=("price accepted above LVN",),
                invalidation_rules=("price falls back inside LVN",),
                structure_stop=lvn.lower_bound,
                structure_target=self._target_for_side(snapshot, SignalSide.LONG),
                trigger_time=snapshot.local_time,
            )
        if snapshot.last_price < lvn.lower_bound and snapshot.delta_30s < 0:
            return SetupCandidate(
                setup_model="lvn_acceptance",
                legacy_setup="lvn_breakdown_acceptance",
                side=SignalSide.SHORT,
                trigger_price=lvn.lower_bound,
                location="below_lvn",
                reasons=("price accepted below LVN",),
                invalidation_rules=("price reclaims LVN",),
                structure_stop=lvn.upper_bound,
                structure_target=self._target_for_side(snapshot, SignalSide.SHORT),
                trigger_time=snapshot.local_time,
            )
        return None

    def _absorption_candidate(self, snapshot: MarketSnapshot, direction: str) -> SetupCandidate:
        side = SignalSide.LONG if direction == "long" else SignalSide.SHORT
        return SetupCandidate(
            setup_model="absorption_response",
            legacy_setup="absorption_response",
            side=side,
            trigger_price=snapshot.last_price,
            location="absorbed_aggression",
            reasons=("aggression absorbed without displacement",),
            invalidation_rules=("absorbed side gains displacement",),
            structure_stop=snapshot.last_price - snapshot.atr_1m_14 if side == SignalSide.LONG else snapshot.last_price + snapshot.atr_1m_14,
            structure_target=self._target_for_side(snapshot, side),
            trigger_time=snapshot.local_time,
        )
