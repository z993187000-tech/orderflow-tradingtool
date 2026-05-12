# Gap Remediation Log — 2026-05-12

本轮完善了项目中标记为缺失或未完成的模块，以下是已完成的工作和待处理的后续 issue。

## 已完成

### Issue GAP-1: Replay 模块缺失 ✅

**问题**: `replay/__init__.py` 仅包含一行 docstring，技术规格要求的 tick/aggTrade 回放能力未实现。

**解决**: 新增 `replay/engine.py`，实现 `ReplayEngine` 类：
- 读取 JSONL journal，提取已记录的 signal
- 将 trade event 重新注入 profile/signal pipeline
- 比较重放信号与原始记录的匹配度
- 输出 `ReplayReport`（total/matched/missed/extra）
- 支持时间范围过滤
- `replay/__init__.py` 更新导出
- CLI 新增 `crypto-tool replay run --journal <path> --csv <path>` 命令

**新增文件**:
- `src/crypto_perp_tool/replay/engine.py`
- `tests/unit/test_replay_engine.py`

**后续 Issue**: ReplayEngine 目前只比较 signal 数量和类型，未比较 entry/stop/target 价格的精确度。下一轮应加入价格偏差容忍度比较。

---

### Issue GAP-2: Telegram Bot 未接通长轮询 ✅

**问题**: `telegram_bot.py` 有 `TelegramCommandHandler` 但缺少长轮询循环，无法实际接收和响应 Telegram 消息。

**解决**: 新增 `TelegramPoller` 类：
- 使用 Telegram Bot API `getUpdates` 长轮询
- 零外部依赖（仅用 `urllib.request`）
- 自动错误重试，连续失败达到上限后停止
- 独立 daemon 线程运行
- `start()` / `stop()` / `is_running()` 生命周期管理
- 新增 `parse_allowed_chat_ids()` 辅助函数

**修改文件**:
- `src/crypto_perp_tool/telegram_bot.py`
- `tests/unit/test_telegram_poller.py`

**后续 Issue**: 当前只响应 text message，未处理 inline keyboard、callback query。需要扩展 `/positions`（未实现）和 `/circuit`、`/resume` 命令。

---

### Issue GAP-3: PositionReconciler 未实现 ✅

**问题**: 技术规格和 Issue 3 要求仓位对账，但 `execution/` 目录只有 `paper_engine.py`。

**解决**: 新增 `execution/reconciler.py`，实现 `PositionReconciler` 类：
- 维护本地仓位状态（`set_local_position` / `get_local_position`）
- `reconcile()` 方法比较本地和交易所仓位
- 检测四种异常状态：MISMATCH / MISSING_PROTECTION / EXCHANGE_ONLY / LOCAL_ONLY
- 不一致时自动暂停新开仓
- `_has_stop_order()` 检查 reduce-only 止损是否存在
- `summary()` 返回对账状态快照

**新增文件**:
- `src/crypto_perp_tool/execution/reconciler.py`
- `tests/unit/test_reconciler.py`
- `src/crypto_perp_tool/execution/__init__.py` 更新导出

**后续 Issue**: paper 模式下对账使用空 exchange 数据。实盘模式需要接入 Binance REST API 获取真实仓位和订单（`GET /fapi/v2/positionRisk` 和 `GET /fapi/v1/openOrders`）。

---

### Issue GAP-4: 参数版本管理缺失 ✅

**问题**: Issue 10 指出策略参数可能过拟合，但没有参数版本记录机制。

**解决**: 
- `Settings` dataclass 新增 `config_version` 字段（SHA-256 前 12 位）
- `default_settings()` 和 `load_settings()` 自动计算参数哈希
- 只对 strategy-critical 参数（risk/execution/profile/signals）计算哈希
- 版本稳定：相同参数产生相同哈希

**修改文件**:
- `src/crypto_perp_tool/config.py`
- `tests/unit/test_config_version.py`

**后续 Issue**: `config_version` 尚未自动写入 journal 和 backtest report。需要在 journal 写入时附带当前 config_version，并在 backtest report 中展示参数快照。

---

## 测试覆盖

本轮新增测试文件:

| 测试文件 | 测试数 | 覆盖范围 |
|----------|--------|---------|
| `test_replay_engine.py` | 4 | journal 加载、空数据、时间范围过滤、报告结构 |
| `test_telegram_poller.py` | 8 | poller 启停、token 验证、错误上限、消息处理、chat_id 解析 |
| `test_reconciler.py` | 11 | 仓位增删、对账状态、数量不匹配、止损缺失、多仓位 |
| `test_config_version.py` | 5 | 版本存在性、稳定性、默认/加载一致性 |

完整测试套件: **203 项全部通过**

## 待处理 Issue（本轮发现）

1. **GAP-NEXT-1**: ReplayEngine 增加价格偏差容忍度比较（entry/stop/target 价格）
2. **GAP-NEXT-2**: TelegramPoller 支持 `/positions`、`/circuit`、`/resume` 命令
3. **GAP-NEXT-3**: config_version 自动写入 journal 和 backtest report
4. **GAP-NEXT-4**: PositionReconciler 接入 Binance REST API 进行真实仓位对账
5. **GAP-NEXT-5**: LiveOrderflowStore 集成 PositionReconciler 定期对账（每 2 秒）
6. **GAP-NEXT-6**: 多币种并发交易（当前 config 支持 BTCUSDT+ETHUSDT，但 LiveOrderflowStore 单实例只处理一个币种）
7. **GAP-NEXT-7**: PostgreSQL/Redis 持久化集成（当前 journal 仅 JSONL 文件）
