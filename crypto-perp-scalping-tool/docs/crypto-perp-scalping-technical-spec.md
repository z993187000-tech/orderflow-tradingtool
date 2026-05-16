# 加密永续剥头皮全自动交易工具技术规范

## 1. 文档目标与来源

本文把视频中的交易框架整理为可开发规格，用于构建一个面向 BTCUSDT / ETHUSDT 永续合约的全自动交易工具。

来源基准：

- 原视频：[Trading LIVE with the #1 Scalper in the WORLD](https://youtu.be/tvERE-Beu2U?si=l3aaWfNL4Fmh1ulQ)
- 辅助转写/摘要：[VideoHighlight: tvERE-Beu2U](https://videohighlight.com/v/tvERE-Beu2U?aiFormatted=false&language=en&mediaType=youtube&summaryId=9xwvtE5-RLK4EtsZiIET&summaryType=default)

视频语境偏 NQ / 指数期货实盘剥头皮，核心不是固定指标组合，而是 Auction Market Theory、成交量分布、订单流、位置和风险控制的组合判断。本文将其改写为加密永续合约可执行版本。

注意：本文是技术复刻规格，不构成投资建议。任何实现必须先经过回测、仿真和小资金验证。

## 2. 策略核心

### 2.1 市场假设

工具把市场理解为一场拍卖：

- 价格在高成交量区域停留，说明市场接受该价格区间。
- 价格在低成交量区域快速穿越，说明该区间流动性薄、接受度低。
- POC / HVN 是价格吸附、止盈或震荡中心。
- LVN 是突破、假突破、快速回补和止损触发的重点区域。
- 单独看到价位不交易，必须同时确认上下文、订单流和风险回报。

### 2.2 从期货转译到加密永续

加密市场与指数期货不同：

- 加密永续 24/7 交易，没有传统日盘收盘。
- 流动性在 UTC 时段、资金费率结算前后、宏观数据发布时显著变化。
- 盘口深度、成交聚合、爆仓流和资金费率会影响短线价格。
- 不同交易所撮合、手续费、滑点和 API 限频差异很大。

第一版固定选择 Binance USDT-M Futures testnet / live API。Bybit 作为后续适配器，不进入 MVP。

默认交易标的：

- `BTCUSDT`
- `ETHUSDT`

默认周期：

- 信号计算：1s 到 5s event loop
- K 线聚合：1m
- 成交量分布：session profile + rolling profile
- 订单流确认：最近 15s / 30s / 60s 三个窗口

## 3. 数据要求

### 3.1 必需实时数据

从 Binance Futures WebSocket 订阅：

- `aggTrade`：聚合成交，用于主动买卖 Delta、成交量突增、短线动能。
- `depth@100ms` 或 `bookTicker`：盘口价差、最优买卖、短线流动性。
- `kline_1m`：结构高低点、VWAP 辅助、回测兼容。
- `markPrice`：资金费率、标记价格、强平风险参考。

### 3.2 派生数据

实时维护：

- Last price
- Bid / ask / spread
- Rolling traded volume
- Buy volume / sell volume
- Delta = aggressive buy volume - aggressive sell volume
- Cumulative Delta
- Aggression Bubble：单笔 `aggTrade` 数量 >= 10 BTC 记为 `large`，>= 50 BTC 记为 `block`；方向由 `isBuyerMaker` 推导。
- VWAP
- ATR_1m_14 / ATR_3m_14：由真实 1m / 3m 成交聚合 K 线计算，用于动态止损和吸收判断。
- Session high / low
- Asia / London / New York 时段高低点
- Session volume profile
- Rolling volume profile
- POC / HVN / LVN / VAH / VAL

Binance `aggTrade` 中 `m=true` 表示 buyer 是 maker，即卖方主动成交，Delta 记为负；`m=false` 表示买方主动成交，Delta 记为正。

实现采用 Binance USDⓈ-M Futures 官方 routed market endpoint：`wss://fstream.binance.com/market/ws/<symbol>@aggTrade`。

## 4. 成交量分布规则

### 4.1 Profile 窗口

MVP 维护多套 profile：

- Session profile：按 UTC 日期重置，覆盖当前自然日。
- Execution profile：最近 30 分钟滚动窗口，用于 VAH/VAL/LVN/HVN 主触发。
- Micro profile：最近 15 分钟滚动窗口，用于开仓前确认。
- Context profile：最近 60 分钟滚动窗口，用于目标位/障碍位参考，不作为方向 veto。

价格分箱：

- BTCUSDT：默认 bin size = 20 USDT。
- ETHUSDT：默认 bin size = 5 USDT。
- 后续可按 ATR 或价格百分比动态调整，但 MVP 使用固定分箱。

### 4.2 POC / Value Area

POC：

- 成交量最大的价格分箱。

Value Area：

- 从 POC 开始向上下扩展，直到覆盖总成交量的 70%。
- 上边界为 VAH，下边界为 VAL。

### 4.3 HVN / LVN

HVN：

- 局部成交量峰值。
- 当前 bin 成交量高于左右相邻 2 个 bin。
- 当前 bin 成交量高于窗口平均 bin 成交量的 1.25 倍。

LVN：

- 局部成交量谷值。
- 当前 bin 成交量低于左右相邻 2 个 bin。
- 当前 bin 成交量低于窗口平均 bin 成交量的 0.55 倍。
- 若 LVN 位于两个 HVN 之间，权重提高。

Level strength：

- `volume_ratio`：当前 bin 成交量 / 平均 bin 成交量。
- `touch_count`：价格触碰该 level 后反应次数。
- `freshness`：最近一次被触碰距当前的时间。
- `confluence`：是否与 VWAP、前高前低、时段高低点重合。

## 5. 交易信号

所有信号必须满足四类条件：

1. 位置：价格靠近 POC / HVN / LVN / VAH / VAL / VWAP / 时段高低点之一。
2. 上下文：当前市场是趋势、区间、突破后接受，还是假突破回收。
3. 订单流确认：Delta、成交量、盘口价差与预期方向一致。
4. 风险回报：止损和目标位明确，最小 R:R 不低于 1.2。

### 5.1 Long Setup A：LVN 突破接受

用途：交易低成交量区被有效穿越后的快速延续。

触发条件：

- 价格从下向上突破 LVN。
- 突破后 15s 内没有快速跌回 LVN 下方。
- 最近 30s Delta 为正，且大于过去 20 个 30s 窗口 Delta 均值的 1.2 倍。
- 最近 30s 成交量高于过去 20 个同窗口均值的 1.5 倍。
- 盘口 spread 不高于最近 5 分钟中位数的 1.5 倍。

入场：

- 优先使用 post-only limit buy 挂在突破后回踩价。
- 若价格快速远离，最多追价一次，追价滑点不得超过配置上限。

止损：

- LVN 下方一个结构低点。
- 或入场价下方 `max(0.35 * ATR_1m_14, 0.15%)`。
- 两者取更保守的一侧。

止盈：

- 第一目标：下一个 HVN / POC。
- 第二目标：VAH 或前高。
- MVP 使用一次性全平；后续可做分批止盈。

失效：

- 价格重新接受 LVN 下方。
- Delta 转负且超过入场窗口 Delta 的 60%。
- 订单超过 10s 未成交。

### 5.2 Short Setup A：LVN 跌破接受

与 Long Setup A 对称：

- 价格从上向下跌破 LVN。
- 15s 内没有快速收回 LVN 上方。
- 最近 30s Delta 为负，绝对值大于均值 1.2 倍。
- 成交量突增，spread 正常。
- 目标为下一个 HVN / POC / VAL / 前低。

### 5.3 Long Setup B：HVN / VAL 假跌破回收

用途：交易关键接受区边缘的失败突破。

触发条件：

- 价格短暂跌破 VAL、前低或 HVN 下边缘。
- 跌破后 60s 内回到 level 上方。
- 下破期间 Delta 明显为负，但价格不再创新低，形成吸收。
- 回收时出现正 Delta 翻转。
- 回收后的第一根 1m K 线收在 level 上方。

入场：

- 回收确认后 limit buy。

止损：

- 假跌破低点下方。

止盈：

- VWAP、POC 或区间上沿。

失效：

- 回收后再次跌破并在 level 下方停留超过 30s。

### 5.4 Short Setup B：HVN / VAH 假突破回收

与 Long Setup B 对称：

- 价格短暂突破 VAH、前高或 HVN 上边缘。
- 60s 内跌回 level 下方。
- 上冲 Delta 很强但价格不再延续，随后负 Delta 翻转。
- 目标为 VWAP、POC 或区间下沿。

### 5.5 Trend Squeeze：VAH/VAL 突破 -> LVN 回踩 -> 攻击性气泡

用途：趋势时段不追突破第一脚，等待价格回踩到突破方向上的 LVN，再用大单主动成交确认延续。

Long 触发条件：

- 最近 90s 内价格突破 VAH 上沿。
- 当前价格回踩到 VAH 上方的 LVN 区间内。
- 当前成交出现买方 Aggression Bubble：单笔 >= 10 BTC，>= 50 BTC 记为 block 级别。
- 最近 30s Delta 为正。
- 目标为上方最近 POC / HVN / VAH / VAL。
- 止损放在大单价格后方，并叠加 ATR_1m_14 / ATR_3m_14 缓冲。

Short 与 Long 对称：最近 90s 跌破 VAL，下方 LVN 回踩，出现卖方 Aggression Bubble，目标为下方最近高价值节点。

### 5.6 CVD 背离假突破

用途：区间或低波动时段识别“价格刺破但订单流没有跟随”的失败拍卖。

Bearish 条件：

- 价格短暂突破 VAH 上沿后重新回到价值区内部。
- 突破阶段价格创新高，但 CVD 没有同步创新高。
- 回收时最近 30s Delta 转负。
- 目标优先看 POC，其次看下方最近 HVN / VAL。

Bullish 条件与 Bearish 对称：价格跌破 VAL 后回到价值区内部，价格创新低但 CVD 没有同步创新低，回收时 Delta 转正，目标优先看 POC。

### 5.7 禁止交易条件

任一条件满足时禁止开新仓：

- 当前 spread 高于最近 5 分钟中位数 2 倍。
- WebSocket 延迟超过 1500ms。
- 本地数据落后交易所事件时间超过 2s。
- 资金费率结算前后 2 分钟。
- 单根 1m K 线振幅超过过去 20 根均值 3 倍。
- 策略已触发日亏损、连续亏损或异常熔断。
- 已有同 symbol 持仓或未完成退出订单。

## 6. 风险控制

### 6.1 账户级限制

默认参数：

- 单笔最大风险：账户权益 0.25%。
- 单日最大亏损：账户权益 1.0%。
- 最大连续亏损：3 笔。
- 单 symbol 最大名义仓位：账户权益 2 倍。
- 最大杠杆：3x。
- 最大允许滑点：BTC 0.03%，ETH 0.04%。

所有风险参数必须由 Risk Engine 最终裁决，Signal Engine 只能提出建议。

### 6.1.1 动态保护

- 动态止损：新趋势挤压信号使用真实 1m / 3m ATR 作为缓冲，止损放在攻击性大单价格后方，而不是固定百分比。
- Break-even shift：开仓后价格向有利方向运行超过 1.5R，自动把止损移动到开仓均价。
- Absorption protection：若持仓方向 CVD/Delta 激增，但价格位移不超过 ATR，判定为主动单撞到被动流动性，paper engine 执行强制减仓并记录 `absorption_reduce`。

### 6.2 仓位计算

```text
risk_amount = account_equity * risk_per_trade
stop_distance = abs(entry_price - stop_price)
quantity = risk_amount / stop_distance
notional = quantity * entry_price
quantity = min(quantity, max_symbol_notional / entry_price)
```

若计算后的 quantity 低于交易所最小下单量，拒绝交易。

### 6.3 熔断

立即停止开新仓并尝试保护已有仓位：

- 当日已实现 PnL <= `-daily_loss_limit`。
- 连续亏损 >= 3。
- 交易所 API 连续 3 次下单失败。
- 止损单提交失败。
- 本地状态与交易所仓位不一致。
- WebSocket 断线超过 5s。

如果已有仓位：

- 优先确认 reduce-only 止损单是否存在。
- 若不存在，立即用市价 reduce-only 平仓。
- 若 API 不可用，记录最高级别告警，暂停全部策略。

## 7. 系统架构

### 7.1 模块

Market Data：

- 负责交易所 WebSocket 和 REST 数据。
- 输出统一的事件流。
- 维护重连、心跳、延迟测量。

Profile Engine：

- 消费成交事件。
- 维护 session / rolling volume profile。
- 输出 POC、HVN、LVN、VAH、VAL、VWAP。

Signal Engine：

- 消费 `MarketSnapshot`。
- 根据 setup 规则输出 `TradeSignal`。
- 每个信号必须包含触发原因和失效条件。

Risk Engine：

- 消费账户状态、持仓状态和 `TradeSignal`。
- 输出 `RiskDecision`。
- 拥有最终开仓否决权。

Execution Engine：

- 把允许交易转换为交易所订单。
- 管理入场单、止损单、止盈单、撤单和重试。
- 所有退出订单必须 reduce-only。

Journal & Replay：

- 保存 market snapshot、signal、risk decision、order、fill、PnL。
- 支持按时间重放策略判断。

### 7.2 数据流

```text
Exchange WebSocket / REST
  -> Market Data
  -> Profile Engine
  -> MarketSnapshot
  -> Signal Engine
  -> Risk Engine
  -> Execution Engine
  -> Exchange
  -> Journal & Replay
```

异常流：

```text
Exchange / Network / State Error
  -> Risk Engine
  -> Circuit Breaker
  -> Execution Engine protective close
  -> Journal alert
```

## 8. Public Interfaces / Types

以下接口是语言无关的逻辑契约。实现时可用 TypeScript、Python dataclass、Pydantic 或 Rust struct。

### 8.1 MarketSnapshot

```ts
type MarketSnapshot = {
  exchange: "binance_futures";
  symbol: "BTCUSDT" | "ETHUSDT";
  eventTime: number;
  localTime: number;
  lastPrice: number;
  bidPrice: number;
  askPrice: number;
  spreadBps: number;
  vwap: number;
  atr1m14: number;
  atr3m14: number;
  delta15s: number;
  delta30s: number;
  delta60s: number;
  cumulativeDelta: number;
  aggressionBubble?: {
    side: "buy" | "sell";
    quantity: number;
    tier: "large" | "block";
    price: number;
  };
  volume30s: number;
  session: TradingSessionState;
  profileLevels: ProfileLevel[];
  position?: PositionState;
};
```

### 8.2 ProfileLevel

```ts
type ProfileLevel = {
  type: "POC" | "HVN" | "LVN" | "VAH" | "VAL";
  price: number;
  lowerBound: number;
  upperBound: number;
  strength: number;
  window: "session" | "execution_30m" | "micro_15m" | "context_60m";
  touchedAt?: number;
  confluence: string[];
};
```

### 8.3 TradeSignal

```ts
type TradeSignal = {
  id: string;
  symbol: "BTCUSDT" | "ETHUSDT";
  side: "long" | "short" | "flat";
  setup:
    | "lvn_break_acceptance"
    | "lvn_breakdown_acceptance"
    | "hvn_val_failed_breakdown"
    | "hvn_vah_failed_breakout"
    | "vah_breakout_lvn_pullback_aggression"
    | "val_breakdown_lvn_pullback_aggression"
    | "cvd_divergence_failed_breakout"
    | "cvd_divergence_failed_breakdown";
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  confidence: number;
  reasons: string[];
  invalidationRules: string[];
  createdAt: number;
};
```

### 8.4 RiskDecision

```ts
type RiskDecision = {
  signalId: string;
  allowed: boolean;
  quantity: number;
  maxSlippageBps: number;
  remainingDailyRisk: number;
  rejectReasons: string[];
};
```

### 8.5 ExecutionOrder

```ts
type ExecutionOrder = {
  clientOrderId: string;
  signalId: string;
  exchange: "binance_futures";
  symbol: "BTCUSDT" | "ETHUSDT";
  side: "BUY" | "SELL";
  type: "LIMIT" | "MARKET" | "STOP_MARKET" | "TAKE_PROFIT_MARKET";
  quantity: number;
  price?: number;
  stopPrice?: number;
  reduceOnly: boolean;
  timeInForce?: "GTC" | "IOC" | "FOK" | "GTX";
};
```

## 9. 执行规则

### 9.1 下单顺序

开仓前：

1. Signal Engine 产生信号。
2. Risk Engine 校验账户、持仓、熔断、仓位。
3. Execution Engine 提交入场单。
4. 入场成交后立即提交 reduce-only 止损。
5. 止损确认成功后提交 reduce-only 止盈。

若止损提交失败：

- 立即市价 reduce-only 平仓。
- 触发熔断。

### 9.2 订单状态机

订单状态：

- `created`
- `submitted`
- `partially_filled`
- `filled`
- `cancel_pending`
- `cancelled`
- `rejected`
- `expired`
- `failed`

入场单超过 10s 未完全成交：

- 撤销剩余数量。
- 若部分成交，按已成交数量提交止损和止盈。
- 若撤单失败，进入保护模式并查询交易所真实仓位。

### 9.3 仓位状态同步

每 2s 通过 REST 校验：

- 本地持仓 quantity
- 交易所持仓 quantity
- 未成交止损单
- 未成交止盈单

若不一致：

- 暂停新信号。
- 以交易所为准重建本地状态。
- 若无法确认保护单存在，市价 reduce-only 平仓。

## 10. Journal 与复盘

每个事件写入 append-only 日志：

```json
{
  "type": "signal",
  "time": 1778191200000,
  "symbol": "BTCUSDT",
  "payload": {
    "setup": "lvn_break_acceptance",
    "entryPrice": 68250,
    "stopPrice": 68130,
    "targetPrice": 68520,
    "reasons": [
      "price accepted above rolling LVN",
      "delta30s positive and above threshold",
      "target at next HVN"
    ]
  }
}
```

最少保存：

- 原始成交事件。
- 盘口快照。
- Profile levels。
- Signal。
- Risk decision。
- Order request / response。
- Fill。
- Position state。
- Realized / unrealized PnL。
- Circuit breaker event。

复盘必须支持：

- 给定时间段重放所有 signal。
- 查看被拒绝交易的拒绝原因。
- 比较 paper fill 与真实行情可成交性。
- 按 setup 统计胜率、平均 R、最大回撤、滑点。

## 11. 回测与仿真

### 11.1 回测数据

最低要求：

- 1m K 线。
- 聚合成交。

推荐要求：

- tick / aggTrade 全量数据。
- 盘口 L1 / L2 快照。
- 资金费率和标记价格。

仅用 1m K 线无法可靠验证订单流策略，只能做粗略过滤器验证。

### 11.2 回测指标

必须输出：

- 总交易数。
- 胜率。
- 平均 R。
- Profit factor。
- 最大回撤。
- 单日最大亏损。
- 连续亏损最大值。
- 平均持仓时间。
- 平均滑点。
- 按 setup 分组表现。
- 按交易时段分组表现。

### 11.3 仿真场景

必须覆盖：

- WebSocket 断线。
- 交易所 REST 下单超时。
- 部分成交。
- 止损单提交失败。
- 盘口 spread 突然扩大。
- 资金费率结算前后。
- 快速反向波动。
- 本地持仓与交易所持仓不一致。

## 12. MVP 交付范围

MVP 包含：

- Binance Futures testnet adapter。
- BTCUSDT / ETHUSDT 数据接入。
- Session + rolling volume profile。
- VWAP、Delta、HVN/LVN、VAH/VAL、POC。
- LVN 接受、HVN/VAH/VAL 假突破回收、Trend Squeeze、CVD 背离假突破等 setup 的信号判断。
- Paper trading execution。
- Risk Engine 与熔断。
- Journal。
- 回放工具。

MVP 不包含：

- 多交易所套利。
- 机器学习预测。
- 自动参数优化。
- 实盘默认开启。
- 图形化大屏。
- 分批止盈。

## 13. 推荐项目结构

```text
src/
  config/
  market_data/
  profile/
  signals/
  risk/
  execution/
  journal/
  replay/
  backtest/
tests/
  unit/
  simulation/
docs/
```

推荐第一版语言：

- Python：开发快，适合研究、回测、原型。
- TypeScript：适合交易工具服务化和前端联动。

若目标是最快复刻和验证，优先 Python。若目标是长期产品化，数据和执行核心可后续迁移到 TypeScript / Rust。

## 14. 验收标准

策略层：

- 每个信号都包含明确 setup、触发条件、止损、目标和失效条件。
- 没有任何信号能绕过 Risk Engine。
- 禁止交易条件触发时不会开新仓。

执行层：

- 入场成交后必须存在 reduce-only 止损。
- 止损提交失败必须保护性平仓。
- 本地状态与交易所状态冲突时必须停止开新仓。

测试层：

- 单元测试覆盖 profile level、VWAP、Delta、信号、仓位计算、熔断。
- 仿真测试覆盖断线、部分成交、止损失败、滑点扩大。
- Paper trading 至少连续运行 2 周，并输出完整 journal。

实盘前门槛：

- Paper trading 未出现未保护仓位。
- 最大回撤低于预设阈值。
- 策略表现不能依赖单一极端行情日。
- 所有 API key 权限必须限制为合约交易，不开放提现权限。

## 15. 默认配置

```yaml
exchange: binance_futures
mode: paper
symbols:
  - BTCUSDT
  - ETHUSDT
risk:
  risk_per_trade: 0.0025
  daily_loss_limit: 0.01
  max_consecutive_losses: 3
  max_leverage: 3
  max_symbol_notional_equity_multiple: 2
execution:
  entry_timeout_seconds: 10
  websocket_stale_ms: 1500
  max_data_lag_ms: 2000
  btc_max_slippage_bps: 3
  eth_max_slippage_bps: 4
profile:
  session_timezone: UTC
  value_area_ratio: 0.70
  execution_window_minutes: 30
  micro_window_minutes: 15
  context_window_minutes: 60
  btc_bin_size: 20
  eth_bin_size: 5
  min_execution_profile_trades: 50
  min_micro_profile_trades: 25
  min_profile_bins: 3
signals:
  min_reward_risk: 1.2
  delta_window_seconds:
    - 15
    - 30
    - 60
  funding_blackout_minutes: 2
  aggression_large_threshold: 10
  aggression_block_threshold: 50
  atr_period: 14
```

## 16. 开发顺序

1. 实现 Market Data 和统一事件格式。
2. 实现 Profile Engine、VWAP、Delta。
3. 实现 Risk Engine 和仓位计算。
4. 实现 Signal Engine，但只输出 paper signal。
5. 实现 Journal 和 replay。
6. 实现 paper execution。
7. 加入仿真测试和回测报告。
8. 接入 Binance testnet 下单。
9. 完成 2 周 paper trading 验收。
10. 通过配置显式开启 live mode，小资金试运行。

## 17. 关键安全原则

- 默认 `mode=paper`。
- Live mode 必须通过配置显式开启。
- API key 不允许提现权限。
- 任意异常优先保护仓位，其次才考虑继续交易。
- 所有交易决策必须可复盘。
- 每一次自动下单都必须能解释为：在哪里交易、为什么交易、错了在哪里退出、赚了在哪里退出。

## 18. 实盘逻辑状态机版本

### 18.1 设计目标

新版本把视频中的实盘交易思想实现为可复用状态机，而不是逐条硬编码 setup。核心变化是先判断市场拍卖状态，再判断方向叙事，然后等待关键位置触发、完整 1m K 收盘确认和订单流位移确认，最后才生成交易计划。

对外仍保留 `SignalEngine.evaluate(...) -> TradeSignal | None`，但内部 pipeline 为：

```text
MarketStateEngine
  -> BiasEngine
  -> SetupCandidateEngine
  -> ConfirmationGate
  -> TradePlanBuilder
  -> RiskEngine
  -> PaperTradingEngine
```

### 18.2 新增配置段

默认配置新增：

```yaml
market_state:
  compression_bars: 5
  compression_range_ratio: 0.65
  absorption_delta_ratio: 2.0
  absorption_max_displacement_atr: 0.25
  failed_auction_window_seconds: 90
  value_acceptance_close_bars: 2
confirmation:
  require_1m_close: true
  close_buffer_bps: 0.36
  max_reclaim_seconds: 20
  min_displacement_atr: 0.15
  min_delta_ratio: 1.2
  min_volume_ratio: 1.3
trade_plan:
  min_reward_risk: 1.2
  fallback_reward_risk: 3.0
  max_reward_risk: 6.0
  structure_target_first: true
  atr_stop_mult: 0.35
management:
  squeeze_break_even_r: 1.25
  failed_auction_break_even_r: 1.5
  lvn_acceptance_break_even_r: 1.5
  first_structure_reduce_ratio: 0.0
  absorption_reduce_ratio: 0.5
  no_followthrough_seconds: 0
```

### 18.3 数据结构

`types.py` 新增：

- `MarketStateResult`
- `BiasResult`
- `SetupCandidate`
- `ConfirmationResult`
- `TradePlan`
- `SignalTrace`

`TradeSignal` 追加可选字段：`setup_model`、`legacy_setup`、`market_state`、`bias`、`target_source`、`management_profile`、`trace`。这些字段用于复盘和报表兼容，不破坏旧调用方。

### 18.4 信号模型

实盘模型统一为 4 类：

- `squeeze_continuation`：value 外接受后的延续突破。
- `failed_auction_reversal`：关键位刺破失败后的回收反转。
- `lvn_acceptance`：LVN 穿越后的外侧接受。
- `absorption_response`：大成交无位移后的反向响应或持仓管理事件。

旧 8 个 setup 不删除，作为 `legacy_setup` 写入 signal/order/report。

### 18.5 Confirmation Gate

所有实盘 setup 默认要求完整 1m K 收盘确认。tick 级刺破只会生成 candidate，不会直接变成 `TradeSignal`。确认层会检查：

- close buffer bps
- delta ratio
- volume ratio
- ATR displacement
- reclaim failure

失败原因必须写入 `last_reject_reasons`，例如 `candle_close_not_confirmed`、`delta_not_confirmed`、`volume_not_confirmed`、`insufficient_displacement`、`trigger_reclaimed`。

### 18.6 交易计划与报告

目标位优先使用前方结构，而不是固定 R：

1. `context_60m` POC/HVN/VAH/VAL。
2. `execution_30m` POC/HVN/VAH/VAL。
3. session high/low 或前高/前低。
4. capped R fallback。

Backtest report 增加 `by_strategy_context`，按 `setup_model|market_state|session` 聚合信号、交易、胜数和净 PnL。Dashboard `/api/orderflow` summary 增加 `market_state`、`bias`、`last_reject_reasons`。

### 18.7 安全边界

本版本仍只改变 paper/live dashboard/backtest 逻辑，不新增真实下单能力。真实交易仍必须通过显式 live mode 配置、环境变量确认、风险控制和后续人工验收。
