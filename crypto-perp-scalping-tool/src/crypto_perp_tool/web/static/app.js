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
  lastBreakEvenShift: document.getElementById("lastBreakEvenShift"),
  lastBreakEvenShiftMeta: document.getElementById("lastBreakEvenShiftMeta"),
  lastAbsorptionReduce: document.getElementById("lastAbsorptionReduce"),
  lastAbsorptionReduceMeta: document.getElementById("lastAbsorptionReduceMeta"),
  lastAggressionBubble: document.getElementById("lastAggressionBubble"),
  lastAggressionBubbleMeta: document.getElementById("lastAggressionBubbleMeta"),
  atrState: document.getElementById("atrState"),
  atrStateMeta: document.getElementById("atrStateMeta"),
  cvdDivergence: document.getElementById("cvdDivergence"),
  cvdDivergenceMeta: document.getElementById("cvdDivergenceMeta"),
  dataLag: document.getElementById("dataLag"),
  lastTradeTime: document.getElementById("lastTradeTime"),
  connection: document.getElementById("connection"),
  circuitResume: document.getElementById("circuitResumeButton"),
  sourceLabel: document.getElementById("sourceLabel"),
  tradeCount: document.getElementById("tradeCount"),
  priceCanvas: document.getElementById("priceCanvas"),
  deltaCanvas: document.getElementById("deltaCanvas"),
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
    drawPrice(els.priceCanvas, data.trades, data.markers, data.profile_levels, data.klines);
    drawDelta(els.deltaCanvas, data.delta_series);
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
  els.lastPriceMeta.textContent = `Perp ${formatNumber(summary.last_trade_price)} / Mark ${formatNumber(summary.mark_price)} / Index ${formatNumber(summary.index_price)} / Mid ${formatNumber(summary.quote_mid_price)} / Spot ${formatNumber(summary.spot_last_price)}`;
  els.lastPriceMeta.title = `Bid ${formatNumber(summary.bid_price)} / Ask ${formatNumber(summary.ask_price)}`;
  els.cumDelta.textContent = formatNumber(summary.cumulative_delta);
  els.signals.textContent = formatNumber(summary.signals);
  els.signalsSplit.textContent = splitLabel(breakdown, "signals");
  els.orders.textContent = formatNumber(summary.orders);
  els.ordersSplit.textContent = splitLabel(breakdown, "orders");
  els.closed.textContent = formatNumber(summary.closed_positions);
  els.closedSplit.textContent = splitLabel(breakdown, "closed_positions");
  const pnlPercent = summary.pnl_percent_24h ?? 0;
  els.pnl.textContent = `${formatNumber(summary.pnl_24h)} (${formatSignedPercent(pnlPercent)})`;
  els.pnl.className = summary.pnl_24h >= 0 ? "buy" : "sell";
  els.pnlSplit.textContent = `模拟 ${formatNumber(breakdown.paper.pnl_24h)} / 实盘 ${formatNumber(breakdown.live.pnl_24h)}`;
  renderPosition(summary.open_position);
  els.signalReasons.textContent = reasonText(summary.signal_reasons);
  els.rejectReasons.textContent = reasonText(summary.reject_reasons);
  renderStrategyState(summary);
  if (summary.data_lag_ms < 0) {
    els.dataLag.textContent = "N/A";
    els.dataLag.title = "replay mode / 回放模式";
  } else {
    const minLag = summary.lag_min_ms ?? summary.data_lag_ms;
    els.dataLag.textContent = `${formatNumber(summary.data_lag_ms)} ms`;
    if (summary.data_lag_ms - minLag > 3000 && minLag < 1000) {
      els.dataLag.title = `median ${formatNumber(summary.data_lag_ms)}ms / min ${formatNumber(minLag)}ms — clock skew possible / 时钟可能偏移`;
    } else {
      els.dataLag.title = `median ${formatNumber(summary.data_lag_ms)}ms / min ${formatNumber(minLag)}ms`;
    }
  }
  els.lastTradeTime.textContent = formatTimestamp(summary.last_trade_time);
  els.connection.textContent = summary.connection_status || summary.source || "csv";
  els.connection.title = summary.connection_message || "";
  const tripped = summary.circuit_state === "tripped";
  els.circuitResume.classList.toggle("is-hidden", !tripped);
  if (tripped) {
    const reason = summary.circuit_reason || "unknown";
    if (summary.cooldown_until) {
      const remaining = Math.max(0, Math.ceil((summary.cooldown_until - Date.now()) / 1000));
      const mins = Math.floor(remaining / 60);
      const secs = remaining % 60;
      els.connection.textContent = `CIRCUIT TRIPPED [${reason}] ${mins}m ${secs}s`;
    } else {
      els.connection.textContent = `CIRCUIT TRIPPED [${reason}]`;
    }
    els.connection.className = "sell";
  } else {
    els.connection.className = "";
  }
  const sessionTag = summary.session ? ` [${summary.session.toUpperCase()}]` : "";
  els.sourceLabel.textContent = summary.source === "binance"
    ? `Binance Futures / 币安永续行情${sessionTag}`
    : `CSV Market Replay / CSV 行情回放${sessionTag}`;
  els.tradeCount.textContent = summary.profile_trade_count
    ? `${summary.trade_count} shown / ${summary.profile_trade_count} profiled`
    : `${summary.trade_count} trades`;
}

