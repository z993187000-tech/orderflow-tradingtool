from __future__ import annotations

import math
import threading
import time
from collections import deque
from copy import deepcopy
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.market_data import MarkPriceEvent, QuoteEvent, SpotPriceEvent, TimeWindowBuffer, TradeEvent
from crypto_perp_tool.market_data.binance import BinanceInstrumentSpec, default_instrument_spec
from crypto_perp_tool.market_data.health import compute_health
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.risk.circuit import CircuitBreaker
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import HistoricalWindows, MarketSnapshot, SignalSide, TradeSignal
from crypto_perp_tool.web.details import empty_execution_details, mode_breakdown, total_pnl_for_range


RANGE_MS = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}


class LiveOrderflowStore:
    def __init__(
        self,
        symbol: str,
        max_events: int = 20_000,
        display_events: int = 500,
        enable_signals: bool = False,
        journal_path: Path | str | None = None,
        equity: float = 10_000,
        instrument_spec: BinanceInstrumentSpec | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self.display_events = display_events
        self.equity = equity
        self.settings = default_settings()
        self._profile_window_ms = self.settings.profile.rolling_window_minutes * 60 * 1000
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._trade_window = TimeWindowBuffer[TradeEvent](max_window_ms=self._profile_window_ms)
        self._quote: QuoteEvent | None = None
        self._mark: MarkPriceEvent | None = None
        self._spot: SpotPriceEvent | None = None
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._reconnect_count = 0
        self._lock = threading.Lock()

        self._bin_size = self.settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
        self._instrument = instrument_spec or default_instrument_spec(self.symbol)
        self._signal_engine = SignalEngine(
            min_reward_risk=self.settings.signals.min_reward_risk,
            max_data_lag_ms=self.settings.execution.max_data_lag_ms,
        ) if enable_signals else None
        self._risk = RiskEngine(self.settings.risk)
        self._journal = JsonlJournal(journal_path) if journal_path is not None else None
        self._circuit_breaker = CircuitBreaker()
        self._signal_count = 0
        self._order_count = 0
        self._rejected_count = 0
        self._closed_positions = 0
        self._realized_pnl = 0.0
        self._consecutive_losses = 0
        self._position: dict[str, Any] | None = None
        self._historical: HistoricalWindows = HistoricalWindows()
        self._details = empty_execution_details()
        self._markers: list[dict[str, Any]] = []
        self._last_event_time = 0
        self._last_received_at = 0
        self._last_delta_15s = 0.0
        self._last_delta_30s = 0.0
        self._last_delta_60s = 0.0
        self._last_volume_30s = 0.0
        self._last_vwap = 0.0
        self._last_signal_reasons: tuple[str, ...] = ()
        self._last_reject_reasons: tuple[str, ...] = ()

    def add_trade(self, event: TradeEvent, received_at: int | None = None) -> None:
        if event.symbol.upper() != self.symbol:
            return
        received_at = int(time.time() * 1000) if received_at is None else int(received_at)
        with self._lock:
            self._events.append(event)
            self._trade_window.append(event.timestamp, event)
            self._last_event_time = event.timestamp
            self._last_received_at = received_at
            self._refresh_indicators(event.timestamp)
            self._try_close(event)
            self._update_historical(event)
            self._try_signal(event, received_at)
            self._refresh_pnl_ranges()

    def add_quote(self, event: QuoteEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._quote = event

    def add_mark(self, event: MarkPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._mark = event

    def add_spot(self, event: SpotPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._spot = event

    def set_connection_status(self, status: str, message: str) -> None:
        with self._lock:
            previous = self._connection_status
            self._connection_status = status
            self._connection_message = message
            if status == "connected" and previous == "error":
                self._reconnect_count += 1

    def _refresh_indicators(self, timestamp: int) -> None:
        self._last_delta_15s = self._trade_window.sum_since(timestamp, 15_000, lambda event: event.delta)
        self._last_delta_30s = self._trade_window.sum_since(timestamp, 30_000, lambda event: event.delta)
        self._last_delta_60s = self._trade_window.sum_since(timestamp, 60_000, lambda event: event.delta)
        self._last_volume_30s = self._trade_window.sum_since(timestamp, 30_000, lambda event: abs(event.delta))
        self._last_vwap = self._vwap(timestamp, self._profile_window_ms)

    def _vwap(self, timestamp: int, window_ms: int) -> float:
        events = self._trade_window.items_since(timestamp, window_ms)
        quantity = sum(event.quantity for event in events)
        if quantity <= 0:
            return 0.0
        return sum(event.price * event.quantity for event in events) / quantity

    def _profile_levels(self, timestamp: int):
        profile = VolumeProfileEngine(bin_size=self._bin_size, value_area_ratio=self.settings.profile.value_area_ratio)
        for event in self._trade_window.items_since(timestamp, self._profile_window_ms):
            profile.add_trade(event.price, event.quantity, timestamp=event.timestamp)
        return profile.levels(window="all")

    def _update_historical(self, event: TradeEvent) -> None:
        spread = self._spread_bps(event)
        self._historical = self._historical.with_window("spread_5min", spread)
        self._historical = self._historical.with_window("delta_30s", self._last_delta_30s)
        self._historical = self._historical.with_window("volume_30s", self._last_volume_30s)
        self._historical = self._historical.with_window("amplitude_1m", max(event.price * 0.002, self._bin_size / 2))

    def _try_signal(self, event: TradeEvent, received_at: int) -> None:
        if self._signal_engine is None:
            return
        if self._circuit_breaker.state == "tripped":
            self._last_reject_reasons = ("circuit_breaker_tripped",)
            return
        if self._position is not None or len(self._events) < 30:
            return

        snapshot = self._snapshot(event, received_at)
        health = compute_health(
            connection_status=self._connection_status,
            last_event_time=event.timestamp,
            last_local_time=received_at,
            reconnect_count=self._reconnect_count,
            symbol=self.symbol,
        )
        signal = self._signal_engine.evaluate(
            snapshot,
            windows=self._historical,
            health=health,
            circuit_tripped=self._circuit_breaker.state == "tripped",
            has_position=self._position is not None,
            next_funding_time=self._mark.next_funding_time if self._mark else 0,
        )
        if signal is None:
            reasons = tuple(getattr(self._signal_engine, "last_reject_reasons", ()) or ())
            if reasons:
                self._last_reject_reasons = reasons
                if reasons != ("no_setup",):
                    self._write_journal("signal_rejected", {"symbol": self.symbol, "reject_reasons": reasons})
            return

        self._record_signal(signal)
        decision = self._risk.evaluate(signal, self._account_state())
        self._write_journal("risk_decision", {"decision": decision})
        if not decision.allowed:
            self._rejected_count += 1
            self._last_reject_reasons = decision.reject_reasons
            self._write_journal(
                "signal_rejected",
                {"signal_id": signal.id, "symbol": signal.symbol, "reject_reasons": decision.reject_reasons},
            )
            return

        self._open_position(signal, decision.quantity, decision.max_slippage_bps)

    def _snapshot(self, event: TradeEvent, received_at: int) -> MarketSnapshot:
        bid_price = self._quote.bid_price if self._quote else event.price * 0.9999
        ask_price = self._quote.ask_price if self._quote else event.price * 1.0001
        return MarketSnapshot(
            exchange=self.settings.exchange,
            symbol=self.symbol,
            event_time=event.timestamp,
            local_time=received_at,
            last_price=event.price,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_bps=self._spread_bps(event),
            vwap=self._last_vwap,
            atr_1m_14=max(event.price * 0.002, self._bin_size / 2),
            delta_15s=self._last_delta_15s,
            delta_30s=self._last_delta_30s,
            delta_60s=self._last_delta_60s,
            volume_30s=self._last_volume_30s,
            profile_levels=tuple(self._profile_levels(event.timestamp)),
        )

    def _spread_bps(self, event: TradeEvent) -> float:
        if self._quote is None:
            return 2.0
        return (self._quote.ask_price - self._quote.bid_price) / self._quote.mid_price * 10_000

    def _record_signal(self, signal: TradeSignal) -> None:
        self._signal_count += 1
        self._last_signal_reasons = tuple(signal.reasons)
        self._last_reject_reasons = ()
        record = {
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
        self._details["paper"]["signals"].append(record)
        self._markers.append(
            {"type": "signal", "timestamp": signal.created_at, "price": signal.entry_price, "label": signal.setup}
        )
        self._write_journal("signal", {"signal": signal})

    def _open_position(self, signal: TradeSignal, quantity: float, slippage_bps: float) -> None:
        fill = self._entry_fill(signal, quantity, slippage_bps)
        if fill["quantity"] <= 0:
            self._rejected_count += 1
            self._last_reject_reasons = ("quantity_below_step_size",)
            self._write_journal(
                "signal_rejected",
                {"signal_id": signal.id, "symbol": signal.symbol, "reject_reasons": self._last_reject_reasons},
            )
            return

        self._position = {
            "signal_id": signal.id,
            "symbol": signal.symbol,
            "side": signal.side,
            "quantity": fill["quantity"],
            "entry_price": fill["fill_price"],
            "signal_entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "target_price": signal.target_price,
            "entry_fee": fill["fee"],
            "opened_at": signal.created_at,
        }
        order = {
            "timestamp": signal.created_at,
            "symbol": signal.symbol,
            "side": signal.side.value,
            "quantity": fill["quantity"],
            "entry_price": fill["fill_price"],
            "signal_entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "target_price": signal.target_price,
            "status": "filled",
            "fee": fill["fee"],
            "slippage_bps": fill["slippage_bps"],
        }
        self._order_count += 1
        self._details["paper"]["orders"].append(order)
        self._write_journal("paper_fill", fill | {"signal_id": signal.id, "symbol": signal.symbol, "side": signal.side.value})
        self._write_journal("paper_order", order | {"signal_id": signal.id})

    def _try_close(self, event: TradeEvent) -> None:
        if self._position is None:
            return
        trigger_price = self._triggered_close_price(event.price)
        if trigger_price is None:
            return

        position = self._position
        self._position = None
        close_fill = self._exit_fill(position, trigger_price)
        gross_pnl = self._position_pnl(position, close_fill["fill_price"])
        net_pnl = gross_pnl - position["entry_fee"] - close_fill["fee"]
        self._realized_pnl += net_pnl
        self._consecutive_losses = self._consecutive_losses + 1 if net_pnl < 0 else 0
        self._closed_positions += 1
        closed = {
            "timestamp": event.timestamp,
            "signal_id": position["signal_id"],
            "symbol": position["symbol"],
            "side": position["side"].value,
            "quantity": position["quantity"],
            "entry_price": position["entry_price"],
            "close_price": close_fill["fill_price"],
            "stop_price": position["stop_price"],
            "target_price": position["target_price"],
            "gross_realized_pnl": gross_pnl,
            "entry_fee": position["entry_fee"],
            "close_fee": close_fill["fee"],
            "fee": position["entry_fee"] + close_fill["fee"],
            "realized_pnl": net_pnl,
            "net_realized_pnl": net_pnl,
        }
        self._details["paper"]["closed_positions"].append(closed)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": event.timestamp,
                "symbol": position["symbol"],
                "side": position["side"].value,
                "realized_pnl": net_pnl,
            }
        )
        self._markers.append(
            {
                "type": "position_closed",
                "timestamp": event.timestamp,
                "price": close_fill["fill_price"],
                "label": f"PnL {net_pnl:.2f}",
            }
        )
        self._write_journal("position_closed", closed)
        self._write_journal("pnl", {"signal_id": position["signal_id"], "symbol": position["symbol"], "realized_pnl": net_pnl})

    def _triggered_close_price(self, current_price: float) -> float | None:
        if self._position is None:
            return None
        side = self._position["side"].value
        if side == "long":
            if current_price <= self._position["stop_price"]:
                return self._position["stop_price"]
            if current_price >= self._position["target_price"]:
                return self._position["target_price"]
        else:
            if current_price >= self._position["stop_price"]:
                return self._position["stop_price"]
            if current_price <= self._position["target_price"]:
                return self._position["target_price"]
        return None

    def _entry_fill(self, signal: TradeSignal, quantity: float, slippage_bps: float) -> dict[str, float | str]:
        quantity = self._round_quantity(quantity)
        if signal.side == SignalSide.LONG:
            reference = self._quote.ask_price if self._quote else signal.entry_price
            fill_price = self._round_price(reference * (1 + slippage_bps / 10_000), "up")
            action = "buy"
        else:
            reference = self._quote.bid_price if self._quote else signal.entry_price
            fill_price = self._round_price(reference * (1 - slippage_bps / 10_000), "down")
            action = "sell"
        return {
            "action": action,
            "quantity": quantity,
            "reference_price": reference,
            "fill_price": fill_price,
            "slippage_bps": slippage_bps,
            "fee": abs(fill_price * quantity) * self._instrument.taker_fee_rate,
        }

    def _exit_fill(self, position: dict[str, Any], trigger_price: float) -> dict[str, float | str]:
        slippage_bps = self.settings.execution.btc_max_slippage_bps if self.symbol == "BTCUSDT" else self.settings.execution.eth_max_slippage_bps
        if position["side"] == SignalSide.LONG:
            fill_price = self._round_price(trigger_price * (1 - slippage_bps / 10_000), "down")
            action = "sell"
        else:
            fill_price = self._round_price(trigger_price * (1 + slippage_bps / 10_000), "up")
            action = "buy"
        fee = abs(fill_price * position["quantity"]) * self._instrument.taker_fee_rate
        return {"action": action, "fill_price": fill_price, "slippage_bps": slippage_bps, "fee": fee}

    def _round_price(self, price: float, direction: str) -> float:
        tick = self._instrument.tick_size
        scaled = price / tick
        rounded = math.ceil(scaled) * tick if direction == "up" else math.floor(scaled) * tick
        return round(rounded, 8)

    def _round_quantity(self, quantity: float) -> float:
        step = self._instrument.step_size
        return round(math.floor(quantity / step) * step, 8)

    def _position_pnl(self, position: dict[str, Any], close_price: float) -> float:
        if position["side"] == SignalSide.LONG:
            return (close_price - position["entry_price"]) * position["quantity"]
        return (position["entry_price"] - close_price) * position["quantity"]

    def _account_state(self) -> AccountState:
        return AccountState(
            equity=self.equity + self._realized_pnl,
            realized_pnl_today=self._details["paper"]["pnl_by_range"]["24h"],
            consecutive_losses=self._consecutive_losses,
        )

    def _refresh_pnl_ranges(self) -> None:
        paper = self._details["paper"]
        now_ms = self._last_event_time or int(time.time() * 1000)
        for key, window_ms in RANGE_MS.items():
            paper["pnl_by_range"][key] = sum(
                float(event["realized_pnl"]) for event in paper["pnl_events"] if now_ms - int(event["timestamp"]) <= window_ms
            )
        paper["pnl_by_range"]["all"] = sum(float(event["realized_pnl"]) for event in paper["pnl_events"])

    def _write_journal(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._journal is not None:
            self._journal.write(event_type, payload)

    def view(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            quote = self._quote
            mark = self._mark
            spot = self._spot
            connection_status = self._connection_status
            connection_message = self._connection_message
            details = to_jsonable(deepcopy(self._details))
            markers = to_jsonable(deepcopy(self._markers))
            position = to_jsonable(deepcopy(self._position))
            last_signal_reasons = list(self._last_signal_reasons)
            last_reject_reasons = list(self._last_reject_reasons)

        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []
        display_events = events[-self.display_events:]

        for index, event in enumerate(display_events):
            cumulative_delta += event.delta
            trades.append({
                "index": index, "timestamp": event.timestamp, "symbol": event.symbol,
                "price": event.price, "quantity": event.quantity,
                "side": "sell" if event.is_buyer_maker else "buy", "delta": event.delta,
            })
            delta_series.append({
                "index": index, "timestamp": event.timestamp,
                "delta": event.delta, "cumulative_delta": cumulative_delta,
            })

        last_trade_price = trades[-1]["price"] if trades else None
        quote_mid_price = quote.mid_price if quote is not None else None
        spot_last_price = spot.price if spot is not None else None
        index_price = mark.index_price if mark is not None else None
        last_price = (
            spot_last_price if spot_last_price is not None
            else index_price if index_price is not None
            else last_trade_price if last_trade_price is not None
            else quote_mid_price
        )
        price_source = (
            "spotTrade" if spot_last_price is not None
            else "indexPrice" if index_price is not None
            else "aggTrade" if last_trade_price is not None
            else "bookTicker"
        )
        derived_connection_status = "connected" if last_price is not None else connection_status
        last_event_time = events[-1].timestamp if events else 0
        profile_events = self._trade_window.items_since(last_event_time, self._profile_window_ms) if last_event_time else []
        levels = self._profile_levels(last_event_time) if last_event_time else ()

        return {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": derived_connection_status,
                "connection_message": connection_message,
                "trade_count": len(trades),
                "seen_trade_count": len(events),
                "profile_trade_count": len(profile_events),
                "last_price": last_price,
                "spot_last_price": spot_last_price,
                "last_trade_price": last_trade_price,
                "bid_price": quote.bid_price if quote is not None else None,
                "ask_price": quote.ask_price if quote is not None else None,
                "quote_mid_price": quote_mid_price,
                "mark_price": mark.mark_price if mark is not None else None,
                "index_price": index_price,
                "funding_rate": mark.funding_rate if mark is not None else None,
                "next_funding_time": mark.next_funding_time if mark is not None else None,
                "price_source": price_source,
                "cumulative_delta": cumulative_delta,
                "delta_15s": self._last_delta_15s,
                "delta_30s": self._last_delta_30s,
                "delta_60s": self._last_delta_60s,
                "volume_30s": self._last_volume_30s,
                "vwap": self._last_vwap,
                "signals": self._signal_count,
                "orders": self._order_count,
                "rejected": self._rejected_count,
                "closed_positions": self._closed_positions,
                "realized_pnl": self._realized_pnl,
                "open_position": position,
                "signal_reasons": last_signal_reasons,
                "reject_reasons": last_reject_reasons,
                "data_lag_ms": max(0, self._last_received_at - self._last_event_time),
                "last_trade_time": self._last_event_time or None,
                "circuit_state": self._circuit_breaker.state,
                "pnl_24h": total_pnl_for_range(details, "24h"),
                "mode_breakdown": mode_breakdown(details),
            },
            "trades": trades,
            "delta_series": delta_series,
            "profile_levels": [
                {"type": level.type.value, "price": level.price,
                 "lower_bound": level.lower_bound, "upper_bound": level.upper_bound,
                 "strength": level.strength, "window": level.window}
                for level in levels
            ],
            "markers": markers,
            "details": details,
        }
