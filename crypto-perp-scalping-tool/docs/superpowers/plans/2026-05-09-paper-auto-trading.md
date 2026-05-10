# Paper Auto Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete paper auto-trading loop on top of the live Binance futures market data stream.

**Architecture:** Add an event-driven paper trading engine that consumes each futures trade, updates the volume profile and order-flow state, evaluates strategy signals, applies risk controls, opens one simulated position, and closes it at stop or target. The Web live store will route Binance trades into this engine and expose paper signals, orders, closed positions, PnL, and chart markers through the existing dashboard API.

**Tech Stack:** Python standard library, existing `SignalEngine`, `RiskEngine`, `VolumeProfileEngine`, `LiveOrderflowStore`, `unittest`, static Web dashboard.

---

### Task 1: Event-Driven Paper Engine

**Files:**
- Create: `src/crypto_perp_tool/execution/paper_engine.py`
- Test: `tests/unit/test_paper_trading_engine.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that a stream of live trade events can produce a paper signal, paper order, closed position, realized PnL, detail records, and chart markers.

- [ ] **Step 2: Run tests to verify RED**

Run: `PYTHONPATH=src python -m unittest tests.unit.test_paper_trading_engine`

Expected: fail because `crypto_perp_tool.execution.paper_engine` does not exist.

- [ ] **Step 3: Implement minimal engine**

Implement `PaperTradingEngine.process_trade()`, `summary()`, `details()`, and `markers()` using the existing signal, risk, and profile engines.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `PYTHONPATH=src python -m unittest tests.unit.test_paper_trading_engine`

Expected: pass.

### Task 2: Live Store Integration

**Files:**
- Modify: `src/crypto_perp_tool/web/live_store.py`
- Test: `tests/unit/test_live_orderflow_store.py`

- [ ] **Step 1: Write failing test**

Add a test proving `LiveOrderflowStore` sends live trades through the paper engine and exposes nonzero paper summary/detail values.

- [ ] **Step 2: Run test to verify RED**

Run: `PYTHONPATH=src python -m unittest tests.unit.test_live_orderflow_store`

Expected: fail because current live store still returns empty paper execution details.

- [ ] **Step 3: Implement live store integration**

Create a `PaperTradingEngine` per store, process each accepted trade, and merge engine summary/details/markers into the view model.

- [ ] **Step 4: Run test to verify GREEN**

Run: `PYTHONPATH=src python -m unittest tests.unit.test_live_orderflow_store`

Expected: pass.

### Task 3: Documentation And Verification

**Files:**
- Modify: `docs/usage.md`
- Modify: `docs/implementation-iteration-log.md`

- [ ] **Step 1: Document paper auto-trading behavior**

Document that live Binance data now runs an automatic paper strategy loop, while real exchange execution remains disabled.

- [ ] **Step 2: Run full verification**

Run unit tests and JavaScript syntax check:

```powershell
$env:PYTHONPATH='crypto-perp-scalping-tool/src'; python -m unittest discover -s crypto-perp-scalping-tool/tests/unit
node --check crypto-perp-scalping-tool\src\crypto_perp_tool\web\static\app.js
```

Expected: all tests pass and JS syntax check exits 0.
