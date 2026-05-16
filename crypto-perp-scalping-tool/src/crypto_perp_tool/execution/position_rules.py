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
    volume: float
    is_closed: bool


def price_moves(side: SignalSide, entry_price: float, price: float) -> tuple[float, float]:
    if side == SignalSide.LONG:
        return price - entry_price, entry_price - price
    return entry_price - price, price - entry_price


def estimated_round_trip_cost(entry_price: float, taker_fee_rate: float = 0.00018) -> float:
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
    return None, None


def max_holding_reduced_target_price(
    side: SignalSide,
    *,
    entry_price: float,
    initial_stop_price: float,
    current_target_r_multiple: float,
    elapsed_ms: int,
    max_holding_ms: int,
    completed_reductions: int,
    round_trip_cost: float = 0.0,
) -> tuple[float | None, float | None, int]:
    if max_holding_ms <= 0 or elapsed_ms < max_holding_ms:
        return None, None, completed_reductions
    reduction_count = elapsed_ms // max_holding_ms
    if reduction_count <= completed_reductions:
        return None, None, completed_reductions
    risk = abs(entry_price - initial_stop_price)
    if risk <= 0 or current_target_r_multiple <= 0:
        return None, None, completed_reductions

    missing_reductions = reduction_count - completed_reductions
    break_even_r = max(round_trip_cost / risk, 0.0)
    target_r_multiple = max(current_target_r_multiple - missing_reductions, break_even_r)
    if target_r_multiple >= current_target_r_multiple:
        return None, None, reduction_count

    if side == SignalSide.LONG:
        target_price = entry_price + risk * target_r_multiple
    else:
        target_price = entry_price - risk * target_r_multiple
    return target_price, target_r_multiple, reduction_count


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
    if len(bars) < consecutive_bars + 1:
        return None

    if side == SignalSide.LONG:
        pullback = bars[-1]
        if not _is_bearish(pullback):
            return None
        momentum_bars = _trailing_run(bars[:-1], bullish=True)
        if len(momentum_bars) < consecutive_bars:
            return None
        if not _has_lower_wick_support(pullback, momentum_bars):
            return None
        candidate = float(momentum_bars[0].low)
        if candidate <= current_stop_price or candidate >= current_price:
            return None
        return candidate

    pullback = bars[-1]
    if not _is_bullish(pullback):
        return None
    momentum_bars = _trailing_run(bars[:-1], bullish=False)
    if len(momentum_bars) < consecutive_bars:
        return None
    if not _has_upper_wick_resistance(pullback, momentum_bars):
        return None
    candidate = float(momentum_bars[0].high)
    if candidate >= current_stop_price or candidate <= current_price:
        return None
    return candidate


def _is_bullish(kline: KlineLike) -> bool:
    return float(kline.close) > float(kline.open)


def _is_bearish(kline: KlineLike) -> bool:
    return float(kline.close) < float(kline.open)


def _trailing_run(klines: Sequence[KlineLike], *, bullish: bool) -> list[KlineLike]:
    run: list[KlineLike] = []
    predicate = _is_bullish if bullish else _is_bearish
    for kline in reversed(klines):
        if not predicate(kline):
            break
        run.append(kline)
    return list(reversed(run))


def _has_lower_wick_support(pullback: KlineLike, momentum_bars: Sequence[KlineLike]) -> bool:
    body = abs(float(pullback.open) - float(pullback.close))
    lower_wick = min(float(pullback.open), float(pullback.close)) - float(pullback.low)
    return _wick_and_volume_supported(lower_wick, body, pullback, momentum_bars)


def _has_upper_wick_resistance(pullback: KlineLike, momentum_bars: Sequence[KlineLike]) -> bool:
    body = abs(float(pullback.open) - float(pullback.close))
    upper_wick = float(pullback.high) - max(float(pullback.open), float(pullback.close))
    return _wick_and_volume_supported(upper_wick, body, pullback, momentum_bars)


def _wick_and_volume_supported(wick: float, body: float, pullback: KlineLike, momentum_bars: Sequence[KlineLike]) -> bool:
    if body <= 0 or wick < body * 0.5:
        return False
    avg_volume = sum(float(kline.volume) for kline in momentum_bars) / len(momentum_bars)
    return avg_volume <= 0 or float(pullback.volume) >= avg_volume * 0.8


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
