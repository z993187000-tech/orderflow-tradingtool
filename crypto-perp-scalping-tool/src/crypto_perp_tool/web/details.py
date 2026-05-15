from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from crypto_perp_tool.serialization import to_jsonable


RANGE_MS = {
    "24h": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
    "30d": 30 * 24 * 60 * 60 * 1000,
}

PROTECTIVE_ACTION_TYPES = {
    "break_even_shift",
    "absorption_reduce",
    "halt_new_entries",
    "protective_close",
}

RISK_EVENT_TYPES = {
    "absorption_detected",
    "circuit_breaker_tripped",
    "fast_reversal_stop_hit",
    "partial_fill",
    "quantity_below_partial_fill",
    "slippage_expanded",
    "stop_submission_failed",
}


def empty_execution_details() -> dict[str, Any]:
    return {
        "paper": _empty_mode_details(),
        "live": _empty_mode_details(),
    }


def build_paper_details_from_journal(journal_path: Path) -> dict[str, Any]:
    details = empty_execution_details()
    if not journal_path.exists():
        return details

    paper = details["paper"]
    for event in _read_journal(journal_path):
        event_type = event.get("type")
        payload = event.get("payload", {})
        timestamp = int(event.get("time") or 0)
        if event_type == "signal":
            signal = payload.get("signal", {})
            paper["signals"].append(
                {
                    "timestamp": signal.get("created_at") or timestamp,
                    "symbol": signal.get("symbol"),
                    "side": signal.get("side"),
                    "setup": signal.get("setup"),
                    "entry_price": signal.get("entry_price"),
                    "stop_price": signal.get("stop_price"),
                    "target_price": signal.get("target_price"),
                    "target_r_multiple": signal.get("target_r_multiple"),
                    "confidence": signal.get("confidence"),
                    "reasons": signal.get("reasons", []),
                }
            )
        elif event_type == "paper_order":
            paper["orders"].append(
                {
                    "timestamp": timestamp,
                    "symbol": payload.get("symbol"),
                    "side": payload.get("side"),
                    "quantity": payload.get("quantity"),
                    "entry_price": payload.get("entry_price"),
                    "stop_price": payload.get("stop_price"),
                    "target_price": payload.get("target_price"),
                    "target_r_multiple": payload.get("target_r_multiple"),
                }
            )
        elif event_type in {"position_closed", "position_reduced"}:
            realized_pnl = float(payload.get("realized_pnl") or 0)
            closed = {
                "timestamp": int(payload.get("timestamp") or timestamp),
                "symbol": payload.get("symbol"),
                "side": payload.get("side"),
                "quantity": payload.get("quantity"),
                "entry_price": payload.get("entry_price"),
                "close_price": payload.get("close_price"),
                "stop_price": payload.get("stop_price"),
                "target_price": payload.get("target_price"),
                "target_r_multiple": payload.get("target_r_multiple"),
                "realized_pnl": realized_pnl,
            }
            if payload.get("exit_reason"):
                closed["exit_reason"] = payload.get("exit_reason")
            paper["closed_positions"].append(closed)
            paper["pnl_events"].append(
                {
                    "timestamp": closed["timestamp"],
                    "symbol": payload.get("symbol"),
                    "side": payload.get("side"),
                    "realized_pnl": realized_pnl,
                }
            )
        elif event_type in PROTECTIVE_ACTION_TYPES:
            paper["protective_actions"].append(_event_record(payload, timestamp, "action", event_type))
        elif event_type in RISK_EVENT_TYPES:
            paper["risk_events"].append(_event_record(payload, timestamp, "type", event_type))

    _refresh_pnl_ranges(paper)
    return to_jsonable(details)


def mode_breakdown(details: dict[str, Any]) -> dict[str, Any]:
    return {
        mode: {
            "signals": len(detail["signals"]),
            "orders": len(detail["orders"]),
            "closed_positions": len(detail["closed_positions"]),
            "pnl_24h": detail["pnl_by_range"]["24h"],
            "realized_pnl": detail["pnl_by_range"]["all"],
        }
        for mode, detail in details.items()
    }


def total_pnl_for_range(details: dict[str, Any], range_key: str) -> float:
    return sum(float(detail["pnl_by_range"][range_key]) for detail in details.values())


def _empty_mode_details() -> dict[str, Any]:
    return {
        "signals": [],
        "orders": [],
        "closed_positions": [],
        "pnl_events": [],
        "pnl_by_range": {"24h": 0.0, "7d": 0.0, "30d": 0.0, "all": 0.0},
        "risk_events": [],
        "protective_actions": [],
    }


def _event_record(payload: dict[str, Any], timestamp: int, label_key: str, label: str) -> dict[str, Any]:
    record = dict(payload)
    record["timestamp"] = int(record.get("timestamp") or timestamp)
    record[label_key] = record.get(label_key) or label
    return record


def _refresh_pnl_ranges(detail: dict[str, Any]) -> None:
    now_ms = int(time.time() * 1000)
    events = detail["pnl_events"]
    detail["pnl_by_range"] = {
        key: sum(float(event["realized_pnl"]) for event in events if now_ms - int(event["timestamp"]) <= window_ms)
        for key, window_ms in RANGE_MS.items()
    }
    detail["pnl_by_range"]["all"] = sum(float(event["realized_pnl"]) for event in events)


def _read_journal(journal_path: Path) -> list[dict[str, Any]]:
    events = []
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
