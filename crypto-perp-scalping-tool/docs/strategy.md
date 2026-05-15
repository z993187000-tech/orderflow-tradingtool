# 核心策略文档

> **维护策略**：本文档是策略逻辑的唯一权威描述。任何涉及 `signals/` 目录、`profile/engine.py`、`risk/engine.py`、`config/default.yaml` 中策略参数的改动，必须同步更新本文档。

---

## 1. 市场哲学

策略基于 **Auction Market Theory（拍卖市场理论）**：

- 价格在**高成交量区域**停留 → 市场接受该价格区间（价值区）
- 价格在**低成交量区域**快速穿越 → 流动性薄、接受度低（失衡区）
- **POC/HVN**：价格吸附、止盈、震荡中心
- **LVN**：突破、假突破、快速回补、止损触发点

核心原则：**不单独看价位**，必须同时确认位置 + 上下文 + 订单流 + 风险回报。

---

## 2. Volume Profile 参数

### 2.1 参数定义

| 参数 | 全称 | 含义 | 作用 |
|------|------|------|------|
| **POC** | Point of Control | 成交量最大的价格箱 | 价值中心，止盈目标 |
| **VAH** | Value Area High | 70% 价值区上边界 | 趋势阻力，突破/假突破判断 |
| **VAL** | Value Area Low | 70% 价值区下边界 | 趋势支撑，跌破/假跌破判断 |
| **H / HVN** | High Volume Node | 局部高成交量节点 | 次级支撑/阻力，止盈目标 |
| **L / LVN** | Low Volume Node | 局部低成交量节点 | 均值回归入场，止损参考 |

### 2.2 计算算法

所有参数由 `VolumeProfileEngine`（`profile/engine.py`）计算。

**分箱（Binning）**：
```
bin_price = floor(price / bin_size) * bin_size
```
- BTC `bin_size = $20`（`config/default.yaml:37`）
- ETH `bin_size = $5`（`config/default.yaml:38`）

**POC（控制点）**（`profile/engine.py:94`）：
```
POC = argmax(bin → bin.volume)
```

**VAH / VAL（价值区）**（`profile/engine.py:141-161`）：
```
target_volume = total_volume * value_area_ratio（70%）
从 POC 出发，向两侧贪婪扩展：
  每次选择相邻箱中成交量更高的一侧纳入
  直到累计成交量 >= target_volume
VAL = 纳入的最低箱下界
VAH = 纳入的最高箱上界
```

**HVN / H（高成交量节点）**（`profile/engine.py:110`）：
```
条件：
  1. 该箱成交量 > 左邻箱 && > 右邻箱（局部峰值）
  2. 该箱成交量 / 平均成交量 >= 1.25
  3. 不是 POC
```

**LVN / L（低成交量节点）**（`profile/engine.py:112`）：
```
条件：
  1. 该箱成交量 < 左邻箱 && < 右邻箱（局部谷值）
  2. 该箱成交量 / 平均成交量 <= 0.55
```

**强度（Strength）**（`profile/engine.py:93`）：
```
strength = bin_volume / average_bin_volume
```

### 2.3 多时间框架 Profile 窗口

策略使用三层 profile 窗口（`config/default.yaml:34-41`）：

| 窗口 | 标签 | 时长 | 用途 | 最低样本门槛 |
|------|------|------|------|------------|
| Execution | `execution_30m` | 30 分钟 | 信号主触发源（市场状态、候选生成） | ≥ 50 笔成交，≥ 3 个非空箱 |
| Micro | `micro_15m` | 15 分钟 | 开仓前微观确认 | ≥ 25 笔成交，≥ 3 个非空箱 |
| Context | `context_60m` | 60 分钟 | 前方结构目标/障碍识别（不做方向否决） | 无强制门槛 |
| Session | `session` | UTC 自然日 | 日内高低点参考 | 无 |

短窗口不满足质量门槛时拒绝 profile-based entry，记录对应 reject reason。

Profile 引擎通过正则 `rolling_Nm` 匹配任意 N 分钟窗口（`profile/engine.py:11,73-75`），扩展新窗口只需修改配置。

---

## 3. 信号流水线

信号引擎（`signals/engine.py`）已重构为 **五阶段流水线**架构。每个阶段由独立子引擎负责：

