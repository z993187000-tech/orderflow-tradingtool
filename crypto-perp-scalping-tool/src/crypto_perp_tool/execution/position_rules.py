from __future__ import annotations

from crypto_perp_tool.types import SignalSide


def price_moves(side: SignalSide, entry_price: float, price: float) -> tuple[float, float]:
    if side == SignalSide.LONG:
        return price - entry_price, entry_price - price
    return entry_price - price, price - entry_price


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
    break_even_after_r: float = 1.5,
) -> float | None:
    risk = abs(entry_price - initial_stop_price)
    if risk <= 0:
        return None
    favorable_move, _ = price_moves(side, entry_price, current_price)
    if favorable_move < risk * break_even_after_r:
        return None
    return entry_price


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
