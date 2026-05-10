const els = {
  symbol: document.getElementById("symbolSelect"),
  refresh: document.getElementById("refreshButton"),
  lastPrice: document.getElementById("lastPrice"),
  lastPriceMeta: document.getElementById("lastPriceMeta"),
  cumDelta: document.getElementById("cumDelta"),
  signals: document.getElementById("signals"),
  signalsSplit: document.getElementById("signalsSplit"),
  orders: document.getElementById("orders"),
  ordersSplit: document.getElementById("ordersSplit"),
  closed: document.getElementById("closed"),
  closedSplit: document.getElementById("closedSplit"),
  pnl: document.getElementById("pnl"),
  pnlSplit: document.getElementById("pnlSplit"),
  currentPosition: document.getElementById("currentPosition"),
  currentPositionMeta: document.getElementById("currentPositionMeta"),
  signalReasons: document.getElementById("signalReasons"),
  rejectReasons: document.getElementById("rejectReasons"),
  dataLag: document.getElementById("dataLag"),
  lastTradeTime: document.getElementById("lastTradeTime"),
  connection: document.getElementById("connection"),
  sourceLabel: document.getElementById("sourceLabel"),
  tradeCount: document.getElementById("tradeCount"),
  priceCanvas: document.getElementById("priceCanvas"),
  deltaCanvas: document.getElementById("deltaCanvas"),
  profile: document.getElementById("profileLevels"),
  tape: document.getElementById("tapeBody"),
  detailPanel: document.getElementById("detailPanel"),
  detailTitle: document.getElementById("detailTitle"),
  detailSubtitle: document.getElementById("detailSubtitle"),
  detailClose: document.getElementById("detailClose"),
  detailBody: document.getElementById("detailBody"),
  rangeTabs: document.querySelector(".range-tabs")
};

const colors = {
  grid: "#333333",
  text: "#aaa69b",
  price: "#4fb6d8",
  buy: "#36c98a",
  sell: "#ef5b5b",
  warn: "#e7b84b"
};

const REFRESH_INTERVAL_MS = 2000;
const modeLabels = {
  paper: "Paper / 模拟",
  live: "Live / 实盘"
};
const detailConfig = {
  signals: { title: "Signals / 信号明细", subtitle: "策略触发记录，按模拟和实盘分组" },
  orders: { title: "Orders / 订单明细", subtitle: "通过风控后的订单记录，按模拟和实盘分组" },
  closed_positions: { title: "Closed / 平仓明细", subtitle: "已完成平仓记录，按模拟和实盘分组" },
  pnl: { title: "PnL / 区间盈亏", subtitle: "按所选区间统计已实现盈亏，区分模拟和实盘" }
};

let latestDashboard = null;
let isLoading = false;
let activeDetail = "signals";
let activeRange = "24h";

async function loadDashboard() {
  if (isLoading) return;
  isLoading = true;
  try {
    const query = new URLSearchParams({
      symbol: els.symbol.value,
      t: Date.now().toString()
    });
    const response = await fetch(`/api/orderflow?${query.toString()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Dashboard API returned ${response.status}`);
    const data = await response.json();
    latestDashboard = data;
    renderSummary(data.summary);
    drawPrice(els.priceCanvas, data.trades, data.markers);
    drawDelta(els.deltaCanvas, data.delta_series);
    renderProfile(data.profile_levels);
    renderTape(data.trades.slice(-12).reverse());
    if (!els.detailPanel.classList.contains("is-hidden")) renderDetailPanel();
  } catch (error) {
    els.connection.textContent = "error";
    els.connection.title = error.message;
  } finally {
    isLoading = false;
  }
}

