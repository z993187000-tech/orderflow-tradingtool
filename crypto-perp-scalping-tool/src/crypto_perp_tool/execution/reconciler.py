from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReconciliationStatus(StrEnum):
    OK = "ok"
    MISMATCH = "mismatch"
    MISSING_PROTECTION = "missing_protection"
    EXCHANGE_ONLY = "exchange_only"
    LOCAL_ONLY = "local_only"
    ERROR = "error"


@dataclass
class ReconciledPosition:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_price: float | None = None
    target_price: float | None = None
    has_stop_order: bool = False
    has_tp_order: bool = False
    exchange_quantity: float = 0.0
    exchange_entry_price: float = 0.0


@dataclass
class ReconciliationResult:
    status: ReconciliationStatus
    local_positions: dict[str, ReconciledPosition] = field(default_factory=dict)
    exchange_positions: dict[str, ReconciledPosition] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    should_pause: bool = False
    timestamp: int = 0


class PositionReconciler:
    """Syncs local position state against exchange state.

    In paper mode this tracks local positions and validates internal consistency.
    In live mode it compares against real exchange positions via REST API and
    enforces the safety policy: exchange state always wins on mismatch.
    """

    def __init__(self, mode: str = "paper") -> None:
        self.mode = mode
        self._local_positions: dict[str, ReconciledPosition] = {}
        self._reconciliation_count = 0
        self._last_mismatch_at: int | None = None

    # ------------------------------------------------------------------
    # Local state tracking
    # ------------------------------------------------------------------

    def set_local_position(self, symbol: str, position: dict[str, Any] | ReconciledPosition | None) -> None:
        key = symbol.upper()
        if position is None:
            self._local_positions.pop(key, None)
            return
        if isinstance(position, ReconciledPosition):
            self._local_positions[key] = position
        else:
            self._local_positions[key] = ReconciledPosition(
                symbol=key,
                side=str(position.get("side", "flat")),
                quantity=float(position.get("quantity", 0)),
                entry_price=float(position.get("entry_price", 0)),
                stop_price=float(position.get("stop_price", 0)) or None,
                target_price=float(position.get("target_price", 0)) or None,
                has_stop_order=bool(position.get("has_stop_order", True)),
                has_tp_order=bool(position.get("has_tp_order", True)),
            )

    def get_local_position(self, symbol: str) -> ReconciledPosition | None:
        return self._local_positions.get(symbol.upper())

    def has_local_position(self, symbol: str) -> bool:
        pos = self._local_positions.get(symbol.upper())
        return pos is not None and pos.quantity > 0

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile(
        self,
        exchange_positions: dict[str, dict[str, Any]] | None = None,
        exchange_orders: dict[str, list[dict[str, Any]]] | None = None,
        timestamp: int | None = None,
    ) -> ReconciliationResult:
        """Compare local state to exchange and return a reconciliation result."""
        import time as _time
        self._reconciliation_count += 1
        timestamp = timestamp or int(_time.time() * 1000)
        exchange_positions = exchange_positions or {}
        exchange_orders = exchange_orders or {}

        result = ReconciliationResult(
            status=ReconciliationStatus.OK,
            timestamp=timestamp,
        )

        all_symbols = set(self._local_positions.keys()) | set(exchange_positions.keys())
        if not all_symbols:
            return result

        for symbol in all_symbols:
            key = symbol.upper()
            local = self._local_positions.get(key)
            exchange = exchange_positions.get(key)

            if local and local.quantity > 0 and not exchange:
                result.mismatches.append(f"{key}: local has position but exchange does not")
                result.status = ReconciliationStatus.MISMATCH
                result.should_pause = True

            elif exchange and not local:
                result.mismatches.append(f"{key}: exchange has position but local does not")
                result.status = ReconciliationStatus.EXCHANGE_ONLY
                result.should_pause = True

            elif local and exchange:
                local_qty = local.quantity
                exchange_qty = float(exchange.get("quantity", 0))
                if abs(local_qty - exchange_qty) > 0.0001:
                    result.mismatches.append(
                        f"{key}: quantity mismatch local={local_qty} exchange={exchange_qty}"
                    )
                    result.status = ReconciliationStatus.MISMATCH
                    result.should_pause = True
                    continue

                ex_orders = exchange_orders.get(key, [])
                if not self._has_stop_order(ex_orders):
                    result.mismatches.append(f"{key}: no reduce-only stop order found on exchange")
                    result.status = ReconciliationStatus.MISSING_PROTECTION
                    result.should_pause = True

        result.local_positions = dict(self._local_positions)
        result.actions_taken = self._resolve_actions(result)
        if result.should_pause:
            self._last_mismatch_at = timestamp

        return result

    def _resolve_actions(self, result: ReconciliationResult) -> list[str]:
        actions: list[str] = []
        if result.status == ReconciliationStatus.OK:
            return actions
        if result.should_pause:
            actions.append("pause_new_signals")
        if result.status == ReconciliationStatus.MISSING_PROTECTION:
            actions.append("attempt_protective_close")
        if result.status == ReconciliationStatus.EXCHANGE_ONLY:
            actions.append("adopt_exchange_state")
        if result.status == ReconciliationStatus.MISMATCH:
            actions.append("adopt_exchange_state")
            actions.append("verify_protective_orders")
        return actions

    @staticmethod
    def _has_stop_order(exchange_orders: list[dict[str, Any]]) -> bool:
        for order in exchange_orders:
            if order.get("type") in ("STOP_MARKET", "STOP") and order.get("reduceOnly", False):
                return True
        return False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reconciliation_count": self._reconciliation_count,
            "last_mismatch_at": self._last_mismatch_at,
            "local_positions": {
                sym: {
                    "side": pos.side,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "has_stop_order": pos.has_stop_order,
                }
                for sym, pos in self._local_positions.items()
                if pos.quantity > 0
            },
        }
