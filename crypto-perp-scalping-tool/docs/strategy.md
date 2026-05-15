# 核心策略文档

> **维护策略**：本文档是策略逻辑的唯一权威描述。任何涉及 `signals/engine.py`、`profile/engine.py`、`risk/engine.py`、`config/default.yaml` 中策略参数的改动，必须同步更新本文档。Code review 时需验证文档与代码一致。

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
| **POC** | Point of Control | 成交量最大的价格箱 | 价值中心，核心止盈目标 |
| **VAH** | Value Area High | 70% 价值区上边界 | 趋势阻力，假突破卖出信号 |
| **VAL** | Value Area Low | 70% 价值区下边界 | 趋势支撑，假跌破买入信号 |
| **H / HVN** | High Volume Node | 局部高成交量节点 | 次级支撑/阻力，止盈目标 |
| **L / LVN** | Low Volume Node | 局部低成交量节点 | 均值回归入场，趋势回踩确认 |

### 2.2 计算算法

所有参数由 `VolumeProfileEngine`（`profile/engine.py`）计算，配置默认值见 `config/default.yaml`。

**分箱（Binning）**：
```
bin_price = floor(price / bin_size) * bin_size
```
- BTC bin_size = `$20`（见 `config/default.yaml`）
- ETH bin_size = `$5`（见 `config/default.yaml`）

**POC（控制点）**（`profile/engine.py:87`）：
```
POC = argmax(bin → bin.volume)
```
即成交量最大的价格箱。

**VAH / VAL（价值区）**（`profile/engine.py:134-154`）：
```
target_volume = total_volume * 0.70   # value_area_ratio = 70%
从 POC 出发，向两侧贪婪扩展：
  每次选择相邻箱中成交量更高的一侧纳入
  直到累计成交量 >= target_volume
VAL = 纳入的最低箱下界
VAH = 纳入的最高箱上界
```

**HVN / H（高成交量节点）**（`profile/engine.py:103`）：
```
条件：
  1. 该箱成交量 > 左邻箱成交量
  2. 该箱成交量 > 右邻箱成交量（局部峰值）
  3. 该箱成交量 / 平均成交量 >= 1.25
  4. 不是 POC
```

**LVN / L（低成交量节点）**（`profile/engine.py:105`）：
```
条件：
  1. 该箱成交量 < 左邻箱成交量
  2. 该箱成交量 < 右邻箱成交量（局部谷值）
  3. 该箱成交量 / 平均成交量 <= 0.55
```

**强度（Strength）**（`profile/engine.py:86`）：
```
strength = bin_volume / average_bin_volume
```

**Profile 窗口**：
- `execution_30m`：最近 30 分钟滚动窗口，作为 VAH/VAL/LVN/HVN 主触发来源。
- `micro_15m`：最近 15 分钟滚动窗口，仅做开仓前 micro confirm。
- `context_60m`：最近 60 分钟滚动窗口，仅做大结构目标/障碍参考，不作为方向 veto。
- `session`：UTC 自然日窗口（`profile/engine.py:66-68`）

短窗口 profile 必须满足样本质量门槛：execution 至少 50 笔成交且至少 3 个非空价格箱；micro 至少 25 笔成交且至少 3 个非空价格箱。不满足时拒绝 profile-based entry，并记录对应 reject reason。

---

## 3. 交易信号（8 种 Setup）

所有信号必须同时满足四类条件：
1. **位置**：价格靠近 POC/HVN/LVN/VAH/VAL/VWAP/时段高低点
2. **上下文**：趋势、区间、突破后接受、假突破回收
3. **订单流确认**：Delta、成交量、攻击性气泡方向一致
4. **风险回报**：R:R >= `min_reward_risk`（默认 1.2）

信号先由 `execution_30m` profile 生成候选，再由 `micro_15m` profile 确认。micro confirm 要求当前价格靠近 15m VAH/VAL/HVN/LVN，且 `delta_15s` 与开仓方向一致。`context_60m` 只用于识别前方 POC/HVN/VAH/VAL 障碍：若障碍压缩后的 R:R 不足则拒绝，否则可降低 confidence 并将目标收敛到障碍位。

### 3.1 信号优先级

信号按以下优先级顺序评估（`signals/engine.py:73-82`），先匹配优先：

