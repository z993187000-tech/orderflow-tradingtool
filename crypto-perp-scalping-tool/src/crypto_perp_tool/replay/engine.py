from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crypto_perp_tool.backtest import BacktestReporter
from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import (
    AggressionBubbleDetector,
    AtrTracker,
    QuoteEvent,
    TimeWindowBuffer,
    TradeEvent,
)
from crypto_perp_tool.profile import build_profile_levels
from crypto_perp_tool.risk import RiskEngine
from crypto_perp_tool.session import SessionDetector
from crypto_perp_tool.signals import SignalEngine
from crypto_perp_tool.types import (
    HistoricalWindows,
    MarketSnapshot,
    SignalSide,
    TradeSignal,
)


@dataclass
class ReplayMatch:
    """Records whether a replayed signal matches the original journal entry."""
    original_time: int
    original_setup: str
    original_side: str
    replayed: bool
    matched_setup: bool = False
    matched_side: bool = False
    replayed_setup: str = ""
    replayed_side: str = ""
    # Price comparison fields
    replayed_entry_price: float = 0.0
    original_entry_price: float = 0.0
    entry_price_diff_pct: float = 0.0
    replayed_stop_price: float = 0.0
    original_stop_price: float = 0.0
    stop_price_diff_pct: float = 0.0
    replayed_target_price: float = 0.0
    original_target_price: float = 0.0
    target_price_diff_pct: float = 0.0
    matched_prices: bool = False


@dataclass
class ReplayReport:
    """Summary of a replay run comparing original to replayed signals."""
    journal_path: str
    symbol: str
    time_range: tuple[int, int] | None = None
    total_journal_signals: int = 0
    replayed_signals: int = 0
    matched: int = 0
    missed: int = 0
    extra: int = 0
    matches: list[ReplayMatch] = field(default_factory=list)
    # Price-level stats
    price_matched: int = 0
    avg_entry_diff_pct: float = 0.0
    avg_stop_diff_pct: float = 0.0
    avg_target_diff_pct: float = 0.0


