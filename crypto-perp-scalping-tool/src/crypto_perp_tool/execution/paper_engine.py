from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal
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
        journal_path: Path | str | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.initial_equity = equity
        self.signal_cooldown_ms = signal_cooldown_ms
        self.settings = default_settings()
        self.bin_size = self.settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
        self.rolling_window_ms = self.settings.profile.rolling_window_minutes * 60 * 1000
        self.risk = RiskEngine(self.settings.risk)
        self.signals = SignalEngine(self.settings.signals.min_reward_risk, self.settings.execution.max_data_lag_ms)
        self.journal = JsonlJournal(journal_path) if journal_path is not None else None
        self._events: list[TradeEvent] = []
        self._rolling_delta: list[float] = []
        self._position: PaperOpenPosition | None = None
        self._last_signal_at: int | None = None
        self._realized_pnl = 0.0
        self._consecutive_losses = 0
        self._details = self._empty_details()
        self._markers: list[dict[str, Any]] = []
        self._last_event_time = 0
        self._last_received_at = 0
        self._last_delta_15s = 0.0
        self._last_delta_30s = 0.0
        self._last_delta_60s = 0.0
        self._last_vwap = 0.0
        self._load_journal_state()

    def process_trade(
        self,
        event: TradeEvent,
        quote: QuoteEvent | None = None,
        received_at: int | None = None,
    ) -> None:
        if event.symbol.upper() != self.symbol:
            return

        received_at = int(time.time() * 1000) if received_at is None else received_at
        self._last_event_time = event.timestamp
        self._last_received_at = received_at
        self._events.append(event)
        self._close_position_if_triggered(event)
        self._rolling_delta.append(event.delta)
        self._refresh_indicators(event.timestamp)

        if self._position is not None or self._signal_is_in_cooldown(event.timestamp):
            self._refresh_pnl_ranges()
            return

        snapshot = self._snapshot(event, quote, received_at)
        signal = self.signals.evaluate(snapshot)
        if signal is None:
            self._refresh_pnl_ranges()
            return

        self._record_signal(signal)
        decision = self.risk.evaluate(signal, self._account_state())
        self._write_journal("risk_decision", {"decision": decision})
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
                "seen_trade_count": len(self._events),
                "profile_trade_count": len(self._profile_events(self._last_event_time)),
                "data_lag_ms": max(0, self._last_received_at - self._last_event_time),
                "delta_15s": self._last_delta_15s,
                "delta_30s": self._last_delta_30s,
                "delta_60s": self._last_delta_60s,
                "vwap": self._last_vwap,
                "open_position": self._position,
            }
        )

    def details(self) -> dict[str, Any]:
        self._refresh_pnl_ranges()
        return to_jsonable(deepcopy(self._details))

    def markers(self) -> list[dict[str, Any]]:
        return to_jsonable(deepcopy(self._markers))

    def _snapshot(self, event: TradeEvent, quote: QuoteEvent | None, received_at: int) -> MarketSnapshot:
        bid_price, ask_price = self._quote_prices(event, quote)
        mid_price = (bid_price + ask_price) / 2
        spread_bps = ((ask_price - bid_price) / mid_price) * 10_000 if mid_price else 0.0
        return MarketSnapshot(
            exchange=self.settings.exchange,
            symbol=event.symbol,
            event_time=event.timestamp,
            local_time=received_at,
            last_price=event.price,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_bps=spread_bps,
            vwap=self._last_vwap,
            atr_1m_14=max(event.price * 0.002, self.bin_size / 2),
            delta_15s=self._last_delta_15s,
            delta_30s=self._last_delta_30s,
            delta_60s=self._last_delta_60s,
            volume_30s=self._sum_abs_delta_since(event.timestamp, 30_000),
            profile_levels=self._profile_levels(event.timestamp),
        )

    def _quote_prices(self, event: TradeEvent, quote: QuoteEvent | None) -> tuple[float, float]:
        if quote is not None and quote.symbol.upper() == self.symbol:
            return quote.bid_price, quote.ask_price
        return event.price * 0.9999, event.price * 1.0001

    def _refresh_indicators(self, timestamp: int) -> None:
        self._last_delta_15s = self._sum_delta_since(timestamp, 15_000)
        self._last_delta_30s = self._sum_delta_since(timestamp, 30_000)
        self._last_delta_60s = self._sum_delta_since(timestamp, 60_000)
        self._last_vwap = self._vwap(timestamp)

    def _sum_delta_since(self, timestamp: int, window_ms: int) -> float:
        return sum(event.delta for event in self._events if timestamp - event.timestamp <= window_ms)

    def _sum_abs_delta_since(self, timestamp: int, window_ms: int) -> float:
        return sum(abs(event.delta) for event in self._events if timestamp - event.timestamp <= window_ms)

    def _vwap(self, timestamp: int) -> float:
        events = self._profile_events(timestamp)
        quantity = sum(event.quantity for event in events)
        if quantity <= 0:
            return 0.0
        return sum(event.price * event.quantity for event in events) / quantity

    def _profile_levels(self, timestamp: int):
        profile = VolumeProfileEngine(bin_size=self.bin_size, value_area_ratio=self.settings.profile.value_area_ratio)
        for event in self._profile_events(timestamp):
            profile.add_trade(event.price, event.quantity)
        return profile.levels("rolling_4h")

    def _profile_events(self, timestamp: int) -> list[TradeEvent]:
        if timestamp <= 0:
            return []
        return [event for event in self._events if timestamp - event.timestamp <= self.rolling_window_ms]

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
        self._write_journal("signal", {"signal": signal})

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
        self._write_journal(
            "paper_fill",
            {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "quantity": quantity,
                "fill_price": signal.entry_price,
            },
        )
        self._write_journal(
            "paper_order",
            {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "quantity": quantity,
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
            },
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
            "signal_id": position.signal_id,
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
        self._write_journal("position_closed", closed)

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

    def _write_journal(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.journal is not None:
            self.journal.write(event_type, payload)

    def _load_journal_state(self) -> None:
        if self.journal is None or not self.journal.path.exists():
            return

        for row in self._journal_rows():
            event_type = row.get("type")
            journal_time = int(row.get("time") or 0)
            payload = row.get("payload") or {}
            if event_type == "signal":
                self._restore_signal(payload.get("signal") or {}, journal_time)
            elif event_type == "paper_order":
                self._restore_order(payload, journal_time)
            elif event_type == "position_closed":
                self._restore_closed_position(payload, journal_time)
        self._refresh_pnl_ranges()

    def _journal_rows(self) -> list[dict[str, Any]]:
        if self.journal is None:
            return []
        rows = []
        for line in self.journal.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _restore_signal(self, signal: dict[str, Any], journal_time: int) -> None:
        if not signal:
            return
        timestamp = int(signal.get("created_at") or signal.get("timestamp") or journal_time)
        side = str(signal.get("side") or "")
        self._last_signal_at = max(self._last_signal_at or timestamp, timestamp)
        self._last_event_time = max(self._last_event_time, timestamp)
        self._details["paper"]["signals"].append(
            {
                "timestamp": timestamp,
                "symbol": str(signal.get("symbol") or self.symbol),
                "side": side,
                "setup": str(signal.get("setup") or ""),
                "entry_price": float(signal.get("entry_price") or 0),
                "stop_price": float(signal.get("stop_price") or 0),
                "target_price": float(signal.get("target_price") or 0),
                "confidence": float(signal.get("confidence") or 0),
                "reasons": list(signal.get("reasons") or []),
            }
        )
        self._markers.append(
            {
                "type": "signal",
                "timestamp": timestamp,
                "price": float(signal.get("entry_price") or 0),
                "label": str(signal.get("setup") or ""),
                "side": side,
            }
        )

    def _restore_order(self, order: dict[str, Any], journal_time: int) -> None:
        if not order:
            return
        side = SignalSide(str(order.get("side") or SignalSide.LONG.value))
        timestamp = int(order.get("timestamp") or order.get("opened_at") or self._last_signal_at or journal_time)
        symbol = str(order.get("symbol") or self.symbol).upper()
        quantity = float(order.get("quantity") or 0)
        entry_price = float(order.get("entry_price") or 0)
        stop_price = float(order.get("stop_price") or 0)
        target_price = float(order.get("target_price") or 0)
        signal_id = str(order.get("signal_id") or "")
        self._last_event_time = max(self._last_event_time, timestamp)
        self._details["paper"]["orders"].append(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "side": side.value,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "status": str(order.get("status") or "filled"),
            }
        )
        self._position = PaperOpenPosition(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            opened_at=timestamp,
        )

    def _restore_closed_position(self, closed: dict[str, Any], journal_time: int) -> None:
        if not closed:
            return
        timestamp = int(closed.get("timestamp") or journal_time)
        side = str(closed.get("side") or "")
        realized_pnl = float(closed.get("realized_pnl") or 0)
        restored_closed = {
            "signal_id": str(closed.get("signal_id") or ""),
            "timestamp": timestamp,
            "symbol": str(closed.get("symbol") or self.symbol).upper(),
            "side": side,
            "quantity": float(closed.get("quantity") or 0),
            "entry_price": float(closed.get("entry_price") or 0),
            "close_price": float(closed.get("close_price") or 0),
            "stop_price": float(closed.get("stop_price") or 0),
            "target_price": float(closed.get("target_price") or 0),
            "realized_pnl": realized_pnl,
        }
        self._last_event_time = max(self._last_event_time, timestamp)
        self._realized_pnl += realized_pnl
        self._consecutive_losses = self._consecutive_losses + 1 if realized_pnl < 0 else 0
        self._details["paper"]["closed_positions"].append(restored_closed)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": timestamp,
                "symbol": restored_closed["symbol"],
                "side": side,
                "realized_pnl": realized_pnl,
            }
        )
        self._markers.append(
            {
                "type": "position_closed",
                "timestamp": timestamp,
                "price": restored_closed["close_price"],
                "label": f"PnL {realized_pnl:.2f}",
                "side": side,
            }
        )
        signal_id = restored_closed["signal_id"]
        if self._position is not None and (not signal_id or signal_id == self._position.signal_id):
            self._position = None