1. VAH 突破 + LVN 回踩 + 买方攻击性气泡（趋势 Long）
2. VAL 跌破 + LVN 回踩 + 卖方攻击性气泡（趋势 Short）
3. 熊方 CVD 背离 + VAH 假突破（趋势 Short）
4. 牛方 CVD 背离 + VAL 假跌破（趋势 Long）
5. LVN 接受（均值回归 Long）
6. LVN 跌破（均值回归 Short）
7. HVN/VAL 假跌破回收（趋势 Long）
8. HVN/VAH 假突破回收（趋势 Short）

---

### 3.2 趋势类信号（仅伦敦/纽约时段触发）

#### Setup 1: VAH 突破 + LVN 回踩 + 攻击性气泡（Long）

- **代码**：`signals/engine.py:309-343`，setup 名 `vah_breakout_lvn_pullback_aggression`
- **时段**：仅 London、NY
- **用途**：趋势市不追突破第一脚，等回踩到突破方向上的 LVN，再用大单确认延续

**触发条件**：
- 存在 VAH 和 LVN 两个 level
- LVN 在 VAH 上方（`lvn.price > vah.price`）——确认突破方向
- 当前价格位于 LVN 区间内（`lvn.lower_bound <= price <= lvn.upper_bound`）——正在回踩
- 最近 90s 内价格曾突破 VAH 上沿（`recent_price_crossed(vah.upper_bound, above=True)`）
- 当前出现买方攻击性气泡（`aggression_bubble_side == "buy"`）
  - 攻击性气泡定义：单笔成交 >= 20 BTC（large）或 >= 50 BTC（block），或按 EMA 百分位阈值
- Delta_30s > 0

**入场**：当前价格（`snapshot.last_price`）
**止损**：`min(LVN.lower_bound, entry_price - ATR_buffer)`
  - ATR_buffer = `atr_stop_mult * max(ATR_1m_14, ATR_3m_14)`，`atr_stop_mult` 默认 0.35
**止盈**：`entry_price + stop_distance * reward_risk`，`reward_risk` 默认 5.0
**置信度**：0.72
**失效条件**：价格跌回 LVN 下方 / 买方攻击消失

#### Setup 2: VAL 跌破 + LVN 回踩 + 攻击性气泡（Short）

- **代码**：`signals/engine.py:347-381`，setup 名 `val_breakdown_lvn_pullback_aggression`
- **时段**：仅 London、NY
- **用途**：与 Setup 1 对称的空方版本

**触发条件**：
- 存在 VAL 和 LVN 两个 level
- LVN 在 VAL 下方（`lvn.price < val.price`）
- 当前价格位于 LVN 区间内
- 最近 90s 内价格曾跌破 VAL 下沿
- 当前出现卖方攻击性气泡（`aggression_bubble_side == "sell"`）
- Delta_30s < 0

**入场**：当前价格
**止损**：`max(LVN.upper_bound, entry_price + ATR_buffer)`
**止盈**：`entry_price - stop_distance * reward_risk`
**置信度**：0.72
**失效条件**：价格回升 LVN 上方 / 卖方攻击消失

#### Setup 3: 熊方 CVD 背离 + VAH 假突破（Short）

- **代码**：`signals/engine.py:385-419`，setup 名 `cvd_divergence_failed_breakout`
- **时段**：仅 London、NY
- **用途**：识别"价格刺破但订单流不跟"的失败突破

**触发条件**：
- 存在 VAH level
- 当前价格低于 VAH（`price < vah.price`）——已回落至价值区内
- 最近 90s 价格记忆中存在熊方 CVD 背离：
  - 价格在 VAH 上方创新高，但 CVD 未同步创新高（`_has_bearish_cvd_divergence`）
- Delta_30s < 0

**入场**：当前价格
**止损**：`max(VAH.upper_bound, 90s 内最高价)` + ATR 缓冲（取更保守侧）
**止盈**：`entry_price - stop_distance * reward_risk`
**置信度**：0.68
**失效条件**：价格重新突破前高 / CVD 创新高

#### Setup 4: 牛方 CVD 背离 + VAL 假跌破（Long）