```
MarketSnapshot
     │
     ▼
┌─────────────────┐
│ 1. 禁止条件检查   │  _check_forbidden() → reject 或继续
└─────────────────┘
     │
     ▼
┌─────────────────┐
│ 2. 市场状态检测   │  MarketStateEngine.evaluate()
│   7 种状态       │  → MarketStateResult
└─────────────────┘
     │  (no_trade? → reject)
     ▼
┌─────────────────┐
│ 3. 方向偏向       │  BiasEngine.evaluate()
│   long/short/   │  → BiasResult
│   neutral       │
└─────────────────┘
     │
     ▼
┌─────────────────┐
│ 4. 候选生成       │  SetupCandidateEngine.generate()
│   4 类候选       │  → SetupCandidate[]
└─────────────────┘
     │  (空? → reject)
     ▼
┌─────────────────┐
│ 5. 确认过滤       │  ConfirmationGate.confirm()
│   4 项检查       │  → ConfirmationResult（逐个候选）
└─────────────────┘
     │  (全部未确认? → reject)
     ▼
┌─────────────────┐
│ 6. 交易计划       │  TradePlanBuilder.build()
│   止损/止盈/     │  → TradeSignal
│   管理配置       │
└─────────────────┘
```

数据流见 `signals/engine.py:72-140`。

---

## 4. 市场状态检测

`MarketStateEngine`（`signals/market_state.py`）按优先级顺序判定 7 种市场状态：

### 4.1 状态定义

| 状态 | 方向 | 触发条件 | 含义 |
|------|------|---------|------|
| `no_trade` | neutral | 无 execution profile levels | 数据不足，禁止交易 |
| `failed_auction` | long/short | 1m K 线突破 VAH/跌破 VAL 但收盘回收至 level 内部 | 假突破/假跌破 → 反向交易 |
| `absorption` | long/short | Delta/攻击性气泡很大但价格位移 < 0.25 ATR | 主动单撞被动墙 → 反向交易 |
| `compression` | long/short | 最近 5 根 1m K 线振幅 < 前 2 根均值的 65%，且价格在 level 内 | 压缩蓄力 → 突破方向交易 |
| `imbalanced_up` | long | 价格在 VAH 上方 + Delta_30s > 0 | 价值区上方接受，趋势多 |
| `imbalanced_down` | short | 价格在 VAL 下方 + Delta_30s < 0 | 价值区下方接受，趋势空 |
| `balanced` | neutral | 价格在 VAL~VAH 之间，或靠近 POC（≤ 0.25 ATR） | 区间震荡 |

### 4.2 状态判定优先级

1. 无 profile levels → `no_trade`
2. 1m K 线假突破/假跌破 → `failed_auction`
3. Delta 激增但无位移 → `absorption`
4. K 线振幅收缩 → `compression`
5. 价格 + Delta 方向一致 → `imbalanced_up/down`
6. 价格在价值区内 → `balanced`

### 4.3 关键配置

```yaml
market_state:
  compression_bars: 5              # 压缩判定 K 线数
  compression_range_ratio: 0.65    # 振幅收缩比例阈值
  absorption_delta_ratio: 2.0      # Delta 须 > 历史均值 * 2
  absorption_max_displacement_atr: 0.25  # 最大位移（ATR 比例）
  failed_auction_window_seconds: 90      # 假突破观察窗口
```

---

## 5. 方向偏向

`BiasEngine`（`signals/bias.py`）确定交易方向：

| 条件 | 偏向 |
|------|------|
| Market state 方向明确（`imbalanced_up/down`、`compression`、`absorption`）且与价位不冲突 | 跟随 state 方向 |
| VAH 上方的 state 看多 + 价格仍在 VAH 下方 → 冲突，回退 neutral |
| VAL 下方的 state 看空 + 价格仍在 VAL 上方 → 冲突，回退 neutral |
| State 为 balanced 且无方向 | 价格 > VWAP → long；价格 < VWAP → short |
| 无任何方向信号 | neutral |

---

## 6. 交易候选生成

`SetupCandidateEngine`（`signals/setups.py`）根据市场状态和偏向生成 4 类候选：

### 6.1 候选类型

| 候选模型 | 对应旧 Setup | 触发条件 | 方向 |
|---------|-------------|---------|------|
| **squeeze_continuation** | vah_breakout / val_breakdown | bias 有方向 + state 为 imbalanced/compression/absorption | 跟随 bias |
| **failed_auction_reversal** | cvd_divergence_failed_breakout/breakdown | state == failed_auction | 反向（假突破方向的反方向） |
| **lvn_acceptance** | lvn_break_acceptance / lvn_breakdown_acceptance | 价格在 LVN 上方/下方 + Delta 同向 | 跟随 Delta 方向 |
| **absorption_response** | 新增 | state == absorption | 反向（吸收方向的反方向） |

