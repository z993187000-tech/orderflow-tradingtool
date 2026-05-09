from __future__ import annotations

import time


def health_payload(source: str, symbol: str) -> dict[str, object]:
    return {
        "status": "ok",
        "service": "crypto-perp-scalping-tool",
        "source": source,
        "symbol": symbol,
        "time": int(time.time() * 1000),
    }
