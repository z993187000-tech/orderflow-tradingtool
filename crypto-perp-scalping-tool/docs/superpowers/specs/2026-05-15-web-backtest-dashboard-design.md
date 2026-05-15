# Web Backtest Dashboard Design

## Goal

Add a dedicated Web Dashboard backtest workspace so the operator can run a historical CSV backtest from the browser and inspect the generated report without leaving the existing local dashboard.

## Scope

The first version supports server-side CSV paths only. The browser sends a project-relative CSV path such as `data/btcusdt_recent.csv`; the server validates the path, reads the file, runs the existing `BacktestEngine`, and returns a JSON report. Browser file upload, persistent report history, and multi-run comparison are out of scope for this version.

## User Experience

The dashboard gains a `Live / Backtest` view switch in the top bar. `Live` keeps the current order-flow dashboard behavior. `Backtest` opens a two-column workspace:

- Left column: form controls for CSV path, symbol, equity, entry slippage, exit slippage, fee, optional start and end timestamps, and optional walk-forward split.
- Right column: the latest backtest report with scorecards, equity curve, setup breakdown, trade table, data quality, config version, and errors.

The backtest view should feel like an operational tool, not a marketing page. It should use the existing dark dashboard palette, compact controls, fixed chart height, and table styling.

## Backtest API

Add `POST /api/backtest/run` to the existing dashboard HTTP handler. The request body is JSON:

```json
{
  "csv_path": "data/btcusdt_recent.csv",
  "symbol": "BTCUSDT",
  "equity": 10000,
  "entry_slippage_bps": 2.0,
  "exit_slippage_bps": 3.0,
  "fee_bps": 4.0,
  "start_ms": null,
  "end_ms": null,
  "split": 0.0
}
```

The response for a normal run includes:

- `mode`: `single`
- `symbol`
- `total_events`
- `equity_start`, `equity_end`, `total_return_pct`
- `report`
- `equity_curve`
- `trade_records`
- `data_quality`
- `config_version`
- `errors`

The response for a split run includes:

- `mode`: `split`
- `symbol`
- `total_events`
- `in_sample`
- `out_of_sample`
- `walk_forward_efficiency`

Both `in_sample` and `out_of_sample` use the same result shape as the single run, so the frontend can render either section with one formatter.

## Path Safety

CSV paths must be project-relative and must stay inside the dashboard server's current working tree. The API rejects:

- absolute paths
- paths containing `..`
- paths that resolve outside the project root
- paths that do not exist

The server returns a JSON error with HTTP 400 instead of exposing a stack trace.

## Error Handling

The frontend displays API errors in the report panel. Expected errors include missing CSV files, missing CSV columns, no events after filtering, invalid split fractions, and malformed JSON. The existing Basic Auth gate applies to the backtest endpoint when `PASSWORD` is configured.

## Testing

Tests cover:

- successful API backtest response for a project-relative CSV path
- rejection of unsafe CSV paths
- JSON error response for invalid inputs
- static HTML includes the Backtest view controls
- JavaScript posts to `/api/backtest/run` and renders report metrics, equity curve, setup breakdown, trades, and errors

Implementation must follow TDD: add each failing test before adding production code.
