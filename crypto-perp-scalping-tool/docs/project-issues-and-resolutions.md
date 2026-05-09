# 项目计划补充 Issue 与解决方案

本文记录当前项目计划中需要补强的关键问题，并给出已经采纳的解决方案。后续实现必须把这些解决方案当作工程约束，而不是可选建议。

## Issue 1：交易核心和用户交互耦合风险

问题：

如果 Telegram Bot 直接承载策略判断、风控或下单逻辑，后续迁移 Web Dashboard、CLI 或 API 时会重复实现交易逻辑，也会增加误操作风险。

解决方案：

- Telegram Bot 只作为操作入口和告警通道。
- 核心交易逻辑固定放在后端模块：`market_data`、`profile`、`signals`、`risk`、`execution`、`journal`。
- Bot 命令只能调用后端 service 层，不直接访问交易所下单 API。
- 所有 Bot 操作必须写入 journal。
- Web Dashboard 后期复用同一 service 层，不另写一套交易逻辑。

落地任务：

- 新增 `interfaces/telegram` 或 `bot/telegram` 模块。
- 新增 `service` 层封装状态查询、暂停、恢复、信号查询。
- Bot 命令禁止直接 import `execution` 交易所适配器。

## Issue 2：实盘开关过于危险

问题：

如果只靠配置里的 `mode=live` 控制实盘，很容易因为环境变量、部署配置或误操作导致提前进入实盘。

解决方案：

- 默认永远是 `paper`。
- 开启 live mode 必须同时满足三个条件：
  - 配置文件或环境变量显式设置 `mode=live`。
  - 服务器环境变量设置 `LIVE_TRADING_CONFIRMATION`。
  - Telegram Bot 或 CLI 执行二次确认命令，且确认人属于白名单。
- live mode 下启动时必须打印和推送风险确认消息。
- 若任一确认条件缺失，系统降级到 paper mode。

落地任务：

- 在 config loader 中加入 live mode guard。
- 在 Telegram Bot 加 `/live_confirm`，但不允许单独开启 live。
- 在 journal 中记录 live guard 检查结果。

## Issue 3：交易所状态与本地状态不一致

问题：

自动交易最危险的情况之一是本地以为没有仓位，但交易所已有仓位；或本地以为止损已挂好，但交易所并没有保护单。

解决方案：

- Execution Engine 必须维护订单状态机。
- 每 2 秒通过 REST 对账一次：
  - 持仓数量。
  - 未成交入场单。
  - reduce-only 止损单。
  - reduce-only 止盈单。
- 对账失败或状态不一致时：
  - 暂停新开仓。
  - 以交易所真实状态重建本地状态。
  - 无法确认保护单存在时，尝试市价 reduce-only 平仓。

落地任务：

- 新增 `PositionReconciler`。
- 新增 `OrderStateStore`。
- 新增仿真测试：部分成交、撤单失败、止损单丢失、本地状态滞后。

## Issue 4：数据质量不足会制造假信号

问题：

Volume profile、Delta、VWAP 都依赖高质量成交数据。WebSocket 断线、延迟、重复事件、乱序事件会导致错误信号。

解决方案：

- Market Data 层必须计算数据新鲜度和事件延迟。
- 所有 market event 必须带 `exchange_event_time`、`local_received_time`、`sequence_id` 或可替代排序字段。
- 数据落后超过阈值时，Signal Engine 必须输出 flat，Risk Engine 禁止开仓。
- WebSocket 重连后必须通过 REST 或 replay 补齐关键状态。
- Journal 保留原始事件，便于排查信号来源。

落地任务：

- 新增 `MarketDataHealth` 类型。
- 在 `MarketSnapshot` 增加数据健康状态。
- 新增测试：延迟超过 1500ms 时拒绝交易。

## Issue 5：回测和实盘存在成交偏差

问题：

仅用 1m K 线回测会严重高估订单流策略，因为无法准确模拟盘口、排队、滑点、部分成交和快速反向波动。

解决方案：

- 回测分两层：
  - 粗回测：1m K 线 + 聚合成交，只验证方向过滤器。
  - 精回放：aggTrade/tick + L1/L2 盘口，验证执行可成交性。
- 所有回测报告必须标记数据精度。
- 不允许用粗回测结果直接决定实盘。
- Paper trading 报告必须和回测报告分开展示。

落地任务：

- Backtest report 增加 `data_quality` 字段。
- Replay 工具支持 tick/aggTrade 输入。
- 验收标准加入：粗回测通过不等于可实盘。

## Issue 6：Zeabur 适合早期，但不适合长期承载实盘核心

