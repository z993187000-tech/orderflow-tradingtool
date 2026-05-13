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

实时模式以 Binance USDⓈ-M Futures WebSocket 为主，同时保留 Binance Spot 最新成交价作为参考。Web 顶部价格优先显示 Binance USDⓈ-M Futures aggTrade 最新成交价，订单流、Delta、成交量分布和 paper fill 都使用永续合约成交：

- Market Endpoint：`wss://fstream.binance.com/market/stream?streams=<symbol>@aggTrade/<symbol>@markPrice@1s`
- Public Endpoint：`wss://fstream.binance.com/public/stream?streams=<symbol>@bookTicker`
- Spot Endpoint：`wss://stream.binance.com:9443/stream?streams=<symbol>@trade`
- Spot Trade Stream：`<symbol>@trade`，只用于 Spot 参考价，不作为 Web 顶部主价格。
- Aggregate Trade Stream：`<symbol>@aggTrade`，用于永续合约成交明细、Delta、成交量分布。
- Mark Price Stream：`<symbol>@markPrice@1s`，用于显示标记价、指数价和资金费率。
- Book Ticker Stream：`<symbol>@bookTicker`，用于显示 bid/ask 和盘口中间价。
- 官方文档：https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams

打开：

```text
http://127.0.0.1:8000
```

当前 Web Dashboard 展示：

- Perp Last / 永续最新成交价：优先显示 Binance USDⓈ-M Futures `aggTrade` 最新成交价；如果暂时没有成交事件，则依次回退到 Futures `bookTicker` 中间价、`mark_price`、`index_price`。下方同时显示 Perp 永续成交价、Mark 标记价、Index 指数价、Mid 盘口中间价和 Spot 参考价。
- Cum Delta / 累计Delta：主动买入成交量减主动卖出成交量的累计值。
- Signals / 信号数：策略生成的交易信号数量。
- Orders / 订单数：通过风控后的模拟订单数量。
- Closed / 平仓数：已完成平仓的模拟仓位数量。
- Paper PnL / 模拟盈亏：模拟交易已实现盈亏。
- Connection / 连接状态：Binance WebSocket 或 CSV 回放状态。
- Price and execution canvas：价格路径、signal marker、平仓 marker，并显示 y 轴价格。
- Aggression bubble marker：单笔 Binance Futures `aggTrade` 数量 >= 10 BTC 时在价格图上显示买/卖气泡，>= 50 BTC 使用 block 级别大气泡。
- Strategy State：单独展示最近 `break_even_shift`、`absorption_reduce`、最近大单气泡、当前 1m/3m ATR 和当前 CVD divergence 状态，用于解释策略为什么保护仓位、为什么减仓、为什么准备关注假突破。
- Cumulative Delta canvas：累计 Delta 曲线，并显示 y 轴 Delta 值。
- Volume Profile levels：POC、HVN、LVN、VAH、VAL。
- Recent Tape：最近成交方向、价格、数量、Delta。

这是 Web 成型的第一层基础：CSV replay 和 Binance live 都输出同一个 `/api/orderflow` 数据结构，后续可以继续扩展盘口深度、真实信号和订单状态。

实时刷新与 profile 计算规则：

- Web 页面每 2 秒自动请求一次 `/api/orderflow`，并使用 `cache=no-store` 避免浏览器缓存旧价格。
- 顶部 Perp Last / 永续最新成交价按 `Perp aggTrade -> bookTicker mid -> mark_price -> index_price` 的优先级显示，避免把现货价格误当作实盘永续成交价。
- Live 模式默认同时启动 BTCUSDT 和 ETHUSDT 数据源，页面下拉框切换时会请求对应 symbol 的独立 live store。
- Live 模式下图表只展示最近 500 笔成交，避免手机端卡顿；Volume Profile 使用最近最多 20,000 笔成交计算，避免 POC、VAH、VAL 被极短窗口压扁。
- POC 显示成交量最大分桶的中心价；VAH/VAL 显示价值区的上沿/下沿边界价，不再直接显示分桶中心价。
- 如果最新价仍明显落后，优先看 Connection / 连接状态和浏览器网络请求，确认 Binance WebSocket 是否仍为 `connected`。
- 手机端价格图高度固定在约 210px、Delta 图约 160px，避免 canvas 因浏览器比例计算被拉成长图。