- **代码**：`signals/engine.py:423-457`，setup 名 `cvd_divergence_failed_breakdown`
- **时段**：仅 London、NY
- **用途**：与 Setup 3 对称的多方版本

**触发条件**：
- 存在 VAL level
- 当前价格高于 VAL（`price > val.price`）
- 最近 90s 价格记忆中价格跌破 VAL 创新低，但 CVD 未同步创新低
- Delta_30s > 0

**入场**：当前价格
**止损**：`min(VAL.lower_bound, 90s 内最低价)` - ATR 缓冲
**止盈**：`entry_price + stop_distance * reward_risk`
**置信度**：0.68
**失效条件**：价格重新跌破前低 / CVD 创新低

#### Setup 5: HVN/VAL 假跌破回收（Long）

- **代码**：`signals/engine.py:221-261`，setup 名 `hvn_val_failed_breakdown`
- **时段**：仅 London、NY
- **用途**：价格短暂跌破支撑后快速回收

**触发条件**：
- 存在最近 VAL 或 HVN level
- 当前价格在 level lower_bound 上方（已回收）
- 最近 60s 内价格曾跌破 level.lower_bound（假跌破）
- 当前价格高于 level.price（回到支撑内）
- Delta_30s > 0（牛方翻转确认）

**入场**：当前价格
**止损**：`min(level.lower_bound, entry_price - ATR_buffer)`
**止盈**：`entry_price + stop_distance * reward_risk`
**置信度**：0.55
**失效条件**：价格再次跌破 level / Delta 转负

#### Setup 6: HVN/VAH 假突破回收（Short）

- **代码**：`signals/engine.py:265-305`，setup 名 `hvn_vah_failed_breakout`
- **时段**：仅 London、NY
- **用途**：与 Setup 5 对称的空方版本

**触发条件**：
- 存在最近 VAH 或 HVN level
- 当前价格在 level upper_bound 下方（已回落）
- 最近 60s 内价格曾突破 level.upper_bound（假突破）
- 当前价格低于 level.price
- Delta_30s < 0

**入场**：当前价格
**止损**：`max(level.upper_bound, entry_price + ATR_buffer)`
**止盈**：`entry_price - stop_distance * reward_risk`
**置信度**：0.55
**失效条件**：价格重新站上 level / Delta 转正

---

### 3.3 均值回归类信号（仅亚洲/休市时段触发）

#### Setup 7: LVN 接受（Long）

- **代码**：`signals/engine.py:143-178`，setup 名 `lvn_acceptance`（类型常量 `lvn_break_acceptance`）
- **时段**：仅 Asia、Dead（纽约时段禁止）

**触发条件**：
- 存在最近 LVN level
- 当前价格在 LVN upper_bound 上方（已从下方向上突破 LVN）
- Delta_30s > 0
- 若有历史窗口数据：
  - Delta_30s > 过去 20 个 30s 窗口 Delta 均值的 1.2 倍
  - Volume_30s > 过去 20 个窗口成交量均值的 1.5 倍

**入场**：当前价格
**止损**：`min(LVN.lower_bound, entry_price - ATR_buffer)`
**止盈**：`entry_price + stop_distance * reward_risk`
**置信度**：0.65
**失效条件**：价格跌回 LVN 下方 / Delta 转负

#### Setup 8: LVN 跌破（Short）

- **代码**：`signals/engine.py:182-217`，setup 名 `lvn_breakdown`（类型常量 `lvn_breakdown_acceptance`）
- **时段**：仅 Asia、Dead（纽约时段禁止）

**触发条件**：
- 存在最近 LVN level
- 当前价格在 LVN lower_bound 下方（已从上方向下跌破 LVN）
- Delta_30s < 0
- 若有历史窗口数据：
  - |Delta_30s| > 过去均值 |Delta| 的 1.2 倍
  - Volume_30s > 过去均值的 1.5 倍

**入场**：当前价格
**止损**：`max(LVN.upper_bound, entry_price + ATR_buffer)`
**止盈**：`entry_price - stop_distance * reward_risk`
**置信度**：0.65
**失效条件**：价格回升 LVN 上方 / Delta 转正

---

## 4. 时段限制

由 `signals/engine.py:21-33` 定义：

