from __future__ import annotations

import time

from crypto_perp_tool.types import MarketDataHealth


def compute_health(
    connection_status: str = "starting",
    last_event_time: int = 0,
    last_local_time: int = 0,
    reconnect_count: int = 0,
    symbol: str = "",
) -> MarketDataHealth:
    if last_event_time > 0 and last_local_time > 0:
        latency_ms = last_local_time - last_event_time
    else:
        latency_ms = 0
    return MarketDataHealth(
        connection_status=connection_status,
        last_event_time=last_event_time,
        last_local_time=last_local_time,
        latency_ms=max(latency_ms, 0),
        reconnect_count=reconnect_count,
        symbol=symbol,
    )
