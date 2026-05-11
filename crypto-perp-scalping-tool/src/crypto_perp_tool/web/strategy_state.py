from __future__ import annotations

from typing import Any, Sequence

from crypto_perp_tool.market_data import TradeEvent
from crypto_perp_tool.types import ProfileLevel, ProfileLevelType


def last_action(actions: Sequence[dict[str, Any]], action_name: str) -> dict[str, Any] | None:
    for action in reversed(actions):
        if action.get("action") == action_name:
            return action
    return None


def cvd_divergence_state(
    events: Sequence[TradeEvent],
    levels: Sequence[ProfileLevel],
) -> dict[str, Any]:
    if not events:
        return {"state": "none", "side": "flat", "reason": "waiting for trades"}
    last_event = events[-1]
    recent = [event for event in events if last_event.timestamp - event.timestamp <= 90_000]
    if len(recent) < 3:
        return {"state": "none", "side": "flat", "reason": "need more recent flow"}

    vah = next((level for level in levels if level.type == ProfileLevelType.VAH), None)
    val = next((level for level in levels if level.type == ProfileLevelType.VAL), None)
    cvd_points: list[tuple[int, float, float]] = []
    cumulative_delta = 0.0
    for event in recent:
        cumulative_delta += event.delta
        cvd_points.append((event.timestamp, event.price, cumulative_delta))

    if vah is not None and last_event.price < vah.price:
        above = [(price, cvd) for _, price, cvd in cvd_points if price > vah.upper_bound]
        if _has_bearish_divergence(above):
            return {
                "state": "bearish_failed_breakout",
                "side": "short",
                "level": "VAH",
                "price": last_event.price,
                "reason": "price broke above VAH but CVD did not confirm",
            }

    if val is not None and last_event.price > val.price:
        below = [(price, cvd) for _, price, cvd in cvd_points if price < val.lower_bound]
        if _has_bullish_divergence(below):
            return {
                "state": "bullish_failed_breakdown",
                "side": "long",
                "level": "VAL",
                "price": last_event.price,
                "reason": "price broke below VAL but CVD did not confirm",
            }

    return {"state": "none", "side": "flat", "reason": "no active CVD divergence"}


def _has_bearish_divergence(points: list[tuple[float, float]]) -> bool:
    if len(points) < 2:
        return False
    high_index = max(range(len(points)), key=lambda index: points[index][0])
    if high_index == 0:
        return False
    prior_cvd_high = max(cvd for _, cvd in points[:high_index])
    return points[high_index][1] <= prior_cvd_high


def _has_bullish_divergence(points: list[tuple[float, float]]) -> bool:
    if len(points) < 2:
        return False
    low_index = min(range(len(points)), key=lambda index: points[index][0])
    if low_index == 0:
        return False
    prior_cvd_low = min(cvd for _, cvd in points[:low_index])
    return points[low_index][1] >= prior_cvd_low