| 信号分类 | 包含 Setup | 允许时段 | 禁止时段 |
|---------|-----------|---------|---------|
| 趋势类 | Setup 1-6 | London, NY | Asia, Dead |
| 均值回归类 | Setup 7-8 | Asia, Dead | NY |

时段定义见 `config/default.yaml:33-40`：
- **Asia**：00:00–07:00 UTC
- **London**：07:00–12:30 UTC
- **NY**：12:30–20:00 UTC
- **Dead**：其余时间

配置项 `session_gating_enabled: true` 控制是否启用时段限制。

---

## 5. 禁止交易条件

由 `signals/engine.py:101-138` 实现，优先级高于所有 setup 评估。任一条件满足即禁止开新仓：

| 条件 | 代码常量 | 说明 |
|------|---------|------|
| 数据延迟过高 | `data_stale` | `exchange_lag_ms > max_data_lag_ms`（默认 2000ms） |
| 盘口价差过宽 | `spread_too_wide` | `spread > 5 分钟中位数 * 2` |
| WebSocket 不健康 | `websocket_stale` | WebSocket 延迟 > 1500ms |
| 资金费率结算 | `funding_blackout` | 结算前后 2 分钟 |
| 极端波动 | `extreme_volatility` | 1m ATR > 过去 20 根均值 * 3 |
| 熔断已触发 | `circuit_breaker_tripped` | 风控熔断激活 |
| 已有持仓 | `existing_position` | 同 symbol 已有未平仓头寸 |

---

## 6. 止损与止盈计算

### 6.1 动态止损（`signals/engine.py:537-571`）

```python
atr = max(ATR_1m_14, ATR_3m_14)
atr_buffer = atr_stop_mult * atr  # atr_stop_mult 默认 0.35

# Long:
adjusted_stop = min(structure_stop, entry_price - atr_buffer)

# Short:
adjusted_stop = max(structure_stop, entry_price + atr_buffer)
```

结构止损优于 ATR 止损时使用结构止损，否则使用 ATR 动态止损。

### 6.2 最小止损距离（成本保护）

```python
cost = entry_price * (2 * taker_fee + spread_slippage_estimate)  # 约 10 bps
min_stop_distance = cost * min_stop_cost_mult  # min_stop_cost_mult 默认 3.0
```

止损距离不能小于 `min_stop_distance`，防止手续费和滑点导致无意义交易。

### 6.3 初始止盈计算

```python
target_distance = stop_distance * reward_risk  # reward_risk 默认 5.0
min_target_distance = cost * min_target_cost_mult  # min_target_cost_mult 默认 2.0
# 取 max(target_distance, min_target_distance)
```

信号发出时的 `target_price` 是初始止盈线，使用固定 R:R 比例，但保证不低于最小目标距离（覆盖手续费）。

### 6.4 连续 1m K 动量止损上移/下移

入场后只统计开仓后的完整 1m K 线（开仓所在未完整 K 线不计入）。当动量连续成立时，止损只向保护盈利方向移动：

- Long：最近 3 根已完成 1m K 都收阳，止损至少上移到最近 2 根已完成 1m K 的最低 low；若当前止损已经更高则不回撤。
- Short：最近 3 根已完成 1m K 都收阴，止损至少下移到最近 2 根已完成 1m K 的最高 high；若当前止损已经更低则不回撤。

候选止损必须仍位于当前价格的保护侧：Long 候选止损低于当前价，Short 候选止损高于当前价。

### 6.5 R:R 验证

所有信号在发出前必须通过（`signals/engine.py:573-578`）：
```python
reward_risk = abs(target - entry) / abs(entry - stop)
if reward_risk < min_reward_risk:  # 默认 1.2
    return None  # 拒绝信号
```

---

## 7. 开仓与平仓流程

### 7.1 开仓流程

1. **Signal Engine** 产生 `TradeSignal`（含 entry/stop/target/reasons）
2. **Risk Engine** 校验（`risk/engine.py:19-38`）：
   - 账户级：日亏损上限、连续亏损次数
   - 信号级：止损距离有效、仓位量 > 最小下单量
3. **Execution Engine** 提交入场单
4. 入场成交后**立即**提交 `reduce-only` 止损单
5. 止盈线使用信号生成时的固定 `target_price`
6. 若止损提交失败 → 立即市价 `reduce-only` 平仓 + 触发熔断

