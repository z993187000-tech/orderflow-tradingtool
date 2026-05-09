# 计划实施框架

## 目标

先搭建可扩展的工程骨架，让后续实现可以按模块推进，而不是把行情、信号、风控和执行混在一个脚本里。

## 第一阶段范围

- 建立 Python package 结构。
- 固化核心数据类型：`MarketSnapshot`、`ProfileLevel`、`TradeSignal`、`RiskDecision`、`ExecutionOrder`。
- 建立模块边界：market data、profile、signals、risk、execution、journal、replay、backtest。
- 先实现可测试的基础逻辑：仓位风险计算、禁止交易条件、基础成交量分布 level 识别。
- 暂不接入真实 Binance API，不执行真实下单。

## 模块边界

- `market_data`：交易所数据适配器与事件格式。
- `profile`：VWAP、POC、HVN、LVN、VAH、VAL。
- `signals`：把市场快照转换为可解释的交易信号。
- `risk`：账户级风险、仓位计算、熔断和否决。
- `execution`：订单状态机和 paper/live 执行适配器。
- `journal`：append-only 事件日志。
- `replay`：按历史事件重放策略判断。
- `backtest`：历史数据评估和指标输出。

## Market Data 接入方案

第一版订单流数据从 Binance USDT-M Futures 公共行情接口获取，交易执行仍保持 `mode=paper`，不在 Market Data 阶段接入真实下单。

启动时先通过 REST `/fapi/v1/exchangeInfo` 校验配置里的交易标的：

- 只启用 `contractType=PERPETUAL` 且 `status=TRADING` 的 symbol。
- 默认目标为 `BTCUSDT` 和 `ETHUSDT`。
- 如果 `ETHUSDT` 校验失败或对应行情流持续不可用，先自动从本次运行的 active symbols 中移除 ETH，只保留 BTC，并把原因写入 journal。
- 如果 `BTCUSDT` 不可用，停止启动 paper trading worker，因为 BTC 是 MVP 的主验证标的。

每个启用 symbol 订阅以下 Binance Futures WebSocket 行情流：

- `aggTrade`：聚合成交，用于主动买卖 Delta、滚动成交量、成交量突增和短线动能。
- `bookTicker`：最优买卖价和数量，用于 bid/ask、spread、短线流动性和数据新鲜度判断。
- `depth@100ms`：作为可选增强，用于后续盘口不平衡、L2 回放和更精细的可成交性验证；MVP 可先用 `bookTicker`。
- `kline_1m`：用于结构高低点、ATR、VWAP 辅助和粗粒度回放兼容。
- `markPrice@1s`：用于标记价格、资金费率和资金费率结算前后禁开仓窗口。

订单流派生规则：

- Binance `aggTrade` 中 `m=true` 表示 buyer 是 maker，即卖方主动成交，Delta 记为负。
- `m=false` 表示买方主动成交，Delta 记为正。
- Market Data 层维护 15s、30s、60s 三个滚动窗口的 buy volume、sell volume、delta 和 total volume。
- 每个输出事件必须带 `exchange_event_time`、`local_received_time`、`symbol`、`stream_type` 和可用于去重或排序的交易所事件字段。
- Market Data 层只输出统一事件和 `MarketSnapshot` 所需字段，不直接生成交易信号。

数据健康规则：

- 记录 WebSocket 延迟、最近事件时间、重连次数和每个 symbol 的 active/inactive 状态。
- 如果单个 symbol 行情落后超过 `execution.max_data_lag_ms`，该 symbol 暂停新信号。
- 如果 WebSocket stale 超过 `execution.websocket_stale_ms`，Risk Engine 必须拒绝新开仓。
- 断线重连后优先恢复公共行情流；必要时通过 REST 补齐最新 ticker、mark price 和交易所状态。
- 所有断线、跳过 symbol、恢复订阅、数据落后事件都写入 journal，便于 replay 和复盘。

## 推荐推进顺序

