from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.signals.bias import BiasEngine
from crypto_perp_tool.signals.confirmation import ConfirmationGate
from crypto_perp_tool.signals.market_state import MarketStateEngine
from crypto_perp_tool.signals.setups import SetupCandidateEngine
from crypto_perp_tool.signals.trade_plan import TradePlanBuilder
from crypto_perp_tool.types import (
    BiasResult,
    ConfirmationResult,
    HistoricalWindows,
    MarketDataHealth,
    MarketSnapshot,
    MarketStateResult,
    SignalTrace,
    TradePlan,
    TradeSignal,
)


class SignalEngine:
    def __init__(
        self,
        min_reward_risk: float = 1.2,
        max_data_lag_ms: int = 2000,
        session_gating_enabled: bool = True,
        reward_risk: float = 5.0,
        dynamic_reward_risk_enabled: bool = True,
        reward_risk_min: float = 3.0,
        reward_risk_max: float = 10.0,
        atr_stop_mult: float = 0.35,
        min_stop_cost_mult: float = 3.0,
        min_target_cost_mult: float = 8.0,
        taker_fee_rate: float = 0.00018,
        execution_window: str = "execution_30m",
        micro_window: str = "micro_15m",
        context_window: str = "context_60m",
    ) -> None:
        self.min_reward_risk = min_reward_risk
        self.max_data_lag_ms = max_data_lag_ms
        self.session_gating_enabled = session_gating_enabled
        self.execution_window = execution_window
        self.micro_window = micro_window
        self.context_window = context_window
        self._market_state = MarketStateEngine()
        self._bias = BiasEngine()
        self._setups = SetupCandidateEngine()
        self._confirmation = ConfirmationGate()
        self._trade_plan = TradePlanBuilder(
            min_reward_risk=min_reward_risk,
            fallback_reward_risk=reward_risk_min,
            max_reward_risk=min(reward_risk_max, max(reward_risk, reward_risk_min)),
            atr_stop_mult=atr_stop_mult,
        )
        self.last_reject_reasons: tuple[str, ...] = ()
        self.last_trace: SignalTrace | None = None

        # Constructor arguments retained for compatibility with older callers.
        self.reward_risk = reward_risk
        self.dynamic_reward_risk_enabled = dynamic_reward_risk_enabled
        self.reward_risk_min = reward_risk_min
        self.reward_risk_max = reward_risk_max
        self.atr_stop_mult = atr_stop_mult
        self.min_stop_cost_mult = min_stop_cost_mult
        self.min_target_cost_mult = min_target_cost_mult
        self._taker_fee_rate = taker_fee_rate

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        windows: HistoricalWindows | None = None,
        health: MarketDataHealth | None = None,
        circuit_tripped: bool = False,
        has_position: bool = False,
        next_funding_time: int = 0,
        klines: Sequence[KlineEvent] = (),
    ) -> TradeSignal | None:
        windows = windows or HistoricalWindows()
        self.last_trace = None

        forbidden = self._check_forbidden(snapshot, windows, health, circuit_tripped, has_position, next_funding_time)
        if forbidden:
            self.last_reject_reasons = tuple(forbidden)
            self.last_trace = self._trace(
                MarketStateResult("no_trade", reasons=self.last_reject_reasons),
                BiasResult("neutral"),
                reject_reasons=self.last_reject_reasons,
            )
            return None

        market_state = self._market_state.evaluate(snapshot, windows=windows, klines=klines)
        if market_state.state == "no_trade":
            self.last_reject_reasons = market_state.reasons or ("no_trade",)
            self.last_trace = self._trace(market_state, BiasResult("neutral"), reject_reasons=self.last_reject_reasons)
            return None

        bias = self._bias.evaluate(snapshot, market_state)
        candidates = self._setups.generate(snapshot, market_state, bias)
        if not candidates:
            self.last_reject_reasons = ("no_candidate",)
            self.last_trace = self._trace(market_state, bias, reject_reasons=self.last_reject_reasons)
            return None

        reject_reasons: list[str] = []
        for candidate in candidates:
            confirmation = self._confirmation.confirm(candidate, snapshot, klines=klines, windows=windows)
            if not confirmation.confirmed:
                reject_reasons.append(confirmation.reject_reason or "confirmation_rejected")
                continue
            signal = self._trade_plan.build(candidate, confirmation, snapshot, market_state=market_state.state, bias=bias.bias)
            if signal is None:
                reject_reasons.append(self._trade_plan.last_reject_reason or "trade_plan_rejected")
                continue
            trade_plan = TradePlan(
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                target_source=signal.target_source,
                reward_risk=signal.target_r_multiple,
                management_profile=signal.management_profile,
            )
            trace = self._trace(
                market_state,
                bias,
                location=candidate.location,
                trigger=candidate.setup_model,
                confirmation=confirmation,
                trade_plan=trade_plan,
            )
            self.last_trace = trace
            self.last_reject_reasons = ()
            return replace(signal, trace=trace)

        self.last_reject_reasons = (reject_reasons[-1],) if reject_reasons else ("no_signal",)
        self.last_trace = self._trace(market_state, bias, reject_reasons=self.last_reject_reasons)
        return None

    def _trace(
        self,
        market_state: MarketStateResult,
        bias: BiasResult,
        *,
        location: str = "",
        trigger: str = "",
        confirmation: ConfirmationResult | None = None,
        trade_plan=None,
        reject_reasons: tuple[str, ...] = (),
    ) -> SignalTrace:
        return SignalTrace(
            market_state=market_state,
            bias=bias,
            location=location,
            trigger=trigger,
            confirmation=confirmation,
            trade_plan=trade_plan,
            reject_reasons=reject_reasons,
        )

    def _check_forbidden(
        self,
        snapshot: MarketSnapshot,
        windows: HistoricalWindows,
        health: MarketDataHealth | None,
        circuit_tripped: bool,
        has_position: bool,
        next_funding_time: int,
    ) -> list[str]:
        reasons: list[str] = []

        if snapshot.exchange_lag_ms > self.max_data_lag_ms:
            reasons.append("data_stale")

        if windows.spread_5min:
            median = windows.median_spread_5min()
            if median > 0 and snapshot.spread_bps > median * 2.0:
                reasons.append("spread_too_wide")

        if health is not None and health.is_stale():
            reasons.append("websocket_stale")

        if next_funding_time > 0:
            distance_ms = abs(snapshot.local_time - next_funding_time)
            if distance_ms < 2 * 60 * 1000:
                reasons.append("funding_blackout")

        if windows.amplitude_1m:
            mean_amp = windows.mean_amplitude_1m()
            if mean_amp > 0 and snapshot.atr_1m_14 > mean_amp * 3.0:
                reasons.append("extreme_volatility")

        if circuit_tripped:
            reasons.append("circuit_breaker_tripped")

        if has_position:
            reasons.append("existing_position")

        return reasons

    def _reward_risk(self, entry: float, stop: float, target: float, side) -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        reward = target - entry if str(side) == "long" or getattr(side, "value", "") == "long" else entry - target
        return reward / risk
