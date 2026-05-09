# 实施迭代 Issue 记录

## Iteration 1：形成可运行初版工具

### Issue A：只有类型骨架，没有用户可运行入口

解决：

- 新增 `crypto_perp_tool.cli`。
- 支持 `config show` 和 `paper run`。
- CLI 输出 JSON，方便后续 Telegram/Web/API 复用。

### Issue B：缺少事件日志，无法审计信号和风控

解决：

- 新增 `JsonlJournal`。
- 写入 signal、risk decision、paper order、Telegram command。
- 日志写入前统一脱敏。

### Issue C：Telegram 不能直接绑定交易核心

解决：

- 新增 `TradingService`。
- 新增 `TelegramCommandHandler`。
- Bot 只调用 service，不直接调用 execution adapter。

### Issue D：策略无法生成可解释 signal

解决：

- 新增 `SignalEngine`。
- 初版支持 LVN upward/downward acceptance。
- Signal 中包含 reasons 和 invalidation rules。

### Issue E：没有 paper replay，无法本地试跑

解决：

- 新增 `PaperRunner`。
- 从 CSV 读取成交事件。
- 计算 profile、生成 signal、执行 Risk Engine、写入 journal。

## Iteration 2：修掉初版显性问题

### Issue F：坏 CSV 报错不友好

解决：

- `_load_csv` 增加必需列校验。
- 缺少列时抛出 `ValueError: missing required columns`。

### Issue G：paper replay 的 VWAP 存在未来函数风险

解决：

- VWAP 改为只使用当前事件之前已经看到的数据。
- 避免 replay 中把未来成交数据泄漏给当前 signal。

### Issue H：缺少可直接运行的样例数据

解决：

- 新增 `data/sample_trades.csv`。
- `docs/usage.md` 提供可复制运行命令。

### Issue I：缺少环境变量模板

解决：

- 新增 `.env.example`。
- 只列变量名，不保存真实 token、API key 或密码。

### Issue J：Telegram pause/resume 没有测试保护

解决：

- 新增 Telegram command 单元测试。
- 验证 `/pause` 和 `/resume` 会改变 service 状态。

## Iteration 3：补齐可操作入口

### Issue K：CLI 不能查看 journal

解决：

- 新增 `crypto-tool journal tail`。
- 支持 `--path` 和 `--limit`。
- 输出 JSON，后续 Web/Telegram 可复用。

### Issue L：用户无法单独校验 Risk Engine

解决：

- 新增 `crypto-tool risk check --json`。
- 输入 signal + account JSON。
- 输出 `RiskDecision`。

### Issue M：手动运行生成的 journal 可能进入版本库

解决：

- `.gitignore` 忽略 `data/*.jsonl`。
- 样例 CSV 保留在 `data/sample_trades.csv`。

## Iteration 4：补齐 paper execution 的交易闭环

### Issue N：paper runner 只记录订单，不记录成交和平仓

解决：

- 新增 `PaperPosition`。
- 通过 Risk Engine 后记录 `paper_fill`。
- 后续价格触及 stop/target 时记录 `position_closed`。
- `PaperRunResult` 增加 `closed_positions` 和 `realized_pnl`。

### Issue O：样例数据不能展示完整闭环

解决：

- 更新 `data/sample_trades.csv`，加入触发目标价的后续成交。
- `docs/usage.md` 说明 `closed_positions` 与 `realized_pnl`。

### Issue P：src layout 缺少打包配置

解决：

- `pyproject.toml` 增加 setuptools build backend。
- 配置 `tool.setuptools.packages.find` 指向 `src`。
- 增加 `crypto-tool` console script。

## Iteration 5：订单流 Web 可视化基础

### Issue Q：无法通过界面观察盘面

解决：

- 新增 `web/orderflow.py` 生成订单流 view model。
- 新增 `/api/orderflow`，输出 summary、trades、delta series、profile levels、markers。
- 新增静态 Web Dashboard，使用 canvas 绘制价格路径和累计 Delta。

### Issue R：Web 需要为后续实时化留接口

解决：

- Web 当前读取 CSV replay，但数据结构按实时 market snapshot 思路组织。
- 后续可把 `/api/orderflow` 数据源替换为实时 Market Data，不改前端主要渲染逻辑。

### Issue S：Web 启动入口缺失

解决：

- CLI 增加 `web serve`。
- 默认读取 `data/sample_trades.csv`。
- 默认监听 `127.0.0.1:8000`。

### Issue T：打包后可能丢失静态资源

解决：

- `pyproject.toml` 增加 `tool.setuptools.package-data`。
- 明确打包 `web/static/*.html`、`*.css`、`*.js`。

