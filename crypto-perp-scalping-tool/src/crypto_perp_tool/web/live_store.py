from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import RiskSettings, default_settings
from crypto_perp_tool.execution.fills import (
    entry_limit_fill_price,
    entry_limit_price,
    exit_fill_price,
    pending_entry_touched,
    position_pnl,
)
from crypto_perp_tool.execution.position_rules import (
    absorption_should_reduce,
    break_even_stop_price,
    estimated_round_trip_cost,
    kline_momentum_stop_price,
    price_moves,
    should_close_for_orderflow_invalidation,
    triggered_close,
)
from crypto_perp_tool.journal import JsonlJournal, TradeLogger
from crypto_perp_tool.market_data import (
    AggressionBubble,
    AggressionBubbleDetector,
    FlashCrashDetector,
    ForceOrderEvent,
    KlineEvent,
    MarkPriceEvent,
    QuoteEvent,
    SpotPriceEvent,
    TimeWindowBuffer,
    TradeEvent,
)
from crypto_perp_tool.market_data.binance import BinanceInstrumentSpec, default_instrument_spec
from crypto_perp_tool.market_data.health import compute_health
from crypto_perp_tool.market_data.latency import compute_exchange_lag_ms
from crypto_perp_tool.profile import build_profile_levels
from crypto_perp_tool.risk import AccountState, RiskEngine
from crypto_perp_tool.risk.circuit import CircuitBreaker
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.session import SessionDetector
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import (
    CircuitBreakerReason,
    HistoricalWindows,
    MarketSnapshot,
    ProfileLevel,
    SignalSide,
    TradeSignal,
    make_trade_record,
)
from crypto_perp_tool.web.details import empty_execution_details, mode_breakdown, total_pnl_for_range
from crypto_perp_tool.web.strategy_state import cvd_divergence_state, last_action


RANGE_MS = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}
KLINE_HISTORY_LIMIT = 96
KLINE_HISTORY_MS = 8 * 60 * 60 * 1000
KLINE_INTERVAL_MS = 5 * 60 * 1000


def _true_range(kline: KlineEvent, prev_close: float | None) -> float:
    hl = kline.high - kline.low
    if prev_close is None:
        return hl
    return max(hl, abs(kline.high - prev_close), abs(kline.low - prev_close))


def _level_price(levels: tuple[ProfileLevel, ...], level_type: str) -> float:
    for level in levels:
        if level.type.value == level_type:
            return level.price
    return 0.0