### 6.2 squeeze_continuation 详细逻辑

```
Long:  bias == long
       → 找最近 VAH，trigger_price = VAH.upper_bound
       → structure_stop = VAH.lower_bound
       → structure_target = 上方最近 POC/HVN/VAH/VAL

Short: bias == short
       → 找最近 VAL，trigger_price = VAL.lower_bound
       → structure_stop = VAL.upper_bound
       → structure_target = 下方最近 POC/HVN/VAH/VAL
```

### 6.3 failed_auction_reversal 详细逻辑

```
state == failed_auction + direction == long:
  → Long entry，structure_stop = VAL.lower_bound
  → 价格跌破 VAL 但 1m 收盘收回

state == failed_auction + direction == short:
  → Short entry，structure_stop = VAH.upper_bound
  → 价格突破 VAH 但 1m 收盘收回
```

### 6.4 lvn_acceptance 详细逻辑

不依赖 market state，直接检查价格与 LVN 的关系：

```
Long:  price > LVN.upper_bound + delta_30s > 0
       → trigger = LVN.upper_bound, structure_stop = LVN.lower_bound

Short: price < LVN.lower_bound + delta_30s < 0
       → trigger = LVN.lower_bound, structure_stop = LVN.upper_bound
```

### 6.5 absorption_response 详细逻辑

```
state == absorption + direction == long:
  → Long entry（卖单被吸收），structure_stop = price - ATR_1m

state == absorption + direction == short:
  → Short entry（买单被吸收），structure_stop = price + ATR_1m
```

---

## 7. 确认过滤

`ConfirmationGate`（`signals/confirmation.py`）对候选进行 4 项确认检查，全部通过才算确认：

### 7.1 1m K 线收盘确认

```
require_1m_close: true 时，必须存在已收盘 1m K 线
Long:  close > trigger_price + buffer（close_buffer_bps: 0.36 bps）
Short: close < trigger_price - buffer
当前价格未回收 trigger（Long: price >= trigger, Short: price <= trigger）
```

若设置 `require_1m_close: false`，跳过此项。

### 7.2 Delta 确认

```
方向 Delta = Long ? delta_30s : -delta_30s
方向 Delta ≤ 0 → 拒绝
若有所史数据：方向 Delta >= 历史正向 Delta 均值 * min_delta_ratio（1.2）
```

### 7.3 成交量确认

```
若有历史数据：volume_30s >= 历史均值 * min_volume_ratio（1.3）
```

### 7.4 位移确认

```
displacement = |close - trigger_price|
displacement >= max(ATR_1m, ATR_3m) * min_displacement_atr（0.15）
```

位移不足说明突破力度不够，拒绝。

---

## 8. 交易计划构建

`TradePlanBuilder`（`signals/trade_plan.py`）将确认后的候选转为可执行的 `TradeSignal`。

### 8.1 入场价

```
entry = confirmed_close  # 1m K 线收盘价（最可靠的确认价）
```

### 8.2 止损

```python
atr = max(ATR_1m_14, ATR_3m_14, price * 0.0001)
atr_buffer = atr * atr_stop_mult  # 默认 0.35

Long:  stop = min(candidate.structure_stop, entry - atr_buffer)
Short: stop = max(candidate.structure_stop, entry + atr_buffer)
```

结构止损优先于 ATR 止损（取更保守侧）。

### 8.3 止盈

优先级：
1. **结构目标**：candidate 自带 structure_target（POC/HVN/VAH/VAL）
2. **多框架结构**：先找 `context_60m` 中的最近 POC/HVN/VAH/VAL；再找 `execution_30m`
3. **回退目标**：`fallback_reward_risk` × stop_distance（默认 3.0R，上限 10.0R）

如果结构目标导致的 R:R < `min_reward_risk`（1.2）且有 structure_target → 拒绝（`structure_reward_risk_too_low`）。
如果无 structure_target 且 R:R 不足 → 使用回退 R:R 代替。

### 8.4 管理配置

每个候选绑定一个 `management_profile`，控制持仓管理行为：

