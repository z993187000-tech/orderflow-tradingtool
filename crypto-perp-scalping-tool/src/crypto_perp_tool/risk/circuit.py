from __future__ import annotations

import time

from crypto_perp_tool.types import CircuitBreakerReason


class CircuitBreaker:
    def __init__(self) -> None:
        self.state: str = "normal"
        self.reason: CircuitBreakerReason | None = None
        self.tripped_at: int | None = None

    def trip(self, reason: CircuitBreakerReason) -> dict:
        self.state = "tripped"
        self.reason = reason
        self.tripped_at = int(time.time() * 1000)
        return {
            "type": "circuit_breaker_tripped",
            "reason": reason.value,
            "tripped_at": self.tripped_at,
        }

    def can_resume(
        self,
        account_ok: bool = True,
        data_healthy: bool = True,
        positions_reconciled: bool = True,
        daily_loss_within_limit: bool = True,
    ) -> bool:
        if self.state != "tripped":
            return False
        return account_ok and data_healthy and positions_reconciled and daily_loss_within_limit

    def resume(self, actor: str) -> dict:
        if self.state != "tripped":
            raise RuntimeError("Cannot resume a circuit breaker that is not tripped")
        self.state = "normal"
        self.reason = None
        self.tripped_at = None
        return {
            "type": "circuit_breaker_resumed",
            "actor": actor,
            "resumed_at": int(time.time() * 1000),
        }
