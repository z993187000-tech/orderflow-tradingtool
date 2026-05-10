# Zeabur 部署准备

## 当前推荐部署形态

在 Zeabur 上先运行 Web Dashboard + Binance public market data。当前服务只连接 Binance 公开 WebSocket 行情，不执行真实下单。

默认启动命令由 Dockerfile 提供：

```sh
crypto-tool web serve --source ${WEB_SOURCE:-binance} --symbol ${SYMBOL:-BTCUSDT} --host 0.0.0.0 --port ${PORT:-8080} --paper-journal ${PAPER_JOURNAL:-data/live-paper.jsonl}
```

## Zeabur 操作步骤

1. 把仓库推到 GitHub。
2. 在 Zeabur 新建 Project。
3. 选择从 GitHub repository 部署。
4. 如果 Zeabur 让你选择 Root Directory，选择：

```text
crypto-perp-scalping-tool
```

5. 部署方式选择 Dockerfile。
6. 配置环境变量：

```text
WEB_SOURCE=binance
SYMBOL=BTCUSDT
PORT=8080
PASSWORD=你的强密码
PAPER_JOURNAL=data/live-paper.jsonl
```

7. 部署完成后打开 Zeabur 分配的域名。
8. 健康检查地址：

```text
/healthz
```

## 环境变量说明

- `WEB_SOURCE`：`binance` 或 `csv`。Zeabur 默认建议 `binance`。
- `SYMBOL`：默认 `BTCUSDT`，也可改成 `ETHUSDT`。
- `PORT`：Zeabur 通常会注入端口；Dockerfile 默认 `8080`。
- `PASSWORD`：公网 Web Dashboard 的访问密码。浏览器弹出登录框时，用户名可填 `admin` 或任意值，密码填这里配置的值。
- `PAPER_JOURNAL`：实时 paper 交易日志基础路径。服务会按 symbol 拆成 `data/live-paper-btcusdt.jsonl` 和 `data/live-paper-ethusdt.jsonl`。

## 公网访问

Zeabur 分配的 `https://*.zeabur.app` 域名可以被其他网络访问，例如手机流量、公司网络、家里 Wi-Fi 都可以打开。

当前项目会把 `/healthz` 保持为公开接口，用于 Zeabur 健康检查；Web 页面和 `/api/orderflow` 会在设置 `PASSWORD` 后要求 Basic Auth 登录。

你的当前公网地址示例：

```text
https://orderflow-tradingtool.zeabur.app/
```

健康检查地址：

```text
https://orderflow-tradingtool.zeabur.app/healthz
```

## 当前模块状态

- Web Dashboard：可运行，支持手机和桌面浏览器。
- Binance WebSocket：已接入 USDⓈ-M Futures `aggTrade`。
- Live paper auto trading：Zeabur live market dashboard 会运行自动 paper 策略闭环，能从 Binance 公开行情生成 paper signal、paper order、paper close 和 paper PnL，并写入 jsonl journal；服务重启时会从 journal 恢复 paper orders、open position、closed position 和 realized PnL，但仍不会向交易所发送真实订单。
- CSV replay：保留，用于测试和复盘。
- Paper runner：可从 CSV 生成 signal、paper fill、position close、PnL。
- Telegram command handler：有命令边界，但还没有联网 long polling worker。
- Live trading execution：未接入，当前 Zeabur 部署不执行真实下单。

## 部署前检查

本地执行：

```powershell
python -m pip install -e .
python -m unittest discover -s tests/unit
python -m crypto_perp_tool.cli web serve --source binance --symbol BTCUSDT --mobile --port 8000
```

确认浏览器能打开后再推送到 GitHub。

## 注意事项

- 当前 Binance WebSocket 使用公开行情，不需要 API key。
- 如果服务暴露到公网，必须设置 `PASSWORD`，不要让交易观察面板裸奔。
- jsonl journal 能恢复进程重启后的 paper 状态；如果 Zeabur 服务没有挂载持久化 Volume，重新部署、迁移或实例替换仍可能让容器文件丢失。长期 paper 验收建议配置 Volume 或后续接入 PostgreSQL。
- Zeabur 适合 paper/live-market 观察和 Web 面板。
- 后续若接真实自动下单，交易核心建议迁移到更可控的 VPS，并启用固定 IP、交易所 API key IP 白名单和独立监控。