| 候选模型 | management_profile | break_even 触发 |
|---------|-------------------|----------------|
| squeeze_continuation | `squeeze` | 1.25R |
| failed_auction_reversal | `failed_auction` | 1.5R |
| lvn_acceptance | `lvn_acceptance` | 1.5R |
| absorption_response | `absorption` | 默认（2.5R） |

### 8.5 置信度

| 候选模型 | confidence |
|---------|-----------|
| squeeze_continuation | 0.72 |
| failed_auction_reversal | 0.68 |
| lvn_acceptance | 0.65 |
| absorption_response | 0.60 |

---

## 9. 时段限制

由 `signals/engine.py` 中的 `_TREND_SETUPS` / `_MEAN_REVERSION_SETUPS` 常量控制，但当前随着流水线重构，时段限制已部分整合到 state 判定中（由 profile 窗口的自然数据量决定）。

时段划分（`config/default.yaml:42-49`）：

| 时段 | UTC 时间 | 特征 |
|------|---------|------|
| Asia | 00:00–07:00 | 低波动，均值回归 |
| London | 07:00–12:30 | 高波动，趋势 |
| NY | 12:30–20:00 | 高波动，趋势 |
| Dead | 其余时间 | 极低波动 |

`session_gating_enabled: true` 控制是否启用时段限制。

---

## 10. 禁止交易条件

由 `signals/engine.py:163-201` 实现，优先级最高，任一满足即禁止开新仓：

| 条件 | 代码常量 | 判定逻辑 |
|------|---------|---------|
| 数据延迟过高 | `data_stale` | `exchange_lag_ms > max_data_lag_ms`（2000ms） |
| 盘口价差过宽 | `spread_too_wide` | `spread > 5 分钟中位数 * 2` |
| WebSocket 不健康 | `websocket_stale` | `health.is_stale()` 返回 true |
| 资金费率结算 | `funding_blackout` | 结算前后 2 分钟 |
| 极端波动 | `extreme_volatility` | `ATR_1m > 20 根均值 * 3` |
| 熔断已触发 | `circuit_breaker_tripped` | 风控熔断激活 |
| 已有持仓 | `existing_position` | 同 symbol 已有未平仓头寸 |

---

## 11. 持仓管理

### 11.1 Break-even 移动

浮盈触发 break_even 阈值后，止损自动移至开仓均价：

| management_profile | break_even_trigger_r |
|-------------------|---------------------|
| squeeze | 1.25R |
| failed_auction | 1.5R |
| lvn_acceptance | 1.5R |
| absorption | 2.5R（默认） |

### 11.2 结构减仓

已取消首个结构目标减仓：
- `first_structure_reduce_ratio: 0.0`

### 11.3 吸收保护

若持仓方向 CVD/Delta 激增但价格不跟：
- `absorption_reduce_ratio: 0.5`（强制减 50%）

### 11.4 无跟随保护

已取消开仓后无有效位移强制平仓：
- `no_followthrough_seconds: 0`

### 11.5 最大持仓时间

`max_holding_ms: 900000`（15 分钟）后不直接平仓；每经过一个最大持仓周期，将止盈线下调 1R，最低不低于保本线。

### 11.6 连续 K 线后的止损上移

`kline_momentum_stop_shift` 不在连续 3 根同向 1m K 线刚形成时立即移动止损，而是等待后续回踩确认：

- Long：连续 3 根或更多阳线后，后续出现一根收阴 K 线；该阴线有明显下影线，且成交量不低于前一段阳线均量的 80%，才把止损调整到连续阳线段第一根 K 线最低价。
- Short：连续 3 根或更多阴线后，后续出现一根收阳 K 线；该阳线有明显上影线，且成交量不低于前一段阴线均量的 80%，才把止损调整到连续阴线段第一根 K 线最高价。
- 若新止损不会改善当前止损，或会落在当前价格不合理一侧，则不移动。

### 11.7 平仓后冷却

`post_close_cooldown_ms: 30000`（30 秒）内禁止同 symbol 开新仓。

---

## 12. 风控集成

### 12.1 账户限制（`risk/engine.py`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `risk_per_trade` | 0.25% | 单笔最大风险占权益比例 |

### 12.2 仓位计算

```python
risk_amount = equity * risk_per_trade
quantity = risk_amount / stop_distance
max_quantity = (equity * max_symbol_notional_equity_multiple) / entry_price
quantity = min(quantity, max_quantity)
```

### 12.3 熔断条件

- 交易所 API 连续 3 次下单失败
- 止损单提交失败
- 本地与交易所仓位不一致
- WebSocket 断线 > 5s
- 闪崩检测（`flash_crash_detected`）

