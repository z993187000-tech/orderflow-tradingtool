from __future__ import annotations


def compute_exchange_lag_ms(
    *,
    event_time: int,
    exchange_event_time: int | None = None,
    received_at: int | None = None,
) -> int:
    """Return exchange lag, falling back to local receive time for legacy data."""
    comparison_time = exchange_event_time if exchange_event_time is not None else received_at
    if comparison_time is None:
        return 0
    return max(0, int(comparison_time) - int(event_time))
