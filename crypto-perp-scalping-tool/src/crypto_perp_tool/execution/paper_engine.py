from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.market_data import AggressionBubble, AggressionBubbleDetector, AtrTracker, QuoteEvent, TradeEvent
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.session import SessionDetector
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
    setup: str
    quantity: float
    entry_price: float
    stop_price: float
    initial_stop_price: float
    target_price: float
    opened_at: int
    entry_fee: float = 0.0
    break_even_shifted: bool = False
    absorption_reduced: bool = False


@dataclass(frozen=True)
class PaperExecutionConfig:
    entry_slippage_bps: float = 0.0
    exit_slippage_bps: float = 0.0
    partial_fill_ratio: float = 1.0
    stop_submission_success: bool = True


class PaperTradingEngine:
    def __init__(
        self,
        symbol: str,
        equity: float = 10_000,
        signal_cooldown_ms: int = 60_000,
        journal_path: Path | str | None = None,
        execution_config: PaperExecutionConfig | None = None,
        taker_fee_rate: float = 0.0004,
    ) -> None:
        self.symbol = symbol.upper()
        self.initial_equity = equity
        self.signal_cooldown_ms = signal_cooldown_ms
        self.taker_fee_rate = taker_fee_rate
        self.settings = default_settings()
        self.bin_size = self.settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
        self.rolling_window_ms = self.settings.profile.rolling_window_minutes * 60 * 1000
        self.risk = RiskEngine(self.settings.risk)
        self.signals = SignalEngine(self.settings.signals.min_reward_risk, self.settings.execution.max_data_lag_ms,
                                    session_gating_enabled=self.settings.signals.session_gating_enabled)
        self.execution_config = execution_config or PaperExecutionConfig()
        self.journal = JsonlJournal(journal_path, config_version=self.settings.config_version) if journal_path is not None else None
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
        self._cumulative_delta = 0.0
        self._bubble_detector = AggressionBubbleDetector(
            large_threshold=self.settings.signals.aggression_large_threshold,
            block_threshold=self.settings.signals.aggression_block_threshold,
            dynamic_enabled=self.settings.signals.aggression_dynamic_enabled,
            percentile_large=self.settings.signals.aggression_percentile_large,
            percentile_block=self.settings.signals.aggression_percentile_block,
            half_life_ms=self.settings.signals.aggression_half_life_minutes * 60 * 1000,
        )
        self._last_bubble: AggressionBubble | None = None
        self._atr_1m = AtrTracker(bar_ms=60_000, period=self.settings.signals.atr_period)
        self._atr_3m = AtrTracker(bar_ms=3 * 60_000, period=self.settings.signals.atr_period)
        self._session_detector = SessionDetector(
            asia_start_hour=self.settings.profile.asia_start_hour,
            asia_end_hour=self.settings.profile.asia_end_hour,
            london_start_hour=self.settings.profile.london_start_hour,
            london_end_hour=self.settings.profile.london_end_hour,
            london_end_minute=self.settings.profile.london_end_minute,
            ny_start_hour=self.settings.profile.ny_start_hour,
            ny_start_minute=self.settings.profile.ny_start_minute,
            ny_end_hour=self.settings.profile.ny_end_hour,
        )
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
        self._cumulative_delta += event.delta
        self._atr_1m.update(event)
        self._atr_3m.update(event)
        self._record_aggression_bubble(event)
        self._rolling_delta.append(event.delta)
        self._refresh_indicators(event.timestamp)
        self._manage_position(event)
        self._close_position_if_triggered(event)

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
        last_price = self._events[-1].price if self._events else 0.0
        equity = self.initial_equity + self._realized_pnl
        pnl_percent_24h = (paper["pnl_by_range"]["24h"] / self.initial_equity * 100) if self.initial_equity else 0.0
        pnl_percent_all = (self._realized_pnl / self.initial_equity * 100) if self.initial_equity else 0.0
        return to_jsonable(
            {
                "signals": len(paper["signals"]),
                "orders": len(paper["orders"]),
                "closed_positions": len(paper["closed_positions"]),
                "realized_pnl": self._realized_pnl,
                "pnl_percent_all": pnl_percent_all,
                "pnl_24h": paper["pnl_by_range"]["24h"],
                "pnl_percent_24h": pnl_percent_24h,
                "equity": equity,
                "initial_equity": self.initial_equity,
                "seen_trade_count": len(self._events),
                "profile_trade_count": len(self._profile_events(self._last_event_time)),
                "data_lag_ms": max(0, self._last_received_at - self._last_event_time),
                "delta_15s": self._last_delta_15s,
                "delta_30s": self._last_delta_30s,
                "delta_60s": self._last_delta_60s,
                "vwap": self._last_vwap,
                "atr_1m_14": self._current_atr(last_price),
                "atr_3m_14": self._atr_3m.latest_atr,
                "last_aggression_bubble": self._last_bubble,
                "open_position": self._position,
                "risk_events": list(paper["risk_events"]),
                "protective_actions": list(paper["protective_actions"]),
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
        bubble = self._last_bubble if self._last_bubble is not None and self._last_bubble.timestamp == event.timestamp else None
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
            atr_1m_14=self._current_atr(event.price),
            delta_15s=self._last_delta_15s,
            delta_30s=self._last_delta_30s,
            delta_60s=self._last_delta_60s,
            volume_30s=self._sum_abs_delta_since(event.timestamp, 30_000),
            profile_levels=self._profile_levels(event.timestamp),
            atr_3m_14=self._atr_3m.latest_atr,
            cumulative_delta=self._cumulative_delta,
            aggression_bubble_side=bubble.side if bubble else None,
            aggression_bubble_quantity=bubble.quantity if bubble else 0.0,
            aggression_bubble_price=bubble.price if bubble else None,
            aggression_bubble_tier=bubble.tier if bubble else None,
            session=self._session_value(event.timestamp),
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

    def _current_atr(self, fallback_price: float) -> float:
        if self._atr_1m.latest_atr > 0:
            return self._atr_1m.latest_atr
        return max(fallback_price * 0.002, self.bin_size / 2)

    def _session_value(self, timestamp_ms: int) -> str:
        if timestamp_ms < 86_400_000:
            return "unknown"
        return self._session_detector.detect(timestamp_ms).value

    def _record_aggression_bubble(self, event: TradeEvent) -> None:
        bubble = self._bubble_detector.detect(event)
        if bubble is None:
            return
        self._last_bubble = bubble
        self._markers.append(
            {
                "type": "aggression_bubble",
                "timestamp": bubble.timestamp,
                "price": bubble.price,
                "label": bubble.label,
                "side": bubble.side,
                "quantity": bubble.quantity,
                "tier": bubble.tier,
            }
        )

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
        fill_ratio = min(max(self.execution_config.partial_fill_ratio, 0.0), 1.0)
        filled_quantity = quantity * fill_ratio
        if filled_quantity <= 0:
            self._record_risk_event("quantity_below_partial_fill", signal.created_at)
            return

        fill_price = self._entry_fill_price(signal)
        entry_fee = abs(fill_price * filled_quantity) * self.taker_fee_rate
        order_status = "filled" if fill_ratio >= 1.0 else "partially_filled"
        self._position = PaperOpenPosition(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=signal.side,
            setup=signal.setup,
            quantity=filled_quantity,
            entry_price=fill_price,
            stop_price=signal.stop_price,
            initial_stop_price=signal.stop_price,
            target_price=signal.target_price,
            opened_at=signal.created_at,
            entry_fee=entry_fee,
        )
        self._details["paper"]["orders"].append(
            {
                "timestamp": signal.created_at,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "setup": signal.setup,
                "quantity": filled_quantity,
                "requested_quantity": quantity,
                "entry_price": fill_price,
                "signal_entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "status": order_status,
                "fill_ratio": fill_ratio,
                "slippage_bps": self.execution_config.entry_slippage_bps,
                "entry_fee": entry_fee,
            }
        )
        if fill_ratio < 1.0:
            self._record_risk_event("partial_fill", signal.created_at)
        self._write_journal(
            "paper_fill",
            {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "quantity": filled_quantity,
                "fill_price": fill_price,
                "fill_ratio": fill_ratio,
                "slippage_bps": self.execution_config.entry_slippage_bps,
                "entry_fee": entry_fee,
            },
        )
        self._write_journal(
            "paper_order",
            {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "setup": signal.setup,
                "quantity": filled_quantity,
                "entry_price": fill_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "status": order_status,
                "entry_fee": entry_fee,
            },
        )
        if not self.execution_config.stop_submission_success:
            self._handle_stop_submission_failure(signal.created_at)

    def _manage_position(self, event: TradeEvent) -> None:
        if self._position is None:
            return
        self._shift_stop_to_break_even(event)
        self._reduce_for_absorption(event)

    def _shift_stop_to_break_even(self, event: TradeEvent) -> None:
        if self._position is None or self._position.break_even_shifted:
            return
        position = self._position
        risk = abs(position.entry_price - position.initial_stop_price)
        if risk <= 0:
            return
        favorable_move = event.price - position.entry_price if position.side == SignalSide.LONG else position.entry_price - event.price
        if favorable_move < risk * 1.5:
            return
        position.stop_price = position.entry_price
        position.break_even_shifted = True
        self._record_protective_action("break_even_shift", event.timestamp, {"signal_id": position.signal_id, "stop_price": position.stop_price})

    def _reduce_for_absorption(self, event: TradeEvent) -> None:
        if self._position is None or self._position.absorption_reduced:
            return
        position = self._position
        same_direction_delta = self._last_delta_30s if position.side == SignalSide.LONG else -self._last_delta_30s
        mean_abs_delta = abs(sum(self._rolling_delta[-30:]) / max(len(self._rolling_delta[-30:]), 1)) if self._rolling_delta else 0
        baseline = max(mean_abs_delta * 2.0, 10.0)
        atr = self._current_atr(event.price)
        price_displacement = abs(event.price - position.entry_price)
        if same_direction_delta < baseline or price_displacement > max(atr, event.price * 0.001):
            return
        reduce_quantity = position.quantity * 0.5
        if reduce_quantity <= 0 or reduce_quantity >= position.quantity:
            return
        close_fill_price = self._exit_fill_price(position, event.price)
        gross_pnl = self._position_pnl(position, close_fill_price) * (reduce_quantity / position.quantity)
        entry_fee_portion = position.entry_fee * (reduce_quantity / position.quantity)
        exit_fee = abs(close_fill_price * reduce_quantity) * self.taker_fee_rate
        net_pnl = gross_pnl - entry_fee_portion - exit_fee
        self._realized_pnl += net_pnl
        self._record_risk_event("absorption_detected", event.timestamp)
        position.quantity -= reduce_quantity
        position.entry_fee -= entry_fee_portion
        position.absorption_reduced = True
        entry_notional = position.entry_price * reduce_quantity
        pnl_percent = (net_pnl / entry_notional * 100) if entry_notional else 0.0
        self._details["paper"]["pnl_events"].append(
            {"timestamp": event.timestamp, "symbol": position.symbol, "side": position.side.value, "realized_pnl": net_pnl, "pnl_percent": pnl_percent}
        )
        self._details["paper"]["closed_positions"].append(
            {
                "timestamp": event.timestamp, "signal_id": position.signal_id,
                "symbol": position.symbol, "side": position.side.value,
                "quantity": reduce_quantity, "entry_price": position.entry_price,
                "close_price": close_fill_price, "stop_price": position.stop_price,
                "target_price": position.target_price,
                "entry_fee": entry_fee_portion,
                "exit_fee": exit_fee,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "pnl_percent": pnl_percent,
                "realized_pnl": net_pnl, "net_realized_pnl": net_pnl,
                "exit_reason": "absorption_reduce",
            }
        )
        self._record_protective_action(
            "absorption_reduce",
            event.timestamp,
            {"signal_id": position.signal_id, "quantity": reduce_quantity, "remaining_quantity": position.quantity},
        )

    def _close_position_if_triggered(self, event: TradeEvent) -> None:
        if self._position is None:
            return

        close_price = self._triggered_close_price(self._position, event.price)
        if close_price is None:
            return

        position = self._position
        self._position = None
        fill_price = self._exit_fill_price(position, close_price)
        gross_pnl = self._position_pnl(position, fill_price)
        exit_fee = abs(fill_price * position.quantity) * self.taker_fee_rate
        net_pnl = gross_pnl - position.entry_fee - exit_fee
        self._realized_pnl += net_pnl
        self._consecutive_losses = self._consecutive_losses + 1 if net_pnl < 0 else 0
        entry_notional = position.entry_price * position.quantity
        pnl_percent = (net_pnl / entry_notional * 100) if entry_notional else 0.0
        closed = {
            "signal_id": position.signal_id,
            "timestamp": event.timestamp,
            "symbol": position.symbol,
            "side": position.side.value,
            "setup": position.setup,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "close_price": fill_price,
            "stop_price": position.stop_price,
            "target_price": position.target_price,
            "entry_fee": position.entry_fee,
            "exit_fee": exit_fee,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "pnl_percent": pnl_percent,
            "realized_pnl": net_pnl,
            "opened_at": position.opened_at,
            "slippage_bps": self.execution_config.exit_slippage_bps,
        }
        self._details["paper"]["closed_positions"].append(closed)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": event.timestamp,
                "symbol": position.symbol,
                "side": position.side.value,
                "realized_pnl": net_pnl,
                "pnl_percent": pnl_percent,
            }
        )
        self._markers.append(
            {
                "type": "position_closed",
                "timestamp": event.timestamp,
                "price": fill_price,
                "label": f"PnL {net_pnl:.2f} ({pnl_percent:+.2f}%)",
                "side": position.side.value,
            }
        )
        self._write_journal("position_closed", closed)

    def _handle_stop_submission_failure(self, timestamp: int) -> None:
        if self._position is None:
            return
        self._record_risk_event("stop_submission_failed", timestamp)
        self._record_risk_event("circuit_breaker_tripped", timestamp)
        self._record_protective_action("protective_close", timestamp)
        position = self._position
        self._position = None
        close_price = self._exit_fill_price(position, position.entry_price)
        gross_pnl = self._position_pnl(position, close_price)
        exit_fee = abs(close_price * position.quantity) * self.taker_fee_rate
        net_pnl = gross_pnl - position.entry_fee - exit_fee
        self._realized_pnl += net_pnl
        entry_notional = position.entry_price * position.quantity
        pnl_percent = (net_pnl / entry_notional * 100) if entry_notional else 0.0
        closed = {
            "signal_id": position.signal_id,
            "timestamp": timestamp,
            "symbol": position.symbol,
            "side": position.side.value,
            "setup": position.setup,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "close_price": close_price,
            "stop_price": position.stop_price,
            "target_price": position.target_price,
            "entry_fee": position.entry_fee,
            "exit_fee": exit_fee,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "pnl_percent": pnl_percent,
            "realized_pnl": net_pnl,
            "opened_at": position.opened_at,
            "exit_reason": "protective_close",
            "slippage_bps": self.execution_config.exit_slippage_bps,
        }
        self._details["paper"]["closed_positions"].append(closed)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": timestamp,
                "symbol": position.symbol,
                "side": position.side.value,
                "realized_pnl": net_pnl,
                "pnl_percent": pnl_percent,
            }
        )
        self._write_journal("position_closed", closed)
        self._write_journal("protective_close", closed)

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

    def _entry_fill_price(self, signal: TradeSignal) -> float:
        adjustment = self.execution_config.entry_slippage_bps / 10_000
        if signal.side == SignalSide.LONG:
            return signal.entry_price * (1 + adjustment)
        return signal.entry_price * (1 - adjustment)

    def _exit_fill_price(self, position: PaperOpenPosition, trigger_price: float) -> float:
        adjustment = self.execution_config.exit_slippage_bps / 10_000
        if position.side == SignalSide.LONG:
            return trigger_price * (1 - adjustment)
        return trigger_price * (1 + adjustment)

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
            "risk_events": [],
            "protective_actions": [],
        }
        return {"paper": deepcopy(empty_mode), "live": deepcopy(empty_mode)}

    def _write_journal(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.journal is not None:
            self.journal.write(event_type, payload)

    def _record_risk_event(self, event_type: str, timestamp: int, payload: dict[str, Any] | None = None) -> None:
        event = {"timestamp": timestamp, "type": event_type}
        if payload:
            event.update(payload)
        self._details["paper"]["risk_events"].append(event)
        self._write_journal(event_type, event)

    def _record_protective_action(self, action: str, timestamp: int, payload: dict[str, Any] | None = None) -> None:
        event = {"timestamp": timestamp, "action": action}
        if payload:
            event.update(payload)
        self._details["paper"]["protective_actions"].append(event)
        self._write_journal(action, event)

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
                "setup": str(order.get("setup") or ""),
                "quantity": quantity,
                "requested_quantity": float(order.get("requested_quantity") or quantity),
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "status": str(order.get("status") or "filled"),
                "fill_ratio": float(order.get("fill_ratio") or 1),
                "slippage_bps": float(order.get("slippage_bps") or 0),
                "entry_fee": float(order.get("entry_fee") or 0),
            }
        )
        self._position = PaperOpenPosition(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            setup=str(order.get("setup") or ""),
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            initial_stop_price=stop_price,
            target_price=target_price,
            opened_at=timestamp,
            entry_fee=float(order.get("entry_fee") or 0),
        )

    def _restore_closed_position(self, closed: dict[str, Any], journal_time: int) -> None:
        if not closed:
            return
        timestamp = int(closed.get("timestamp") or journal_time)
        side = str(closed.get("side") or "")
        realized_pnl = float(closed.get("net_pnl") or closed.get("realized_pnl") or 0)
        restored_closed = {
            "signal_id": str(closed.get("signal_id") or ""),
            "timestamp": timestamp,
            "symbol": str(closed.get("symbol") or self.symbol).upper(),
            "side": side,
            "setup": str(closed.get("setup") or ""),
            "quantity": float(closed.get("quantity") or 0),
            "entry_price": float(closed.get("entry_price") or 0),
            "close_price": float(closed.get("close_price") or 0),
            "stop_price": float(closed.get("stop_price") or 0),
            "target_price": float(closed.get("target_price") or 0),
            "entry_fee": float(closed.get("entry_fee") or 0),
            "exit_fee": float(closed.get("exit_fee") or 0),
            "gross_pnl": float(closed.get("gross_pnl") or realized_pnl),
            "net_pnl": realized_pnl,
            "pnl_percent": float(closed.get("pnl_percent") or 0),
            "realized_pnl": realized_pnl,
            "opened_at": int(closed.get("opened_at") or timestamp),
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