function renderSummary(summary) {
  const breakdown = summary.mode_breakdown || emptyBreakdown();
  els.lastPrice.textContent = formatNumber(summary.last_price);
  els.lastPrice.title = summary.price_source ? `price source: ${summary.price_source}` : "";
  els.lastPriceMeta.textContent = `Perp ${formatNumber(summary.last_trade_price)} / Mark ${formatNumber(summary.mark_price)} / Index ${formatNumber(summary.index_price)} / Mid ${formatNumber(summary.quote_mid_price)}`;
  els.lastPriceMeta.title = `Spot ${formatNumber(summary.spot_last_price)} / Bid ${formatNumber(summary.bid_price)} / Ask ${formatNumber(summary.ask_price)}`;
  els.cumDelta.textContent = formatNumber(summary.cumulative_delta);
  els.signals.textContent = formatNumber(summary.signals);
  els.signalsSplit.textContent = splitLabel(breakdown, "signals");
  els.orders.textContent = formatNumber(summary.orders);
  els.ordersSplit.textContent = splitLabel(breakdown, "orders");
  els.closed.textContent = formatNumber(summary.closed_positions);
  els.closedSplit.textContent = splitLabel(breakdown, "closed_positions");
  els.pnl.textContent = formatNumber(summary.pnl_24h);
  els.pnl.className = summary.pnl_24h >= 0 ? "buy" : "sell";
  els.pnlSplit.textContent = `模拟 ${formatNumber(breakdown.paper.pnl_24h)} / 实盘 ${formatNumber(breakdown.live.pnl_24h)}`;
  renderPosition(summary.open_position);
  els.signalReasons.textContent = reasonText(summary.signal_reasons);
  els.rejectReasons.textContent = reasonText(summary.reject_reasons);
  els.dataLag.textContent = `${formatNumber(summary.data_lag_ms)} ms`;
  els.lastTradeTime.textContent = formatTimestamp(summary.last_trade_time);
  els.connection.textContent = summary.connection_status || summary.source || "csv";
  els.connection.title = summary.connection_message || "";
  els.sourceLabel.textContent = summary.source === "binance"
    ? "Binance Spot + Futures / 币安现货与永续行情"
    : "CSV Market Replay / CSV 行情回放";
  els.tradeCount.textContent = summary.profile_trade_count
    ? `${summary.trade_count} shown / ${summary.profile_trade_count} profiled`
    : `${summary.trade_count} trades`;
}

function renderPosition(position) {
  if (!position) {
    els.currentPosition.textContent = "flat";
    els.currentPositionMeta.textContent = "无持仓";
    els.currentPosition.className = "";
    return;
  }
  els.currentPosition.textContent = `${position.side} ${formatNumber(position.quantity)}`;
  els.currentPosition.className = position.side === "long" ? "buy" : "sell";
  els.currentPositionMeta.textContent = `Entry ${formatNumber(position.entry_price)} / SL ${formatNumber(position.stop_price)} / TP ${formatNumber(position.target_price)}`;
}

function renderDetailPanel() {
  if (!latestDashboard) return;
  const config = detailConfig[activeDetail];
  els.detailTitle.textContent = config.title;
  els.detailSubtitle.textContent = config.subtitle;
  els.rangeTabs.classList.toggle("is-hidden", activeDetail !== "pnl");
  document.querySelectorAll("[data-range]").forEach(button => {
    button.classList.toggle("is-active", button.dataset.range === activeRange);
  });
  els.detailBody.innerHTML = ["paper", "live"].map(mode => renderModeDetail(mode)).join("");
}

function renderModeDetail(mode) {
  const detail = latestDashboard.details?.[mode] || emptyDetail();
  const records = activeDetail === "pnl" ? detail.pnl_events : detail[activeDetail];
  const value = activeDetail === "pnl" ? detail.pnl_by_range[activeRange] : records.length;
  const valueClass = activeDetail === "pnl" && value < 0 ? "sell" : "buy";
  return `<section class="detail-column">
    <div class="detail-column-head">
      <span>${modeLabels[mode]}</span>
      <strong class="${valueClass}">${formatNumber(value)}</strong>
    </div>
    ${renderRecordTable(activeDetail, records.slice(-8).reverse())}
  </section>`;
}

function renderRecordTable(kind, records) {
  if (!records.length) return `<p class="empty-state">暂无记录</p>`;
  const rows = records.map(record => recordRow(kind, record)).join("");
  return `<div class="table-wrap detail-table"><table>${recordHeader(kind)}<tbody>${rows}</tbody></table></div>`;
}

