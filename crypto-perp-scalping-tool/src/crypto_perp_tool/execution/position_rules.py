from __future__ import annotations

from typing import Protocol, Sequence

from crypto_perp_tool.types import SignalSide

ONE_MINUTE_MS = 60_000


class KlineLike(Protocol):
    timestamp: int
    interval: str
    open: float
    high: float
    low: float
    close: float
    is_closed: bool


def price_moves(side: SignalSide, entry_price: float, price: float) -> tuple[float, float]:
    if side == SignalSide.LONG:
        return price - entry_price, entry_price - price
    return entry_price - price, price - entry_price


def estimated_round_trip_cost(entry_price: float, taker_fee_rate: float = 0.0004) -> float:
    """Estimate round-trip cost: 2x taker fee + spread + slippage (~10 bps)."""
    return entry_price * (2.0 * taker_fee_rate + 0.0002)


def triggered_close(
    side: SignalSide,
    *,
    stop_price: float,
    target_price: float,
    opened_at: int,
    current_price: float,
    timestamp: int,
    max_holding_ms: int,
    trail_stop_price: float | None = None,
) -> tuple[float | None, str | None]:
    if side == SignalSide.LONG:
        if current_price <= stop_price:
            reason = "trailing_stop" if trail_stop_price is not None else "stop_loss"
            return stop_price, reason
        if current_price >= target_price:
            return target_price, "target"
    else:
        if current_price >= stop_price:
            reason = "trailing_stop" if trail_stop_price is not None else "stop_loss"
            return stop_price, reason
        if current_price <= target_price:
            return target_price, "target"
    if timestamp - opened_at >= max_holding_ms:
        return current_price, "time_stop"
    return None, None


def partial_take_profit_price(
    side: SignalSide,
    *,
    entry_price: float,
    initial_stop_price: float,
    current_price: float,
    first_take_profit_r: float,
) -> float | None:
    risk = abs(entry_price - initial_stop_price)
    if risk <= 0:
        return None
    favorable_move, _ = price_moves(side, entry_price, current_price)
    if favorable_move < risk * first_take_profit_r:
        return None
    if side == SignalSide.LONG:
        return entry_price + risk * first_take_profit_r
    return entry_price - risk * first_take_profit_r


def break_even_stop_price(
    side: SignalSide,
    *,
    entry_price: float,
    initial_stop_price: float,
    current_price: float,
    break_even_trigger_r: float = 2.5,
    round_trip_cost: float = 0.0,
) -> float | None:
    risk = abs(entry_price - initial_stop_price)
    if risk <= 0:
        return None
    favorable_move, _ = price_moves(side, entry_price, current_price)
    if favorable_move < risk * break_even_trigger_r:
        return None
    if side == SignalSide.LONG:
        return entry_price + round_trip_cost
    return entry_price - round_trip_cost


def kline_momentum_stop_price(
    side: SignalSide,
    *,
    opened_at: int,
    current_stop_price: float,
    current_price: float,
    closed_klines: Sequence[KlineLike],
    consecutive_bars: int = 3,
    reference_bars: int = 2,
) -> float | None:
    if consecutive_bars <= 0 or reference_bars <= 0:
        return None
    first_full_bar_start = opened_at if opened_at % ONE_MINUTE_MS == 0 else opened_at + (ONE_MINUTE_MS - opened_at % ONE_MINUTE_MS)
    bars = sorted(
        (
            kline
            for kline in closed_klines
            if kline.interval == "1m" and kline.is_closed and kline.timestamp >= first_full_bar_start
        ),
        key=lambda kline: kline.timestamp,
    )
    if len(bars) < max(consecutive_bars, reference_bars):
        return None

    momentum_bars = bars[-consecutive_bars:]
    reference = bars[-reference_bars:]
    if side == SignalSide.LONG:
        if not all(kline.close > kline.open for kline in momentum_bars):
            return None
        candidate = min(float(kline.low) for kline in reference)
        if candidate <= current_stop_price or candidate >= current_price:
            return None
        return candidate

    if not all(kline.close < kline.open for kline in momentum_bars):
        return None
    candidate = max(float(kline.high) for kline in reference)
    if candidate >= current_stop_price or candidate <= current_price:
        return None
    return candidate


def trailing_stop_price(
    side: SignalSide,
    *,
    entry_price: float,
    initial_stop_price: float,
    current_stop_price: float,
    current_price: float,
    atr: float,
    trail_after_r: float,
    trail_atr_multiple: float,
) -> float | None:
    risk = abs(entry_price - initial_stop_price)
    if risk <= 0:
        return None
    favorable_move, _ = price_moves(side, entry_price, current_price)
    if favorable_move < risk * trail_after_r:
        return None
    atr_buffer = atr * trail_atr_multiple
    if side == SignalSide.LONG:
        candidate = current_price - atr_buffer
        return candidate if candidate > current_stop_price else None
    candidate = current_price + atr_buffer
    return candidate if candidate < current_stop_price else None


def absorption_should_reduce(
    side: SignalSide,
    *,
    delta_30s: float,
    baseline: float,
    entry_price: float,
    current_price: float,
    atr: float,
) -> bool:
    same_direction_delta = delta_30s if side == SignalSide.LONG else -delta_30s
    price_displacement = abs(current_price - entry_price)
    return same_direction_delta >= baseline and price_displacement <= max(atr, current_price * 0.001)


def should_close_for_orderflow_invalidation(
    side: SignalSide,
    *,
    delta_30s: float,
    baseline: float,
    entry_price: float,
    initial_stop_price: float,
    current_price: float,
) -> bool:
    risk = abs(entry_price - initial_stop_price)
    if risk <= 0:
        return False
    opposite_delta = -delta_30s if side == SignalSide.LONG else delta_30s
    favorable_move, _ = price_moves(side, entry_price, current_price)
    return opposite_delta >= baseline and favorable_move <= risk * 0.25