class LiveOrderflowStore:
    def __init__(
        self,
        symbol: str,
        max_events: int = 20_000,
        display_events: int = 500,
        enable_signals: bool = False,
        journal_path: Path | str | None = None,
        trade_log_path: Path | str | None = None,
        equity: float = 10_000,
        instrument_spec: BinanceInstrumentSpec | None = None,
        testing_mode: bool = False,
        state_path: Path | str | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.max_events = max_events
        self.display_events = display_events
        self.equity = equity
        self.settings = default_settings()
        self._execution_profile_window_ms = self.settings.profile.execution_window_minutes * 60 * 1000
        self._micro_profile_window_ms = self.settings.profile.micro_window_minutes * 60 * 1000
        self._context_profile_window_ms = self.settings.profile.context_window_minutes * 60 * 1000
        self._profile_window_ms = self._execution_profile_window_ms
        self._events: deque[TradeEvent] = deque(maxlen=max_events)
        self._trade_window = TimeWindowBuffer[TradeEvent](max_window_ms=self._context_profile_window_ms)
        self._profile_quantity = 0.0
        self._profile_notional = 0.0
        self._indicator_windows: dict[int, deque[TradeEvent]] = {
            15_000: deque(),
            30_000: deque(),
            60_000: deque(),
        }
        self._indicator_delta_sums: dict[int, float] = {
            15_000: 0.0,
            30_000: 0.0,
            60_000: 0.0,
        }
        self._volume_30s_sum = 0.0
        self._quote: QuoteEvent | None = None
        self._mark: MarkPriceEvent | None = None
        self._spot: SpotPriceEvent | None = None
        self._connection_status = "starting"
        self._connection_message = "waiting for Binance stream"
        self._reconnect_count = 0
        self._lock = threading.Lock()
        self._view_version = 0
        self._view_cache: dict[str, Any] | None = None
        self._view_cache_version = -1
        self._view_cache_created = 0.0
        self._view_cache_ttl_seconds = 1.0

        self._bin_size = self.settings.profile.btc_bin_size if self.symbol == "BTCUSDT" else self.settings.profile.eth_bin_size
        self._instrument = instrument_spec or default_instrument_spec(self.symbol)
        self._signal_engine = SignalEngine(
            min_reward_risk=self.settings.signals.min_reward_risk,
            max_data_lag_ms=self.settings.execution.max_data_lag_ms,
            session_gating_enabled=self.settings.signals.session_gating_enabled,
            reward_risk=self.settings.execution.reward_risk,
            dynamic_reward_risk_enabled=self.settings.execution.dynamic_reward_risk_enabled,
            reward_risk_min=self.settings.execution.reward_risk_min,
            reward_risk_max=self.settings.execution.reward_risk_max,
            atr_stop_mult=self.settings.execution.atr_stop_mult,
            min_stop_cost_mult=self.settings.execution.min_stop_cost_mult,
            min_target_cost_mult=self.settings.execution.min_target_cost_mult,
            taker_fee_rate=self._instrument.taker_fee_rate,
            execution_window=f"execution_{self.settings.profile.execution_window_minutes}m",
            micro_window=f"micro_{self.settings.profile.micro_window_minutes}m",
            context_window=f"context_{self.settings.profile.context_window_minutes}m",
        ) if enable_signals else None
        self.testing_mode = testing_mode
        self._risk = RiskEngine(self.settings.risk, testing_mode=testing_mode)
        self._journal = JsonlJournal(journal_path, config_version=self.settings.config_version) if journal_path is not None else None
        self._trade_log: TradeLogger | None = TradeLogger(trade_log_path) if trade_log_path is not None else None
        self._circuit_breaker = CircuitBreaker()
        self._signal_count = 0
        self._order_count = 0
        self._rejected_count = 0
        self._closed_positions = 0
        self._realized_pnl = 0.0
        self._consecutive_losses = 0
        self._position: dict[str, Any] | None = None
        self._pending_entry: dict[str, Any] | None = None
        self._historical: HistoricalWindows = HistoricalWindows()
        self._details = empty_execution_details()
        self._markers: list[dict[str, Any]] = []
        self._last_event_time = 0
        self._last_received_at = 0
        self._last_received_monotonic_ms = 0
        self._recent_lags: deque[int] = deque(maxlen=20)
        self._klines: deque[KlineEvent] = deque(maxlen=KLINE_HISTORY_LIMIT)
        self._synthetic_kline_keys: set[tuple[str, int]] = set()
        self._last_delta_15s = 0.0
        self._last_delta_30s = 0.0
        self._last_delta_60s = 0.0
        self._last_volume_30s = 0.0
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
        _atr_period = self.settings.signals.atr_period
        self._tr_1m: deque[float] = deque(maxlen=_atr_period)
        self._tr_3m: deque[float] = deque(maxlen=_atr_period)
        self._prev_close_1m: float | None = None
        self._prev_close_3m: float | None = None
        self._atr_1m_value: float = 0.0
        self._atr_3m_value: float = 0.0
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
        self._flash_crash_detector = FlashCrashDetector()
        self._state_path = Path(state_path) if state_path is not None else None
        self._last_state_save_ms = 0
        self._last_signal_time = -1
        self._last_close_time = -1
        self._signal_cooldown_ms = 30_000
        self._last_signal_reasons: tuple[str, ...] = ()
        self._last_reject_reasons: tuple[str, ...] = ()
        self._restored_state_info = self._restore_state()

    def _invalidate_view_cache(self) -> None:
        self._view_version += 1

    def add_trade(self, event: TradeEvent, received_at: int | None = None) -> None:
        if event.symbol.upper() != self.symbol:
            return
        received_at = int(time.time() * 1000) if received_at is None else int(received_at)
        received_monotonic_ms = int(time.monotonic() * 1000)
        with self._lock:
            self._invalidate_view_cache()
            self._flash_crash_detector.add_price(event.timestamp, event.price)
            self._events.append(event)
            evicted_events = self._trade_window.append(event.timestamp, event)
            self._profile_quantity += event.quantity
            self._profile_notional += event.price * event.quantity
            for evicted in evicted_events:
                self._profile_quantity -= evicted.quantity
                self._profile_notional -= evicted.price * evicted.quantity
            if self._profile_quantity < 0:
                self._profile_quantity = 0.0
                self._profile_notional = 0.0
            self._last_event_time = event.timestamp
            self._last_received_at = received_at
            self._last_received_monotonic_ms = received_monotonic_ms
            self._recent_lags.append(
                compute_exchange_lag_ms(
                    event_time=event.timestamp,
                    exchange_event_time=event.exchange_event_time,
                    received_at=received_at,
                )
            )
            self._update_trade_kline(event)
            self._cumulative_delta += event.delta
            self._record_aggression_bubble(event)
            self._refresh_indicators(event)
            filled_pending_entry = self._try_fill_pending_entry(event)
            if not filled_pending_entry:
                skip_close = self._manage_position(event)
                if not skip_close:
                    self._try_close(event)
            self._update_historical(event)
            if not self.testing_mode and self._circuit_breaker.state != "tripped":
                atr = self._current_atr(event.price)
                if self._flash_crash_detector.detect(event.timestamp, atr):
                    result = self._circuit_breaker.trip(CircuitBreakerReason.FLASH_CRASH_DETECTED)
                    self._write_journal("flash_crash_detected", result)
                    self._markers.append({
                        "type": "flash_crash",
                        "timestamp": event.timestamp,
                        "price": event.price,
                        "label": "FLASH CRASH",
                    })
                    self._save_state()
            self._try_signal(event, received_at)
            self._refresh_pnl_ranges()
            now_ms = int(time.time() * 1000)
            if now_ms - self._last_state_save_ms > 60_000:
                self._save_state()
                self._last_state_save_ms = now_ms

    def add_force_order(self, event: ForceOrderEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._invalidate_view_cache()
            self._last_event_time = event.timestamp
            self._last_received_at = int(time.time() * 1000)

    def add_quote(self, event: QuoteEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._invalidate_view_cache()
            self._quote = event

    def add_mark(self, event: MarkPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._invalidate_view_cache()
            self._mark = event

    def add_spot(self, event: SpotPriceEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._invalidate_view_cache()
            self._spot = event

    def add_kline(self, event: KlineEvent) -> None:
        if event.symbol.upper() != self.symbol:
            return
        with self._lock:
            self._invalidate_view_cache()
            self._upsert_kline(event, synthetic=False)
            self._update_atr_from_kline(event)

    def seed_klines(self, events: list[KlineEvent] | tuple[KlineEvent, ...]) -> None:
        with self._lock:
            self._invalidate_view_cache()
            for event in events:
                if event.symbol.upper() == self.symbol:
                    self._upsert_kline(event, synthetic=False)
            self._init_atr_from_klines()

    def _update_trade_kline(self, event: TradeEvent) -> None:
        bucket_start = (event.timestamp // KLINE_INTERVAL_MS) * KLINE_INTERVAL_MS
        key = ("5m", bucket_start)
        existing = self._find_kline(*key)
        if existing is None:
            self._upsert_kline(
                KlineEvent(
                    timestamp=bucket_start,
                    close_time=bucket_start + KLINE_INTERVAL_MS - 1,
                    symbol=event.symbol.upper(),
                    interval="5m",
                    open=event.price,
                    high=event.price,
                    low=event.price,
                    close=event.price,
                    volume=event.quantity,
                    quote_volume=event.price * event.quantity,
                    trade_count=1,
                    is_closed=False,
                ),
                synthetic=True,
            )
            return

        high = max(existing.high, event.price)
        low = min(existing.low, event.price)
        if key in self._synthetic_kline_keys:
            self._upsert_kline(
                replace(
                    existing,
                    high=high,
                    low=low,
                    close=event.price,
                    volume=existing.volume + event.quantity,
                    quote_volume=existing.quote_volume + event.price * event.quantity,
                    trade_count=existing.trade_count + 1,
                    is_closed=False,
                ),
                synthetic=True,
            )
            return

        latest_kline_ts = max((kline.timestamp for kline in self._klines), default=bucket_start)
        if existing.is_closed and bucket_start < latest_kline_ts:
            return
        self._upsert_kline(
            replace(existing, high=high, low=low, close=event.price, is_closed=False),
            synthetic=False,
        )

    def _find_kline(self, interval: str, timestamp: int) -> KlineEvent | None:
        for event in self._klines:
            if event.interval == interval and event.timestamp == timestamp:
                return event
        return None

    def _upsert_kline(self, event: KlineEvent, synthetic: bool = False) -> None:
        key = (event.interval, event.timestamp)
        for index, existing in enumerate(self._klines):
            if (existing.interval, existing.timestamp) == key:
                self._klines[index] = event
                break
        else:
            self._klines.append(event)

        if synthetic:
            self._synthetic_kline_keys.add(key)
        else:
            self._synthetic_kline_keys.discard(key)

        ordered = sorted(self._klines, key=lambda kline: kline.timestamp)
        if ordered:
            cutoff = ordered[-1].timestamp - KLINE_HISTORY_MS
            ordered = [kline for kline in ordered if kline.timestamp > cutoff]
        self._klines = deque(ordered[-KLINE_HISTORY_LIMIT:], maxlen=KLINE_HISTORY_LIMIT)
        active_keys = {(kline.interval, kline.timestamp) for kline in self._klines}
        self._synthetic_kline_keys.intersection_update(active_keys)

    def _update_atr_from_kline(self, event: KlineEvent) -> None:
        if not event.is_closed:
            return
        if event.interval == "1m":
            tr = _true_range(event, self._prev_close_1m)
            self._tr_1m.append(tr)
            self._prev_close_1m = event.close
            self._atr_1m_value = sum(self._tr_1m) / len(self._tr_1m)
        elif event.interval == "3m":
            tr = _true_range(event, self._prev_close_3m)
            self._tr_3m.append(tr)
            self._prev_close_3m = event.close
            self._atr_3m_value = sum(self._tr_3m) / len(self._tr_3m)

    def _init_atr_from_klines(self) -> None:
        """Recompute 1m and 3m ATR from all completed klines in storage."""
        for interval, tr_deque_ref, prev_close_ref in (
            ("1m", self._tr_1m, "_prev_close_1m"),
            ("3m", self._tr_3m, "_prev_close_3m"),
        ):
            completed = sorted(
                [k for k in self._klines if k.interval == interval and k.is_closed],
                key=lambda k: k.timestamp,
            )
            tr_deque_ref.clear()
            prev_close: float | None = None
            atr_value = 0.0
            for bar in completed:
                tr = _true_range(bar, prev_close)
                tr_deque_ref.append(tr)
                prev_close = bar.close
                atr_value = sum(tr_deque_ref) / len(tr_deque_ref)
            setattr(self, prev_close_ref, prev_close)
            if interval == "1m":
                self._atr_1m_value = atr_value
            else:
                self._atr_3m_value = atr_value

    def set_connection_status(self, status: str, message: str) -> None:
        with self._lock:
            self._invalidate_view_cache()
            previous = self._connection_status
            self._connection_status = status
            self._connection_message = message
            if status == "connected" and previous == "error":
                self._reconnect_count += 1

    def resume_circuit(self, actor: str = "web") -> dict:
        with self._lock:
            if self._circuit_breaker.state != "tripped":
                return {"resumed": False, "reason": "circuit is not tripped"}
            ok = self._circuit_breaker.can_resume(
                account_ok=True,
                data_healthy=self._connection_status == "connected",
                positions_reconciled=self._position is None,
                daily_loss_within_limit=self._details["paper"]["pnl_by_range"]["24h"] > -self.settings.risk.daily_loss_limit * self.equity,
            )
            if not ok:
                return {"resumed": False, "reason": "resume conditions not met"}
            self._invalidate_view_cache()
            event = self._circuit_breaker.resume(actor=actor)
            self._write_journal("circuit_breaker_resumed", event)
            self._save_state()
            return {"resumed": True, "state": self._circuit_breaker.state}

    def update_risk_settings(self, new_risk: RiskSettings) -> None:
        with self._lock:
            self.settings = replace(self.settings, risk=new_risk)
            self._risk = RiskEngine(new_risk, testing_mode=self.testing_mode)

    def update_equity(self, equity: float) -> None:
        with self._lock:
            self.equity = equity
            self._invalidate_view_cache()

    def update_circuit_cooldown(self, cooldown_ms: int) -> None:
        with self._lock:
            self._circuit_breaker.hard_cooldown_ms = cooldown_ms

    def update_flash_crash_params(self, atr_multiplier: float | None = None, pct_threshold: float | None = None) -> None:
        with self._lock:
            if atr_multiplier is not None:
                self._flash_crash_detector.atr_multiplier = atr_multiplier
            if pct_threshold is not None:
                self._flash_crash_detector.pct_threshold = pct_threshold

    def update_strategy_params(self, reward_risk: float | None = None, atr_stop_mult: float | None = None,
                               dynamic_reward_risk_enabled: bool | None = None,
                               reward_risk_min: float | None = None,
                               reward_risk_max: float | None = None,
                               min_stop_cost_mult: float | None = None,
                               min_target_cost_mult: float | None = None, max_holding_ms: int | None = None,
                               kline_stop_shift_consecutive_bars: int | None = None,
                               kline_stop_shift_reference_bars: int | None = None) -> None:
        with self._lock:
            exec_settings = self.settings.execution
            kwargs: dict[str, Any] = {}
            if reward_risk is not None:
                kwargs["reward_risk"] = float(reward_risk)
            if dynamic_reward_risk_enabled is not None:
                kwargs["dynamic_reward_risk_enabled"] = bool(dynamic_reward_risk_enabled)
            if reward_risk_min is not None:
                kwargs["reward_risk_min"] = float(reward_risk_min)
            if reward_risk_max is not None:
                kwargs["reward_risk_max"] = float(reward_risk_max)
            if atr_stop_mult is not None:
                kwargs["atr_stop_mult"] = float(atr_stop_mult)
            if kline_stop_shift_consecutive_bars is not None:
                kwargs["kline_stop_shift_consecutive_bars"] = int(kline_stop_shift_consecutive_bars)
            if kline_stop_shift_reference_bars is not None:
                kwargs["kline_stop_shift_reference_bars"] = int(kline_stop_shift_reference_bars)
            if min_stop_cost_mult is not None:
                kwargs["min_stop_cost_mult"] = float(min_stop_cost_mult)
            if min_target_cost_mult is not None:
                kwargs["min_target_cost_mult"] = float(min_target_cost_mult)
            if max_holding_ms is not None:
                kwargs["max_holding_ms"] = int(max_holding_ms)
            if kwargs:
                new_exec = replace(exec_settings, **kwargs)
                self.settings = replace(self.settings, execution=new_exec)
                if self._signal_engine is not None:
                    if reward_risk is not None:
                        self._signal_engine.reward_risk = float(reward_risk)
                    if dynamic_reward_risk_enabled is not None:
                        self._signal_engine.dynamic_reward_risk_enabled = bool(dynamic_reward_risk_enabled)
                    if reward_risk_min is not None:
                        self._signal_engine.reward_risk_min = float(reward_risk_min)
                    if reward_risk_max is not None:
                        self._signal_engine.reward_risk_max = float(reward_risk_max)
                    if atr_stop_mult is not None:
                        self._signal_engine.atr_stop_mult = float(atr_stop_mult)
                    if min_stop_cost_mult is not None:
                        self._signal_engine.min_stop_cost_mult = float(min_stop_cost_mult)
                    if min_target_cost_mult is not None:
                        self._signal_engine.min_target_cost_mult = float(min_target_cost_mult)

    def _refresh_indicators(self, event: TradeEvent) -> None:
        for window_ms in self._indicator_windows:
            self._update_indicator_window(window_ms, event)
        self._last_delta_15s = self._indicator_delta_sums[15_000]
        self._last_delta_30s = self._indicator_delta_sums[30_000]
        self._last_delta_60s = self._indicator_delta_sums[60_000]
        self._last_volume_30s = self._volume_30s_sum
        self._last_vwap = self._profile_notional / self._profile_quantity if self._profile_quantity > 0 else 0.0

    def _update_indicator_window(self, window_ms: int, event: TradeEvent) -> None:
        window = self._indicator_windows[window_ms]
        window.append(event)
        self._indicator_delta_sums[window_ms] += event.delta
        if window_ms == 30_000:
            self._volume_30s_sum += abs(event.delta)
        cutoff = event.timestamp - window_ms
        while window and window[0].timestamp < cutoff:
            evicted = window.popleft()
            self._indicator_delta_sums[window_ms] -= evicted.delta
            if window_ms == 30_000:
                self._volume_30s_sum -= abs(evicted.delta)

    def _vwap(self, timestamp: int, window_ms: int) -> float:
        events = self._trade_window.items_since(timestamp, window_ms)
        quantity = sum(event.quantity for event in events)
        if quantity <= 0:
            return 0.0
        return sum(event.price * event.quantity for event in events) / quantity

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
        settings = self.settings.profile
        events = self._trade_window.items_since(timestamp, self._context_profile_window_ms)
        trades = [(event.price, event.quantity, event.timestamp) for event in events]
        return (
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=self._execution_profile_window_ms,
                label=f"execution_{settings.execution_window_minutes}m",
                bin_size=self._bin_size,
                value_area_ratio=settings.value_area_ratio,
                min_trades=settings.min_execution_profile_trades,
                min_bins=settings.min_profile_bins,
            ),
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=self._micro_profile_window_ms,
                label=f"micro_{settings.micro_window_minutes}m",
                bin_size=self._bin_size,
                value_area_ratio=settings.value_area_ratio,
                min_trades=settings.min_micro_profile_trades,
                min_bins=settings.min_profile_bins,
            ),
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=self._context_profile_window_ms,
                label=f"context_{settings.context_window_minutes}m",
                bin_size=self._bin_size,
                value_area_ratio=settings.value_area_ratio,
            ),
        )

    def _update_historical(self, event: TradeEvent) -> None:
        spread = self._spread_bps(event)
        self._historical = self._historical.with_window("spread_5min", spread)
        self._historical = self._historical.with_window("delta_30s", self._last_delta_30s)
        self._historical = self._historical.with_window("volume_30s", self._last_volume_30s)
        self._historical = self._historical.with_window("amplitude_1m", self._current_atr(event.price))

    def _current_atr(self, fallback_price: float) -> float:
        if self._atr_1m_value > 0:
            return self._atr_1m_value
        return max(fallback_price * 0.002, self._bin_size / 2)

    def _try_signal(self, event: TradeEvent, received_at: int) -> None:
        if self._signal_engine is None:
            return
        if not self.testing_mode and self._circuit_breaker.state == "tripped":
            self._last_reject_reasons = ("circuit_breaker_tripped",)
            return
        if self._position is not None or self._pending_entry is not None or len(self._events) < 30:
            return
        if self._last_signal_time >= 0 and event.timestamp - self._last_signal_time < self._signal_cooldown_ms:
            return
        if self._last_close_time >= 0 and event.timestamp - self._last_close_time < self.settings.execution.post_close_cooldown_ms:
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
        bubble = self._last_bubble if self._last_bubble is not None and self._last_bubble.timestamp == event.timestamp else None
        return MarketSnapshot(
            exchange=self.settings.exchange,
            symbol=self.symbol,
            event_time=event.timestamp,
            local_time=received_at,
            exchange_event_time=event.exchange_event_time,
            last_price=event.price,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_bps=self._spread_bps(event),
            vwap=self._last_vwap,
            atr_1m_14=self._current_atr(event.price),
            delta_15s=self._last_delta_15s,
            delta_30s=self._last_delta_30s,
            delta_60s=self._last_delta_60s,
            volume_30s=self._last_volume_30s,
            profile_levels=tuple(self._profile_levels(event.timestamp)),
            atr_3m_14=self._atr_3m_value,
            cumulative_delta=self._cumulative_delta,
            aggression_bubble_side=bubble.side if bubble else None,
            aggression_bubble_quantity=bubble.quantity if bubble else 0.0,
            aggression_bubble_price=bubble.price if bubble else None,
            aggression_bubble_tier=bubble.tier if bubble else None,
            session=self._session_detector.detect(event.timestamp).value,
        )

    def _median_recent_lag(self) -> int:
        if not self._recent_lags:
            return 0
        sorted_lags = sorted(self._recent_lags)
        n = len(sorted_lags)
        return sorted_lags[n // 2] if n % 2 else (sorted_lags[n // 2 - 1] + sorted_lags[n // 2]) // 2

    def _min_recent_lag(self) -> int:
        if not self._recent_lags:
            return 0
        return min(self._recent_lags)

    def _stream_freshness_ms(self, last_received_at: int | None = None) -> int:
        if not self._last_received_monotonic_ms:
            return -1
        return max(0, int(time.monotonic() * 1000) - int(self._last_received_monotonic_ms))

    def _spread_bps(self, event: TradeEvent) -> float:
        return self._spread_bps_from_quote()

    def _spread_bps_from_quote(self) -> float:
        if self._quote is None:
            return 2.0
        return (self._quote.ask_price - self._quote.bid_price) / self._quote.mid_price * 10_000

    def _record_signal(self, signal: TradeSignal) -> None:
        self._signal_count += 1
        self._last_signal_time = signal.created_at
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
            "target_r_multiple": signal.target_r_multiple,
            "confidence": signal.confidence,
            "reasons": list(signal.reasons),
        }
        self._details["paper"]["signals"].append(record)
        self._markers.append(
            {"type": "signal", "timestamp": signal.created_at, "price": signal.entry_price, "label": signal.setup}
        )
        self._write_journal("signal", {"signal": signal})

    def _open_position(self, signal: TradeSignal, quantity: float, slippage_bps: float) -> None:
        raw_limit_price = entry_limit_price(signal, self.settings.execution.limit_entry_pullback_bps)
        limit_price = self._round_price(raw_limit_price, "down" if signal.side == SignalSide.LONG else "up")
        self._pending_entry = {
            "signal": signal,
            "quantity": quantity,
            "limit_price": limit_price,
            "created_at": signal.created_at,
            "expires_at": signal.created_at + self.settings.execution.pending_entry_timeout_ms,
            "slippage_bps": slippage_bps,
        }
        self._write_journal(
            "paper_entry_order",
            {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "entry_order_type": "limit",
                "limit_price": limit_price,
                "status": "pending",
                "expires_at": self._pending_entry["expires_at"],
            },
        )

    def _try_fill_pending_entry(self, event: TradeEvent) -> bool:
        if self._pending_entry is None:
            return False
        pending = self._pending_entry
        signal = pending["signal"]
        if event.timestamp > int(pending["expires_at"]):
            self._write_journal(
                "paper_order_cancelled",
                {
                    "signal_id": signal.id,
                    "symbol": signal.symbol,
                    "side": signal.side.value,
                    "entry_order_type": "limit",
                    "limit_price": pending["limit_price"],
                    "status": "cancelled",
                },
            )
            self._last_reject_reasons = ("entry_timeout",)
            self._record_risk_event("entry_timeout", event.timestamp, {"signal_id": signal.id, "limit_price": pending["limit_price"]})
            self._pending_entry = None
            return False
        if not pending_entry_touched(signal.side, float(pending["limit_price"]), event.price):
            return False
        self._pending_entry = None
        self._fill_pending_entry(pending, event)
        return True

    def _fill_pending_entry(self, pending: dict[str, Any], event: TradeEvent) -> None:
        signal: TradeSignal = pending["signal"]
        fill = self._entry_limit_fill(signal, float(pending["quantity"]), float(pending["limit_price"]), event.price)
        if fill["quantity"] <= 0:
            self._rejected_count += 1
            self._last_reject_reasons = ("quantity_below_step_size",)
            self._write_journal(
                "signal_rejected",
                {"signal_id": signal.id, "symbol": signal.symbol, "reject_reasons": self._last_reject_reasons},
            )
            return

        entry_session = self._session_detector.detect(signal.created_at).value
        atr = self._current_atr(fill["fill_price"])
        spread = self._spread_bps_from_quote()
        levels = self._profile_levels(signal.created_at)
        poc = _level_price(levels, "POC")
        vah = _level_price(levels, "VAH")
        val = _level_price(levels, "VAL")

        self._position = {
            "signal_id": signal.id,
            "symbol": signal.symbol,
            "side": signal.side,
            "setup": signal.setup,
            "quantity": fill["quantity"],
            "entry_price": fill["fill_price"],
            "signal_entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "initial_stop_price": signal.stop_price,
            "target_price": signal.target_price,
            "target_r_multiple": signal.target_r_multiple,
            "entry_fee": fill["fee"],
            "opened_at": event.timestamp,
            "break_even_shifted": False,
            "absorption_reduced": False,
            "first_take_profit_done": False,
            "trail_stop_price": None,
            "max_favorable_move": 0.0,
            "max_adverse_move": 0.0,
            "initial_quantity": fill["quantity"],
            "entry_order_type": "limit",
            "entry_session": entry_session,
            "vwap_at_entry": self._last_vwap,
            "atr_at_entry": atr,
            "spread_bps_at_entry": spread,
            "poc_at_entry": poc,
            "vah_at_entry": vah,
            "val_at_entry": val,
        }
        order = {
            "timestamp": event.timestamp,
            "symbol": signal.symbol,
            "side": signal.side.value,
            "quantity": fill["quantity"],
            "entry_price": fill["fill_price"],
            "signal_entry_price": signal.entry_price,
            "entry_order_type": "limit",
            "limit_price": pending["limit_price"],
            "stop_price": signal.stop_price,
            "target_price": signal.target_price,
            "target_r_multiple": signal.target_r_multiple,
            "status": "filled",
            "fee": fill["fee"],
            "slippage_bps": 0.0,
        }
        self._order_count += 1
        self._details["paper"]["orders"].append(order)
        self._write_journal(
            "paper_fill",
            fill | {
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.side.value,
                "entry_order_type": "limit",
                "limit_price": pending["limit_price"],
            },
        )
        self._write_journal("paper_order", order | {"signal_id": signal.id})

    def _manage_position(self, event: TradeEvent) -> bool:
        if self._position is None:
            return False
        self._update_max_moves(event.price)
        self._shift_stop_to_break_even(event)
        self._shift_stop_after_kline_momentum(event)
        self._reduce_for_absorption(event)
        return self._close_for_orderflow_invalidation(event)

    def _update_max_moves(self, price: float) -> None:
        if self._position is None:
            return
        side = self._position["side"]
        entry = float(self._position["entry_price"])
        favorable, adverse = price_moves(side, entry, price)
        if favorable > self._position["max_favorable_move"]:
            self._position["max_favorable_move"] = favorable
        if adverse > self._position["max_adverse_move"]:
            self._position["max_adverse_move"] = adverse

    def _shift_stop_to_break_even(self, event: TradeEvent) -> None:
        if self._position is None or self._position.get("break_even_shifted"):
            return
        position = self._position
        initial_stop = float(position.get("initial_stop_price") or position["stop_price"])
        entry = float(position["entry_price"])
        side = position["side"]
        target_r_multiple = float(position.get("target_r_multiple") or self.settings.execution.reward_risk)
        round_trip_cost = estimated_round_trip_cost(entry, self._instrument.taker_fee_rate)
        stop_price = break_even_stop_price(
            side,
            entry_price=entry,
            initial_stop_price=initial_stop,
            current_price=event.price,
            break_even_trigger_r=target_r_multiple / 2.0,
            round_trip_cost=round_trip_cost,
        )
        if stop_price is None:
            return
        position["stop_price"] = stop_price
        position["break_even_shifted"] = True
        self._record_protective_action(
            "break_even_shift",
            event.timestamp,
            {"signal_id": position["signal_id"], "stop_price": stop_price, "trigger_price": event.price},
        )
        self._markers.append(
            {
                "type": "break_even_shift",
                "timestamp": event.timestamp,
                "price": stop_price,
                "label": "BE",
                "side": side.value,
            }
        )

    def _shift_stop_after_kline_momentum(self, event: TradeEvent) -> None:
        if self._position is None:
            return
        position = self._position
        side = position["side"]
        stop_price = kline_momentum_stop_price(
            side,
            opened_at=int(position.get("opened_at", event.timestamp)),
            current_stop_price=float(position["stop_price"]),
            current_price=event.price,
            closed_klines=tuple(kline for kline in self._klines if kline.interval == "1m" and kline.is_closed),
            consecutive_bars=self.settings.execution.kline_stop_shift_consecutive_bars,
            reference_bars=self.settings.execution.kline_stop_shift_reference_bars,
        )
        if stop_price is None:
            return
        position["stop_price"] = stop_price
        self._record_protective_action(
            "kline_momentum_stop_shift",
            event.timestamp,
            {
                "signal_id": position["signal_id"],
                "side": side.value,
                "stop_price": stop_price,
                "trigger_price": event.price,
                "consecutive_bars": self.settings.execution.kline_stop_shift_consecutive_bars,
                "reference_bars": self.settings.execution.kline_stop_shift_reference_bars,
            },
        )
        self._markers.append(
            {
                "type": "kline_momentum_stop_shift",
                "timestamp": event.timestamp,
                "price": stop_price,
                "label": "1m stop",
                "side": side.value,
            }
        )

    def _reduce_for_absorption(self, event: TradeEvent) -> None:
        if self._position is None or self._position.get("absorption_reduced"):
            return
        position = self._position
        side = position["side"]
        baseline = max(abs(self._historical.mean_delta_30s()) * 2.0, 10.0)
        atr = self._current_atr(event.price)
        entry = float(position["entry_price"])
        if not absorption_should_reduce(
            side,
            delta_30s=self._last_delta_30s,
            baseline=baseline,
            entry_price=entry,
            current_price=event.price,
            atr=atr,
        ):
            return
        reduce_quantity = self._round_quantity(float(position["quantity"]) * 0.5)
        if reduce_quantity <= 0 or reduce_quantity >= float(position["quantity"]):
            return
        self._record_risk_event(
            "absorption_detected",
            event.timestamp,
            {
                "signal_id": position["signal_id"],
                "delta_30s": self._last_delta_30s,
                "price_displacement": abs(event.price - entry),
                "atr": atr,
            },
        )
        self._reduce_position(event, reduce_quantity, "absorption_reduce")

    def _close_for_orderflow_invalidation(self, event: TradeEvent) -> bool:
        if self._position is None:
            return False
        position = self._position
        entry = float(position["entry_price"])
        initial_stop = float(position.get("initial_stop_price") or position["stop_price"])
        side = position["side"]
        baseline = max(abs(self._historical.mean_delta_30s()) * 2.0, 10.0)
        if not should_close_for_orderflow_invalidation(
            side,
            delta_30s=self._last_delta_30s,
            baseline=baseline,
            entry_price=entry,
            initial_stop_price=initial_stop,
            current_price=event.price,
        ):
            return False
        self._close_position(event, event.price, "orderflow_invalidation")
        return True

    def _reduce_position(self, event: TradeEvent, quantity: float, reason: str, trigger_price: float | None = None) -> None:
        if self._position is None:
            return
        position = self._position
        original_quantity = float(position["quantity"])
        close_position = dict(position)
        close_position["quantity"] = quantity
        close_fill = self._exit_fill(close_position, event.price if trigger_price is None else trigger_price)
        gross_pnl = position_pnl(
            close_position["side"],
            float(close_position["entry_price"]),
            float(close_position["quantity"]),
            float(close_fill["fill_price"]),
        )
        entry_fee = float(position["entry_fee"]) * (quantity / original_quantity)
        net_pnl = gross_pnl - entry_fee - close_fill["fee"]
        position["quantity"] = self._round_quantity(original_quantity - quantity)
        position["entry_fee"] = float(position["entry_fee"]) - entry_fee
        if reason == "absorption_reduce":
            position["absorption_reduced"] = True
        self._realized_pnl += net_pnl
        reduced = {
            "timestamp": event.timestamp,
            "signal_id": position["signal_id"],
            "symbol": position["symbol"],
            "side": position["side"].value,
            "setup": position.get("setup", "unknown"),
            "quantity": quantity,
            "remaining_quantity": position["quantity"],
            "entry_price": position["entry_price"],
            "signal_entry_price": position.get("signal_entry_price", position["entry_price"]),
            "close_price": close_fill["fill_price"],
            "stop_price": position["stop_price"],
            "initial_stop_price": position.get("initial_stop_price", position["stop_price"]),
            "target_price": position["target_price"],
            "target_r_multiple": position.get("target_r_multiple", self.settings.execution.reward_risk),
            "entry_order_type": position.get("entry_order_type", "market"),
            "gross_realized_pnl": gross_pnl,
            "entry_fee": entry_fee,
            "close_fee": close_fill["fee"],
            "fee": entry_fee + close_fill["fee"],
            "realized_pnl": net_pnl,
            "net_realized_pnl": net_pnl,
            "exit_reason": reason,
            "opened_at": position.get("opened_at", event.timestamp),
            "break_even_shifted": bool(position.get("break_even_shifted", False)),
            "absorption_reduced": bool(position.get("absorption_reduced", False)),
            "first_take_profit_done": bool(position.get("first_take_profit_done", False)),
            "trail_stop_price": position.get("trail_stop_price"),
            "max_favorable_move": position.get("max_favorable_move", 0.0),
            "max_adverse_move": position.get("max_adverse_move", 0.0),
        }
        self._details["paper"]["closed_positions"].append(reduced)
        self._details["paper"]["pnl_events"].append(
            {
                "timestamp": event.timestamp,
                "symbol": position["symbol"],
                "side": position["side"].value,
                "realized_pnl": net_pnl,
            }
        )
        self._closed_positions += 1
        self._record_protective_action(
            reason,
            event.timestamp,
            {"signal_id": position["signal_id"], "quantity": quantity, "remaining_quantity": position["quantity"]},
        )
        self._markers.append(
            {
                "type": reason,
                "timestamp": event.timestamp,
                "price": close_fill["fill_price"],
                "label": "Absorb reduce",
                "side": position["side"].value,
            }
        )
        self._write_journal("position_reduced", reduced)
        reduce_position_ctx = dict(position)
        reduce_position_ctx["quantity"] = quantity
        self._write_trade_record(reduce_position_ctx, event.timestamp, close_fill, gross_pnl, net_pnl, reason)
        self._save_state()

    def _try_close(self, event: TradeEvent) -> None:
        if self._position is None:
            return
        trigger_price, exit_reason = triggered_close(
            self._position["side"],
            stop_price=float(self._position["stop_price"]),
            target_price=float(self._position["target_price"]),
            opened_at=int(self._position.get("opened_at", event.timestamp)),
            current_price=event.price,
            timestamp=event.timestamp,
            max_holding_ms=self.settings.execution.max_holding_ms,
            trail_stop_price=self._position.get("trail_stop_price"),
        )
        if trigger_price is None:
            return
        self._close_position(event, trigger_price, exit_reason or "target")

    def _close_position(self, event: TradeEvent, trigger_price: float, exit_reason: str) -> None:
        if self._position is None:
            return

        position = self._position
        self._position = None
        self._last_close_time = event.timestamp
        close_fill = self._exit_fill(position, trigger_price)
        gross_pnl = position_pnl(
            position["side"],
            float(position["entry_price"]),
            float(position["quantity"]),
            float(close_fill["fill_price"]),
        )
        net_pnl = gross_pnl - position["entry_fee"] - close_fill["fee"]
        self._realized_pnl += net_pnl
        self._consecutive_losses = self._consecutive_losses + 1 if net_pnl < 0 else 0
        self._closed_positions += 1
        closed = {
            "timestamp": event.timestamp,
            "signal_id": position["signal_id"],
            "symbol": position["symbol"],
            "side": position["side"].value,
            "setup": position.get("setup", "unknown"),
            "quantity": position["quantity"],
            "entry_price": position["entry_price"],
            "signal_entry_price": position.get("signal_entry_price", position["entry_price"]),
            "close_price": close_fill["fill_price"],
            "stop_price": position["stop_price"],
            "initial_stop_price": position.get("initial_stop_price", position["stop_price"]),
            "target_price": position["target_price"],
            "target_r_multiple": position.get("target_r_multiple", self.settings.execution.reward_risk),
            "entry_order_type": position.get("entry_order_type", "market"),
            "gross_realized_pnl": gross_pnl,
            "entry_fee": position["entry_fee"],
            "close_fee": close_fill["fee"],
            "fee": position["entry_fee"] + close_fill["fee"],
            "realized_pnl": net_pnl,
            "net_realized_pnl": net_pnl,
            "exit_reason": exit_reason,
            "opened_at": position.get("opened_at", event.timestamp),
            "break_even_shifted": bool(position.get("break_even_shifted", False)),
            "absorption_reduced": bool(position.get("absorption_reduced", False)),
            "first_take_profit_done": bool(position.get("first_take_profit_done", False)),
            "trail_stop_price": position.get("trail_stop_price"),
            "max_favorable_move": position.get("max_favorable_move", 0.0),
            "max_adverse_move": position.get("max_adverse_move", 0.0),
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
        self._write_trade_record(position, event.timestamp, close_fill, gross_pnl, net_pnl, exit_reason)
        self._save_state()

    def _entry_limit_fill(self, signal: TradeSignal, quantity: float, limit_price: float, event_price: float) -> dict[str, float | str]:
        quantity = self._round_quantity(quantity)
        raw_fill_price = entry_limit_fill_price(signal, limit_price, event_price)
        if signal.side == SignalSide.LONG:
            fill_price = self._round_price(raw_fill_price, "down")
            action = "buy"
        else:
            fill_price = self._round_price(raw_fill_price, "up")
            action = "sell"
        return {
            "action": action,
            "quantity": quantity,
            "reference_price": limit_price,
            "fill_price": fill_price,
            "slippage_bps": 0.0,
            "fee": abs(fill_price * quantity) * self._instrument.taker_fee_rate,
        }

    def _exit_fill(self, position: dict[str, Any], trigger_price: float) -> dict[str, float | str]:
        slippage_bps = self.settings.execution.btc_max_slippage_bps if self.symbol == "BTCUSDT" else self.settings.execution.eth_max_slippage_bps
        raw_fill_price = exit_fill_price(position["side"], trigger_price, slippage_bps)
        if position["side"] == SignalSide.LONG:
            fill_price = self._round_price(raw_fill_price, "down")
            action = "sell"
        else:
            fill_price = self._round_price(raw_fill_price, "up")
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

    def _write_trade_record(
        self, position: dict[str, Any], exit_time: int, close_fill: dict[str, Any],
        gross_pnl: float, net_pnl: float, exit_reason: str,
    ) -> None:
        if self._trade_log is None:
            return
        side = position["side"].value if isinstance(position["side"], SignalSide) else position["side"]
        record = make_trade_record(
            signal_id=position["signal_id"],
            setup=position.get("setup", "unknown"),
            symbol=position["symbol"],
            side=side,
            entry_time=position["opened_at"],
            entry_price=position["entry_price"],
            quantity=position["quantity"],
            entry_fee=position.get("entry_fee", 0.0),
            signal_entry_price=position.get("signal_entry_price", position["entry_price"]),
            initial_stop_price=position.get("initial_stop_price", position["stop_price"]),
            stop_price=position["stop_price"],
            target_price=position["target_price"],
            exit_time=exit_time,
            exit_price=close_fill["fill_price"],
            exit_reason=exit_reason,
            exit_fee=close_fill["fee"],
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            target_r_multiple=float(position.get("target_r_multiple", self.settings.execution.reward_risk)),
            break_even_shifted=bool(position.get("break_even_shifted", False)),
            absorption_reduced=bool(position.get("absorption_reduced", False)),
            max_favorable_move=float(position.get("max_favorable_move", 0.0)),
            max_adverse_move=float(position.get("max_adverse_move", 0.0)),
            entry_session=position.get("entry_session", "unknown"),
            vwap_at_entry=float(position.get("vwap_at_entry", 0.0)),
            atr_at_entry=float(position.get("atr_at_entry", 0.0)),
            spread_bps_at_entry=float(position.get("spread_bps_at_entry", 0.0)),
            poc_at_entry=float(position.get("poc_at_entry", 0.0)),
            vah_at_entry=float(position.get("vah_at_entry", 0.0)),
            val_at_entry=float(position.get("val_at_entry", 0.0)),
        )
        self._trade_log.write(record)

    def save_state(self, paused: bool = False) -> None:
        """Public state save, lock-wrapped for use from external callers."""
        with self._lock:
            self._save_state(paused=paused)

    def _save_state(self, paused: bool = False) -> None:
        if self._state_path is None:
            return
        state = self._build_state_dict(paused)
        tmp_path = self._state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._state_path)

    def _build_state_dict(self, paused: bool = False) -> dict[str, Any]:
        cb = self._circuit_breaker
        return {
            "state_format_version": 1,
            "config_version": self.settings.config_version,
            "symbol": self.symbol,
            "saved_at_ms": int(time.time() * 1000),
            "account": {
                "realized_pnl": self._realized_pnl,
                "consecutive_losses": self._consecutive_losses,
            },
            "counters": {
                "signal_count": self._signal_count,
                "order_count": self._order_count,
                "rejected_count": self._rejected_count,
                "closed_positions": self._closed_positions,
            },
            "circuit_breaker": {
                "state": cb.state,
                "reason": cb.reason.value if cb.reason else None,
                "tripped_at_ms": cb.tripped_at,
                "cooldown_until_ms": cb.cooldown_until,
            },
            "trading_service": {"paused": paused},
            "open_position": to_jsonable(deepcopy(self._position)) if self._position is not None else None,
            "cumulative_delta": self._cumulative_delta,
            "last_signal_time_ms": self._last_signal_time,
            "last_event_time_ms": self._last_event_time,
            "connection": {
                "status": self._connection_status,
                "message": self._connection_message,
                "reconnect_count": self._reconnect_count,
            },
            "strategy_params": {
                "reward_risk": self.settings.execution.reward_risk,
                "dynamic_reward_risk_enabled": self.settings.execution.dynamic_reward_risk_enabled,
                "reward_risk_min": self.settings.execution.reward_risk_min,
                "reward_risk_max": self.settings.execution.reward_risk_max,
                "atr_stop_mult": self.settings.execution.atr_stop_mult,
                "kline_stop_shift_consecutive_bars": self.settings.execution.kline_stop_shift_consecutive_bars,
                "kline_stop_shift_reference_bars": self.settings.execution.kline_stop_shift_reference_bars,
                "min_stop_cost_mult": self.settings.execution.min_stop_cost_mult,
                "min_target_cost_mult": self.settings.execution.min_target_cost_mult,
                "max_holding_ms": self.settings.execution.max_holding_ms,
            },
            "last_signal_reasons": list(self._last_signal_reasons),
            "last_reject_reasons": list(self._last_reject_reasons),
            "details": to_jsonable(deepcopy(self._details)),
            "markers": to_jsonable(deepcopy(self._markers)),
        }

    def _restore_state(self) -> dict[str, Any]:
        if self._state_path is not None and self._state_path.exists():
            return self._restore_from_state_file()
        return self._restore_from_journal()

    def _restore_from_state_file(self) -> dict[str, Any]:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._restore_from_journal()
        if state.get("state_format_version") != 1:
            return self._restore_from_journal()
        self._realized_pnl = float(state["account"]["realized_pnl"])
        self._consecutive_losses = int(state["account"]["consecutive_losses"])
        self._signal_count = int(state["counters"]["signal_count"])
        self._order_count = int(state["counters"]["order_count"])
        self._rejected_count = int(state["counters"]["rejected_count"])
        self._closed_positions = int(state["counters"]["closed_positions"])
        cb = state["circuit_breaker"]
        if cb["state"] == "tripped" and cb["reason"]:
            reason = CircuitBreakerReason(cb["reason"])
            self._circuit_breaker.trip(reason)
            self._circuit_breaker.tripped_at = cb.get("tripped_at_ms") or 0
            self._circuit_breaker.cooldown_until = cb.get("cooldown_until_ms") or 0
        elif cb["state"] == "normal":
            self._circuit_breaker._state = "normal"
        if state.get("open_position") is not None:
            self._position = self._restore_position(state["open_position"])
        self._cumulative_delta = float(state.get("cumulative_delta", 0.0))
        self._last_signal_time = int(state.get("last_signal_time_ms", -1))
        self._last_event_time = int(state.get("last_event_time_ms", 0))
        conn = state.get("connection", {})
        self._connection_status = conn.get("status", "starting")
        self._connection_message = conn.get("message", "waiting for Binance stream")
        self._reconnect_count = int(conn.get("reconnect_count", 0))
        self._last_signal_reasons = tuple(state.get("last_signal_reasons", ()))
        self._last_reject_reasons = tuple(state.get("last_reject_reasons", ()))
        strat = state.get("strategy_params")
        if strat:
            self.update_strategy_params(
                reward_risk=strat.get("reward_risk"),
                dynamic_reward_risk_enabled=strat.get("dynamic_reward_risk_enabled"),
                reward_risk_min=strat.get("reward_risk_min"),
                reward_risk_max=strat.get("reward_risk_max"),
                atr_stop_mult=strat.get("atr_stop_mult"),
                kline_stop_shift_consecutive_bars=strat.get("kline_stop_shift_consecutive_bars"),
                kline_stop_shift_reference_bars=strat.get("kline_stop_shift_reference_bars"),
                min_stop_cost_mult=strat.get("min_stop_cost_mult"),
                min_target_cost_mult=strat.get("min_target_cost_mult"),
                max_holding_ms=strat.get("max_holding_ms"),
            )
        if state.get("details"):
            self._details = state["details"]
            if "live" not in self._details or not self._details["live"]:
                from crypto_perp_tool.web.details import _empty_mode_details
                self._details["live"] = _empty_mode_details()
        if state.get("markers"):
            self._markers = state["markers"]
        if state.get("config_version") != self.settings.config_version:
            self._details["paper"]["signals"] = []
            self._details["paper"]["orders"] = []
            self._details["paper"]["closed_positions"] = []
            self._details["paper"]["pnl_events"] = []
            self._details["paper"]["pnl_by_range"] = {"24h": 0.0, "7d": 0.0, "30d": 0.0, "all": 0.0}
            self._details["paper"]["risk_events"] = []
            self._details["paper"]["protective_actions"] = []
            self._markers = []
            self._signal_count = 0
            self._order_count = 0
            self._rejected_count = 0
            self._closed_positions = 0
            print(f"[LiveOrderflowStore {self.symbol}] config_version changed — resetting details/markers, keeping PnL")
        self._refresh_pnl_ranges()
        return {"paused": bool(state.get("trading_service", {}).get("paused", False))}

    def _restore_position(self, pos: dict[str, Any]) -> dict[str, Any]:
        if pos is None:
            return None
        restored = dict(pos)
        side = restored.get("side", "long")
        if isinstance(side, str):
            side = SignalSide(side)
            restored["side"] = side
        if float(restored.get("target_r_multiple") or 0) <= 0:
            restored["target_r_multiple"] = self._restored_target_r(
                float(restored.get("entry_price") or 0),
                float(restored.get("initial_stop_price") or restored.get("stop_price") or 0),
                float(restored.get("target_price") or 0),
                restored["side"],
            )
        return restored

    def _restored_target_r(self, entry_price: float, stop_price: float, target_price: float, side: SignalSide) -> float:
        risk = abs(entry_price - stop_price)
        if risk <= 0:
            return self.settings.execution.reward_risk
        reward = target_price - entry_price if side == SignalSide.LONG else entry_price - target_price
        if reward <= 0:
            return self.settings.execution.reward_risk
        return round(reward / risk, 4)

    def _restore_from_journal(self) -> dict[str, Any]:
        if self._journal is None or not self._journal.path.exists():
            return {"paused": False}
        from crypto_perp_tool.web.details import build_paper_details_from_journal
        details = build_paper_details_from_journal(self._journal.path)
        self._details = details
        paper = self._details["paper"]
        self._realized_pnl = paper["pnl_by_range"]["all"]
        self._consecutive_losses = self._count_consecutive_losses(paper["pnl_events"])
        self._signal_count = len(paper["signals"])
        self._order_count = len(paper["orders"])
        self._closed_positions = len(paper["closed_positions"])
        signals = paper["signals"]
        if signals:
            self._last_signal_time = int(signals[-1].get("timestamp", -1))
        self._position = self._find_open_position_from_journal()
        if paper["closed_positions"]:
            last_close = paper["closed_positions"][-1]
            self._last_event_time = int(last_close.get("timestamp", 0))
        self._markers = self._build_markers_from_details(paper)
        self._refresh_pnl_ranges()
        return {"paused": False}

    def _count_consecutive_losses(self, pnl_events: list[dict[str, Any]]) -> int:
        count = 0
        for event in reversed(pnl_events):
            if float(event["realized_pnl"]) < 0:
                count += 1
            else:
                break
        return count

    def _find_open_position_from_journal(self) -> dict[str, Any] | None:
        if self._journal is None:
            return None
        from crypto_perp_tool.web.details import _read_journal
        open_signal_id: str | None = None
        open_pos: dict[str, Any] | None = None
        closed_ids: set[str] = set()
        for event in _read_journal(self._journal.path):
            event_type = event.get("type")
            payload = event.get("payload", {})
            if event_type == "paper_order":
                sid = payload.get("signal_id")
                if sid:
                    open_signal_id = sid
                    entry_price = float(payload.get("entry_price") or 0)
                    stop_price = float(payload.get("stop_price") or 0)
                    target_price = float(payload.get("target_price") or 0)
                    side = SignalSide(payload.get("side", "long"))
                    target_r_multiple = float(payload.get("target_r_multiple") or 0)
                    if target_r_multiple <= 0:
                        target_r_multiple = self._restored_target_r(entry_price, stop_price, target_price, side)
                    qty = float(payload.get("quantity") or 0)
                    open_pos = {
                        "signal_id": sid,
                        "symbol": payload.get("symbol", self.symbol),
                        "side": side,
                        "setup": payload.get("setup", "unknown"),
                        "quantity": qty,
                        "entry_price": entry_price,
                        "signal_entry_price": float(payload.get("signal_entry_price") or entry_price),
                        "stop_price": stop_price,
                        "initial_stop_price": stop_price,
                        "target_price": target_price,
                        "target_r_multiple": target_r_multiple,
                        "entry_fee": 0.0,
                        "opened_at": int(event.get("time", 0)),
                        "break_even_shifted": False,
                        "absorption_reduced": False,
                        "max_favorable_move": 0.0,
                        "max_adverse_move": 0.0,
                        "entry_session": "unknown",
                        "vwap_at_entry": 0.0,
                        "atr_at_entry": 0.0,
                        "spread_bps_at_entry": 0.0,
                        "poc_at_entry": 0.0,
                        "vah_at_entry": 0.0,
                        "val_at_entry": 0.0,
                    }
            elif event_type in {"position_closed", "position_reduced"}:
                sid = payload.get("signal_id")
                if sid:
                    closed_ids.add(sid)
        if open_signal_id and open_signal_id not in closed_ids and open_pos is not None:
            return open_pos
        return None

    def _build_markers_from_details(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        markers: list[dict[str, Any]] = []
        for signal in paper.get("signals", []):
            markers.append({
                "type": "signal",
                "timestamp": signal.get("timestamp", 0),
                "price": signal.get("entry_price", 0),
                "label": signal.get("setup", "signal"),
            })
        for closed in paper.get("closed_positions", []):
            markers.append({
                "type": "position_closed",
                "timestamp": closed.get("timestamp", 0),
                "price": closed.get("close_price", 0),
                "label": f"PnL {closed.get('realized_pnl', 0):.2f}",
            })
        for action in paper.get("protective_actions", []):
            if action.get("action") == "break_even_shift":
                markers.append({
                    "type": "break_even_shift",
                    "timestamp": action.get("timestamp", 0),
                    "price": action.get("stop_price", 0),
                    "label": "BE",
                    "side": action.get("side", "long"),
                })
            elif action.get("action") == "absorption_reduce":
                markers.append({
                    "type": "absorption_reduce",
                    "timestamp": action.get("timestamp", 0),
                    "price": action.get("trigger_price", 0),
                    "label": "Absorb reduce",
                    "side": action.get("side", "long"),
                })
            elif action.get("action") == "kline_momentum_stop_shift":
                markers.append({
                    "type": "kline_momentum_stop_shift",
                    "timestamp": action.get("timestamp", 0),
                    "price": action.get("stop_price", 0),
                    "label": "1m stop",
                    "side": action.get("side", "long"),
                })
        return markers

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

    def view(self) -> dict[str, Any]:
        with self._lock:
            now_monotonic = time.monotonic()
            if (
                self._view_cache is not None
                and self._view_cache_version == self._view_version
                and now_monotonic - self._view_cache_created <= self._view_cache_ttl_seconds
            ):
                return self._view_cache
            view_version = self._view_version
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
            last_bubble = to_jsonable(deepcopy(self._last_bubble))
            klines = list(self._klines)
            last_received_at = self._last_received_at

        cumulative_delta = 0.0
        trades: list[dict[str, Any]] = []
        delta_series: list[dict[str, Any]] = []
        last_event_time = events[-1].timestamp if events else 0
        display_limit = max(1, self.display_events)
        display_events = events[-display_limit:]

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

        visible_markers = self._markers_for_display(markers, [trade["timestamp"] for trade in trades])
        last_trade_price = trades[-1]["price"] if trades else None
        quote_mid_price = quote.mid_price if quote is not None else None
        spot_last_price = spot.price if spot is not None else None
        index_price = mark.index_price if mark is not None else None
        mark_price = mark.mark_price if mark is not None else None
        last_price = (
            last_trade_price if last_trade_price is not None
            else quote_mid_price if quote_mid_price is not None
            else mark_price if mark_price is not None
            else index_price
        )
        price_source = (
            "aggTrade" if last_trade_price is not None
            else "bookTicker" if quote_mid_price is not None
            else "markPrice" if mark_price is not None
            else "indexPrice" if index_price is not None
            else None
        )
        derived_connection_status = (
            "connected" if connection_status == "starting" and last_price is not None
            else connection_status
        )
        last_event_time = events[-1].timestamp if events else 0
        profile_events = self._trade_window.items_since(last_event_time, self._profile_window_ms) if last_event_time else []
        levels = self._profile_levels(last_event_time) if last_event_time else ()
        paper_actions = details.get("paper", {}).get("protective_actions", [])
        last_break_even_shift = last_action(paper_actions, "break_even_shift")
        last_absorption_reduce = last_action(paper_actions, "absorption_reduce")
        divergence_state = cvd_divergence_state(events, levels)
        exchange_lag_ms = self._median_recent_lag()
        exchange_lag_min_ms = self._min_recent_lag()
        stream_freshness_ms = self._stream_freshness_ms(last_received_at)

        payload = {
            "summary": {
                "source": "binance",
                "symbol": self.symbol,
                "connection_status": derived_connection_status,
                "connection_message": connection_message,
                "session": self._session_detector.detect(self._last_event_time or int(time.time() * 1000)).value,
                "trade_count": len(trades),
                "seen_trade_count": len(events),
                "profile_trade_count": len(profile_events),
                "last_price": last_price,
                "spot_last_price": spot_last_price,
                "last_trade_price": last_trade_price,
                "bid_price": quote.bid_price if quote is not None else None,
                "ask_price": quote.ask_price if quote is not None else None,
                "quote_mid_price": quote_mid_price,
                "mark_price": mark_price,
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
                "atr_1m_14": self._current_atr(last_price or 0),
                "atr_3m_14": self._atr_3m_value,
                "signals": self._signal_count,
                "orders": self._order_count,
                "rejected": self._rejected_count,
                "closed_positions": self._closed_positions,
                "realized_pnl": self._realized_pnl,
                "open_position": position,
                "signal_reasons": last_signal_reasons,
                "reject_reasons": last_reject_reasons,
                "data_lag_ms": exchange_lag_ms,
                "exchange_lag_ms": exchange_lag_ms,
                "lag_min_ms": exchange_lag_min_ms,
                "exchange_lag_min_ms": exchange_lag_min_ms,
                "stream_freshness_ms": stream_freshness_ms,
                "last_received_time": last_received_at or None,
                "last_trade_time": self._last_event_time or None,
                "last_aggression_bubble": last_bubble,
                "last_break_even_shift": last_break_even_shift,
                "last_absorption_reduce": last_absorption_reduce,
                "cvd_divergence": divergence_state,
                "circuit_state": self._circuit_breaker.state,
                "circuit_reason": self._circuit_breaker.reason.value if self._circuit_breaker.reason else None,
                "cooldown_until": self._circuit_breaker.cooldown_until,
                "pnl_24h": total_pnl_for_range(details, "24h"),
                "pnl_percent_24h": (total_pnl_for_range(details, "24h") / self.equity * 100) if self.equity else 0.0,
                "pnl_percent_all": (self._realized_pnl / self.equity * 100) if self.equity else 0.0,
                "mode_breakdown": mode_breakdown(details),
                "trade_log_path": str(self._trade_log.path) if self._trade_log is not None else None,
            },
            "trades": trades,
            "delta_series": delta_series,
            "klines": [
                {
                    "timestamp": k.timestamp,
                    "close_time": k.close_time,
                    "interval": k.interval,
                    "open": k.open, "high": k.high, "low": k.low, "close": k.close,
                    "volume": k.volume,
                    "quote_volume": k.quote_volume,
                    "trade_count": k.trade_count,
                    "is_closed": k.is_closed,
                }
                for k in klines
            ],
            "profile_levels": [
                {"type": level.type.value, "price": level.price,
                 "lower_bound": level.lower_bound, "upper_bound": level.upper_bound,
                 "strength": level.strength, "window": level.window}
                for level in levels
            ],
            "markers": visible_markers,
            "details": details,
        }

        with self._lock:
            if view_version == self._view_version:
                self._view_cache = payload
                self._view_cache_version = view_version
                self._view_cache_created = time.monotonic()

        return payload

    def _markers_for_display(self, markers: list[dict[str, Any]], display_timestamps: list[int]) -> list[dict[str, Any]]:
        if not display_timestamps:
            return markers
        first_timestamp = display_timestamps[0]
        last_timestamp = display_timestamps[-1]
        visible_markers: list[dict[str, Any]] = []
        for marker in markers:
            marker_timestamp = int(marker.get("timestamp") or 0)
            marker_copy = dict(marker)
            if first_timestamp <= marker_timestamp <= last_timestamp:
                marker_copy["index"] = min(
                    range(len(display_timestamps)),
                    key=lambda index: abs(display_timestamps[index] - marker_timestamp),
                )
            visible_markers.append(marker_copy)
        return visible_markers
