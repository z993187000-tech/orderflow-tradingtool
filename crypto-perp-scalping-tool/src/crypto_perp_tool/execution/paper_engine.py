from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import QuoteEvent, TradeEvent
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import MarketSnapshot, SignalSide, TradeSignal


RANGE_MS = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}


@dataclass
class PaperOpenPosition:
    signal_id: str
    symbol: str
    side: SignalSide
    quantity: float
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: int


class PaperTradingEngine:
    def __init__(
        self,
        symbol: str,
        equity: float = 10_000,
        signal_cooldown_ms: int = 60_000,
    ) -> None:
        self.symbol = symbol.upper()
        self.initial_equity = equity
        self.signal_cooldown_ms = signal_cooldown_ms
        self.settings = default_settings()
        bin_size = self.settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
        self.profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=self.settings.profile.value_area_ratio)
        self.risk = RiskEngine(self.settings.risk)
        self.signals = SignalEngine(self.settings.signals.min_reward_risk, self.settings.execution.max_data_lag_ms)
        self._events: list[TradeEvent] = []
        self._rolling_delta: list[float] = []
        self._position: PaperOpenPosition | None = None
        self._last_signal_at: int | None = None
        self._realized_pnl = 0.0
        self._consecutive_losses = 0
        self._details = self._empty_details()
        self._markers: list[dict[str, Any]] = []
        self._last_event_time = 0

    def process_trade(self, event: TradeEvent, quote: QuoteEvent | None = None) -> None:
        if event.symbol.upper() != self.symbol:
            return

        self._last_event_time = event.timestamp
        self._events.append(event)
        self._close_position_if_triggered(event)
        self.profile.add_trade(event.price, event.quantity)
        self._rolling_delta.append(event.delta)

        if self._position is not None or self._signal_is_in_cooldown(event.timestamp):
            self._refresh_pnl_ranges()
            return

        snapshot = self._snapshot(event, quote)
        signal = self.signals.evaluate(snapshot)
        if signal is None:
            self._refresh_pnl_ranges()
            return

        self._record_signal(signal)
        decision = self.risk.evaluate(signal, self._account_state())
        if not decision.allowed:
            self._refresh_pnl_ranges()
            return

        self._open_position(signal, decision.quantity)
        self._refresh_pnl_ranges()

    def summary(self) -> dict[str, Any]:
        paper = self._details["paper"]
        return to_jsonable(
            {
                "signals": len(paper["signals"]),
                "orders": len(paper["orders"]),
                "closed_positions": len(paper["closed_positions"]),
                "realized_pnl": self._realized_pnl,
                "pnl_24h": paper["pnl_by_range"]["24h"],
                "profile_trade_count": len(self._events),
                "open_position": self._position,
            }
        )

    def details(self) -> dict[str, Any]:
        self._refresh_pnl_ranges()
        return to_jsonable(deepcopy(self._details))

    def markers(self) -> list[dict[str, Any]]:
        return to_jsonable(deepcopy(self._markers))

    def _snapshot(self, event: TradeEvent, quote: QuoteEvent | None) -> MarketSnapshot:
        bid_price, ask_price = self._quote_prices(event, quote)
        mid_price = (bid_price + ask_price) / 2
        spread_bps = ((ask_price - bid_price) / mid_price) * 10_000 if mid_price else 0.0
        bin_size = self.profile.bin_size
        return MarketSnapshot(
            exchange=self.settings.exchange,
            symbol=event.symbol,
            event_time=event.timestamp,
            local_time=event.timestamp,
            last_price=event.price,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_bps=spread_bps,
            vwap=self._vwap(),
            atr_1m_14=max(event.price * 0.002, bin_size / 2),
            delta_15s=sum(self._rolling_delta[-15:]),
            delta_30s=sum(self._rolling_delta[-30:]),
            delta_60s=sum(self._rolling_delta[-60:]),
            volume_30s=sum(abs(delta) for delta in self._rolling_delta[-30:]),
            profile_levels=self.profile.levels("rolling_4h"),
        )

    def _quote_prices(self, event: TradeEvent, quote: QuoteEvent | None) -> tuple[float, float]:
        if quote is not None and quote.symbol.upper() == self.symbol:
            return quote.bid_price, quote.ask_price
        return event.price * 0.9999, event.price * 1.0001

    def _vwap(self) -> float:
        quantity = sum(event.quantity for event in self._events)
        if quantity <= 0:
            return 0.0
        return sum(event.price * event.quantity for event in self._events) / quantity

    def _signal_is_in_cooldown(self, timestamp: int) -> bool:
        if self._last_signal_at is None:
            return False
        return timestamp - self._last_signal_at < self.signal_cooldown_ms

    def _account_state(self) -> AccountState:
        return AccountState(
            equity=self.initial_equity + self._realized_pnl,
            realized_pnl_today=self._details["paper"]["pnl_by_range"]["24h"],
            consecutive_losses=self._consecutive_losses,
        )

    def _record_signal(self, signal: TradeSignal) -> None:
        self._last_signal_at = signal.created_at
        self._details["paper"]["signals"].append(
            {
                "timestamp": signal.created_at,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "setup": signal.setup,
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "confidence": signal.confidence,
                "reasons": list(signal.reasons),
            }
        )
        self._markers.append(
            {
                "type": "signal",
                "timestamp": signal.created_at,
                "price": signal.entry_price,
                "label": signal.setup,
                "side": signal.side.value,
            }
        )

    def _open_position(self, signal: TradeSignal, quantity: float) -> None:
        self._position = PaperOpenPosition(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            opened_at=signal.created_at,
        )
        self._details["paper"]["orders"].append(
            {
                "timestamp": signal.created_at,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "quantity": quantity,
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "status": "filled",
            }
        )

    def _close_position_if_triggered(self, event: TradeEvent) -> None:
        if self._position is None:
            return

        close_price = self._triggered_close_price(self._position, event.price)
        if close_price is None:
            return

        position = self._position
        self._position = None
        realized_pnl = self._position_pnl(position, close_price)
        self._realized_pnl += realized_pnl
        self._consecutive_losses = self._consecutive_losses + 1 if realized_pnl < 0 else 0
        closed = {
            "timestamp": event.timestamp,
            "symbol": position.symbol,
            "side": position.side.value,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "close_price": close_price,
            "stop_price": position.stop_price,
            "target_price": position.target_price,
            "realized_pnl": realized_pnl,
        }
        self._details["paper"]["closed_positions"].append(closed)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": event.timestamp,
                "symbol": position.symbol,
                "side": position.side.value,
                "realized_pnl": realized_pnl,
            }
        )
        self._markers.append(
            {
                "type": "position_closed",
                "timestamp": event.timestamp,
                "price": close_price,
                "label": f"PnL {realized_pnl:.2f}",
                "side": position.side.value,
            }
        )

    def _triggered_close_price(self, position: PaperOpenPosition, price: float) -> float | None:
        if position.side == SignalSide.LONG:
            if price <= position.stop_price:
                return position.stop_price
            if price >= position.target_price:
                return position.target_price
        if position.side == SignalSide.SHORT:
            if price >= position.stop_price:
                return position.stop_price
            if price <= position.target_price:
                return position.target_price
        return None

    def _position_pnl(self, position: PaperOpenPosition, close_price: float) -> float:
        if position.side == SignalSide.LONG:
            return (close_price - position.entry_price) * position.quantity
        return (position.entry_price - close_price) * position.quantity

    def _refresh_pnl_ranges(self) -> None:
        paper = self._details["paper"]
        events = paper["pnl_events"]
        now_ms = self._last_event_time
        paper["pnl_by_range"] = {
            key: sum(float(event["realized_pnl"]) for event in events if now_ms - int(event["timestamp"]) <= window_ms)
            for key, window_ms in RANGE_MS.items()
        }
        paper["pnl_by_range"]["all"] = sum(float(event["realized_pnl"]) for event in events)

    def _empty_details(self) -> dict[str, Any]:
        empty_mode = {
            "signals": [],
            "orders": [],
            "closed_positions": [],
            "pnl_events": [],
            "pnl_by_range": {"24h": 0.0, "7d": 0.0, "30d": 0.0, "all": 0.0},
        }
        return {"paper": deepcopy(empty_mode), "live": deepcopy(empty_mode)}
