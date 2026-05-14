from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.execution.fills import (
    entry_limit_fill_price,
    entry_limit_price,
    exit_fill_price,
    pending_entry_touched,
    position_pnl,
)
from crypto_perp_tool.execution.models import PaperExecutionConfig, PaperOpenPosition, PaperPendingEntry
from crypto_perp_tool.execution.position_rules import (
    absorption_should_reduce,
    break_even_stop_price,
    partial_take_profit_price,
    price_moves,
    should_close_for_orderflow_invalidation,
    trailing_stop_price,
    triggered_close,
)
from crypto_perp_tool.journal import JsonlJournal
from crypto_perp_tool.market_data import AggressionBubble, AggressionBubbleDetector, AtrTracker, QuoteEvent, TradeEvent
from crypto_perp_tool.market_data.latency import compute_exchange_lag_ms
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
        self._pending_entry: PaperPendingEntry | None = None
        self._last_signal_at: int | None = None
        self._last_close_at: int | None = None
        self._realized_pnl = 0.0
        self._consecutive_losses = 0
        self._details = self._empty_details()
        self._markers: list[dict[str, Any]] = []
        self._last_event_time = 0
        self._last_received_at = 0
        self._last_exchange_lag_ms = 0
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
        self._profile_engine = VolumeProfileEngine(
            bin_size=self.bin_size, value_area_ratio=self.settings.profile.value_area_ratio,
        )
        self._last_profile_prune = 0
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
        self._last_exchange_lag_ms = compute_exchange_lag_ms(
            event_time=event.timestamp,
            exchange_event_time=event.exchange_event_time,
            received_at=received_at,
        )
        self._events.append(event)
        self._cumulative_delta += event.delta
        self._atr_1m.update(event)
        self._atr_3m.update(event)
        self._profile_engine.add_trade(event.price, event.quantity, timestamp=event.timestamp)
        if event.timestamp - self._last_profile_prune > 60_000:
            self._profile_engine.prune(event.timestamp - self.rolling_window_ms)
            self._last_profile_prune = event.timestamp
        self._record_aggression_bubble(event)
        self._rolling_delta.append(event.delta)
        self._refresh_indicators(event.timestamp)
        filled_pending_entry = self._try_fill_pending_entry(event)
        if not filled_pending_entry:
            skip_close = self._manage_position(event)
            if not skip_close:
                self._close_position_if_triggered(event)

        if (
            self._position is not None
            or self._pending_entry is not None
            or self._signal_is_in_cooldown(event.timestamp)
            or self._post_close_is_in_cooldown(event.timestamp)
        ):
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

        self._queue_entry(signal, decision.quantity)
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
                "data_lag_ms": self._compute_data_lag(),
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
            exchange_event_time=event.exchange_event_time,
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

    def _compute_data_lag(self) -> int:
        lag_ms = self._last_exchange_lag_ms
        return -1 if lag_ms > 3_600_000 else lag_ms

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
        return self._profile_engine.levels("rolling_4h")

    def _profile_events(self, timestamp: int) -> list[TradeEvent]:
        if timestamp <= 0:
            return []
        return [event for event in self._events if timestamp - event.timestamp <= self.rolling_window_ms]

    def _signal_is_in_cooldown(self, timestamp: int) -> bool:
        if self._last_signal_at is None:
            return False
        return timestamp - self._last_signal_at < self.signal_cooldown_ms

    def _post_close_is_in_cooldown(self, timestamp: int) -> bool:
        if self._last_close_at is None:
            return False
        return timestamp - self._last_close_at < self.execution_config.post_close_cooldown_ms

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

    def _queue_entry(self, signal: TradeSignal, quantity: float) -> None:
        limit_price = entry_limit_price(signal, self.execution_config.limit_entry_pullback_bps)
        self._pending_entry = PaperPendingEntry(
            signal=signal,
            quantity=quantity,
            limit_price=limit_price,
            created_at=signal.created_at,
            expires_at=signal.created_at + self.execution_config.pending_entry_timeout_ms,
        )
        self._write_journal(
            "paper_entry_order",
            {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "entry_order_type": "limit",
                "limit_price": limit_price,
                "status": "pending",
                "expires_at": self._pending_entry.expires_at,
            },
        )

    def _try_fill_pending_entry(self, event: TradeEvent) -> bool:
        if self._pending_entry is None:
            return False

        pending = self._pending_entry
        if event.timestamp > pending.expires_at:
            self._record_risk_event(
                "entry_timeout",
                event.timestamp,
                {"signal_id": pending.signal.id, "limit_price": pending.limit_price},
            )
            self._write_journal(
                "paper_order_cancelled",
                {
                    "signal_id": pending.signal.id,
                    "symbol": pending.signal.symbol,
                    "side": pending.signal.side.value,
                    "entry_order_type": "limit",
                    "limit_price": pending.limit_price,
                    "status": "cancelled",
                },
            )
            self._pending_entry = None
            return False

        if not pending_entry_touched(pending.signal.side, pending.limit_price, event.price):
            return False

        self._pending_entry = None
        self._fill_entry(pending, event)
        return True

    def _fill_entry(self, pending: PaperPendingEntry, event: TradeEvent) -> None:
        signal = pending.signal
        fill_ratio = min(max(self.execution_config.partial_fill_ratio, 0.0), 1.0)
        filled_quantity = pending.quantity * fill_ratio
        if filled_quantity <= 0:
            self._record_risk_event("quantity_below_partial_fill", signal.created_at)
            return

        fill_price = entry_limit_fill_price(signal, pending.limit_price, event.price)
        entry_fee = abs(fill_price * filled_quantity) * self.taker_fee_rate
        order_status = "filled" if fill_ratio >= 1.0 else "partially_filled"
        self._position = PaperOpenPosition(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=signal.side,
            setup=signal.setup,
            quantity=filled_quantity,
            entry_price=fill_price,
            signal_entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            initial_stop_price=signal.stop_price,
            target_price=signal.target_price,
            opened_at=event.timestamp,
            entry_fee=entry_fee,
            initial_quantity=filled_quantity,
        )
        self._details["paper"]["orders"].append(
            {
                "timestamp": event.timestamp,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "setup": signal.setup,
                "quantity": filled_quantity,
                "requested_quantity": pending.quantity,
                "entry_price": fill_price,
                "signal_entry_price": signal.entry_price,
                "entry_order_type": "limit",
                "limit_price": pending.limit_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "status": order_status,
                "fill_ratio": fill_ratio,
                "slippage_bps": 0.0,
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
                "entry_order_type": "limit",
                "limit_price": pending.limit_price,
                "slippage_bps": 0.0,
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
                "signal_entry_price": signal.entry_price,
                "entry_order_type": "limit",
                "limit_price": pending.limit_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "status": order_status,
                "entry_fee": entry_fee,
            },
        )
        if not self.execution_config.stop_submission_success:
            self._handle_stop_submission_failure(signal.created_at)

    def _manage_position(self, event: TradeEvent) -> bool:
        if self._position is None:
            return False
        self._update_max_moves(event.price)
        if self._take_partial_profit(event):
            self._update_trailing_stop(event)
            return True
        self._shift_stop_to_break_even(event)
        self._update_trailing_stop(event)
        self._reduce_for_absorption(event)
        return self._close_for_orderflow_invalidation(event)

    def _update_max_moves(self, price: float) -> None:
        if self._position is None:
            return
        position = self._position
        favorable, adverse = price_moves(position.side, position.entry_price, price)
        position.max_favorable_move = max(position.max_favorable_move, favorable)
        position.max_adverse_move = max(position.max_adverse_move, adverse)

    def _take_partial_profit(self, event: TradeEvent) -> bool:
        if self._position is None or self._position.first_take_profit_done:
            return False
        position = self._position
        trigger_price = partial_take_profit_price(
            position.side,
            entry_price=position.entry_price,
            initial_stop_price=position.initial_stop_price,
            current_price=event.price,
            first_take_profit_r=self.execution_config.first_take_profit_r,
        )
        if trigger_price is None:
            return False
        reduce_quantity = position.quantity * self.execution_config.first_take_profit_ratio
        if reduce_quantity <= 0 or reduce_quantity >= position.quantity:
            return False
        self._reduce_position(event.timestamp, trigger_price, reduce_quantity, "partial_take_profit")
        if self._position is not None:
            self._position.stop_price = self._position.entry_price
            self._position.break_even_shifted = True
            self._position.first_take_profit_done = True
            self._record_protective_action(
                "break_even_shift",
                event.timestamp,
                {"signal_id": self._position.signal_id, "stop_price": self._position.stop_price, "trigger_price": event.price},
            )
        return True

    def _update_trailing_stop(self, event: TradeEvent) -> None:
        if self._position is None:
            return
        position = self._position
        trail_stop = trailing_stop_price(
            position.side,
            entry_price=position.entry_price,
            initial_stop_price=position.initial_stop_price,
            current_stop_price=position.stop_price,
            current_price=event.price,
            atr=self._current_atr(event.price),
            trail_after_r=self.execution_config.trail_after_r,
            trail_atr_multiple=self.execution_config.trail_atr_multiple,
        )
        if trail_stop is None:
            return
        position.stop_price = trail_stop
        position.trail_stop_price = trail_stop
        self._record_protective_action(
            "trail_stop_update",
            event.timestamp,
            {"signal_id": position.signal_id, "stop_price": trail_stop, "trigger_price": event.price},
        )

    def _close_for_orderflow_invalidation(self, event: TradeEvent) -> bool:
        if self._position is None:
            return False
        position = self._position
        mean_abs_delta = abs(sum(self._rolling_delta[-30:]) / max(len(self._rolling_delta[-30:]), 1)) if self._rolling_delta else 0
        baseline = max(mean_abs_delta * 2.0, 10.0)
        if not should_close_for_orderflow_invalidation(
            position.side,
            delta_30s=self._last_delta_30s,
            baseline=baseline,
            entry_price=position.entry_price,
            initial_stop_price=position.initial_stop_price,
            current_price=event.price,
        ):
            return False
        self._close_position(event.timestamp, event.price, "orderflow_invalidation")
        return True

    def _shift_stop_to_break_even(self, event: TradeEvent) -> None:
        if self._position is None or self._position.break_even_shifted:
            return
        position = self._position
        stop_price = break_even_stop_price(
            position.side,
            entry_price=position.entry_price,
            initial_stop_price=position.initial_stop_price,
            current_price=event.price,
        )
        if stop_price is None:
            return
        position.stop_price = stop_price
        position.break_even_shifted = True
        self._record_protective_action("break_even_shift", event.timestamp, {"signal_id": position.signal_id, "stop_price": position.stop_price})

    def _reduce_for_absorption(self, event: TradeEvent) -> None:
        if self._position is None or self._position.absorption_reduced:
            return
        position = self._position
        mean_abs_delta = abs(sum(self._rolling_delta[-30:]) / max(len(self._rolling_delta[-30:]), 1)) if self._rolling_delta else 0
        baseline = max(mean_abs_delta * 2.0, 10.0)
        atr = self._current_atr(event.price)
        if not absorption_should_reduce(
            position.side,
            delta_30s=self._last_delta_30s,
            baseline=baseline,
            entry_price=position.entry_price,
            current_price=event.price,
            atr=atr,
        ):
            return
        reduce_quantity = position.quantity * 0.5
        if reduce_quantity <= 0 or reduce_quantity >= position.quantity:
            return
        close_fill_price = exit_fill_price(position.side, event.price, self.execution_config.exit_slippage_bps)
        gross_pnl = position_pnl(position.side, position.entry_price, position.quantity, close_fill_price) * (reduce_quantity / position.quantity)
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

        close_price, exit_reason = triggered_close(
            self._position.side,
            stop_price=self._position.stop_price,
            target_price=self._position.target_price,
            opened_at=self._position.opened_at,
            current_price=event.price,
            timestamp=event.timestamp,
            max_holding_ms=self.execution_config.max_holding_ms,
            trail_stop_price=self._position.trail_stop_price,
        )
        if close_price is None:
            return

        self._close_position(event.timestamp, close_price, exit_reason or "target")

    def _close_position(self, timestamp: int, trigger_price: float, exit_reason: str) -> None:
        if self._position is None:
            return
        position = self._position
        self._position = None
        self._last_close_at = timestamp
        fill_price = exit_fill_price(position.side, trigger_price, self.execution_config.exit_slippage_bps)
        gross_pnl = position_pnl(position.side, position.entry_price, position.quantity, fill_price)
        exit_fee = abs(fill_price * position.quantity) * self.taker_fee_rate
        net_pnl = gross_pnl - position.entry_fee - exit_fee
        self._realized_pnl += net_pnl
        self._consecutive_losses = self._consecutive_losses + 1 if net_pnl < 0 else 0
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
            "signal_entry_price": position.signal_entry_price,
            "close_price": fill_price,
            "stop_price": position.stop_price,
            "initial_stop_price": position.initial_stop_price,
            "target_price": position.target_price,
            "entry_order_type": position.entry_order_type,
            "entry_fee": position.entry_fee,
            "exit_fee": exit_fee,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "pnl_percent": pnl_percent,
            "realized_pnl": net_pnl,
            "opened_at": position.opened_at,
            "exit_reason": exit_reason,
            "break_even_shifted": position.break_even_shifted,
            "absorption_reduced": position.absorption_reduced,
            "first_take_profit_done": position.first_take_profit_done,
            "trail_stop_price": position.trail_stop_price,
            "max_favorable_move": position.max_favorable_move,
            "max_adverse_move": position.max_adverse_move,
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
        self._markers.append(
            {
                "type": "position_closed",
                "timestamp": timestamp,
                "price": fill_price,
                "label": f"PnL {net_pnl:.2f} ({pnl_percent:+.2f}%)",
                "side": position.side.value,
            }
        )
        self._write_journal("position_closed", closed)

    def _reduce_position(self, timestamp: int, trigger_price: float, quantity: float, exit_reason: str) -> None:
        if self._position is None:
            return
        position = self._position
        original_quantity = position.quantity
        if quantity <= 0 or quantity >= original_quantity:
            return
        close_fill_price = exit_fill_price(position.side, trigger_price, self.execution_config.exit_slippage_bps)
        gross_pnl = position_pnl(position.side, position.entry_price, quantity, close_fill_price)
        entry_fee_portion = position.entry_fee * (quantity / original_quantity)
        exit_fee = abs(close_fill_price * quantity) * self.taker_fee_rate
        net_pnl = gross_pnl - entry_fee_portion - exit_fee
        position.quantity = original_quantity - quantity
        position.entry_fee -= entry_fee_portion
        self._realized_pnl += net_pnl
        entry_notional = position.entry_price * quantity
        pnl_percent = (net_pnl / entry_notional * 100) if entry_notional else 0.0
        reduced = {
            "signal_id": position.signal_id,
            "timestamp": timestamp,
            "symbol": position.symbol,
            "side": position.side.value,
            "setup": position.setup,
            "quantity": quantity,
            "remaining_quantity": position.quantity,
            "entry_price": position.entry_price,
            "signal_entry_price": position.signal_entry_price,
            "close_price": close_fill_price,
            "stop_price": position.stop_price,
            "initial_stop_price": position.initial_stop_price,
            "target_price": position.target_price,
            "entry_order_type": position.entry_order_type,
            "entry_fee": entry_fee_portion,
            "exit_fee": exit_fee,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "pnl_percent": pnl_percent,
            "realized_pnl": net_pnl,
            "opened_at": position.opened_at,
            "exit_reason": exit_reason,
            "break_even_shifted": position.break_even_shifted,
            "absorption_reduced": position.absorption_reduced,
            "first_take_profit_done": position.first_take_profit_done,
            "trail_stop_price": position.trail_stop_price,
            "max_favorable_move": position.max_favorable_move,
            "max_adverse_move": position.max_adverse_move,
            "slippage_bps": self.execution_config.exit_slippage_bps,
        }
        self._details["paper"]["closed_positions"].append(reduced)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": timestamp,
                "symbol": position.symbol,
                "side": position.side.value,
                "realized_pnl": net_pnl,
                "pnl_percent": pnl_percent,
            }
        )
        self._record_protective_action(
            exit_reason,
            timestamp,
            {"signal_id": position.signal_id, "quantity": quantity, "remaining_quantity": position.quantity},
        )
        self._write_journal("position_reduced", reduced)

    def _handle_stop_submission_failure(self, timestamp: int) -> None:
        if self._position is None:
            return
        self._record_risk_event("stop_submission_failed", timestamp)
        self._record_risk_event("circuit_breaker_tripped", timestamp)
        self._record_protective_action("protective_close", timestamp)
        position = self._position
        self._position = None
        close_price = exit_fill_price(position.side, position.entry_price, self.execution_config.exit_slippage_bps)
        gross_pnl = position_pnl(position.side, position.entry_price, position.quantity, close_price)
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
        status = str(order.get("status") or "filled")
        signal_entry_price = float(order.get("signal_entry_price") or entry_price)
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
                "signal_entry_price": signal_entry_price,
                "entry_order_type": str(order.get("entry_order_type") or "market"),
                "limit_price": float(order.get("limit_price") or entry_price),
                "stop_price": stop_price,
                "target_price": target_price,
                "status": status,
                "fill_ratio": float(order.get("fill_ratio") or 1),
                "slippage_bps": float(order.get("slippage_bps") or 0),
                "entry_fee": float(order.get("entry_fee") or 0),
            }
        )
        if status not in {"filled", "partially_filled"}:
            return
        self._position = PaperOpenPosition(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            setup=str(order.get("setup") or ""),
            quantity=quantity,
            entry_price=entry_price,
            signal_entry_price=signal_entry_price,
            stop_price=stop_price,
            initial_stop_price=stop_price,
            target_price=target_price,
            opened_at=timestamp,
            entry_fee=float(order.get("entry_fee") or 0),
            initial_quantity=float(order.get("requested_quantity") or quantity),
            entry_order_type=str(order.get("entry_order_type") or "market"),
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
            "opened_at": int(closed.get("opened_at") or timestamp),
            "exit_reason": str(closed.get("exit_reason") or "unknown"),
            "entry_fee": float(closed.get("entry_fee") or 0),
            "exit_fee": float(closed.get("exit_fee") or 0),
            "gross_pnl": float(closed.get("gross_pnl") or realized_pnl),
            "net_pnl": realized_pnl,
            "pnl_percent": float(closed.get("pnl_percent") or 0),
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
