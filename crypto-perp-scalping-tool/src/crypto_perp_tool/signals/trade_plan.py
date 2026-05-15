from __future__ import annotations

from crypto_perp_tool.config import TradePlanSettings
from crypto_perp_tool.types import ConfirmationResult, MarketSnapshot, ProfileLevel, ProfileLevelType, SetupCandidate, SignalSide, TradeSignal


class TradePlanBuilder:
    def __init__(
        self,
        *,
        min_reward_risk: float | None = None,
        fallback_reward_risk: float | None = None,
        max_reward_risk: float | None = None,
        atr_stop_mult: float | None = None,
        settings: TradePlanSettings | None = None,
    ) -> None:
        settings = settings or TradePlanSettings()
        self.min_reward_risk = settings.min_reward_risk if min_reward_risk is None else min_reward_risk
        self.fallback_reward_risk = settings.fallback_reward_risk if fallback_reward_risk is None else fallback_reward_risk
        self.max_reward_risk = settings.max_reward_risk if max_reward_risk is None else max_reward_risk
        self.atr_stop_mult = settings.atr_stop_mult if atr_stop_mult is None else atr_stop_mult
        self.last_reject_reason = ""

    def build(
        self,
        candidate: SetupCandidate,
        confirmation: ConfirmationResult,
        snapshot: MarketSnapshot,
        *,
        market_state: str = "",
        bias: str = "",
    ) -> TradeSignal | None:
        self.last_reject_reason = ""
        if not confirmation.confirmed:
            self.last_reject_reason = confirmation.reject_reason
            return None
        entry = confirmation.confirmed_close or snapshot.last_price
        stop = self._stop(candidate, snapshot, entry)
        target, target_source = self._target(candidate, snapshot, entry, stop)
        reward_risk = self._reward_risk(entry, stop, target, candidate.side)
        if reward_risk < self.min_reward_risk:
            if candidate.structure_target is not None:
                self.last_reject_reason = "structure_reward_risk_too_low"
                return None
            capped = min(self.fallback_reward_risk, self.max_reward_risk)
            distance = abs(entry - stop) * capped
            target = entry + distance if candidate.side == SignalSide.LONG else entry - distance
            target_source = f"fallback_{capped:.1f}R"
            reward_risk = self._reward_risk(entry, stop, target, candidate.side)
        management_profile = self._management_profile(candidate.setup_model)
        return TradeSignal(
            id=f"{snapshot.symbol}-{snapshot.event_time}-{candidate.setup_model}-{candidate.side.value}",
            symbol=snapshot.symbol,
            side=candidate.side,
            setup=candidate.legacy_setup,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            confidence=self._confidence(candidate.setup_model),
            reasons=(*candidate.reasons, *confirmation.reasons, f"target_source {target_source}", f"target {reward_risk:.1f}R"),
            invalidation_rules=candidate.invalidation_rules,
            created_at=snapshot.local_time,
            target_r_multiple=round(reward_risk, 4),
            setup_model=candidate.setup_model,
            legacy_setup=candidate.legacy_setup,
            market_state=market_state,
            bias=bias,
            target_source=target_source,
            management_profile=management_profile,
        )

    def _stop(self, candidate: SetupCandidate, snapshot: MarketSnapshot, entry: float) -> float:
        atr = max(snapshot.atr_1m_14, snapshot.atr_3m_14, snapshot.last_price * 0.0001)
        atr_buffer = atr * self.atr_stop_mult
        if candidate.side == SignalSide.LONG:
            structure = candidate.structure_stop if candidate.structure_stop is not None else entry - atr
            return min(structure, entry - atr_buffer)
        structure = candidate.structure_stop if candidate.structure_stop is not None else entry + atr
        return max(structure, entry + atr_buffer)

    def _target(self, candidate: SetupCandidate, snapshot: MarketSnapshot, entry: float, stop: float) -> tuple[float, str]:
        target = candidate.structure_target
        source = "candidate_structure"
        if target is None:
            target, source = self._nearest_structure_target(snapshot, candidate.side, entry)
        if target is None:
            capped = min(self.fallback_reward_risk, self.max_reward_risk)
            distance = abs(entry - stop) * capped
            if candidate.side == SignalSide.LONG:
                return entry + distance, f"fallback_{capped:.1f}R"
            return entry - distance, f"fallback_{capped:.1f}R"
        return target, source

    def _nearest_structure_target(self, snapshot: MarketSnapshot, side: SignalSide, entry: float) -> tuple[float | None, str]:
        target_types = {ProfileLevelType.POC, ProfileLevelType.HVN, ProfileLevelType.VAH, ProfileLevelType.VAL}
        levels = [level for level in snapshot.profile_levels if level.type in target_types]
        context = [level for level in levels if level.window == "context_60m"]
        execution = [level for level in levels if level.window in {"execution_30m", "rolling_4h"}]
        for group, prefix in ((context, "context_60m"), (execution, "execution_30m")):
            target = self._nearest_from_group(group, side, entry)
            if target is not None:
                level, price = target
                return price, f"{prefix}_{level.type.value}"
        return None, ""

    def _nearest_from_group(self, levels: list[ProfileLevel], side: SignalSide, entry: float) -> tuple[ProfileLevel, float] | None:
        if side == SignalSide.LONG:
            candidates = [(level, level.lower_bound) for level in levels if level.lower_bound > entry]
            return min(candidates, key=lambda item: item[1], default=None)
        candidates = [(level, level.upper_bound) for level in levels if level.upper_bound < entry]
        return max(candidates, key=lambda item: item[1], default=None)

    def _reward_risk(self, entry: float, stop: float, target: float, side: SignalSide) -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        reward = target - entry if side == SignalSide.LONG else entry - target
        return reward / risk

    def _management_profile(self, setup_model: str) -> str:
        if setup_model == "squeeze_continuation":
            return "squeeze"
        if setup_model == "failed_auction_reversal":
            return "failed_auction"
        if setup_model == "lvn_acceptance":
            return "lvn_acceptance"
        return "absorption"

    def _confidence(self, setup_model: str) -> float:
        if setup_model == "squeeze_continuation":
            return 0.72
        if setup_model == "failed_auction_reversal":
            return 0.68
        if setup_model == "lvn_acceptance":
            return 0.65
        return 0.60
