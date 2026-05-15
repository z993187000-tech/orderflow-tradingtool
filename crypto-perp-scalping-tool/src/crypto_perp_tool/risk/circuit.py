from __future__ import annotations

import time

from crypto_perp_tool.types import CircuitBreakerReason


class CircuitBreaker:
    def __init__(self, hard_cooldown_ms: int = 3 * 60 * 1000) -> None:
        self.state: str = "normal"
        self.reason: CircuitBreakerReason | None = None
        self.tripped_at: int | None = None
        self.cooldown_until: int | None = None
        self.hard_cooldown_ms = hard_cooldown_ms

    def trip(self, reason: CircuitBreakerReason) -> dict:
        self.state = "tripped"
        self.reason = reason
        now_ms = int(time.time() * 1000)
        self.tripped_at = now_ms
        self.cooldown_until = now_ms + self.hard_cooldown_ms
        return {
            "type": "circuit_breaker_tripped",
            "reason": reason.value,
            "tripped_at": self.tripped_at,
            "cooldown_until": self.cooldown_until,
        }

    def can_resume(
        self,
        account_ok: bool = True,
        data_healthy: bool = True,
        positions_reconciled: bool = True,
    ) -> bool:
        if self.state != "tripped":
            return False
        now_ms = int(time.time() * 1000)
        if self.cooldown_until is not None and now_ms < self.cooldown_until:
            return False
        return account_ok and data_healthy and positions_reconciled

    def resume(self, actor: str) -> dict:
        if self.state != "tripped":
            raise RuntimeError("Cannot resume a circuit breaker that is not tripped")
        self.state = "normal"
        self.reason = None
        self.tripped_at = None
        self.cooldown_until = None
        return {
            "type": "circuit_breaker_resumed",
            "actor": actor,
            "resumed_at": int(time.time() * 1000),
        }
