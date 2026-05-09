# 初版工具使用说明

## 运行测试

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests/unit
```

也可以先安装为 editable package：

```powershell
python -m pip install -e .
crypto-tool config show
```

Binance 实时 WebSocket 模式依赖 `websockets`，已经写入 `pyproject.toml`。执行 `python -m pip install -e .` 会安装该依赖。

## 查看默认配置

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli config show
```

默认配置保持 `mode=paper`。即使传入 live mode，缺少 `LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_LIVE_RISK` 时也会降级回 paper。

## 运行样例 paper replay

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli paper run --csv data/sample_trades.csv --journal data/journal.jsonl
```

输出字段：

- `trades`：读取的成交事件数。
- `signals`：生成的策略信号数。
- `orders`：通过 Risk Engine 的 paper orders 数。
- `rejected`：被 Risk Engine 拒绝的信号数。
- `closed_positions`：命中止盈/止损后关闭的 paper positions 数。
- `realized_pnl`：paper replay 的已实现 PnL。
- `journal_path`：事件日志路径。

## 查看 journal

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli journal tail --path data/journal.jsonl --limit 5
```

## 启动订单流 Web 盘面

回放模式：

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli web serve --source csv --csv data/sample_trades.csv --port 8000
```

Binance 实时模式：

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --port 8000
```

手机访问模式：

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --mobile --port 8000
```

启动后终端会打印：

```text
Local: http://127.0.0.1:8000
Phone/LAN: http://你的电脑局域网IP:8000
```

手机和电脑必须在同一个 Wi-Fi/局域网下。若手机打不开，通常需要允许 Windows 防火墙放行 Python，或确认路由器没有开启 AP/client isolation。

实时模式使用 Binance USDⓈ-M Futures Aggregate Trade Stream：

- 官方文档：https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams
- Stream name：`<symbol>@aggTrade`
- Endpoint：`wss://fstream.binance.com/market/ws/<symbol>@aggTrade`

打开：

```text
http://127.0.0.1:8000
```

当前 Web Dashboard 展示：

- Last price、累计 Delta、信号数、paper orders、平仓数、paper PnL。
- Price and execution canvas：价格路径、signal marker、平仓 marker。
- Cumulative Delta canvas：累计 Delta 曲线。
- Volume Profile levels：POC、HVN、LVN、VAH、VAL。
- Recent Tape：最近成交方向、价格、数量、Delta。
- Connection：实时模式下显示 Binance WebSocket 连接状态。

这是 Web 成型的第一层基础：CSV replay 和 Binance live 都输出同一个 `/api/orderflow` 数据结构，后续可以继续扩展盘口深度、真实信号和订单状态。

## 单独检查 Risk Engine

准备一个 JSON 文件：

```json
{
  "signal": {
    "id": "sig-1",
    "symbol": "BTCUSDT",
    "side": "long",
    "setup": "lvn_break_acceptance",
    "entry_price": 100,
    "stop_price": 99,
    "target_price": 102,
    "confidence": 0.7,
    "reasons": ["accepted above LVN"],
    "invalidation_rules": ["back below LVN"],
    "created_at": 1
  },
  "account": {
    "equity": 10000,
    "realized_pnl_today": 0,
    "consecutive_losses": 0
  }
}
```

运行：

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli risk check --json risk-input.json
```

## CSV 格式

必需列：

- `timestamp`
- `price`
- `quantity`

可选列：

- `symbol`：缺失时使用 `BTCUSDT`。
- `is_buyer_maker`：Binance aggTrade 语义，`true` 记为主动卖出成交，`false` 记为主动买入成交。

## Telegram Bot 初版边界

当前代码提供 `TelegramCommandHandler`，用于验证命令边界和 service 调用。它还不是联网 long-polling bot。

已支持命令：

- `/status`
- `/pause`
- `/resume`
- `/risk`
- `/journal`

所有命令都必须通过 chat id 白名单。未授权 chat id 会被拒绝并写入 journal。

## 当前不包含

- 真实 Binance WebSocket。
- 真实下单。
- Telegram long polling。
- Web Dashboard。

这些属于下一阶段，在 paper replay、journal、risk、signal 的核心契约稳定后再接入。