问题：

PaaS 部署方便，但自动交易核心需要长期 WebSocket 稳定、固定 IP、可控重启、明确资源隔离和独立监控。若全部依赖 Zeabur，后期实盘风险偏高。

解决方案：

- Zeabur 用于早期：
  - Telegram Bot。
  - Paper trading worker。
  - PostgreSQL / Redis。
  - Web Dashboard。
- 实盘核心后期迁移到 VPS/云服务器：
  - 新加坡、东京或香港区域。
  - Docker Compose + systemd。
  - 固定 IP。
  - 交易所 API key IP 白名单。
  - 独立监控和 Telegram 告警。
- Web Dashboard 可以继续放 Zeabur，交易核心和执行服务放 VPS。

落地任务：

- 增加 deployment profile：`local`、`zeabur-paper`、`vps-live`。
- 为 live worker 单独准备 Docker Compose。
- 文档中明确 Zeabur 不作为 live core 的最终建议。

## Issue 7：密钥和权限管理不足

问题：

交易所 API key、Telegram token、数据库密码如果写入代码、配置文件或日志，会直接造成资金和系统安全风险。

解决方案：

- repo 内只保存 `.env.example`，不保存真实 `.env`。
- API key 只允许合约交易权限，不允许提现权限。
- 实盘 API key 必须配置 IP 白名单。
- 日志和 Telegram 消息必须脱敏：
  - 不输出完整 API key。
  - 不输出完整 token。
  - 不输出数据库连接密码。
- 本地、Zeabur、VPS 分别使用各自的环境变量。

落地任务：

- 新增 `.env.example`。
- 新增 secret redaction 工具。
- Journal 写入前统一调用脱敏函数。

## Issue 8：缺少人工接管与熔断后的恢复流程

问题：

触发熔断后，如果没有明确恢复流程，系统可能长期停摆，或者在没有排查原因的情况下恢复交易。

解决方案：

- 熔断状态必须有原因码：
  - `daily_loss_limit_reached`
  - `max_consecutive_losses_reached`
  - `websocket_stale`
  - `order_protection_missing`
  - `position_mismatch`
  - `exchange_api_failure`
- 熔断后只允许人工恢复。
- 恢复前必须完成检查：
  - 当前无未保护仓位。
  - 数据连接健康。
  - 交易所状态与本地状态一致。
  - 当日亏损未超过硬限制。
- 恢复命令必须写入 journal。

落地任务：

- 新增 `CircuitBreakerState`。
- Telegram Bot 增加 `/circuit` 和 `/resume`。
- 新增仿真测试：熔断后未满足恢复条件时不能恢复。

## Issue 9：缺少可观测性和审计指标

问题：

如果只记录最终订单和 PnL，后续无法解释为什么下单、为什么拒绝、为什么熔断，也无法定位策略问题。

解决方案：

- 所有关键事件写入 append-only journal：
  - market health。
  - profile levels。
  - signal。
  - risk decision。
  - order request/response。
  - fill。
  - position reconciliation。
  - circuit breaker。
  - Telegram command。
- 每个 signal 必须带 `reasons` 和 `invalidation_rules`。
- 每个 reject 必须带 `reject_reasons`。
- 指标按 setup、symbol、session、数据质量分组统计。

落地任务：

- 定义 journal event schema。
- 新增 replay 命令读取 journal。
- Telegram `/journal` 只读取脱敏后的 journal view。

## Issue 10：策略参数可能过拟合

问题：

LVN/HVN 阈值、Delta 窗口、止损距离和 session 规则如果只根据少量行情调参，容易过拟合，实盘表现会显著下降。

解决方案：

- 参数分为三层：
  - hard safety：风控硬限制，不参与优化。
  - strategy defaults：默认策略参数。
  - experiment overrides：实验参数。
- 每次实验必须记录参数版本。
- 回测必须按时间段、symbol、行情类型拆分。
- 禁止用同一段数据同时调参和验收。

落地任务：

- 增加 config version。
- Backtest report 保存参数快照。
- Journal 中记录运行参数 hash。

## 已采纳的优先级

第一批优先解决：

1. Issue 1：交互层和交易核心解耦。
2. Issue 2：live mode 三重确认。
3. Issue 3：交易所状态对账。
4. Issue 4：数据健康状态。
5. Issue 7：密钥和日志脱敏。

第二批解决：

1. Issue 5：回测成交偏差。
2. Issue 8：熔断恢复流程。
3. Issue 9：可观测性和审计。

第三批解决：

1. Issue 6：live core 迁移 VPS。
2. Issue 10：参数版本与过拟合控制。
