# Strategy & Risk Layer Design

Date: 2026-05-10
Status: approved

## Scope

Phase 1 of gap remediation: strategy layer, risk controls, and live data pipeline integration.

## Changes

### 1. Profile Engine — Time-aware dual profiles

- `add_trade(price, quantity, timestamp)` — timestamp now required
- Internal `_timed_bins: dict[float, list[float]]` tracks per-trade timestamps
- `_evict_before(cutoff)` prunes expired data before `levels()` 
- `levels("session")` — UTC calendar day reset
- `levels("rolling_4h")` — last 4 hours sliding window
- New: `session_high`, `session_low` properties
- HVN/LVN detection unchanged but now operates on time-filtered bins

### 2. Signal Engine — 4 setups + forbidden conditions

Keeps same public API `evaluate(snapshot, windows) → TradeSignal | None`.

Adds `windows: HistoricalWindows` parameter carrying:
- Last 20 delta/volume values for each window size (15s/30s/60s)
- 5-minute spread history for median
- 20-bar 1m candle amplitude history

Four setup methods:
- `_setup_lvn_acceptance` — Long A: price > LVN, delta positive, 15s acceptance
- `_setup_lvn_breakdown` — Short A: symmetric
- `_setup_hvn_val_failed_breakdown` — Long B: false breakdown recovery within 60s
- `_setup_hvn_vah_failed_breakout` — Short B: false breakout failure within 60s

Forbidden conditions (checked first, before setup evaluation):
- spread > 2x 5-min median
- WebSocket stale > 1500ms
- data lag > 2000ms
- funding rate settlement ±2 min
- 1m candle amplitude > 3x 20-bar mean
- circuit breaker tripped
- existing position on same symbol

### 3. Circuit Breaker — new module `risk/circuit.py`

State machine: `normal` → `tripped` (irreversible without manual resume).

Trip reasons: daily_loss_limit, max_consecutive_losses, websocket_stale, order_protection_missing, position_mismatch, exchange_api_failure.

`can_resume()` gates: no unprotected positions, data healthy, exchange state reconciled, daily loss within hard limit.

### 4. MarketDataHealth — new type

Tracks: connection status, event/localtime delta, latency, reconnect count. `is_stale(ws_ms, lag_ms) → bool` consumed by SignalEngine forbidden check and RiskEngine.

### 5. Live pipeline

`LiveOrderflowStore` gets persistent `VolumeProfileEngine` and optional `SignalEngine`. Each trade event updates profile; periodic snapshot triggers signal evaluation. Web `/api/orderflow` returns live signal/order/PnL counts instead of hardcoded zeros.

### Files

| File | Action |
|------|--------|
| `profile/engine.py` | Rewrite for timestamp tracking |
| `signals/engine.py` | Rewrite for 4 setups + forbidden |
| `risk/circuit.py` | New |
| `risk/__init__.py` | Export CircuitBreaker |
| `market_data/health.py` | New |
| `types.py` | Add types |
| `web/live_store.py` | Integrate profile + signals |
| `paper.py` | Adapt to new ProfileEngine API |

### Tests

| File | Action |
|------|--------|
| `tests/unit/test_profile_engine.py` | Update for timestamp API |
| `tests/unit/test_signal_engine.py` | Expand for 4 setups + forbidden |
| `tests/unit/test_circuit_breaker.py` | New |
| `tests/unit/test_market_data_health.py` | New |
| `tests/unit/test_live_orderflow_store.py` | Update for profile/signal integration |

## Self-review

- No placeholders or TBDs
- Architecture follows existing module boundaries
- Signal Engine API unchanged (backward compatible)
- Profile Engine `add_trade` signature change is breaking — `paper.py` and `live_store.py` adapted
- Scope is single phase, no decomposition needed
