from __future__ import annotations

from crypto_perp_tool.types import BiasResult, MarketSnapshot, MarketStateResult, ProfileLevelType


class BiasEngine:
    def evaluate(self, snapshot: MarketSnapshot, market_state: MarketStateResult) -> BiasResult:
        if market_state.state in {"no_trade", "balanced"}:
            return BiasResult("neutral", (f"market_state={market_state.state}",))
        if market_state.direction in {"long", "short"}:
            if self._state_direction_conflicts_with_location(snapshot, market_state.direction):
                return BiasResult("neutral", ("state direction conflicts with value location",))
            return BiasResult(market_state.direction, (f"market_state={market_state.state}",))
        if snapshot.last_price > snapshot.vwap:
            return BiasResult("long", ("price above VWAP",))
        if snapshot.last_price < snapshot.vwap:
            return BiasResult("short", ("price below VWAP",))
        return BiasResult("neutral", ("no directional edge",))

    def _state_direction_conflicts_with_location(self, snapshot: MarketSnapshot, direction: str) -> bool:
        vah = next((level for level in snapshot.profile_levels if level.type == ProfileLevelType.VAH), None)
        val = next((level for level in snapshot.profile_levels if level.type == ProfileLevelType.VAL), None)
        if direction == "long" and val is not None and snapshot.last_price < val.lower_bound:
            return True
        if direction == "short" and vah is not None and snapshot.last_price > vah.upper_bound:
            return True
        return False
