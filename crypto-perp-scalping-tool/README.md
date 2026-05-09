# Crypto Perpetual Scalping Tool

Automated crypto perpetual scalping framework based on auction market theory, volume profile, order-flow confirmation, and strict risk gates.

The first implementation target is paper trading for BTCUSDT and ETHUSDT on Binance USDT-M Futures. Live trading must remain disabled until the paper-trading acceptance criteria in the technical spec are met.

## Project Layout

```text
config/                 Default runtime configuration
docs/                   Strategy and implementation documents
src/crypto_perp_tool/   Python package skeleton
tests/                  Unit tests for the implementation contracts
```

## Documents

- [Technical spec](docs/crypto-perp-scalping-technical-spec.md)
- [Implementation framework](docs/implementation-framework.md)
- [Project issues and resolutions](docs/project-issues-and-resolutions.md)
- [Usage](docs/usage.md)
- [Zeabur deployment](docs/zeabur-deployment.md)

## Interaction And Deployment

The short-term operator interface is a Telegram Bot for status, alerts, paper-trading control, signal inspection, and journal lookup. The Web Dashboard comes later for charts, volume profile visualization, replay, and reporting.

Early deployments can run on Zeabur for Telegram Bot, paper-trading worker, database services, and the later Web Dashboard. Live trading core should eventually move to a more controlled VPS or cloud server with fixed IP, stronger process supervision, and independent monitoring.

## Local Order-Flow Dashboard

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --port 8000
```

Then open `http://127.0.0.1:8000`.

For phone access on the same Wi-Fi:

```powershell
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --mobile --port 8000
```

Open the printed `Phone/LAN` URL on your phone.

For public access through Zeabur, open:

```text
https://orderflow-tradingtool.zeabur.app/
```

Set the Zeabur environment variable `PASSWORD` before exposing the dashboard publicly. The browser login username can be `admin` or any value; the password is the `PASSWORD` value. `/healthz` stays public for deployment health checks.

## Safety

This project is an engineering scaffold, not financial advice. Keep `mode=paper` until the strategy has passed backtests, simulation tests, and a paper-trading burn-in.