1. 完成类型和配置。
2. 完成 profile engine 单元测试。
3. 完成 risk engine 单元测试。
4. 完成 signal engine 的四个 setup。
5. 完成 paper execution。
6. 完成 journal/replay。
7. 接 Binance 公共行情数据：`exchangeInfo` 校验、`aggTrade`、`bookTicker`、`kline_1m`、`markPrice@1s`，先 paper-only。
8. 根据 paper 行情数据接入结果决定是否保留 `ETHUSDT`；如果 ETH 数据质量不稳定，先从 active symbols 中移除。
9. 接 Binance testnet 下单。
10. 连续 paper trading 验收后再考虑 live mode。

补充规划问题和解决方案见 [project-issues-and-resolutions.md](project-issues-and-resolutions.md)。第一批优先解决交互解耦、live mode 三重确认、交易所状态对账、数据健康状态、密钥与日志脱敏。

## 当前安全默认

- 默认 `mode=paper`。
- 默认交易标的为 `BTCUSDT` 和 `ETHUSDT`。
- live mode 需要显式配置开启。
- Risk Engine 是唯一有权批准开仓的模块。
- Execution Engine 必须保证入场成交后存在 reduce-only 止损。

## 用户交互路线

短期先使用 Telegram Bot，后期再补 Web Dashboard。

Telegram Bot 定位为控制台和通知中心，不承载核心交易逻辑。核心策略、风控、执行、日志仍然放在后端服务中，Bot 只调用后端接口或命令层。

第一阶段 Bot 功能：

- `/status`：查看运行模式、连接状态、账户权益、今日 PnL、熔断状态。
- `/positions`：查看当前持仓、止损、止盈、未成交订单。
- `/signals`：查看最新信号、触发 setup、入场价、止损、目标位、拒绝原因。
- `/pause`：暂停新开仓，但保留已有仓位保护逻辑。
- `/resume`：恢复 paper trading 新信号。
- `/risk`：查看单笔风险、日亏损限制、连续亏损、最大杠杆。
- `/journal`：查看最近交易事件和异常事件。

安全要求：

- 必须配置 `TELEGRAM_ALLOWED_CHAT_IDS` 白名单。
- live mode 不能通过单条 Telegram 命令直接开启，必须要求二次确认和服务端配置共同满足。
- Telegram token、交易所 API key、数据库密码只能放在服务器环境变量或密钥管理中。
- 所有 Telegram 操作都必须写入 journal，包含 chat id、命令、时间、结果。

Web Dashboard 放到后期实现，用于展示成交量分布、订单流、信号解释、交易日志、回测报告和复盘图表。Web 只做观察和管理，不应绕过 Risk Engine 直接下单。

## 部署路线

短期可以使用 Zeabur 部署 Telegram Bot、paper trading worker、PostgreSQL/Redis 和后续 Web Dashboard。Zeabur 的优势是部署快、环境变量管理方便、适合从 GitHub 自动部署，适合早期 paper trading 和产品原型。

Zeabur 适合承载：

- Telegram Bot service。
- Paper trading worker。
- Journal API 或轻量后端。
- PostgreSQL / Redis。
- 后期 Web Dashboard。

Zeabur 不建议作为第一阶段 live 自动交易核心的最终形态。实盘核心服务需要更强的可控性，包括长期 WebSocket 稳定性、固定 IP、明确资源限制、可控重启策略、独立监控和更接近交易所的区域。

推荐部署阶段：

1. 本地开发和单元测试。
2. Zeabur 部署 Telegram Bot + paper trading worker。
3. Zeabur 连续运行 1-2 周 paper trading，观察断线、延迟、日志、资源占用。
4. 准备实盘时迁移交易核心到新加坡、东京或香港的 VPS/云服务器。
5. Web Dashboard 可以继续放在 Zeabur，交易核心放在 VPS。

实盘服务器建议：

- Docker Compose + systemd。
- PostgreSQL + Redis。
- 固定 IP，并在交易所 API key 配置 IP 白名单。
- 独立监控和 Telegram 告警。
- API key 只开放合约交易权限，不开放提现权限。