function renderStrategyState(summary) {
  renderProtectionAction(
    els.lastBreakEvenShift,
    els.lastBreakEvenShiftMeta,
    summary.last_break_even_shift,
    "idle",
    action => `SL ${formatNumber(action.stop_price)}`
  );
  renderProtectionAction(
    els.lastAbsorptionReduce,
    els.lastAbsorptionReduceMeta,
    summary.last_absorption_reduce,
    "idle",
    action => `Qty ${formatNumber(action.quantity)} / Left ${formatNumber(action.remaining_quantity)}`
  );

  const bubble = summary.last_aggression_bubble;
  if (bubble) {
    els.lastAggressionBubble.textContent = `${bubble.tier || "large"} ${bubble.side || "--"}`;
    els.lastAggressionBubble.className = bubble.side === "sell" ? "sell" : "buy";
    els.lastAggressionBubbleMeta.textContent = `${formatNumber(bubble.quantity)} @ ${formatNumber(bubble.price)} / ${formatTimestamp(bubble.timestamp)}`;
  } else {
    els.lastAggressionBubble.textContent = "none";
    els.lastAggressionBubble.className = "";
    els.lastAggressionBubbleMeta.textContent = "--";
  }

  els.atrState.textContent = `1m ${formatNumber(summary.atr_1m_14)}`;
  els.atrState.className = "";
  els.atrStateMeta.textContent = `3m ${formatNumber(summary.atr_3m_14)}`;

  const divergence = summary.cvd_divergence || {};
  els.cvdDivergence.textContent = divergence.state || "none";
  els.cvdDivergence.className = divergence.side === "short" ? "sell" : divergence.side === "long" ? "buy" : "";
  els.cvdDivergenceMeta.textContent = divergence.reason || "--";
}

function renderProtectionAction(valueEl, metaEl, action, emptyLabel, metaFormatter) {
  if (!action) {
    valueEl.textContent = emptyLabel;
    valueEl.className = "";
    metaEl.textContent = "--";
    return;
  }
  valueEl.textContent = action.action || "--";
  valueEl.className = action.action === "absorption_reduce" ? "warn" : "buy";
  metaEl.textContent = `${metaFormatter(action)} / ${formatTimestamp(action.timestamp)}`;
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
  if (kind === "closed_positions") return "<thead><tr><th>Time / 时间</th><th>Side / 方向</th><th>Entry / 入场</th><th>Close / 平仓</th><th>PnL / 盈亏</th><th>% / 收益率</th></tr></thead>";
  return "<thead><tr><th>Time / 时间</th><th>Side / 方向</th><th>PnL / 盈亏</th><th>% / 收益率</th></tr></thead>";
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
    const pct = record.pnl_percent ?? 0;
    return `<tr><td>${formatTimestamp(record.timestamp)}</td><td>${record.side || "--"}</td><td>${formatNumber(record.entry_price)}</td><td>${formatNumber(record.close_price)}</td><td class="${pnlClass}">${formatNumber(pnl)}</td><td class="${pnlClass}">${formatSignedPercent(pct)}</td></tr>`;
  }
  const pnlClass = record.realized_pnl >= 0 ? "buy" : "sell";
  const pct = record.pnl_percent ?? 0;
  return `<tr><td>${formatTimestamp(record.timestamp)}</td><td>${record.side || "--"}</td><td class="${pnlClass}">${formatNumber(record.realized_pnl)}</td><td class="${pnlClass}">${formatSignedPercent(pct)}</td></tr>`;
}

