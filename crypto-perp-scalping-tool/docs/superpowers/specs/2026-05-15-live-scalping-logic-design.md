# Live Scalping Logic Design

## Goal

把 `SignalEngine.evaluate(...) -> TradeSignal | None` 从固定 setup 顺序扫描升级为实盘状态机 pipeline。新流程先判断市场状态和方向叙事，再在关键位置等待触发、完整 1m K 收盘确认、订单流位移确认，最后生成结构化交易计划并交给风控与 paper execution。

默认范围仍然是 paper/live dashboard/backtest 逻辑，不新增真实下单能力。`mode=paper` 继续作为默认安全模式。

## Pipeline

```text
MarketStateEngine
  -> BiasEngine
  -> SetupCandidateEngine
  -> ConfirmationGate
  -> TradePlanBuilder
  -> RiskEngine
  -> PaperTradingEngine
```

`SignalEngine` 对外接口保持兼容，内部作为 orchestrator。每次评估都会生成或保留 `SignalTrace`，用于复盘：

- `market_state`: `balanced`, `imbalanced_up`, `imbalanced_down`, `compression`, `absorption`, `failed_auction`, `no_trade`
- `bias`: `long`, `short`, `neutral`
- `location`: 当前价格相对 VAH/VAL/POC/HVN/LVN/VWAP/session high-low 的描述
- `trigger`: candidate 触发事件
- `confirmation`: 1m close、delta、volume、displacement、reclaim 检查结果
- `trade_plan`: entry、stop、target、target source、R:R、management profile
- `reject_reasons`: 未产生信号的原因

## Market State

`signals/market_state.py` 输出 `MarketStateResult`。

状态定义：

- `balanced`: 价格在 value area 内，ATR/成交量不扩张，POC 附近反复成交。
- `imbalanced_up`: 价格在 VAH 上方接受，delta 同向，回踩不回 value。
- `imbalanced_down`: 价格在 VAL 下方接受，delta 同向，反弹不回 value。
- `compression`: 最近 N 根完整 1m K range 收缩，成交量下降，价格靠近关键位。
- `absorption`: 主动买/卖显著增加，但价格位移低于 ATR 阈值。
- `failed_auction`: 突破 VAH/VAL/HVN/LVN 后，在确认窗口内收回。
- `no_trade`: 数据不健康、spread 扩张、极端波动、profile 样本不足、资金费率黑窗或状态冲突。

默认参数在 `config/default.yaml` 的 `market_state` 节中维护：

```yaml
market_state:
  compression_bars: 5
  compression_range_ratio: 0.65
  absorption_delta_ratio: 2.0
  absorption_max_displacement_atr: 0.25
  failed_auction_window_seconds: 90
  value_acceptance_close_bars: 2
```

## Bias

`signals/bias.py` 输出 `BiasResult`。

规则：

- `long`: 价格在 VAH 或 VWAP 上方，且市场状态支持上行接受、上行压缩突破、卖方吸收后的上行响应或下破失败回收。
- `short`: 价格在 VAL 或 VWAP 下方，且市场状态支持下行接受、下行压缩突破、买方吸收后的下行响应或上破失败回收。
- `neutral`: 价格在 POC 附近、状态为 `balanced`、或者位置与订单流冲突。

Bias 只控制 candidate eligibility，不直接生成交易信号。

## Setup Models

旧 8 个 setup 收敛为 4 类实盘模型，旧名称保存在 `legacy_setup`，用于历史报表兼容。

| Setup model | 适用情境 | 旧 setup 映射 |
| --- | --- | --- |
| `squeeze_continuation` | value 外接受后，反向主动盘失败并突破关键高/低点 | `vah_breakout_lvn_pullback_aggression`, `val_breakdown_lvn_pullback_aggression` |
| `failed_auction_reversal` | 刺破 VAH/VAL/HVN/LVN 后无法延续，1m close 回到 level/value 内侧 | `cvd_divergence_failed_breakout`, `cvd_divergence_failed_breakdown`, `hvn_val_failed_breakdown`, `hvn_vah_failed_breakout` |
| `lvn_acceptance` | 穿越 LVN 后没有快速回到 LVN 内，1m close 站稳外侧 | `lvn_break_acceptance`, `lvn_breakdown_acceptance` |
| `absorption_response` | 大 delta/大单成交但位移不足 | 新增响应模型，用于反向 candidate 或持仓减仓/退出 |