### 7.2 平仓条件

| 方式 | 说明 |
|------|------|
| 止盈触发 | 价格触及 target_price，`reduce-only` 止盈单成交 |
| 止损触发 | 价格触及 stop_price，`reduce-only` 止损单成交 |
| Break-even shift | 持仓浮盈 > `break_even_trigger_r` * R（默认 2.5R），止损移至开仓均价 |
| 1m momentum stop shift | 多单连续 3 根完整 1m 阳线后上移止损；空单连续 3 根完整 1m 阴线后对称下移止损 |
| Absorption reduce | CVD/Delta 激增但价格不位移，判定为被动流动性吸收，强制减仓 |
| 持仓超时 | 持仓时间超过 `max_holding_ms`（默认 900s / 15 分钟），强制平仓 |
| 熔断平仓 | 熔断触发时保护性市价平仓 |

### 7.3 平仓后冷却

平仓后 `post_close_cooldown_ms`（默认 30s）内禁止同 symbol 开新仓。

---

## 8. 风控集成

### 8.1 账户限制（`risk/engine.py:47-55`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `risk_per_trade` | 0.25% | 单笔最大风险占权益比例 |
| `daily_loss_limit` | 1.0% | 单日最大亏损占权益比例 |
| `max_consecutive_losses` | 3 | 最大连续亏损次数 |

### 8.2 仓位计算（`risk/engine.py:40-45`）

```python
risk_amount = equity * risk_per_trade
quantity = risk_amount / stop_distance
max_quantity = (equity * max_symbol_notional_equity_multiple) / entry_price
quantity = min(quantity, max_quantity)
```

### 8.3 熔断条件

- 日亏损 >= `daily_loss_limit`
- 连续亏损 >= 3 笔
- 交易所 API 连续 3 次下单失败
- 止损单提交失败
- 本地与交易所仓位不一致
- WebSocket 断线 > 5s

熔断后立即停止开新仓，保护已有仓位。

---

## 9. 配置参考

核心策略参数汇总（`config/default.yaml`）：

```yaml
profile:
  value_area_ratio: 0.70         # 价值区占比
  execution_window_minutes: 30   # 主触发 profile
  micro_window_minutes: 15       # micro confirm profile
  context_window_minutes: 60     # 大结构参考 profile
  btc_bin_size: 20               # BTC 分箱 $20
  eth_bin_size: 5                # ETH 分箱 $5
  min_execution_profile_trades: 50
  min_micro_profile_trades: 25
  min_profile_bins: 3

signals:
  min_reward_risk: 1.2           # 最小 R:R
  session_gating_enabled: true   # 时段限制开关
  aggression_large_threshold: 20 # 攻击性气泡大单阈值 (BTC)
  aggression_block_threshold: 50 # 攻击性气泡巨单阈值 (BTC)

execution:
  reward_risk: 5.0               # 目标 R:R
  atr_stop_mult: 0.35            # ATR 止损系数
  kline_stop_shift_consecutive_bars: 3  # 动量止损需要的连续完整 1m K 数
  kline_stop_shift_reference_bars: 2    # 止损参考最近完整 1m K 数
  break_even_trigger_r: 2.5      # 保本止损触发 R 倍数
  min_stop_cost_mult: 1.0        # 最小止损 = 手续费 * 1
  min_target_cost_mult: 2.0      # 最小止盈 = 手续费 * 2
  max_holding_ms: 900000         # 最大持仓 15 分钟
  post_close_cooldown_ms: 30000  # 平仓后冷却 30s
```

---

## 10. 关键源文件映射

| 模块 | 文件 | 职责 |
|------|------|------|
| Profile 计算 | `profile/engine.py` | POC/VAH/VAL/HVN/LVN 计算 |
| 信号引擎 | `signals/engine.py` | 8 种 setup 评估、禁止条件 |
| 风控引擎 | `risk/engine.py` | 账户限制、仓位计算 |
| 熔断 | `risk/circuit.py` | 熔断状态机 |
| 执行引擎 | `execution/paper_engine.py` | 纸单执行、订单管理 |
| 实时数据 | `web/live_store.py` | 实时成交窗口、信号触发 |
| 配置 | `config/default.yaml` | 所有策略默认参数 |
| 类型定义 | `types.py` | ProfileLevel、TradeSignal、RiskDecision |