## Iteration 6：接入 Binance 实时 Market Data

### Issue U：Web 只能读取 CSV replay

解决：

- 新增 `BinanceStreamConfig`、`BinanceAggTradeParser`、`BinanceAggTradeClient`。
- aggTrade stream 使用 Binance USDⓈ-M Futures routed market endpoint：`wss://fstream.binance.com/market/ws/<symbol>@aggTrade`。
- `m=true` 解析为主动卖出成交，Delta 为负。

### Issue V：实时数据需要和 CSV 共用前端结构

解决：

- 新增 `LiveOrderflowStore`。
- Binance live 和 CSV replay 都输出 `/api/orderflow` view model。
- 前端无需知道数据来自 CSV 还是 Binance。

### Issue W：实时连接失败时界面会像空数据

解决：

- Binance client 增加 status callback。
- `LiveOrderflowStore` 暴露 `connection_status` 和 `connection_message`。
- Dashboard 增加 Connection 指标。

### Issue X：未使用 routed market endpoint 会连接但不推送 aggTrade

解决：

- 根据 Binance 官方 Websocket Market Streams 文档，常规 market data 使用 `/market` routed path。
- 将默认 base URL 从 `wss://fstream.binance.com/ws` 改为 `wss://fstream.binance.com/market/ws`。
- 单元测试固定 URL，防止退回 unrouted endpoint。

## Iteration 7：手机访问 Web Dashboard

### Issue Y：默认只绑定 127.0.0.1，手机无法打开

解决：

- CLI 增加 `--mobile`。
- `--mobile` 会绑定 `0.0.0.0`，让同一局域网设备可以访问。
- 默认仍绑定 `127.0.0.1`，避免无意暴露服务。

### Issue Z：用户不知道手机应该打开哪个地址

解决：

- 新增 LAN IP 探测。
- 启动时打印 `Local` 和 `Phone/LAN` URL。
- 文档补充 Windows 防火墙和同一 Wi-Fi 要求。

## Iteration 8：Live Paper Auto Trading Loop

### Issue AA：Live Web 只有行情观察，没有自动 paper 交易闭环

解决：

- 新增 `PaperTradingEngine`，实时消费 Binance futures `aggTrade`。
- 每笔成交都会更新 profile、delta、VWAP，并调用 `SignalEngine` 和 `RiskEngine`。
- 风控通过后立即创建 paper order 和 open position。
- 后续成交触发 stop/target 时自动记录 closed position 和 realized PnL。

### Issue AB：Web 顶部 paper 指标仍是占位值

解决：

- `LiveOrderflowStore` 为每个 symbol 持有独立 paper engine。
- `/api/orderflow` 的 Signals、Orders、Closed、Paper PnL、details 和 markers 来自实时 paper engine。
- Price And Execution 图表显示 signal marker 和 position close marker。

### Issue AC：Recent Tape 时间戳不可读，订单明细缺少止损止盈

解决：

- Recent Tape 的毫秒时间戳格式化为可读时间。
- Orders 明细表新增 Stop / 止损 与 Target / 止盈列。
- Orders 卡片在有 open paper position 时显示当前入场、止损、止盈。

## Iteration 9：收敛实时 paper 交易基础问题

### Issue AD：profile 和 Delta 使用交易笔数窗口，不能代表真实时间窗口

解决：

- `PaperTradingEngine` 改为按毫秒时间窗口计算 rolling profile、VWAP、15s/30s/60s Delta。
- `LiveOrderflowStore` 的 Volume Profile 使用配置里的 rolling minutes，而不是全部已缓存成交。
- summary 暴露 `seen_trade_count` 和 `profile_trade_count`，便于检查窗口是否被异常压缩。

### Issue AE：实时数据延迟风控被事件时间绕过

解决：

- `process_trade()` 新增 `received_at`，实盘默认使用本机接收时间。
- `MarketSnapshot.local_time` 使用接收时间，`event_time` 使用交易所事件时间。
- `data_lag_ms` 进入 summary，延迟超限时信号会被拒绝。

### Issue AF：Zeabur/Web live paper 交易没有落盘和恢复

解决：

- `PaperTradingEngine` 支持 `journal_path`，写入 signal、risk decision、paper fill、paper order 和 position close。
- `LiveOrderflowStore`、`serve_dashboard`、CLI 与 Dockerfile 接入 `--paper-journal` / `PAPER_JOURNAL`。
- BTCUSDT 与 ETHUSDT journal 自动拆分为独立 jsonl 文件，避免多 symbol 混写。
- 服务重启时从 journal 恢复 paper orders、open position、closed position、realized PnL 和 markers。
