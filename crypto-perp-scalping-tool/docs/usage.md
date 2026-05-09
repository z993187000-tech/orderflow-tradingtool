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

带访问密码的公网/共享网络模式：

```powershell
$env:PYTHONPATH='src'
$env:PASSWORD='你的强密码'
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --port 8000
```

设置 `PASSWORD` 后，Web 页面和 `/api/orderflow` 会要求浏览器 Basic Auth 登录。用户名可填 `admin` 或任意值，密码填 `PASSWORD` 的值。`/healthz` 保持公开，用于 Zeabur 健康检查。

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

实时模式同时接入 Binance Spot 和 USDⓈ-M Futures WebSocket。Web 顶部价格使用永续合约最新成交价，因为实盘买入卖出执行的是合约价格；现货、标记价和指数价仅作为辅助参考：

- Market Endpoint：`wss://fstream.binance.com/market/stream?streams=<symbol>@aggTrade/<symbol>@markPrice@1s`
- Public Endpoint：`wss://fstream.binance.com/public/stream?streams=<symbol>@bookTicker`
- Spot Endpoint：`wss://stream.binance.com:9443/stream?streams=<symbol>@trade`
- Spot Trade Stream：`<symbol>@trade`，用于辅助显示现货最新价。
- Aggregate Trade Stream：`<symbol>@aggTrade`，用于顶部 Futures Last / 合约最新价、永续合约成交明细、Delta、成交量分布。
- Mark Price Stream：`<symbol>@markPrice@1s`，用于显示标记价、指数价和资金费率。
- Book Ticker Stream：`<symbol>@bookTicker`，用于显示 bid/ask 和盘口中间价。
- 官方文档：https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams

打开：

```text
http://127.0.0.1:8000
```

当前 Web Dashboard 展示：

- Futures Last / 合约最新价：优先显示 Binance USDⓈ-M Futures `aggTrade` 最新成交价，用来对齐真实合约成交和实盘执行价格；下方同时显示 Spot 现货价、Mark 标记价、Index 指数价和 Mid 盘口中间价。
- Cum Delta / 累计Delta：主动买入成交量减主动卖出成交量的累计值。
- Signals / 信号数：策略生成的交易信号数量。
- Orders / 订单数：通过风控后的模拟订单数量。
- Closed / 平仓数：已完成平仓的模拟仓位数量。
- Paper PnL / 模拟盈亏：模拟交易已实现盈亏。
- Connection / 连接状态：Binance WebSocket 或 CSV 回放状态。
- Price and execution canvas：价格路径、signal marker、平仓 marker，并显示 y 轴价格。
- Cumulative Delta canvas：累计 Delta 曲线，并显示 y 轴 Delta 值。
- Volume Profile levels：POC、HVN、LVN、VAH、VAL。
- Recent Tape：最近成交方向、价格、数量、Delta。

这是 Web 成型的第一层基础：CSV replay 和 Binance live 都输出同一个 `/api/orderflow` 数据结构，后续可以继续扩展盘口深度、真实信号和订单状态。

实时刷新与 profile 计算规则：

- Web 页面每 2 秒自动请求一次 `/api/orderflow`，并使用 `cache=no-store` 避免浏览器缓存旧价格。
- 顶部 Futures Last / 合约最新价按 `Perp aggTrade -> Index price -> bookTicker mid` 的优先级显示；Spot 现货价只在辅助行展示，不参与主价格选择。
- Live 模式默认同时启动 BTCUSDT 和 ETHUSDT 数据源，页面下拉框切换时会请求对应 symbol 的独立 live store。
- Live 模式下图表只展示最近 500 笔成交，避免手机端卡顿；Volume Profile 使用最近最多 20,000 笔成交计算，避免 POC、VAH、VAL 被极短窗口压扁。
- POC 显示成交量最大分桶的中心价；VAH/VAL 显示价值区的上沿/下沿边界价，不再直接显示分桶中心价。
- 如果最新价仍明显落后，优先看 Connection / 连接状态和浏览器网络请求，确认 Binance WebSocket 是否仍为 `connected`。
- 手机端价格图高度固定在约 210px、Delta 图约 160px，避免 canvas 因浏览器比例计算被拉成长图。

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

- 真实下单。
- Telegram long polling。

这些属于下一阶段，在 paper replay、journal、risk、signal 和 Web 观察面板的核心契约稳定后再接入。