function drawPrice(canvas, trades, markers, profileLevels, klines) {
  const ctx = setupCanvas(canvas);
  if (!trades.length) return;
  const histogramWidth = (profileLevels && profileLevels.length) ? 72 : 0;
  const chartRight = canvas.width - histogramWidth;

  const profilePrices = (profileLevels || []).map(l => l.price).filter(p => Number.isFinite(Number(p)));
  let prices, timestamps, minTs, maxTs;

  if (klines && klines.length) {
    prices = klines.flatMap(k => [k.high, k.low]).concat(profilePrices);
    timestamps = klines.map(k => k.timestamp);
    minTs = timestamps[0];
    maxTs = timestamps[timestamps.length - 1];
  } else {
    prices = trades.map(t => t.price).concat(profilePrices);
    timestamps = trades.map(t => t.timestamp);
    minTs = timestamps[0];
    maxTs = timestamps[timestamps.length - 1];
  }

  const scale = makeTimeScale(prices, minTs, maxTs, chartRight, canvas.height, 28, 50);

  drawGrid(ctx, canvas);
  drawYAxis(ctx, canvas, scale, prices);
  drawTimeAxis(ctx, canvas, scale, minTs, maxTs);
  drawProfileLines(ctx, canvas, scale, profileLevels);

  if (klines && klines.length) {
    drawKlines(ctx, klines, scale, chartRight);
  } else {
    ctx.strokeStyle = colors.price;
    ctx.lineWidth = 2;
    ctx.beginPath();
    trades.forEach((trade, index) => {
      const x = scale.x(trade.timestamp);
      const y = scale.y(trade.price);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  const placedLabels = [];
  markers.forEach(marker => {
    const markerTs = marker.timestamp || 0;
    const x = scale.x(markerTs);
    const y = scale.y(Number(marker.price));
    if (marker.type === "aggression_bubble") {
      drawAggressionBubble(ctx, marker, x, y, canvas);
      return;
    }
    ctx.fillStyle = marker.type === "signal" ? colors.warn : colors.buy;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = colors.text;
    let labelY = Math.max(y - 8, 14);
    for (const placed of placedLabels) {
      if (Math.abs(labelY - placed) < 14) labelY = placed + 14;
    }
    placedLabels.push(labelY);
    ctx.fillText(marker.label || marker.type, Math.min(x + 8, chartRight - 90), labelY);
  });

  if (histogramWidth > 0) drawVolumeProfileHistogram(ctx, canvas, scale, profileLevels, histogramWidth);
}

function drawKlines(ctx, klines, scale, chartRight) {
  const candleCount = klines.length;
  if (candleCount < 2) return;
  const slotWidth = (chartRight - 50) / candleCount;
  const candleWidth = Math.max(1, Math.min(slotWidth * 0.7, 12));

  for (const k of klines) {
    const x = scale.x(k.timestamp);
    const openY = scale.y(k.open);
    const closeY = scale.y(k.close);
    const highY = scale.y(k.high);
    const lowY = scale.y(k.low);
    const bullish = k.close >= k.open;
    const color = bullish ? colors.buy : colors.sell;

    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();

    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(1, Math.abs(closeY - openY));
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.85;
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    ctx.globalAlpha = 1;
  }
}

function drawProfileLines(ctx, canvas, scale, profileLevels) {
  if (!profileLevels || !profileLevels.length) return;
  const lineColors = { POC: "#e7b84b", HVN: "#36c98a", LVN: "#ef5b5b", VAH: "#4fb6d8", VAL: "#4fb6d8" };
  const lineLabels = { POC: "POC", HVN: "HVN", LVN: "LVN", VAH: "VAH", VAL: "VAL" };
  const labelX = canvas.width - 6;
  ctx.textAlign = "right";
  ctx.font = "11px Segoe UI, Arial";

  const bestByType = {};
  for (const level of profileLevels) {
    const existing = bestByType[level.type];
    if (!existing || level.strength > existing.strength) {
      bestByType[level.type] = level;
    }
  }

  for (const level of Object.values(bestByType)) {
    const y = scale.y(level.price);
    if (y < 10 || y > canvas.height - 10) continue;
    const color = lineColors[level.type] || "#aaa69b";
    ctx.save();
    ctx.globalAlpha = 0.45;
    ctx.strokeStyle = color;
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(50, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = color;
    const rangeLabel = (level.lower_bound != null && level.upper_bound != null)
      ? `${formatNumber(level.lower_bound)}-${formatNumber(level.upper_bound)}`
      : formatNumber(level.price);
    ctx.fillText((lineLabels[level.type] || level.type) + " " + rangeLabel, labelX, y - 4);
  }
}

function drawTimeAxis(ctx, canvas, scale, minTs, maxTs) {
  const range = maxTs - minTs || 1;
  const targetTicks = 5;
  const intervalMs = range / targetTicks;
  ctx.fillStyle = colors.text;
  ctx.font = "11px Segoe UI, Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let i = 0; i <= targetTicks; i += 1) {
    const ts = minTs + intervalMs * i;
    const x = scale.x(ts);
    if (x < 50 || x > canvas.width - 10) continue;
    ctx.fillText(formatTimeTick(ts), x, canvas.height - 16);
  }
}

function drawAggressionBubble(ctx, marker, x, y, canvas) {
  const quantity = Math.max(0, Number(marker.quantity) || 0);
  const radius = Math.min(marker.tier === "block" ? 22 : 16, Math.max(6, Math.sqrt(marker.quantity || quantity) * 1.8));
  const color = marker.side === "sell" ? colors.sell : colors.buy;
  ctx.save();
  ctx.globalAlpha = 0.28;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 0.95;
  ctx.strokeStyle = color;
  ctx.lineWidth = marker.tier === "block" ? 3 : 2;
  ctx.stroke();
  ctx.restore();
  ctx.fillStyle = colors.text;
  ctx.fillText(marker.label || "aggression_bubble", Math.min(x + radius + 6, canvas.width - 150), Math.max(y - radius, 14));
}

function drawDelta(canvas, series) {
  const ctx = setupCanvas(canvas);
  if (!series.length) return;
  const values = series.map(point => point.cumulative_delta);
  const timestamps = series.map(point => point.timestamp);
  const minTs = timestamps[0];
  const maxTs = timestamps[timestamps.length - 1];
  const scale = makeTimeScale(values, minTs, maxTs, canvas.width, canvas.height, 22, 50);
  drawGrid(ctx, canvas);
  drawYAxis(ctx, canvas, scale, values);
  const zeroY = scale.y(0);
  ctx.strokeStyle = colors.grid;
  ctx.beginPath();
  ctx.moveTo(50, zeroY);
  ctx.lineTo(canvas.width, zeroY);
  ctx.stroke();

  ctx.strokeStyle = values[values.length - 1] >= 0 ? colors.buy : colors.sell;
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((point, index) => {
    const x = scale.x(point.timestamp);
    const y = scale.y(point.cumulative_delta);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  drawTimeAxis(ctx, canvas, scale, minTs, maxTs);
}

function drawVolumeProfileHistogram(ctx, canvas, scale, profileLevels, histWidth) {
  if (!profileLevels || !profileLevels.length) return;
  const maxHvnLvn = 5;
  const seen = new Set();
  const deduped = [];
  const hvnLvnCount = { HVN: 0, LVN: 0 };
  for (const level of profileLevels) {
    const key = `${level.type}:${level.price}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if ((level.type === "HVN" || level.type === "LVN") && hvnLvnCount[level.type] >= maxHvnLvn) continue;
    if (level.type === "HVN") hvnLvnCount.HVN += 1;
    if (level.type === "LVN") hvnLvnCount.LVN += 1;
    deduped.push(level);
  }
  const maxStrength = Math.max(...deduped.map(l => l.strength), 1);
  const colorFor = { POC: colors.warn, HVN: colors.buy, LVN: colors.sell, VAH: colors.price, VAL: colors.price };
  const labelX = canvas.width - 4;
  const barAreaLeft = canvas.width - histWidth + 4;

  ctx.textAlign = "right";
  ctx.font = "10px Segoe UI, Arial";

  for (const level of deduped) {
    const y = scale.y(level.price);
    if (y < 8 || y > canvas.height - 8) continue;
    const color = colorFor[level.type] || colors.price;
    const barWidth = Math.max(4, Math.round((level.strength / maxStrength) * (histWidth - 16)));
    const barY = y - 2;
    const barH = Math.max(3, Math.min(6, (canvas.height - 56) / Math.max(deduped.length, 1)));

    ctx.fillStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.fillRect(barAreaLeft, barY, barWidth, barH);
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = color;
    const histType = level.type === "POC" ? "POC" : level.type === "HVN" ? "H" : level.type === "LVN" ? "L" : level.type;
    const histRangeLabel = (level.lower_bound != null && level.upper_bound != null)
      ? `${formatNumber(level.lower_bound)}-${formatNumber(level.upper_bound)}`
      : formatNumber(level.price);
    ctx.fillText(histType + " " + histRangeLabel, labelX, y + 3);
  }
  ctx.globalAlpha = 1;

  ctx.strokeStyle = "#3a3a3a";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(barAreaLeft - 2, 0);
  ctx.lineTo(barAreaLeft - 2, canvas.height);
  ctx.stroke();
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

function makeTimeScale(values, minTs, maxTs, width, height, padY, padX) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = (max - min) * 0.08 || max * 0.01 || 1;
  const rangeMin = min - padding;
  const rangeMax = max + padding;
  const span = rangeMax - rangeMin || 1;
  const tsRange = maxTs - minTs || 1;
  return {
    x: ts => padX + ((ts - minTs) / tsRange) * (width - padX * 2),
    y: value => height - padY - ((value - rangeMin) / span) * (height - padY * 2)
  };
}

function makeScale(values, width, height, pad) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = (max - min) * 0.08 || max * 0.01 || 1;
  const rangeMin = min - padding;
  const rangeMax = max + padding;
  const span = rangeMax - rangeMin || 1;
  return {
    x: (index, count) => pad + (index / Math.max(count - 1, 1)) * (width - pad * 2),
    y: value => height - pad - ((value - rangeMin) / span) * (height - pad * 2)
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

function formatTimeTick(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const d = new Date(Number(value));
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
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

function formatSignedPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const num = Number(value);
  const sign = num > 0 ? "+" : "";
  return sign + num.toFixed(2) + "%";
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
async function resumeCircuit() {
  if (isLoading) return;
  isLoading = true;
  els.circuitResume.disabled = true;
  try {
    const query = new URLSearchParams({ symbol: els.symbol.value, t: Date.now().toString() });
    const response = await fetch(`/api/circuit/resume?${query.toString()}`, { method: "POST", cache: "no-store" });
    const result = await response.json();
    if (result.resumed) {
      els.circuitResume.classList.add("is-hidden");
      els.connection.className = "";
      loadDashboard();
    } else {
      alert(`Resume failed: ${result.reason || "unknown"}`);
    }
  } catch (error) {
    alert(`Resume error: ${error.message}`);
  } finally {
    isLoading = false;
    els.circuitResume.disabled = false;
  }
}

els.circuitResume.addEventListener("click", resumeCircuit);
window.addEventListener("resize", loadDashboard);
loadDashboard();
setInterval(loadDashboard, REFRESH_INTERVAL_MS);