class ReplayEngine:
    """Re-drives journaled trade events through the signal/profile pipeline and
    compares output to the originally recorded signals."""

    def __init__(
        self,
        journal_path: Path | str,
        symbol: str | None = None,
        settings: Any = None,
    ) -> None:
        self.journal_path = Path(journal_path)
        self.symbol = (symbol or "BTCUSDT").upper()
        self.settings = settings or default_settings()
        self.bin_size = (
            self.settings.profile.btc_bin_size if self.symbol == "BTCUSDT"
            else self.settings.profile.eth_bin_size
        )
        self._events: list[TradeEvent] = []
        self._quotes: dict[int, QuoteEvent] = {}
        self._original_signals: list[dict[str, Any]] = []
        self._trade_window = TimeWindowBuffer[TradeEvent](
            max_window_ms=self.settings.profile.context_window_minutes * 60 * 1000
        )
        self._report: ReplayReport | None = None

    # ------------------------------------------------------------------
    # Journal loading
    # ------------------------------------------------------------------

    def load_journal(self, start_ms: int | None = None, end_ms: int | None = None) -> None:
        """Read the JSONL journal and extract trade events, quotes, and signals."""
        if not self.journal_path.exists():
            raise FileNotFoundError(f"Journal not found: {self.journal_path}")

        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")
            journal_time = int(event.get("time", 0))
            payload = event.get("payload", {})

            if start_ms is not None and journal_time < start_ms:
                continue
            if end_ms is not None and journal_time > end_ms:
                continue

            if event_type == "signal":
                signal_data = payload.get("signal", payload)
                if signal_data:
                    self._original_signals.append(_normalize_signal(signal_data, journal_time))

    # ------------------------------------------------------------------
    # Main replay entry point
    # ------------------------------------------------------------------

    def replay(
        self,
        events: list[TradeEvent],
        quotes: list[QuoteEvent] | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> ReplayReport:
        """Feed events through the pipeline and compare to journal signals."""
        if quotes:
            for q in quotes:
                self._quotes[q.timestamp] = q

        self._events = sorted(events, key=lambda e: e.timestamp)
        for event in self._events:
            if start_ms is not None and event.timestamp < start_ms:
                continue
            if end_ms is not None and event.timestamp > end_ms:
                continue
            self._trade_window.append(event.timestamp, event)

        self._load_original(start_ms, end_ms)

        # Build pipeline
        signal_engine = SignalEngine(
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
            execution_window=f"execution_{self.settings.profile.execution_window_minutes}m",
            micro_window=f"micro_{self.settings.profile.micro_window_minutes}m",
            context_window=f"context_{self.settings.profile.context_window_minutes}m",
        )
        risk_engine = RiskEngine(self.settings.risk)

        bubble_detector = AggressionBubbleDetector(
            large_threshold=self.settings.signals.aggression_large_threshold,
            block_threshold=self.settings.signals.aggression_block_threshold,
            dynamic_enabled=False,
        )
        atr_1m = AtrTracker(bar_ms=60_000, period=self.settings.signals.atr_period)
        atr_3m = AtrTracker(bar_ms=3 * 60_000, period=self.settings.signals.atr_period)
        session_detector = SessionDetector(
            asia_start_hour=self.settings.profile.asia_start_hour,
            asia_end_hour=self.settings.profile.asia_end_hour,
            london_start_hour=self.settings.profile.london_start_hour,
            london_end_hour=self.settings.profile.london_end_hour,
            london_end_minute=self.settings.profile.london_end_minute,
            ny_start_hour=self.settings.profile.ny_start_hour,
            ny_start_minute=self.settings.profile.ny_start_minute,
            ny_end_hour=self.settings.profile.ny_end_hour,
        )
        historical = HistoricalWindows()
        cumulative_delta = 0.0
        last_vwap = 0.0
        replayed: list[ReplayMatch] = []
        cooldown_at = -1

        for event in self._events:
            if start_ms is not None and event.timestamp < start_ms:
                continue
            if end_ms is not None and event.timestamp > end_ms:
                continue

            cumulative_delta += event.delta
            atr_1m.update(event)
            atr_3m.update(event)
            bubble_detector.detect(event)
            self._trade_window.append(event.timestamp, event)
            delta_15s = self._trade_window.sum_since(event.timestamp, 15_000, lambda e: e.delta)
            delta_30s = self._trade_window.sum_since(event.timestamp, 30_000, lambda e: e.delta)
            delta_60s = self._trade_window.sum_since(event.timestamp, 60_000, lambda e: e.delta)
            volume_30s = self._trade_window.sum_since(event.timestamp, 30_000, lambda e: abs(e.delta))
            last_vwap = self._compute_vwap(event.timestamp)
            spread = self._spread_bps(event)

            historical = (
                historical.with_window("spread_5min", spread)
                .with_window("delta_30s", delta_30s)
                .with_window("volume_30s", volume_30s)
                .with_window("amplitude_1m", event.price * 0.002)
            )

            if cooldown_at > 0 and event.timestamp - cooldown_at < 60_000:
                continue

            quote = self._quotes.get(event.timestamp)
            bid = quote.bid_price if quote else event.price * 0.9999
            ask = quote.ask_price if quote else event.price * 1.0001

            snapshot = MarketSnapshot(
                exchange=self.settings.exchange,
                symbol=self.symbol,
                event_time=event.timestamp,
                local_time=event.timestamp,
                last_price=event.price,
                bid_price=bid,
                ask_price=ask,
                spread_bps=spread,
                vwap=last_vwap,
                atr_1m_14=max(atr_1m.latest_atr, atr_3m.latest_atr) or max(event.price * 0.002, self.bin_size / 2),
                delta_15s=delta_15s,
                delta_30s=delta_30s,
                delta_60s=delta_60s,
                volume_30s=volume_30s,
                profile_levels=self._profile_levels(event.timestamp),
                atr_3m_14=atr_3m.latest_atr,
                cumulative_delta=cumulative_delta,
                session=session_detector.detect(event.timestamp).value,
            )

            signal = signal_engine.evaluate(snapshot, windows=historical)
            if signal is None:
                continue

            cooldown_at = event.timestamp
            match = self._compare_signal(signal, event.timestamp)
            replayed.append(match)

        price_diffs = [m for m in replayed if m.original_entry_price > 0]
        self._report = ReplayReport(
            journal_path=str(self.journal_path),
            symbol=self.symbol,
            time_range=(start_ms, end_ms) if start_ms or end_ms else None,
            total_journal_signals=len(self._original_signals),
            replayed_signals=len(replayed),
            matched=sum(1 for m in replayed if m.matched_setup and m.matched_side),
            missed=sum(1 for m in replayed if not m.replayed),
            extra=max(0, len(replayed) - len(self._original_signals)),
            matches=replayed,
            price_matched=sum(1 for m in replayed if m.matched_prices),
            avg_entry_diff_pct=round(sum(m.entry_price_diff_pct for m in price_diffs) / len(price_diffs), 4) if price_diffs else 0.0,
            avg_stop_diff_pct=round(sum(m.stop_price_diff_pct for m in price_diffs) / len(price_diffs), 4) if price_diffs else 0.0,
            avg_target_diff_pct=round(sum(m.target_price_diff_pct for m in price_diffs) / len(price_diffs), 4) if price_diffs else 0.0,
        )
        return self._report

    def report(self) -> ReplayReport | None:
        return self._report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_original(self, start_ms: int | None, end_ms: int | None) -> None:
        if self._original_signals:
            return
        self.load_journal(start_ms, end_ms)

    ENTRY_TOLERANCE_PCT = 0.5
    STOP_TOLERANCE_PCT = 0.5
    TARGET_TOLERANCE_PCT = 1.0

    @staticmethod
    def _pct_diff(a: float, b: float) -> float:
        if a == b:
            return 0.0
        denom = max(abs(a), abs(b))
        if denom == 0:
            return 0.0
        return abs(a - b) / denom * 100

    def _compare_signal(self, signal: TradeSignal, timestamp: int) -> ReplayMatch:
        match = ReplayMatch(
            original_time=timestamp,
            original_setup="",
            original_side="",
            replayed=True,
            replayed_setup=signal.setup,
            replayed_side=signal.side.value,
            replayed_entry_price=signal.entry_price,
            replayed_stop_price=signal.stop_price,
            replayed_target_price=signal.target_price,
        )
        for orig in self._original_signals:
            time_diff = abs(int(orig.get("created_at", 0)) - timestamp)
            if time_diff > 30_000:
                continue
            match.original_time = int(orig.get("created_at", 0))
            match.original_setup = str(orig.get("setup", ""))
            match.original_side = str(orig.get("side", ""))
            match.matched_setup = signal.setup == match.original_setup
            match.matched_side = signal.side.value == match.original_side

            # Compare prices with tolerance
            match.original_entry_price = float(orig.get("entry_price", 0))
            match.original_stop_price = float(orig.get("stop_price", 0))
            match.original_target_price = float(orig.get("target_price", 0))
            match.entry_price_diff_pct = round(self._pct_diff(match.replayed_entry_price, match.original_entry_price), 4)
            match.stop_price_diff_pct = round(self._pct_diff(match.replayed_stop_price, match.original_stop_price), 4)
            match.target_price_diff_pct = round(self._pct_diff(match.replayed_target_price, match.original_target_price), 4)
            match.matched_prices = (
                match.entry_price_diff_pct <= self.ENTRY_TOLERANCE_PCT
                and match.stop_price_diff_pct <= self.STOP_TOLERANCE_PCT
                and match.target_price_diff_pct <= self.TARGET_TOLERANCE_PCT
            )
            break
        return match

    def _compute_vwap(self, timestamp: int) -> float:
        events = list(self._trade_window.items_since(
            timestamp, self.settings.profile.context_window_minutes * 60 * 1000
        ))
        quantity = sum(e.quantity for e in events)
        if quantity <= 0:
            return 0.0
        return sum(e.price * e.quantity for e in events) / quantity

    def _profile_levels(self, timestamp: int):
        settings = self.settings.profile
        events = self._trade_window.items_since(timestamp, settings.context_window_minutes * 60 * 1000)
        trades = [(event.price, event.quantity, event.timestamp) for event in events]
        return (
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=settings.execution_window_minutes * 60 * 1000,
                label=f"execution_{settings.execution_window_minutes}m",
                bin_size=self.bin_size,
                value_area_ratio=settings.value_area_ratio,
                min_trades=settings.min_execution_profile_trades,
                min_bins=settings.min_profile_bins,
            ),
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=settings.micro_window_minutes * 60 * 1000,
                label=f"micro_{settings.micro_window_minutes}m",
                bin_size=self.bin_size,
                value_area_ratio=settings.value_area_ratio,
                min_trades=settings.min_micro_profile_trades,
                min_bins=settings.min_profile_bins,
            ),
            *build_profile_levels(
                trades,
                timestamp=timestamp,
                window_ms=settings.context_window_minutes * 60 * 1000,
                label=f"context_{settings.context_window_minutes}m",
                bin_size=self.bin_size,
                value_area_ratio=settings.value_area_ratio,
            ),
        )

    def _spread_bps(self, event: TradeEvent) -> float:
        quote = self._quotes.get(event.timestamp)
        if quote:
            return (quote.ask_price - quote.bid_price) / quote.mid_price * 10_000
        return 2.0


def _normalize_signal(data: dict[str, Any], fallback_time: int) -> dict[str, Any]:
    return {
        "created_at": int(data.get("created_at", fallback_time)),
        "setup": str(data.get("setup", "")),
        "side": str(data.get("side", "")),
        "entry_price": float(data.get("entry_price", 0)),
        "stop_price": float(data.get("stop_price", 0)),
        "target_price": float(data.get("target_price", 0)),
        "target_r_multiple": float(data.get("target_r_multiple", 0)),
    }