当前 paper 策略已包含：

- `vah_breakout_lvn_pullback_aggression` / `val_breakdown_lvn_pullback_aggression`：VAH/VAL 突破后等待 LVN 回踩，并要求同向 Aggression Bubble 确认。
- `cvd_divergence_failed_breakout` / `cvd_divergence_failed_breakdown`：价格刺破 VAH/VAL 但 CVD 未跟随创新高/新低，回到价值区后按假突破处理，目标优先看 POC。
- 真实 1m / 3m ATR：由成交流聚合 K 线计算，用于动态止损和吸收保护。
- `break_even_shift`：价格走出 1.5R 后将止损移动到开仓均价。
- `absorption_reduce`：持仓方向 Delta 激增但价格不动时，paper engine 记录吸收事件并强制减仓。

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

## 运行故障仿真

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli simulation run
```

当前内置 5 个仿真场景：

- `websocket_disconnect`：行情事件到达过慢，验证新开仓被停止并记录 `data_stale`。
- `slippage_expansion`：扩大入场/出场滑点，验证报告输出平均滑点。
- `fast_reversal`：入场后快速反向触发止损，验证亏损和平仓记录。
- `partial_fill`：入场只部分成交，验证订单状态为 `partially_filled`，仓位只按成交数量计算。
- `stop_submission_failure`：入场后止损提交失败，验证触发 `protective_close` 和 `circuit_breaker_tripped`。

输出包含每个场景的 `summary`、`report`、`reject_reasons`、`risk_events` 和 `protective_actions`。这一步仍然是 paper/simulation，不会连接交易所下单。

## CSV 格式

必需列：

- `timestamp`
- `price`
- `quantity`

可选列：

- `symbol`：缺失时使用 `BTCUSDT`。
- `is_buyer_maker`：Binance aggTrade 语义，`true` 记为主动卖出成交，`false` 记为主动买入成交。

## 运行回测

`BacktestEngine` 使用和实时模式相同的 `PaperTradingEngine` 执行核心，逐笔处理历史成交数据，经过 profile → signal → risk → execution 完整管线，输出回测报告、权益曲线和交易记录。

```powershell
$env:PYTHONPATH='src'
python -m crypto_perp_tool.cli backtest run --csv data/sample_trades.csv --symbol BTCUSDT --equity 10000
```

可选参数：

```powershell
--entry-slippage 2.0    # 入场滑点 bps，默认 2
--exit-slippage 3.0     # 出场滑点 bps，默认 3
--fee 4.0               # Taker 手续费 bps，默认 4
--start 1700000000000   # 起始时间戳(ms)过滤
--end   1700100000000   # 结束时间戳(ms)过滤
--split 0.6             # 样本内比例（启用 Walk-Forward 分割）
```

### Walk-Forward 分割

指定 `--split 0.6` 时，引擎按时间顺序将数据分为前 60% 样本内（in-sample）和后 40% 样本外（out-of-sample），分别运行并比较：

```powershell
python -m crypto_perp_tool.cli backtest run --csv data/historical.csv --split 0.6
```

输出包含 `in_sample`、`out_of_sample` 和 `walk_forward_efficiency`（OOS profit_factor / IS profit_factor），用于评估参数过拟合程度。

### 输出字段

- `symbol`：交易标的
- `total_events`：处理的总成交事件数
- `equity_start` / `equity_end`：起止权益
- `total_return_pct`：总回报率
- `report`：`BacktestReport`，包含 `total_trades`、`win_rate`、`profit_factor`、`average_r`、`max_drawdown`、`max_consecutive_losses`、`by_setup` 分组统计等
- `trade_count`：完整交易记录数
- `config_version`：策略参数哈希（用于区分不同参数组合的结果）
- `data_quality`：数据精度（`aggTrade` 或 `aggTrade_no_quotes`）

### CSV 格式

必需列：`timestamp`、`price`、`quantity`
可选列：`symbol`（缺失时使用 `--symbol` 参数的值）、`is_buyer_maker`

## Telegram Bot

Telegram Bot 用于远程查看交易状态、管理启停和接收告警。Bot 只作为操作入口和通知通道，不承载交易核心逻辑。

### 环境变量配置

启动前需要在环境变量中设置以下值：

```powershell
$env:TELEGRAM_BOT_TOKEN='1234567890:ABCDEFghijklmnopqrstuvwxyz'
$env:TELEGRAM_ALLOWED_CHAT_IDS='123456789,987654321'
```

- `TELEGRAM_BOT_TOKEN`：从 [@BotFather](https://t.me/BotFather) 获取的 Bot Token。
- `TELEGRAM_ALLOWED_CHAT_IDS`：允许使用 Bot 的 Telegram 用户或群组 chat id，逗号分隔。只有白名单内的 chat id 才能执行命令，其他请求会被拒绝并写入 journal。

获取你的 chat id：在 Telegram 中搜索 `@userinfobot`，发送任意消息即可查看。

### 启动 Bot 长轮询

`TelegramPoller` 以 daemon 线程运行，在 Web Dashboard 启动时自动启动。如果在启动时环境变量中缺少 `TELEGRAM_BOT_TOKEN`，poller 会记录一条日志但不影响 Web 功能：

```powershell
$env:PYTHONPATH='src'
$env:TELEGRAM_BOT_TOKEN='你的bot token'
$env:TELEGRAM_ALLOWED_CHAT_IDS='你的chat id'
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --port 8000
```

也可以在代码中手动启动：

```python
from crypto_perp_tool.telegram_bot import TelegramPoller, parse_allowed_chat_ids
import os