---

## 11. 维护约定

1. **策略逻辑改动**（新增/修改/删除 setup、调整禁止条件、修改止损止盈算法）→ 必须更新本文档对应章节
2. **参数默认值改动**（`config/default.yaml` 中的策略参数）→ 必须更新第 9 节
3. **Level 计算算法改动**（`profile/engine.py`）→ 必须更新第 2.2 节
4. **时段划分改动** → 必须更新第 4 节
5. Code review 时对照本文档验证代码实现

---

## 12. 实盘状态机 Pipeline 更新

当前信号逻辑已经从“固定 8 个 setup 顺序扫描”升级为状态机 pipeline：

```text
MarketStateEngine
  -> BiasEngine
  -> SetupCandidateEngine
  -> ConfirmationGate
  -> TradePlanBuilder
  -> RiskEngine
  -> PaperTradingEngine
```

`SignalEngine.evaluate(...)` 对外接口保持兼容，但内部只负责 orchestration。每次评估会保留 `last_trace` 和 `last_reject_reasons`，用于复盘当前为什么允许、拒绝或没有交易。

### 12.1 市场状态

`signals/market_state.py` 将行情归类为：

- `balanced`：价值区内反复成交，POC 附近吸附。
- `imbalanced_up` / `imbalanced_down`：价格在 VAH 上方或 VAL 下方被接受，订单流同向。
- `compression`：完整 1m K range 收缩且靠近关键位。
- `absorption`：大 delta 或大单成交但价格位移不足。
- `failed_auction`：刺破 VAH/VAL/HVN/LVN 后在确认窗口内收回。
- `no_trade`：数据、spread、profile、资金费率或状态冲突不允许开仓。

### 12.2 Bias 与 4 类实盘模型

`signals/bias.py` 只输出方向叙事：`long`、`short` 或 `neutral`。Bias 不能直接开仓，只决定哪些 candidate 可以进入确认层。

旧 setup 名称作为 `legacy_setup` 保留，实盘模型收敛为：

| 实盘模型 | 用途 |
|------|------|
| `squeeze_continuation` | value 外接受后的延续突破，要求反向主动盘失败和 1m 收盘确认 |
| `failed_auction_reversal` | 刺破关键位后无法延续并收回 value/level 内侧 |
| `lvn_acceptance` | 穿越 LVN 后站稳外侧，delta/volume 同向 |
| `absorption_response` | 大成交无位移，作为反向 candidate 或持仓减仓/退出条件 |

### 12.3 Confirmation Gate

所有实盘 setup 默认必须通过完整 1m K 收盘确认。Long 条件为最近完整 1m close 高于 trigger + buffer，`delta_30s > 0`，`volume_30s` 放大，触发到确认的位移达到 ATR 阈值，且确认后没有快速跌回 trigger 下方。Short 完全对称。

这意味着 tick 刺破不再直接产生信号；没有完整 1m close 时会记录 `candle_close_not_confirmed`。

### 12.4 结构优先交易计划

`signals/trade_plan.py` 优先使用前方结构目标：`context_60m` 的 POC/HVN/VAH/VAL，其次 `execution_30m`，再到 session high/low 或前高前低。没有可用结构目标时才回退到 capped R multiple。

止损优先放在 failed auction high/low、LVN/HVN/value edge 或确认 1m K high/low 的保护侧，ATR buffer 只做补充保护。

### 12.5 Setup-aware Paper 管理

Paper position 保存 `setup_model` 和 `management_profile`：

- `squeeze_continuation`：1.25R 触发保本，突破后无 follow-through 会超时退出。
- `failed_auction_reversal`：到 POC/value mid 优先兑现，重新突破失败点则退出。
- `lvn_acceptance`：回到 LVN 内侧退出，前方 HVN/POC 优先止盈。
- `absorption_response`：反向 delta 放大但无位移时减仓或退出。

Dashboard 和 backtest 现在会输出 `market_state`、`bias`、`setup_model`、`target_source` 和 `last_reject_reasons`，回测报告按 `setup_model|market_state|session` 聚合。
