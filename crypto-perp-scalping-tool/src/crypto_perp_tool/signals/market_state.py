from __future__ import annotations

from collections.abc import Sequence

from crypto_perp_tool.config import MarketStateSettings
from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.types import HistoricalWindows, MarketSnapshot, MarketStateResult, ProfileLevel, ProfileLevelType


class MarketStateEngine:
    def __init__(self, settings: MarketStateSettings | None = None) -> None:
        self.settings = settings or MarketStateSettings()

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        windows: HistoricalWindows | None = None,
        klines: Sequence[KlineEvent] = (),
    ) -> MarketStateResult:
        levels = self._execution_levels(snapshot)
        if not levels:
            return MarketStateResult("no_trade", reasons=("profile_levels_missing",))

        failed = self._failed_auction(snapshot, klines, levels)
        if failed is not None:
            return failed

        absorbed = self._absorption(snapshot, windows)
        if absorbed is not None:
            return absorbed

        compressed = self._compression(snapshot, klines, levels)
        if compressed is not None:
            return compressed

        vah = self._nearest(levels, ProfileLevelType.VAH, snapshot.last_price)
        val = self._nearest(levels, ProfileLevelType.VAL, snapshot.last_price)
        poc = self._nearest(levels, ProfileLevelType.POC, snapshot.last_price)
        if vah is not None and snapshot.last_price > vah.upper_bound and snapshot.delta_30s > 0:
            return MarketStateResult("imbalanced_up", "long", ("price accepted above VAH", "delta positive"))
        if val is not None and snapshot.last_price < val.lower_bound and snapshot.delta_30s < 0:
            return MarketStateResult("imbalanced_down", "short", ("price accepted below VAL", "delta negative"))
        if val is not None and vah is not None and val.lower_bound <= snapshot.last_price <= vah.upper_bound:
            return MarketStateResult("balanced", "neutral", ("price inside value area",))
        if poc is not None and abs(snapshot.last_price - poc.price) <= max(snapshot.atr_1m_14 * 0.25, 1e-8):
            return MarketStateResult("balanced", "neutral", ("price near POC",))
        return MarketStateResult("balanced", "neutral", ("no directional imbalance",))

    def _execution_levels(self, snapshot: MarketSnapshot) -> list[ProfileLevel]:
        levels = [level for level in snapshot.profile_levels if level.window in {"execution_30m", "rolling_4h"}]
        return levels or list(snapshot.profile_levels)

    def _nearest(self, levels: Sequence[ProfileLevel], level_type: ProfileLevelType, reference_price: float) -> ProfileLevel | None:
        candidates = [level for level in levels if level.type == level_type]
        if not candidates:
            return None
        return min(candidates, key=lambda level: abs(level.price - reference_price))

    def _failed_auction(
        self,
        snapshot: MarketSnapshot,
        klines: Sequence[KlineEvent],
        levels: Sequence[ProfileLevel],
    ) -> MarketStateResult | None:
        closed = [kline for kline in klines if kline.interval == "1m" and kline.is_closed]
        if not closed:
            return None
        last = closed[-1]
        vah = self._nearest(levels, ProfileLevelType.VAH, snapshot.last_price)
        val = self._nearest(levels, ProfileLevelType.VAL, snapshot.last_price)
        if vah is not None and last.high > vah.upper_bound and last.close < vah.price:
            return MarketStateResult("failed_auction", "short", ("VAH breakout closed back inside value",))
        if val is not None and last.low < val.lower_bound and last.close > val.price:
            return MarketStateResult("failed_auction", "long", ("VAL breakdown closed back inside value",))
        return None

    def _absorption(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> MarketStateResult | None:
        if snapshot.aggression_bubble_side is None and snapshot.delta_30s == 0:
            return None
        mean_abs_delta = 0.0
        if windows is not None and windows.delta_30s:
            mean_abs_delta = sum(abs(value) for value in windows.delta_30s) / len(windows.delta_30s)
        baseline = max(mean_abs_delta * self.settings.absorption_delta_ratio, 1e-8)
        if abs(snapshot.delta_30s) < baseline:
            return None
        reference_price = snapshot.aggression_bubble_price or snapshot.vwap or snapshot.last_price
        displacement = abs(snapshot.last_price - reference_price)
        atr = max(snapshot.atr_1m_14, snapshot.atr_3m_14, snapshot.last_price * 0.0001)
        if displacement > atr * self.settings.absorption_max_displacement_atr:
            return None
        if snapshot.aggression_bubble_side == "sell" or snapshot.delta_30s < 0:
            return MarketStateResult("absorption", "long", ("sell aggression absorbed",))
        return MarketStateResult("absorption", "short", ("buy aggression absorbed",))

    def _compression(
        self,
        snapshot: MarketSnapshot,
        klines: Sequence[KlineEvent],
        levels: Sequence[ProfileLevel],
    ) -> MarketStateResult | None:
        closed = [kline for kline in klines if kline.interval == "1m" and kline.is_closed]
        needed = self.settings.compression_bars
        if len(closed) < needed + 2:
            return None
        recent = closed[-needed:]
        previous = closed[-needed - 2:-needed]
        recent_range = sum(kline.high - kline.low for kline in recent) / len(recent)
        previous_range = sum(kline.high - kline.low for kline in previous) / len(previous)
        if previous_range <= 0 or recent_range > previous_range * self.settings.compression_range_ratio:
            return None
        if not any(level.lower_bound <= snapshot.last_price <= level.upper_bound for level in levels):
            return None
        direction = "long" if snapshot.last_price >= snapshot.vwap else "short"
        return MarketStateResult("compression", direction, ("1m ranges contracting near profile level",))
