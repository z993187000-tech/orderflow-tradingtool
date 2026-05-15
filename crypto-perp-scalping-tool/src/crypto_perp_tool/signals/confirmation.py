from __future__ import annotations

from collections.abc import Sequence

from crypto_perp_tool.config import ConfirmationSettings
from crypto_perp_tool.market_data import KlineEvent
from crypto_perp_tool.types import ConfirmationResult, HistoricalWindows, MarketSnapshot, SetupCandidate, SignalSide


class ConfirmationGate:
    def __init__(self, settings: ConfirmationSettings | None = None) -> None:
        self.settings = settings or ConfirmationSettings()

    def confirm(
        self,
        candidate: SetupCandidate,
        snapshot: MarketSnapshot,
        klines: Sequence[KlineEvent] = (),
        windows: HistoricalWindows | None = None,
    ) -> ConfirmationResult:
        closed = [kline for kline in klines if kline.interval == "1m" and kline.is_closed]
        if self.settings.require_1m_close and not closed:
            return ConfirmationResult(False, reject_reason="candle_close_not_confirmed")
        close = closed[-1].close if closed else snapshot.last_price
        buffer = candidate.trigger_price * self.settings.close_buffer_bps / 10_000
        if candidate.side == SignalSide.LONG:
            if close <= candidate.trigger_price + buffer:
                return ConfirmationResult(False, reject_reason="candle_close_not_confirmed")
            if snapshot.last_price < candidate.trigger_price:
                return ConfirmationResult(False, reject_reason="trigger_reclaimed")
        else:
            if close >= candidate.trigger_price - buffer:
                return ConfirmationResult(False, reject_reason="candle_close_not_confirmed")
            if snapshot.last_price > candidate.trigger_price:
                return ConfirmationResult(False, reject_reason="trigger_reclaimed")

        if not self._delta_confirmed(candidate, snapshot, windows):
            return ConfirmationResult(False, reject_reason="delta_not_confirmed")
        if not self._volume_confirmed(snapshot, windows):
            return ConfirmationResult(False, reject_reason="volume_not_confirmed")
        displacement = abs(close - candidate.trigger_price)
        atr = max(snapshot.atr_1m_14, snapshot.atr_3m_14, snapshot.last_price * 0.0001)
        if displacement < atr * self.settings.min_displacement_atr:
            return ConfirmationResult(False, reject_reason="displacement_not_confirmed", confirmed_close=close, displacement=displacement)
        return ConfirmationResult(
            True,
            reasons=("1m close confirmed", "delta confirmed", "volume confirmed", "displacement confirmed"),
            confirmed_close=close,
            displacement=displacement,
        )

    def _delta_confirmed(self, candidate: SetupCandidate, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> bool:
        directional_delta = snapshot.delta_30s if candidate.side == SignalSide.LONG else -snapshot.delta_30s
        if directional_delta <= 0:
            return False
        if windows is None or not windows.delta_30s:
            return True
        directional_history = [value if candidate.side == SignalSide.LONG else -value for value in windows.delta_30s]
        positive_history = [value for value in directional_history if value > 0]
        if not positive_history:
            return True
        baseline = sum(positive_history) / len(positive_history)
        return directional_delta >= baseline * self.settings.min_delta_ratio

    def _volume_confirmed(self, snapshot: MarketSnapshot, windows: HistoricalWindows | None) -> bool:
        if windows is None or not windows.volume_30s:
            return True
        baseline = sum(windows.volume_30s) / len(windows.volume_30s)
        return baseline <= 0 or snapshot.volume_30s >= baseline * self.settings.min_volume_ratio
