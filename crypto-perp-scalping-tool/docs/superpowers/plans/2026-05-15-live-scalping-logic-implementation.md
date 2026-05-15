# Live Scalping Logic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox syntax for tracking and should be verified with focused tests before moving on.

**Goal:** Replace fixed setup scanning with a live trading pipeline while preserving `SignalEngine.evaluate(...) -> TradeSignal | None` and keeping paper mode as the only default execution path.

**Architecture:** `SignalEngine` orchestrates `MarketStateEngine -> BiasEngine -> SetupCandidateEngine -> ConfirmationGate -> TradePlanBuilder`; `RiskEngine` and `PaperTradingEngine` remain downstream.

**Tech Stack:** Python dataclasses, existing `unittest`, existing dashboard/backtest/paper execution modules.

---

## File Structure

- Modify `src/crypto_perp_tool/types.py`: add trace and pipeline dataclasses; extend `TradeSignal` with optional metadata.
- Modify `src/crypto_perp_tool/config.py` and `config/default.yaml`: add `market_state`, `confirmation`, `trade_plan`, and `management`.
- Add `src/crypto_perp_tool/signals/market_state.py`.
- Add `src/crypto_perp_tool/signals/bias.py`.
- Add `src/crypto_perp_tool/signals/setups.py`.
- Add `src/crypto_perp_tool/signals/confirmation.py`.
- Add `src/crypto_perp_tool/signals/trade_plan.py`.
- Modify `src/crypto_perp_tool/signals/engine.py`: rewire as orchestrator.
- Modify `src/crypto_perp_tool/paper.py`: pass closed 1m klines into signal evaluation.
- Modify `src/crypto_perp_tool/execution/models.py` and `src/crypto_perp_tool/execution/paper_engine.py`: persist setup-aware management metadata and apply setup-specific management.
- Modify `src/crypto_perp_tool/web/live_store.py`: pass closed 1m klines and expose market state/bias/reject reasons in `/api/orderflow`.
- Modify `src/crypto_perp_tool/backtest/report.py`: add `by_strategy_context`.
- Update strategy and usage documentation.

## Task 1: Strategy Types And Config

**Files:**
- `src/crypto_perp_tool/types.py`
- `src/crypto_perp_tool/config.py`
- `config/default.yaml`
- `tests/unit/test_config.py`
- `tests/unit/test_types.py`

- [x] Add `MarketStateResult`, `BiasResult`, `SetupCandidate`, `ConfirmationResult`, `TradePlan`, and `SignalTrace`.
- [x] Extend `TradeSignal` with optional `setup_model`, `legacy_setup`, `market_state`, `bias`, `target_source`, `management_profile`, and `trace`.
- [x] Add default config sections for `market_state`, `confirmation`, `trade_plan`, and `management`.
- [x] Verify dataclasses serialize via `to_jsonable`.

## Task 2: MarketStateEngine

**Files:**
- `src/crypto_perp_tool/signals/market_state.py`
- `tests/unit/test_market_state.py`

- [x] Detect `balanced`, `imbalanced_up`, `imbalanced_down`, `compression`, `absorption`, `failed_auction`, and `no_trade`.
- [x] Reject unhealthy data/profile conditions as `no_trade`.
- [x] Cover sustained VAH acceptance, absorption, failed auction, compression, and bad data tests.

## Task 3: BiasEngine

**Files:**
- `src/crypto_perp_tool/signals/bias.py`
- `tests/unit/test_bias.py`

- [x] Return `long` for accepted value-up context with non-conflicting order flow.
- [x] Return `short` for accepted value-down context with non-conflicting order flow.
- [x] Return `neutral` near POC or when state/order-flow conditions conflict.
- [x] Ensure bias only controls setup eligibility.

## Task 4: Setup Candidate Generation

**Files:**
- `src/crypto_perp_tool/signals/setups.py`
- `tests/unit/test_setup_candidates.py`

