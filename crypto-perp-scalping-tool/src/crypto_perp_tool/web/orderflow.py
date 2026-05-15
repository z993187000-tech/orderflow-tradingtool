from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import AggressionBubbleDetector, AtrTracker, TradeEvent
from crypto_perp_tool.paper import PaperRunner
from crypto_perp_tool.profile import build_profile_levels
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.web.details import build_paper_details_from_journal, mode_breakdown, total_pnl_for_range
from crypto_perp_tool.web.strategy_state import cvd_divergence_state, last_action


def build_orderflow_view(data_path: Path | str, symbol: str = "BTCUSDT") -> dict[str, Any]:
    path = Path(data_path)
    events = _load_trade_events(path, symbol)
    settings = default_settings()
    bin_size = settings.profile.btc_bin_size if symbol == "BTCUSDT" else settings.profile.eth_bin_size
    bubble_detector = AggressionBubbleDetector(
        large_threshold=settings.signals.aggression_large_threshold,
        block_threshold=settings.signals.aggression_block_threshold,
    )
    atr_1m = AtrTracker(bar_ms=60_000, period=settings.signals.atr_period)
    atr_3m = AtrTracker(bar_ms=3 * 60_000, period=settings.signals.atr_period)

    cumulative_delta = 0.0
    trades: list[dict[str, Any]] = []
    delta_series: list[dict[str, Any]] = []
    bubble_markers: list[dict[str, Any]] = []
    last_bubble = None

    for index, event in enumerate(events):
        cumulative_delta += event.delta
        atr_1m.update(event)
        atr_3m.update(event)
        bubble = bubble_detector.detect(event)
        if bubble is not None:
            last_bubble = bubble
            bubble_markers.append(
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
        trades.append(
            {
                "index": index,
                "timestamp": event.timestamp,
                "symbol": event.symbol,
                "price": event.price,
                "quantity": event.quantity,
                "side": "sell" if event.is_buyer_maker else "buy",
                "delta": event.delta,
            }
        )
        delta_series.append(
            {
                "index": index,
                "timestamp": event.timestamp,
                "delta": event.delta,
                "cumulative_delta": cumulative_delta,
            }
        )

    with tempfile.TemporaryDirectory() as tmp:
        journal_path = Path(tmp) / "paper_journal.jsonl"
        result = PaperRunner(equity=10_000, journal_path=journal_path).run_csv(path, symbol=symbol)
        markers = _attach_marker_indexes(bubble_markers, trades) + _markers_from_journal(journal_path, trades)
        details = build_paper_details_from_journal(journal_path)

    levels = _profile_levels(events, bin_size, settings.profile)
    profile_levels = [
        {
            "type": level.type.value,
            "price": level.price,
            "lower_bound": level.lower_bound,
            "upper_bound": level.upper_bound,
            "strength": level.strength,
            "window": level.window,
        }
        for level in levels
    ]
    last_price = trades[-1]["price"] if trades else None
    paper_actions = details.get("paper", {}).get("protective_actions", [])

    return {
        "summary": {
            "symbol": symbol,
            "trade_count": len(trades),
            "last_price": last_price,
            "cumulative_delta": cumulative_delta,
            "atr_1m_14": _current_atr(atr_1m.latest_atr, last_price or 0, bin_size),
            "atr_3m_14": atr_3m.latest_atr,
            "last_aggression_bubble": to_jsonable(last_bubble),
            "last_break_even_shift": last_action(paper_actions, "break_even_shift"),
            "last_absorption_reduce": last_action(paper_actions, "absorption_reduce"),
            "cvd_divergence": cvd_divergence_state(events, levels),
            "signals": result.signals,
            "orders": result.orders,
            "closed_positions": result.closed_positions,
            "realized_pnl": result.realized_pnl,
            "pnl_24h": total_pnl_for_range(details, "24h"),
            "pnl_percent_24h": (total_pnl_for_range(details, "24h") / 10_000 * 100),
            "pnl_percent_all": (result.realized_pnl / 10_000 * 100),
            "mode_breakdown": mode_breakdown(details),
            "data_lag_ms": -1,
            "exchange_lag_ms": -1,
            "lag_min_ms": -1,
            "exchange_lag_min_ms": -1,
            "stream_freshness_ms": -1,
            "last_received_time": None,
            "last_trade_time": trades[-1]["timestamp"] if trades else None,
        },
        "trades": trades,
        "delta_series": delta_series,
        "klines": [],
        "profile_levels": profile_levels,
        "markers": markers,
        "details": details,
    }


def _current_atr(latest_atr: float, fallback_price: float, bin_size: float) -> float:
    if latest_atr > 0:
        return latest_atr
    return max(fallback_price * 0.002, bin_size / 2)


def _profile_levels(events: list[TradeEvent], bin_size: float, settings):
    if not events:
        return ()
    timestamp = events[-1].timestamp
    trades = [(event.price, event.quantity, event.timestamp) for event in events]
    return (
        *build_profile_levels(
            trades,
            timestamp=timestamp,
            window_ms=settings.execution_window_minutes * 60 * 1000,
            label=f"execution_{settings.execution_window_minutes}m",
            bin_size=bin_size,
            value_area_ratio=settings.value_area_ratio,
            min_trades=settings.min_execution_profile_trades,
            min_bins=settings.min_profile_bins,
        ),
        *build_profile_levels(
            trades,
            timestamp=timestamp,
            window_ms=settings.micro_window_minutes * 60 * 1000,
            label=f"micro_{settings.micro_window_minutes}m",
            bin_size=bin_size,
            value_area_ratio=settings.value_area_ratio,
            min_trades=settings.min_micro_profile_trades,
            min_bins=settings.min_profile_bins,
        ),
        *build_profile_levels(
            trades,
            timestamp=timestamp,
            window_ms=settings.context_window_minutes * 60 * 1000,
            label=f"context_{settings.context_window_minutes}m",
            bin_size=bin_size,
            value_area_ratio=settings.value_area_ratio,
        ),
    )


def _load_trade_events(path: Path, symbol: str) -> list[TradeEvent]:
    runner = PaperRunner(equity=10_000, journal_path=Path(tempfile.gettempdir()) / "unused_orderflow_journal.jsonl")
    return runner._load_csv(path, symbol)


def _markers_from_journal(journal_path: Path, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not journal_path.exists():
        return []
    events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    markers: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        event_type = event.get("type")
        if event_type == "signal":
            signal = payload.get("signal", {})
            markers.append(
                {
                    "type": "signal",
                    "timestamp": signal.get("created_at"),
                    "price": signal.get("entry_price"),
                    "label": signal.get("setup"),
                    "side": signal.get("side"),
                }
            )
        if event_type == "position_closed":
            markers.append(
                {
                    "type": "position_closed",
                    "timestamp": event.get("time"),
                    "price": payload.get("close_price"),
                    "label": f"PnL {payload.get('realized_pnl', 0):.2f}",
                    "side": payload.get("side"),
                }
            )
    return _attach_marker_indexes(markers, trades)


def _attach_marker_indexes(markers: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for marker in markers:
        price = marker.get("price")
        if price is None or not trades:
            marker["index"] = 0
            continue
        marker["index"] = min(range(len(trades)), key=lambda idx: abs(trades[idx]["price"] - price))
    return to_jsonable(markers)