poller = TelegramPoller(
    handler=handler,
    token=os.environ["TELEGRAM_BOT_TOKEN"],
    poll_interval=2.0,
)
poller.start()
# 停止：poller.stop()
```

### 支持的命令

| 命令 | 功能 | 示例输出 |
|------|------|---------|
| `/status` | 查看运行模式、交易所、交易标的和启停状态 | `mode=paper exchange=binance_futures symbols=BTCUSDT,ETHUSDT paused=false` |
| `/positions` | 查看当前持仓详情 | `BTCUSDT long lvn_break_acceptance\nEntry: 96000.00  Stop: 95800.00  Target: 96500.00\nQty: 0.01  BE shifted: False  Absorb: False` |
| `/pause` | 暂停新开仓信号（已有仓位的保护止损仍有效） | `new entries paused; protective exits remain active` |
| `/resume` | 恢复新开仓信号 | `paper trading entries resumed` |
| `/risk` | 查看风险参数配置 | `risk_per_trade=0.0025 daily_loss_limit=0.01 max_consecutive_losses=3 max_leverage=3 max_symbol_notional_equity_multiple=2.0` |
| `/circuit` | 查看熔断状态 | `Circuit: normal` 或 `Circuit: tripped\nReason: daily_loss_limit_reached\nCooldown until: 1700000000000` |
| `/journal` | 查看最近 3 条 journal 事件 | 最近事件的 JSON 字符串 |

### 未连接交易引擎时的行为

当 Bot 未关联 `LiveOrderflowStore` 时（例如仅通过 CLI 启动且未接入实时数据时），`/positions` 和 `/circuit` 返回：

```text
not connected to trading engine
```

`/status`、`/pause`、`/resume`、`/risk`、`/journal` 始终可用，因为它们直接访问 `TradingService`。

### 安全要求

- 必须配置 `TELEGRAM_ALLOWED_CHAT_IDS` 白名单。未授权 chat id 会被拒绝并写入 journal（事件类型：`telegram_command_rejected`）。
- 所有成功命令写入 journal（事件类型：`telegram_command`），包含 chat id、命令和结果。
- live mode 不能通过 Telegram 单独开启，必须同时满足 config `mode=live`、`LIVE_TRADING_CONFIRMATION` 环境变量和服务端二次确认。
- Telegram token 只能放在服务器环境变量或密钥管理中，不允许写入 repo 或日志。journal 写入前会通过 `redact()` 脱敏。

### 容错机制

- `TelegramPoller` 在网络错误或 Telegram API 返回失败时自动重试。
- 连续失败达到 10 次后 poller 自动停止，并写入 `telegram_poller_max_errors` journal 事件。
- 轮询间隔默认 2 秒，错误后指数退避到最大 30 秒。
- Telegram API HTTP 429（频率限制）时自动等待后重试。
