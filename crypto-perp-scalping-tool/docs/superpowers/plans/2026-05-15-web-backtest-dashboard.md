# Web Backtest Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated Backtest tab to the existing Web Dashboard that runs server-side CSV backtests and renders the latest report.

**Architecture:** Reuse `BacktestEngine` and `BacktestReporter` behind a new authenticated `POST /api/backtest/run` endpoint in the existing standard-library HTTP server. Keep the frontend as static HTML/CSS/JS and render the report client-side from the endpoint's JSON response.

**Tech Stack:** Python 3.11 standard library HTTP server, existing `crypto_perp_tool.backtest`, static HTML/CSS/JavaScript, `unittest`.

---

## File Structure

- Modify `src/crypto_perp_tool/web/server.py`: add JSON POST body parsing, safe project-relative CSV resolution, `/api/backtest/run`, and backtest response formatting.
- Modify `src/crypto_perp_tool/web/static/index.html`: add `Live / Backtest` view switch, backtest form, report panel, canvas, setup table, trade table, and error region.
- Modify `src/crypto_perp_tool/web/static/app.js`: add view switching, form submission, API request, report rendering, equity curve drawing, split result support, and error rendering.
- Modify `src/crypto_perp_tool/web/static/app.css`: add layout and responsive styling for the dedicated backtest workspace.
- Modify `tests/unit/test_web_server.py`: add endpoint and path safety tests.
- Modify `tests/unit/test_web_static_ui.py`: add static UI contract tests.

## Task 1: Backtest API Contract

**Files:**
- Modify: `tests/unit/test_web_server.py`
- Modify: `src/crypto_perp_tool/web/server.py`

- [ ] **Step 1: Write failing API tests**

Add tests that instantiate `create_app_handler(...)`, start a temporary `ThreadingHTTPServer`, and POST JSON to `/api/backtest/run`.

Required assertions:

- response status is `200` for `{"csv_path": "data/btcusdt_recent.csv", "symbol": "BTCUSDT", "equity": 10000}`
- response JSON has `mode == "single"`, `symbol == "BTCUSDT"`, `total_events > 0`, `report`, and `equity_curve`
- response status is `400` for `{"csv_path": "../CLAUDE.md"}`
- error body contains an `error` field

- [ ] **Step 2: Verify API tests fail**

Run:

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.unit.test_web_server.WebServerTests.test_backtest_run_endpoint_returns_report tests.unit.test_web_server.WebServerTests.test_backtest_run_endpoint_rejects_unsafe_paths
```

Expected: tests fail because `/api/backtest/run` is not implemented.

- [ ] **Step 3: Implement minimal API**

In `server.py`, add helpers:

- `_read_json_body(handler) -> dict`
- `_send_json_status(handler, payload, status)`
- `_safe_project_path(raw_path, project_root) -> Path`
- `_format_backtest_api_result(result) -> dict`
- `_format_split_backtest_api_result(is_result, oos_result) -> dict`

In `DashboardHandler.do_POST`, handle `/api/backtest/run` after auth, parse JSON, validate path under `Path.cwd()`, run `BacktestEngine`, and return JSON.

- [ ] **Step 4: Verify API tests pass**

Run the same command from Step 2. Expected: both tests pass.

## Task 2: Backtest Static UI Contract

**Files:**
- Modify: `tests/unit/test_web_static_ui.py`
- Modify: `src/crypto_perp_tool/web/static/index.html`
- Modify: `src/crypto_perp_tool/web/static/app.js`
- Modify: `src/crypto_perp_tool/web/static/app.css`

- [ ] **Step 1: Write failing static UI tests**

Add tests asserting:

- HTML contains `id="liveView"`, `id="backtestView"`, `id="backtestForm"`, `id="backtestCsvPath"`, and `id="backtestReport"`
- JS contains `/api/backtest/run`, `renderBacktestReport`, `drawBacktestEquityCurve`, `renderBacktestSplitReport`, and `backtestForm`
- CSS contains `.view-tabs`, `.backtest-workspace`, `.backtest-form`, and `.backtest-report`

- [ ] **Step 2: Verify static UI tests fail**

Run:

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.unit.test_web_static_ui.WebStaticUiTests.test_dashboard_contains_backtest_workspace tests.unit.test_web_static_ui.WebStaticUiTests.test_javascript_runs_and_renders_backtest_report tests.unit.test_web_static_ui.WebStaticUiTests.test_css_styles_backtest_workspace
```

Expected: tests fail because the backtest workspace does not exist.

- [ ] **Step 3: Implement static UI**

Update `index.html`:

- add topbar buttons with `data-view="live"` and `data-view="backtest"`
- wrap existing dashboard content in `<div id="liveView">`
- add `<section id="backtestView" class="backtest-view is-hidden">`
- include form fields for CSV path, symbol, equity, entry slippage, exit slippage, fee, start, end, and split
- include report nodes for status, scorecards, equity canvas, setup table, trade table, and metadata

Update `app.js`:

- bind view buttons
- collect form values
- POST JSON to `/api/backtest/run`
- render single and split reports
- draw equity curve on canvas
- render setup breakdown and trade records
- show errors without throwing

Update `app.css`:

- style view tabs, backtest workspace grid, form controls, report scorecards, tables, and responsive single-column layout under 900px

- [ ] **Step 4: Verify static UI tests pass**

Run the same command from Step 2. Expected: all three tests pass.

## Task 3: Regression Suite

**Files:**
- Existing test suite only.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.unit.test_web_server tests.unit.test_web_static_ui tests.unit.test_backtest_engine tests.unit.test_backtest_report
```

Expected: all focused tests pass.

- [ ] **Step 2: Run full unit suite**

Run:

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests/unit
```

Expected: all unit tests pass or any unrelated pre-existing failures are reported with exact failing tests.

## Self-Review

- Spec coverage: The plan covers the Backtest tab, server CSV path input, safe path validation, API output, report rendering, error rendering, and tests.
- Placeholder scan: No `TBD`, `TODO`, or unspecified implementation steps remain.
- Type consistency: Request fields match the design document and helper names are used consistently across tasks.