function recordHeader(kind) {
  if (kind === "signals") return "<thead><tr><th>Time / 时间</th><th>Side / 方向</th><th>Setup / 形态</th><th>Entry / 入场</th><th>Reasons / 原因</th></tr></thead>";
  if (kind === "orders") return "<thead><tr><th>Time / 时间</th><th>Side / 方向</th><th>Qty / 数量</th><th>Entry / 入场</th><th>Stop / 止损</th><th>Target / 止盈</th><th>Fee / 手续费</th></tr></thead>";
  if (kind === "closed_positions") return "<thead><tr><th>Time / 时间</th><th>Side / 方向</th><th>Entry / 入场</th><th>Close / 平仓</th><th>PnL / 盈亏</th></tr></thead>";
  return "<thead><tr><th>Time / 时间</th><th>Side / 方向</th><th>PnL / 盈亏</th></tr></thead>";
}

function recordRow(kind, record) {
  if (kind === "signals") {
    return `<tr><td>${formatTimestamp(record.timestamp)}</td><td>${record.side || "--"}</td><td>${record.setup || "--"}</td><td>${formatNumber(record.entry_price)}</td><td>${reasonText(record.reasons)}</td></tr>`;
  }
  if (kind === "orders") {
    return `<tr><td>${formatTimestamp(record.timestamp)}</td><td>${record.side || "--"}</td><td>${formatNumber(record.quantity)}</td><td>${formatNumber(record.entry_price)}</td><td>${formatNumber(record.stop_price)}</td><td>${formatNumber(record.target_price)}</td><td>${formatNumber(record.fee)}</td></tr>`;
  }
  if (kind === "closed_positions") {
    const pnl = record.net_realized_pnl ?? record.realized_pnl;
    const pnlClass = pnl >= 0 ? "buy" : "sell";
    return `<tr><td>${formatTimestamp(record.timestamp)}</td><td>${record.side || "--"}</td><td>${formatNumber(record.entry_price)}</td><td>${formatNumber(record.close_price)}</td><td class="${pnlClass}">${formatNumber(pnl)}</td></tr>`;
  }
  const pnlClass = record.realized_pnl >= 0 ? "buy" : "sell";
  return `<tr><td>${formatTimestamp(record.timestamp)}</td><td>${record.side || "--"}</td><td class="${pnlClass}">${formatNumber(record.realized_pnl)}</td></tr>`;
}