- [x] Map legacy trend setups to `squeeze_continuation`.
- [x] Map CVD/HVN/VAH/VAL failed breakout/breakdown setups to `failed_auction_reversal`.
- [x] Map LVN break setups to `lvn_acceptance`.
- [x] Add `absorption_response` as candidate/management model.
- [x] Preserve legacy setup names for reports.

## Task 5: ConfirmationGate

**Files:**
- `src/crypto_perp_tool/signals/confirmation.py`
- `tests/unit/test_confirmation.py`

- [x] Require complete closed 1m candle when configured.
- [x] Apply close buffer bps, delta ratio, volume ratio, ATR displacement, and reclaim checks.
- [x] Reject tick-only breaks with `candle_close_not_confirmed`.
- [x] Reject missing delta/volume confirmation and trigger reclaim.

## Task 6: TradePlanBuilder

**Files:**
- `src/crypto_perp_tool/signals/trade_plan.py`
- `tests/unit/test_trade_plan.py`

- [x] Build structure-first targets from context/execution profile levels.
- [x] Filter explicit structure targets when R:R is too low.
- [x] Fall back to capped R when no valid structure target is available.
- [x] Keep stops on the protective side and include management profile metadata.

## Task 7: SignalEngine Orchestrator

**Files:**
- `src/crypto_perp_tool/signals/engine.py`
- `tests/unit/test_signal_engine.py`

- [x] Preserve public `evaluate(...)` signature and add optional `klines`.
- [x] Run forbidden checks before pipeline.
- [x] Evaluate market state, bias, candidates, confirmation, and trade plan in order.
- [x] Persist `last_reject_reasons` and `last_trace`.
- [x] Update tests for 1m close confirmation behavior.

## Task 8: Setup-Aware Paper Management

**Files:**
- `src/crypto_perp_tool/execution/models.py`
- `src/crypto_perp_tool/execution/paper_engine.py`
- `src/crypto_perp_tool/paper.py`
- `tests/unit/test_paper_trading_engine.py`

- [x] Store `setup_model`, `legacy_setup`, `market_state`, `bias`, `target_source`, and `management_profile` through signal/order/position records.
- [x] Use setup-specific break-even thresholds.
- [x] Exit squeeze positions that fail to follow through within the configured timeout.
- [x] Preserve old default break-even behavior for legacy/unknown positions.
- [x] Pass closed 1m klines during CSV paper replay and live paper processing.

## Task 9: Live Store And Backtest Reporting

**Files:**
- `src/crypto_perp_tool/web/live_store.py`
- `src/crypto_perp_tool/backtest/report.py`
- `tests/unit/test_live_orderflow_store.py`
- `tests/unit/test_backtest_report.py`

- [x] `/api/orderflow` summary includes `market_state`, `bias`, and `last_reject_reasons`.
- [x] Signal markers include strategy metadata for dashboard consumers.
- [x] Backtest report groups by `setup_model|market_state|session`.
- [x] Keep old `setup` and `reject_reasons` fields for backward compatibility.

## Task 10: Documentation And Verification

**Files:**
- `docs/superpowers/specs/2026-05-15-live-scalping-logic-design.md`
- `docs/superpowers/plans/2026-05-15-live-scalping-logic-implementation.md`
- `docs/strategy.md`
- `docs/crypto-perp-scalping-technical-spec.md`
- `docs/usage.md`

- [x] Document the new pipeline.
- [x] Document the four reusable live setup models.
- [x] Document confirmation gate parameters and structure-first trade planning.
- [x] Document setup-aware paper management and paper-first safety.
- [x] Run full unit verification.

## Verification

Primary command:

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests/unit
```

Optional replay:

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli backtest run --csv data/sample_trades.csv
```

## Acceptance Criteria

- Tick刺破但未完整 1m 收盘确认，不允许开仓。
- 大 delta 但无位移识别为 `absorption`。
- Seller aggression failed 后，突破并收盘确认才允许 long squeeze candidate。
- 结构目标优先于固定 R。
- 明确结构目标 R:R 不足时拒绝。
- 价格回到 trigger 内侧时拒绝或由 paper management 退出。
- Paper mode 不产生 live order。
- 全量 unit suite 通过。