Candidate 只包含方向、触发价、位置、所需确认和兼容字段，不包含最终 target。

## Confirmation Gate

`signals/confirmation.py` 输出 `ConfirmationResult`。默认所有实盘 setup 都必须通过：

```yaml
confirmation:
  require_1m_close: true
  close_buffer_bps: 0.36
  max_reclaim_seconds: 20
  min_displacement_atr: 0.15
  min_delta_ratio: 1.2
  min_volume_ratio: 1.3
```

Long 必须满足：

- 最近完整 1m K close > trigger + buffer。
- `delta_30s > 0` 且大于历史均值阈值。
- `volume_30s` 大于历史均值阈值。
- 从触发到确认的价格位移 >= `min_displacement_atr * ATR`。
- 确认后未在 reclaim 窗口内跌回 trigger 下方。

Short 完全对称。tick 刺破但 1m 未收盘，不允许开仓。

## Trade Plan

`signals/trade_plan.py` 输出最终 `TradeSignal` 或 reject。

目标计算结构优先：

1. `context_60m` 前方 POC/HVN/VAH/VAL。
2. `execution_30m` 前方 POC/HVN/VAH/VAL。
3. session high/low 或前高/前低。
4. 没有可用结构目标时，使用 capped R multiple。

止损计算优先级：

1. failed auction high/low 外侧。
2. LVN/HVN/value edge 外侧。
3. 确认 1m K high/low 外侧。
4. ATR buffer 只做补充保护。

默认参数：

```yaml
trade_plan:
  min_reward_risk: 1.2
  fallback_reward_risk: 3.0
  max_reward_risk: 6.0
  structure_target_first: true
  atr_stop_mult: 0.35
```

如果 candidate 明确指定结构目标且 R:R 不足，拒绝并记录 `structure_reward_risk_too_low`。自动发现的过近结构目标可回退到 capped R，避免样本不足时整段回放无信号。

## Position Management

`PaperTradingEngine` 保存 `setup_model` 和 `management_profile`，并按 setup 使用不同管理规则：

```yaml
management:
  squeeze_break_even_r: 1.25
  failed_auction_break_even_r: 1.5
  lvn_acceptance_break_even_r: 1.5
  first_structure_reduce_ratio: 0.5
  absorption_reduce_ratio: 0.5
  no_followthrough_seconds: 45
```

规则：

- `squeeze_continuation`: 快速验证；突破后无 follow-through 则退出，1.25R 或第一结构位触发保护。
- `failed_auction_reversal`: 到 POC/value mid 优先兑现，若再次突破失败点则退出。
- `lvn_acceptance`: 回到 LVN 内侧退出，前方 HVN/POC 优先止盈。
- `absorption_response`: 不追高 R；反向 delta + 无位移时减仓或退出。

## Reporting And Dashboard

- `/api/orderflow` summary 暴露 `market_state`, `bias`, `last_reject_reasons`。
- signal/order/closed position 记录追加 `setup_model`, `legacy_setup`, `market_state`, `bias`, `target_source`, `management_profile`。
- backtest report 增加 `by_strategy_context`，按 `setup_model|market_state|session` 聚合信号数、交易数、胜数和净 PnL。

## Safety

- 默认 paper-first，不新增 live order。
- RiskEngine 仍然是交易前置门。
- 资金费率黑窗、数据延迟、spread 扩张、极端波动、已有仓位、熔断状态优先于所有 setup。
- 所有交易决策必须能通过 trace/reject reason 复盘。