---

## 13. 完整配置参考

核心策略参数（`config/default.yaml`）：

```yaml
# === Profile ===
profile:
  value_area_ratio: 0.70               # 价值区 70%
  execution_window_minutes: 30         # 主触发窗口 30min
  micro_window_minutes: 15             # 微观确认窗口 15min
  context_window_minutes: 60           # 结构上下文窗口 60min
  btc_bin_size: 20                     # BTC $20 分箱
  eth_bin_size: 5                      # ETH $5 分箱
  min_execution_profile_trades: 50     # execution 最低样本
  min_micro_profile_trades: 25         # micro 最低样本
  min_profile_bins: 3                  # 最低非空箱数

# === Execution ===
execution:
  reward_risk: 5.0                     # 默认目标 R:R
  dynamic_reward_risk_enabled: true    # 启用动态 R:R
  reward_risk_min: 3.0                 # 最小 R:R
  reward_risk_max: 10.0                # 最大 R:R
  atr_stop_mult: 0.35                  # ATR 止损系数
  min_stop_cost_mult: 1.0              # 最小止损 = 手续费 * 1
  min_target_cost_mult: 2.0            # 最小止盈 = 手续费 * 2
  max_holding_ms: 900000               # 每 15min 下调一次止盈线
  post_close_cooldown_ms: 30000        # 平仓冷却 30s

# === Market State ===
market_state:
  compression_bars: 5                  # 压缩判定 K 线数
  compression_range_ratio: 0.65        # 振幅收缩阈值
  absorption_delta_ratio: 2.0          # Delta 激增倍数
  absorption_max_displacement_atr: 0.25
  failed_auction_window_seconds: 90

# === Confirmation ===
confirmation:
  require_1m_close: true               # 要求 1m 收盘确认
  close_buffer_bps: 0.36               # 收盘缓冲
  min_displacement_atr: 0.15           # 最小位移
  min_delta_ratio: 1.2                 # Delta 相对阈值
  min_volume_ratio: 1.3                # 成交量相对阈值

# === Trade Plan ===
trade_plan:
  min_reward_risk: 1.2                 # 最小 R:R
  fallback_reward_risk: 3.0            # 回退 R:R
  max_reward_risk: 6.0                 # 上限 R:R
  structure_target_first: true         # 结构目标优先
  atr_stop_mult: 0.35

# === Management ===
management:
  squeeze_break_even_r: 1.25
  failed_auction_break_even_r: 1.5
  lvn_acceptance_break_even_r: 1.5
  first_structure_reduce_ratio: 0.0
  absorption_reduce_ratio: 0.5
  no_followthrough_seconds: 0
```

---

## 14. 关键源文件映射

| 模块 | 文件 | 职责 |
|------|------|------|
| Profile 计算 | `profile/engine.py` | POC/VAH/VAL/HVN/LVN、多时间框架 build_profile_levels() |
| 信号引擎（主协调） | `signals/engine.py` | 流水线编排、禁止条件 |
| 市场状态 | `signals/market_state.py` | 7 种状态检测 |
| 方向偏向 | `signals/bias.py` | long/short/neutral 判定 |
| 候选生成 | `signals/setups.py` | 4 类交易候选 |
| 确认过滤 | `signals/confirmation.py` | 1m close/Delta/量/位移 |
| 交易计划 | `signals/trade_plan.py` | 止损/止盈/管理配置 |
| 风控引擎 | `risk/engine.py` | 账户限制、仓位计算 |
| 熔断 | `risk/circuit.py` | 熔断状态机 |
| 执行引擎 | `execution/paper_engine.py` | 纸单执行 |
| 实时数据 | `web/live_store.py` | 实时成交窗口、信号触发 |
| 配置 | `config/default.yaml` | 所有策略默认参数 |
| 类型定义 | `types.py` | 所有数据结构和枚举 |

---

## 15. 维护约定

1. **信号流水线改动**（新增/修改 state/candidate/confirmation/plan）→ 更新第 3-8 节
2. **Profile 计算改动**（算法、窗口、分箱）→ 更新第 2 节
3. **参数默认值改动**（`config/default.yaml`）→ 更新第 13 节
4. **时段划分改动** → 更新第 9 节
5. **管理策略改动**（break-even/减仓/保护）→ 更新第 11 节
6. Code review 时对照本文档验证代码实现
