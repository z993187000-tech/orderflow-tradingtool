from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from crypto_perp_tool.config import default_settings
from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.paper import PaperRunner
from crypto_perp_tool.profile import VolumeProfileEngine
from crypto_perp_tool.serialization import to_jsonable
from crypto_perp_tool.web.details import build_paper_details_from_journal, mode_breakdown, total_pnl_for_range


def build_orderflow_view(data_path: Path | str, symbol: str = "BTCUSDT") -> dict[str, Any]:
    path = Path(data_path)
    events = _load_trade_events(path, symbol)
    settings = default_settings()
    bin_size = settings.profile.btc_bin_size if symbol == "BTCUSDT" else settings.profile.eth_bin_size
    profile = VolumeProfileEngine(bin_size=bin_size, value_area_ratio=settings.profile.value_area_ratio)

    cumulative_delta = 0.0
    trades: list[dict[str, Any]] = []
    delta_series: list[dict[str, Any]] = []

    for index, event in enumerate(events):
        profile.add_trade(event.price, event.quantity)
        cumulative_delta += event.delta
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
        markers = _markers_from_journal(journal_path, trades)
        details = build_paper_details_from_journal(journal_path)

    profile_levels = [
        {
            "type": level.type.value,
            "price": level.price,
            "lower_bound": level.lower_bound,
            "upper_bound": level.upper_bound,
            "strength": level.strength,
            "window": level.window,
        }
        for level in profile.levels("rolling_4h")
    ]

    return {
        "summary": {
            "symbol": symbol,
            "trade_count": len(trades),
            "last_price": trades[-1]["price"] if trades else None,
            "cumulative_delta": cumulative_delta,
            "signals": result.signals,
            "orders": result.orders,
            "closed_positions": result.closed_positions,
            "realized_pnl": result.realized_pnl,
            "pnl_24h": total_pnl_for_range(details, "24h"),
            "mode_breakdown": mode_breakdown(details),
        },
        "trades": trades,
        "delta_series": delta_series,
        "profile_levels": profile_levels,
        "markers": markers,
        "details": details,
    }


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