function drawPrice(canvas, trades, markers) {
  const ctx = setupCanvas(canvas);
  if (!trades.length) return;
  const prices = trades.map(t => t.price);
  const scale = makeScale(prices, canvas.width, canvas.height, 26);
  drawGrid(ctx, canvas);
  drawYAxis(ctx, canvas, scale, prices);
  ctx.strokeStyle = colors.price;
  ctx.lineWidth = 2;
  ctx.beginPath();
  trades.forEach((trade, index) => {
    const x = scale.x(index, trades.length);
    const y = scale.y(trade.price);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  markers.forEach(marker => {
    const x = scale.x(marker.index ?? 0, trades.length);
    const y = scale.y(marker.price);
    ctx.fillStyle = marker.type === "signal" ? colors.warn : colors.buy;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = colors.text;
    ctx.fillText(marker.label || marker.type, Math.min(x + 8, canvas.width - 120), Math.max(y - 8, 14));
  });
}

function drawDelta(canvas, series) {
  const ctx = setupCanvas(canvas);
  if (!series.length) return;
  const values = series.map(point => point.cumulative_delta);
  const scale = makeScale(values, canvas.width, canvas.height, 22);
  drawGrid(ctx, canvas);
  drawYAxis(ctx, canvas, scale, values);
  const zeroY = scale.y(0);
  ctx.strokeStyle = colors.grid;
  ctx.beginPath();
  ctx.moveTo(0, zeroY);
  ctx.lineTo(canvas.width, zeroY);
  ctx.stroke();

  ctx.strokeStyle = values[values.length - 1] >= 0 ? colors.buy : colors.sell;
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((point, index) => {
    const x = scale.x(index, series.length);
    const y = scale.y(point.cumulative_delta);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderProfile(levels) {
  const maxStrength = Math.max(...levels.map(level => level.strength), 1);
  const colorFor = { POC: colors.warn, HVN: colors.buy, LVN: colors.sell, VAH: colors.price, VAL: colors.price };
  els.profile.innerHTML = levels
    .sort((a, b) => b.price - a.price)
    .map(level => {
      const width = Math.max(8, Math.round((level.strength / maxStrength) * 100));
      const color = colorFor[level.type] || colors.price;
      return `<div class="level-row">
        <span class="badge" style="background:${color}">${level.type}</span>
        <span class="bar"><span style="width:${width}%;background:${color}"></span></span>
        <span class="price">${formatNumber(level.price)}</span>
      </div>`;
    })
    .join("");
}

function renderTape(trades) {
  els.tape.innerHTML = trades.map(trade => {
    const klass = trade.side === "buy" ? "buy" : "sell";
    return `<tr>
      <td>${formatTapeTimestamp(trade.timestamp)}</td>
      <td class="${klass}">${trade.side}</td>
      <td>${formatNumber(trade.price)}</td>
      <td>${formatNumber(trade.quantity)}</td>
      <td class="${trade.delta >= 0 ? "buy" : "sell"}">${formatNumber(trade.delta)}</td>
    </tr>`;
  }).join("");
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width));
  canvas.height = Math.max(1, Math.floor(rect.height || Number(canvas.getAttribute("height"))));
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.font = "12px Segoe UI, Arial";
  return ctx;
}

function makeScale(values, width, height, pad) {
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  return {
    x: (index, count) => pad + (index / Math.max(count - 1, 1)) * (width - pad * 2),
    y: value => height - pad - ((value - min) / span) * (height - pad * 2)
  };
}

function drawGrid(ctx, canvas) {
  ctx.strokeStyle = colors.grid;
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = (canvas.height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(54, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  }
}

function drawYAxis(ctx, canvas, scale, values) {
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const ticks = [max, min + (max - min) * 0.5, min];
  ctx.fillStyle = colors.text;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ticks.forEach(value => {
    const y = scale.y(value);
    ctx.fillText(formatAxisValue(value), 8, Math.min(Math.max(y, 12), canvas.height - 12));
  });
}

function splitLabel(breakdown, key) {
  return `模拟 ${formatNumber(breakdown.paper[key])} / 实盘 ${formatNumber(breakdown.live[key])}`;
}

function reasonText(reasons) {
  if (!reasons || !reasons.length) return "--";
  return Array.isArray(reasons) ? reasons.join(" / ") : String(reasons);
}

function emptyBreakdown() {
  return {
    paper: { signals: 0, orders: 0, closed_positions: 0, pnl_24h: 0 },
    live: { signals: 0, orders: 0, closed_positions: 0, pnl_24h: 0 }
  };
}

function emptyDetail() {
  return {
    signals: [],
    orders: [],
    closed_positions: [],
    pnl_events: [],
    pnl_by_range: { "24h": 0, "7d": 0, "30d": 0, all: 0 }
  };
}

function formatTimestamp(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const number = Number(value);
  if (number < 946684800000) return number.toString();
  return new Date(number).toLocaleString();
}

function formatTapeTimestamp(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const number = Number(value);
  if (number < 946684800000) return number.toString();
  return new Date(number).toLocaleTimeString(undefined, { hour12: false });
}

function formatAxisValue(value) {
  const abs = Math.abs(value);
  if (abs >= 1000) return Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 10) return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

els.refresh.addEventListener("click", loadDashboard);
els.symbol.addEventListener("change", loadDashboard);
els.detailClose.addEventListener("click", () => {
  els.detailPanel.classList.add("is-hidden");
});
document.querySelectorAll("[data-detail]").forEach(element => {
  element.addEventListener("click", () => {
    activeDetail = element.dataset.detail;
    els.detailPanel.classList.remove("is-hidden");
    renderDetailPanel();
  });
});
document.querySelectorAll("[data-range]").forEach(element => {
  element.addEventListener("click", () => {
    activeRange = element.dataset.range;
    activeDetail = "pnl";
    els.detailPanel.classList.remove("is-hidden");
    renderDetailPanel();
  });
});
window.addEventListener("resize", loadDashboard);
loadDashboard();
setInterval(loadDashboard, REFRESH_INTERVAL_MS);
