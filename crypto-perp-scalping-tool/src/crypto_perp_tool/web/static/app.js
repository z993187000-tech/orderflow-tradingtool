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
  streamFreshness: document.getElementById("streamFreshness"),
  lastTradeTime: document.getElementById("lastTradeTime"),
  connection: document.getElementById("connection"),
  circuitResume: document.getElementById("circuitResumeButton"),
  sourceLabel: document.getElementById("sourceLabel"),
  tradeCount: document.getElementById("tradeCount"),
  priceCanvas: document.getElementById("priceCanvas"),
  tape: document.getElementById("tapeBody"),
  detailPanel: document.getElementById("detailPanel"),
  detailTitle: document.getElementById("detailTitle"),
  detailSubtitle: document.getElementById("detailSubtitle"),
  detailClose: document.getElementById("detailClose"),
  detailBody: document.getElementById("detailBody"),
  rangeTabs: document.querySelector(".range-tabs"),
  viewButtons: document.querySelectorAll("[data-view]"),
  liveView: document.getElementById("liveView"),
  backtestView: document.getElementById("backtestView"),
  backtestForm: document.getElementById("backtestForm"),
  backtestCsvPath: document.getElementById("backtestCsvPath"),
  backtestSymbol: document.getElementById("backtestSymbol"),
  backtestEquity: document.getElementById("backtestEquity"),
  backtestEntrySlippage: document.getElementById("backtestEntrySlippage"),
  backtestExitSlippage: document.getElementById("backtestExitSlippage"),
  backtestFee: document.getElementById("backtestFee"),
  backtestSplit: document.getElementById("backtestSplit"),
  backtestStart: document.getElementById("backtestStart"),
  backtestEnd: document.getElementById("backtestEnd"),
  backtestRunButton: document.getElementById("backtestRunButton"),
  backtestStatus: document.getElementById("backtestStatus"),
  backtestReport: document.getElementById("backtestReport"),
  backtestMetricGrid: document.getElementById("backtestMetricGrid"),
  backtestEquityCanvas: document.getElementById("backtestEquityCanvas"),
  backtestSetupBody: document.getElementById("backtestSetupBody"),
  backtestTradeBody: document.getElementById("backtestTradeBody"),
  backtestMeta: document.getElementById("backtestMeta"),
  backtestErrors: document.getElementById("backtestErrors")
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
const LARGE_TAPE_MIN_QTY = 20;
const PROFILE_OVERLAY_WIDTH = 96;
const PRICE_MIN_VISIBLE_RATIO = 0.02;
const PRICE_MIN_VISIBLE_MS = 1000;
const PRICE_MARKER_HIT_RADIUS = 16;
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
let activeView = "live";
let activeDetail = "signals";
let activeRange = "24h";
const priceView = {
  minTs: null,
  maxTs: null,
  isCustom: false,
  dragging: false,
  dragMoved: false,
  dragStartX: 0,
  dragStartMinTs: null,
  dragStartMaxTs: null,
  selectedMarkerKey: null,
  markerHitboxes: []
};

async function loadDashboard() {
  if (activeView !== "live") return;
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
    renderPriceChart();
    const largeTapeTrades = data.trades
      .filter(trade => trade.quantity >= LARGE_TAPE_MIN_QTY)
      .slice(-12)
      .reverse();
    renderTape(largeTapeTrades);
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
  const exchangeLag = summary.exchange_lag_ms ?? summary.data_lag_ms;
  const exchangeLagMin = summary.exchange_lag_min_ms ?? summary.lag_min_ms ?? exchangeLag;
  if (exchangeLag === undefined || exchangeLag < 0) {
    els.dataLag.textContent = "N/A";
    els.dataLag.title = "exchange lag unavailable in replay mode / 回放模式无交易所延迟";
  } else {
    els.dataLag.textContent = `${formatNumber(exchangeLag)} ms`;
    if (exchangeLag - exchangeLagMin > 3000 && exchangeLagMin < 1000) {
      els.dataLag.title = `median ${formatNumber(exchangeLag)}ms / min ${formatNumber(exchangeLagMin)}ms — clock skew possible / 时钟可能偏移`;
    } else {
      els.dataLag.title = `median exchange lag ${formatNumber(exchangeLag)}ms / min ${formatNumber(exchangeLagMin)}ms`;
    }
  }
  if (summary.stream_freshness_ms === undefined || summary.stream_freshness_ms < 0) {
    els.streamFreshness.textContent = "N/A";
    els.streamFreshness.title = "stream freshness unavailable in replay mode / 回放模式无流新鲜度";
  } else {
    els.streamFreshness.textContent = `${formatNumber(summary.stream_freshness_ms)} ms`;
    els.streamFreshness.title = `local time since last received trade / 距离本地上次收到成交 ${formatNumber(summary.stream_freshness_ms)}ms`;
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

function renderPriceChart() {
  if (!latestDashboard) return;
  drawPrice(
    els.priceCanvas,
    latestDashboard.trades,
    latestDashboard.markers,
    latestDashboard.profile_levels,
    latestDashboard.klines
  );
}

function drawPrice(canvas, trades, markers, profileLevels, klines) {
  const ctx = setupCanvas(canvas);
  priceView.markerHitboxes = [];
  const safeTrades = Array.isArray(trades) ? trades : [];
  const safeKlines = Array.isArray(klines) ? klines : [];
  const hasPriceData = safeTrades.length || (safeKlines && safeKlines.length);
  if (!hasPriceData) return;
  const selectedProfileLevels = latestProfileLevels(profileLevels);
  const chartRight = canvas.width;
  const fullRange = priceDataTimeRange(safeTrades, safeKlines);
  if (!fullRange) return;
  const { minTs, maxTs } = visibleTimeRange(fullRange);
  const visibleTrades = visibleItemsForTimeRange(safeTrades, minTs, maxTs);
  const visibleKlines = visibleKlinesForTimeRange(safeKlines, minTs, maxTs);

  const profilePrices = selectedProfileLevels.map(l => l.price).filter(p => Number.isFinite(Number(p)));
  let prices;

  if (safeKlines.length) {
    const priceKlines = visibleKlines.length ? visibleKlines : safeKlines;
    prices = priceKlines.flatMap(k => [k.high, k.low]).concat(profilePrices);
  } else {
    const priceTrades = visibleTrades.length ? visibleTrades : safeTrades;
    prices = priceTrades.map(t => t.price).concat(profilePrices);
  }

  const scale = makeTimeScale(prices, minTs, maxTs, chartRight, canvas.height, 28, 50);

  drawGrid(ctx, canvas);
  drawYAxis(ctx, canvas, scale, prices);
  drawTimeAxis(ctx, canvas, scale, minTs, maxTs);
  drawProfileLines(ctx, canvas, scale, selectedProfileLevels);
  drawVolumeProfileOverlay(ctx, canvas, scale, selectedProfileLevels);

  if (safeKlines.length) {
    drawKlines(ctx, visibleKlines, scale, chartRight, minTs, maxTs);
  } else {
    ctx.strokeStyle = colors.price;
    ctx.lineWidth = 2;
    ctx.beginPath();
    visibleTrades.forEach((trade, index) => {
      const x = scale.x(trade.timestamp);
      const y = scale.y(trade.price);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  const placedLabels = [];
  (markers || []).filter(marker => marker.timestamp >= minTs && marker.timestamp <= maxTs).forEach(marker => {
    const markerTs = marker.timestamp || 0;
    const x = scale.x(markerTs);
    const y = scale.y(Number(marker.price));
    const markerKey = priceMarkerKey(marker);
    const isSelected = markerKey === priceView.selectedMarkerKey;
    if (marker.type === "aggression_bubble") {
      const radius = aggressionBubbleRadius(marker);
      priceView.markerHitboxes.push({ key: markerKey, x, y, radius: Math.max(radius + 4, PRICE_MARKER_HIT_RADIUS) });
      drawAggressionBubble(ctx, marker, x, y, canvas, isSelected);
      if (isSelected) drawMarkerDetail(ctx, marker, x + radius + 6, y - radius, canvas, placedLabels);
      return;
    }
    ctx.fillStyle = marker.type === "signal" ? colors.warn : colors.buy;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    priceView.markerHitboxes.push({ key: markerKey, x, y, radius: PRICE_MARKER_HIT_RADIUS });
    if (isSelected) drawMarkerDetail(ctx, marker, x + 8, y - 8, canvas, placedLabels);
  });
}

function priceMarkerKey(marker) {
  return [
    marker.type || "marker",
    marker.timestamp || 0,
    marker.price || 0,
    marker.label || "",
    marker.side || "",
    marker.quantity || ""
  ].join("|");
}

function markerDetailText(marker) {
  const parts = [marker.label || marker.type || "marker"];
  if (marker.side) parts.push(String(marker.side).toUpperCase());
  if (marker.quantity !== undefined) parts.push(`Qty ${formatNumber(marker.quantity)}`);
  if (marker.price !== undefined) parts.push(`@ ${formatNumber(marker.price)}`);
  return parts.join(" ");
}

function drawMarkerDetail(ctx, marker, x, y, canvas, placedLabels) {
  const text = markerDetailText(marker);
  const paddingX = 7;
  const paddingY = 5;
  ctx.save();
  ctx.font = "11px Segoe UI, Arial";
  const width = Math.min(canvas.width - 16, ctx.measureText(text).width + paddingX * 2);
  const height = 22;
  let labelX = Math.min(Math.max(54, x), canvas.width - width - 8);
  let labelY = Math.min(Math.max(8, y), canvas.height - height - 8);
  for (const placed of placedLabels) {
    if (Math.abs(labelY - placed) < height) labelY = Math.min(canvas.height - height - 8, placed + height + 2);
  }
  placedLabels.push(labelY);
  ctx.fillStyle = "rgba(18, 18, 18, 0.86)";
  ctx.strokeStyle = "rgba(170, 166, 155, 0.32)";
  ctx.lineWidth = 1;
  ctx.fillRect(labelX, labelY, width, height);
  ctx.strokeRect(labelX, labelY, width, height);
  ctx.fillStyle = colors.text;
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillText(text, labelX + paddingX, labelY + height / 2, width - paddingX * 2);
  ctx.restore();
}

function priceDataTimeRange(trades, klines) {
  const points = [];
  for (const trade of trades || []) {
    const ts = Number(trade.timestamp);
    if (Number.isFinite(ts)) points.push(ts);
  }
  for (const kline of klines || []) {
    const start = Number(kline.timestamp);
    if (Number.isFinite(start)) points.push(start);
    const close = Number(kline.close_time);
    if (kline.is_closed && Number.isFinite(close)) points.push(close);
  }
  if (!points.length) return null;
  return { minTs: Math.min(...points), maxTs: Math.max(...points) };
}

function visibleTimeRange(fullRange) {
  const fullSpan = Math.max(1, fullRange.maxTs - fullRange.minTs);
  if (!priceView.isCustom || priceView.minTs === null || priceView.maxTs === null) {
    priceView.minTs = fullRange.minTs;
    priceView.maxTs = fullRange.maxTs;
    priceView.isCustom = false;
    return { minTs: fullRange.minTs, maxTs: fullRange.maxTs };
  }

  const minSpan = Math.min(fullSpan, Math.max(PRICE_MIN_VISIBLE_MS, fullSpan * PRICE_MIN_VISIBLE_RATIO));
  const span = Math.min(fullSpan, Math.max(minSpan, priceView.maxTs - priceView.minTs));
  let minTs = priceView.minTs;
  let maxTs = minTs + span;
  if (minTs < fullRange.minTs) {
    minTs = fullRange.minTs;
    maxTs = minTs + span;
  }
  if (maxTs > fullRange.maxTs) {
    maxTs = fullRange.maxTs;
    minTs = maxTs - span;
  }
  priceView.minTs = minTs;
  priceView.maxTs = maxTs;
  priceView.isCustom = span < fullSpan;
  return { minTs, maxTs };
}

function visibleItemsForTimeRange(items, minTs, maxTs) {
  let before = null;
  let after = null;
  const visible = [];
  for (const item of items || []) {
    const ts = Number(item.timestamp);
    if (!Number.isFinite(ts)) continue;
    if (ts < minTs) {
      before = item;
    } else if (ts > maxTs) {
      if (!after) after = item;
    } else {
      visible.push(item);
    }
  }
  return [before, ...visible, after].filter(Boolean);
}

function visibleKlinesForTimeRange(klines, minTs, maxTs) {
  let before = null;
  let after = null;
  const visible = [];
  for (const kline of klines || []) {
    const start = Number(kline.timestamp);
    const close = Number(kline.close_time);
    if (!Number.isFinite(start)) continue;
    const end = Number.isFinite(close) ? close : start;
    const kMin = Math.min(start, end);
    const kMax = Math.max(start, end);
    if (kMax < minTs) {
      before = kline;
    } else if (kMin > maxTs) {
      if (!after) after = kline;
    } else {
      visible.push(kline);
    }
  }
  return [before, ...visible, after].filter(Boolean);
}

function drawKlines(ctx, klines, scale, chartRight, minTs, maxTs) {
  const candleCount = klines.length;
  if (!candleCount) return;
  const plotWidth = Math.max(1, chartRight - 100);
  const fallbackSlotWidth = plotWidth / Math.max(candleCount, 1);
  const fallbackDuration = inferredKlineDurationMs(klines, Math.max(1, maxTs - minTs));

  for (const k of klines) {
    const start = Number(k.timestamp);
    const duration = klineDurationMs(k, fallbackDuration);
    const timeWidth = Math.abs(scale.x(start + duration) - scale.x(start));
    const candleWidth = Math.max(2, Math.min((timeWidth || fallbackSlotWidth) * 0.82, fallbackSlotWidth * 0.92));
    const centerTs = k.is_closed ? start + duration / 2 : Math.min(start + duration / 2, Math.max(start, maxTs));
    const x = scale.x(centerTs);
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

function klineDurationMs(kline, fallbackDuration) {
  const start = Number(kline.timestamp);
  const close = Number(kline.close_time);
  if (Number.isFinite(start) && Number.isFinite(close) && close > start) return close - start + 1;
  return fallbackDuration;
}

function inferredKlineDurationMs(klines, visibleSpan) {
  const starts = (klines || [])
    .map(kline => Number(kline.timestamp))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const diffs = [];
  for (let index = 1; index < starts.length; index += 1) {
    const diff = starts[index] - starts[index - 1];
    if (diff > 0) diffs.push(diff);
  }
  if (diffs.length) return diffs[Math.floor(diffs.length / 2)];
  return Math.max(1, visibleSpan);
}

function latestProfileLevels(profileLevels) {
  const latestByType = {};
  for (const level of profileLevels || []) {
    const existing = latestByType[level.type];
    if (!existing || profileLevelRank(level) > profileLevelRank(existing)) {
      latestByType[level.type] = level;
    }
  }
  return Object.values(latestByType);
}

function profileLevelRank(level) {
  const touchedAt = Number(level.touched_at);
  if (Number.isFinite(touchedAt) && touchedAt > 0) return touchedAt;
  return Number(level.strength) || 0;
}

function drawProfileLines(ctx, canvas, scale, profileLevels) {
  if (!profileLevels || !profileLevels.length) return;
  const lineColors = { POC: "#e7b84b", HVN: "#36c98a", LVN: "#ef5b5b", VAH: "#4fb6d8", VAL: "#4fb6d8" };

  for (const level of profileLevels) {
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

function aggressionBubbleRadius(marker) {
  const quantity = Math.max(0, Number(marker.quantity) || 0);
  return Math.min(marker.tier === "block" ? 22 : 16, Math.max(6, Math.sqrt(quantity) * 1.8));
}

function drawAggressionBubble(ctx, marker, x, y, canvas, showDetail) {
  const radius = aggressionBubbleRadius(marker);
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
  if (showDetail) {
    ctx.save();
    ctx.strokeStyle = colors.text;
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(x, y, radius + 4, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
}

function drawVolumeProfileOverlay(ctx, canvas, scale, profileLevels) {
  if (!profileLevels || !profileLevels.length) return;
  const maxStrength = Math.max(...profileLevels.map(l => Number(l.strength) || 0), 1);
  const colorFor = { POC: colors.warn, HVN: colors.buy, LVN: colors.sell, VAH: colors.price, VAL: colors.price };
  const barRight = canvas.width - 8;
  const barAreaLeft = Math.max(54, canvas.width - PROFILE_OVERLAY_WIDTH);
  const barAreaWidth = Math.max(8, barRight - barAreaLeft);

  ctx.save();
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  ctx.font = "10px Segoe UI, Arial";

  for (const level of profileLevels) {
    const y = scale.y(level.price);
    if (y < 8 || y > canvas.height - 8) continue;
    const color = colorFor[level.type] || colors.price;
    const barWidth = Math.max(6, Math.round(((Number(level.strength) || 0) / maxStrength) * barAreaWidth));
    const lowerY = level.lower_bound != null ? scale.y(level.lower_bound) : y + 4;
    const upperY = level.upper_bound != null ? scale.y(level.upper_bound) : y - 4;
    const barY = Math.max(8, Math.min(lowerY, upperY));
    const barH = Math.max(4, Math.min(canvas.height - 8 - barY, Math.abs(lowerY - upperY)));

    ctx.fillStyle = color;
    ctx.globalAlpha = 0.24;
    ctx.fillRect(barRight - barWidth, barY, barWidth, barH);
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = color;
    const histType = level.type === "POC" ? "POC" : level.type === "HVN" ? "H" : level.type === "LVN" ? "L" : level.type;
    const histRangeLabel = (level.lower_bound != null && level.upper_bound != null)
      ? `${formatNumber(level.lower_bound)}-${formatNumber(level.upper_bound)}`
      : formatNumber(level.price);
    ctx.fillText(histType + " " + histRangeLabel, barRight - 2, Math.max(12, Math.min(y + 3, canvas.height - 12)));
  }

  ctx.globalAlpha = 0.25;
  ctx.strokeStyle = "#3a3a3a";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(barAreaLeft, 8);
  ctx.lineTo(barAreaLeft, canvas.height - 8);
  ctx.stroke();
  ctx.restore();
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

function bindPriceCanvasInteractions() {
  const canvas = els.priceCanvas;
  if (!canvas) return;
  canvas.addEventListener("wheel", event => {
    event.preventDefault();
    zoomPriceView(event);
  }, { passive: false });
  canvas.addEventListener("mousedown", event => {
    if (event.button !== 0) return;
    const range = currentVisiblePriceRange();
    if (!range) return;
    priceView.dragging = true;
    priceView.dragMoved = false;
    priceView.dragStartX = event.clientX;
    priceView.dragStartMinTs = range.minTs;
    priceView.dragStartMaxTs = range.maxTs;
    canvas.classList.add("is-dragging");
  });
  canvas.addEventListener("click", event => {
    if (event.button !== 0) return;
    togglePriceMarkerDetail(event);
  });
  canvas.addEventListener("dblclick", event => {
    event.preventDefault();
    resetPriceView();
  });
  window.addEventListener("mousemove", event => {
    if (!priceView.dragging) return;
    const span = priceView.dragStartMaxTs - priceView.dragStartMinTs;
    const deltaX = event.clientX - priceView.dragStartX;
    if (Math.abs(deltaX) <= 3) return;
    priceView.dragMoved = true;
    const deltaMs = -(deltaX / pricePlotWidth(canvas)) * span;
    panPriceView(deltaMs, {
      minTs: priceView.dragStartMinTs,
      maxTs: priceView.dragStartMaxTs
    });
  });
  window.addEventListener("mouseup", endPriceDrag);
}

function togglePriceMarkerDetail(event) {
  if (priceView.dragMoved) {
    priceView.dragMoved = false;
    return;
  }
  const hit = hitPriceMarker(event, els.priceCanvas);
  const nextKey = hit && priceView.selectedMarkerKey !== hit.key ? hit.key : null;
  if (priceView.selectedMarkerKey === nextKey) return;
  priceView.selectedMarkerKey = nextKey;
  renderPriceChart();
}

function hitPriceMarker(event, canvas) {
  const point = priceCanvasPoint(event, canvas);
  for (let index = priceView.markerHitboxes.length - 1; index >= 0; index -= 1) {
    const hitbox = priceView.markerHitboxes[index];
    const distance = Math.hypot(point.x - hitbox.x, point.y - hitbox.y);
    if (distance <= hitbox.radius) return hitbox;
  }
  return null;
}

function priceCanvasPoint(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / Math.max(1, rect.width);
  const scaleY = canvas.height / Math.max(1, rect.height);
  return {
    x: (event.clientX - rect.left) * scaleX,
    y: (event.clientY - rect.top) * scaleY
  };
}

function currentFullPriceRange() {
  if (!latestDashboard) return null;
  return priceDataTimeRange(latestDashboard.trades || [], latestDashboard.klines || []);
}

function currentVisiblePriceRange() {
  const fullRange = currentFullPriceRange();
  return fullRange ? visibleTimeRange(fullRange) : null;
}

function zoomPriceView(event) {
  const fullRange = currentFullPriceRange();
  if (!fullRange) return;
  const currentRange = visibleTimeRange(fullRange);
  const fullSpan = Math.max(1, fullRange.maxTs - fullRange.minTs);
  const currentSpan = Math.max(1, currentRange.maxTs - currentRange.minTs);
  const zoomFactor = event.deltaY < 0 ? 0.8 : 1.25;
  const minSpan = Math.min(fullSpan, Math.max(PRICE_MIN_VISIBLE_MS, fullSpan * PRICE_MIN_VISIBLE_RATIO));
  const nextSpan = Math.min(fullSpan, Math.max(minSpan, currentSpan * zoomFactor));
  const cursorRatio = priceCursorRatio(event, els.priceCanvas);
  const cursorTs = currentRange.minTs + currentSpan * cursorRatio;
  priceView.minTs = cursorTs - nextSpan * cursorRatio;
  priceView.maxTs = priceView.minTs + nextSpan;
  priceView.isCustom = nextSpan < fullSpan;
  renderPriceChart();
}

function panPriceView(deltaMs, baseRange = null) {
  const range = baseRange || currentVisiblePriceRange();
  if (!range) return;
  priceView.minTs = range.minTs + deltaMs;
  priceView.maxTs = range.maxTs + deltaMs;
  priceView.isCustom = true;
  renderPriceChart();
}

function resetPriceView(redraw = true) {
  priceView.minTs = null;
  priceView.maxTs = null;
  priceView.isCustom = false;
  priceView.selectedMarkerKey = null;
  if (redraw) renderPriceChart();
}

function endPriceDrag() {
  if (!priceView.dragging) return;
  priceView.dragging = false;
  els.priceCanvas.classList.remove("is-dragging");
}

function priceCursorRatio(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  return clamp((x - 50) / pricePlotWidth(canvas), 0, 1);
}

function pricePlotWidth(canvas) {
  const rect = canvas.getBoundingClientRect();
  return Math.max(1, rect.width - 100);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
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

function switchView(view) {
  activeView = view === "backtest" ? "backtest" : "live";
  els.liveView.classList.toggle("is-hidden", activeView !== "live");
  els.backtestView.classList.toggle("is-hidden", activeView !== "backtest");
  els.viewButtons.forEach(button => {
    button.classList.toggle("is-active", button.dataset.view === activeView);
  });
  if (activeView === "live") loadDashboard();
}

function backtestFormPayload() {
  return {
    csv_path: els.backtestCsvPath.value.trim(),
    symbol: els.backtestSymbol.value,
    equity: numberOrDefault(els.backtestEquity, 10000),
    entry_slippage_bps: numberOrDefault(els.backtestEntrySlippage, 2.0),
    exit_slippage_bps: numberOrDefault(els.backtestExitSlippage, 3.0),
    fee_bps: numberOrDefault(els.backtestFee, 4.0),
    start_ms: numberOrNull(els.backtestStart),
    end_ms: numberOrNull(els.backtestEnd),
    split: numberOrDefault(els.backtestSplit, 0.0)
  };
}

function numberOrDefault(element, fallback) {
  const value = Number(element.value);
  return Number.isFinite(value) ? value : fallback;
}

function numberOrNull(element) {
  if (!element.value.trim()) return null;
  const value = Number(element.value);
  return Number.isFinite(value) ? value : null;
}

async function runBacktest(event) {
  event.preventDefault();
  els.backtestRunButton.disabled = true;
  els.backtestStatus.textContent = "Running";
  renderBacktestError("");
  try {
    const response = await fetch("/api/backtest/run", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(backtestFormPayload())
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Backtest API returned ${response.status}`);
    renderBacktestReport(payload);
    els.backtestStatus.textContent = "Complete";
  } catch (error) {
    renderBacktestError(error.message);
    els.backtestStatus.textContent = "Error";
  } finally {
    els.backtestRunButton.disabled = false;
  }
}

function renderBacktestReport(payload) {
  els.backtestReport.classList.remove("is-empty");
  renderBacktestError("");
  if (payload.mode === "split") {
    renderBacktestSplitReport(payload);
    return;
  }
  renderBacktestSingleReport(payload, "Single Run");
}

function renderBacktestSplitReport(payload) {
  const isResult = payload.in_sample || {};
  const oosResult = payload.out_of_sample || {};
  const isReport = isResult.report || {};
  const oosReport = oosResult.report || {};
  els.backtestMeta.textContent = `${payload.symbol || "--"} split / WFE ${formatNumber(payload.walk_forward_efficiency)}`;
  els.backtestMetricGrid.innerHTML = [
    backtestMetricCard("IS Return", formatSignedPercent(isResult.total_return_pct)),
    backtestMetricCard("OOS Return", formatSignedPercent(oosResult.total_return_pct)),
    backtestMetricCard("IS Net PnL", formatNumber(isReport.net_pnl)),
    backtestMetricCard("OOS Net PnL", formatNumber(oosReport.net_pnl)),
    backtestMetricCard("OOS PF", formatNumber(oosReport.profit_factor)),
    backtestMetricCard("OOS Max DD", formatNumber(oosReport.max_drawdown))
  ].join("");
  drawBacktestEquityCurve(oosResult.equity_curve || []);
  renderBacktestSetupRows(oosReport.by_setup || {});
  renderBacktestTradeRows(oosResult.trade_records || []);
  renderBacktestResultErrors([...(isResult.errors || []), ...(oosResult.errors || [])]);
}

function renderBacktestSingleReport(result, label) {
  const report = result.report || {};
  els.backtestMeta.textContent = `${label} / ${result.symbol || "--"} / ${formatNumber(result.total_events)} events / ${result.config_version || "no config version"}`;
  els.backtestMetricGrid.innerHTML = [
    backtestMetricCard("Return", formatSignedPercent(result.total_return_pct)),
    backtestMetricCard("Net PnL", formatNumber(report.net_pnl)),
    backtestMetricCard("Win Rate", formatPercentRate(report.win_rate)),
    backtestMetricCard("Profit Factor", formatNumber(report.profit_factor)),
    backtestMetricCard("Max Drawdown", formatNumber(report.max_drawdown)),
    backtestMetricCard("Trades", formatNumber(report.total_trades))
  ].join("");
  drawBacktestEquityCurve(result.equity_curve || []);
  renderBacktestSetupRows(report.by_setup || {});
  renderBacktestTradeRows(result.trade_records || []);
  renderBacktestResultErrors(result.errors || []);
}

function backtestMetricCard(label, value) {
  return `<div class="backtest-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderBacktestSetupRows(bySetup) {
  const rows = Object.entries(bySetup).map(([setup, stats]) => `<tr>
    <td>${escapeHtml(setup)}</td>
    <td>${formatNumber(stats.signals)}</td>
    <td>${formatNumber(stats.trades)}</td>
    <td>${formatNumber(stats.wins)}</td>
    <td class="${Number(stats.net_pnl) >= 0 ? "buy" : "sell"}">${formatNumber(stats.net_pnl)}</td>
  </tr>`);
  els.backtestSetupBody.innerHTML = rows.length ? rows.join("") : `<tr><td colspan="5">No setup data</td></tr>`;
}

function renderBacktestTradeRows(records) {
  const rows = records.slice(-12).reverse().map(record => `<tr>
    <td>${formatTimestamp(record.exit_time)}</td>
    <td>${escapeHtml(record.side || "--")}</td>
    <td>${formatNumber(record.entry_price)}</td>
    <td>${formatNumber(record.exit_price)}</td>
    <td class="${Number(record.net_pnl) >= 0 ? "buy" : "sell"}">${formatNumber(record.net_pnl)}</td>
    <td>${formatNumber(record.r_multiple)}</td>
  </tr>`);
  els.backtestTradeBody.innerHTML = rows.length ? rows.join("") : `<tr><td colspan="6">No closed trades</td></tr>`;
}

function renderBacktestResultErrors(errors) {
  renderBacktestError((errors || []).join(" / "));
}

function renderBacktestError(message) {
  els.backtestErrors.textContent = message || "";
  els.backtestErrors.classList.toggle("is-hidden", !message);
}

function drawBacktestEquityCurve(values) {
  const canvas = els.backtestEquityCanvas;
  const ctx = setupCanvas(canvas);
  const points = (values || []).map(Number).filter(Number.isFinite);
  if (!points.length) return;
  const scale = makeScale(points, canvas.width, canvas.height, 24);
  drawGrid(ctx, canvas);
  ctx.strokeStyle = points[points.length - 1] >= points[0] ? colors.buy : colors.sell;
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((value, index) => {
    const x = scale.x(index, points.length);
    const y = scale.y(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function formatPercentRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return (Number(value) * 100).toFixed(2) + "%";
}

function escapeHtml(value) {
  return String(value ?? "--")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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

els.viewButtons.forEach(button => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
els.backtestForm.addEventListener("submit", runBacktest);
els.refresh.addEventListener("click", loadDashboard);
els.symbol.addEventListener("change", () => {
  resetPriceView(false);
  loadDashboard();
});
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
bindPriceCanvasInteractions();
loadDashboard();
setInterval(loadDashboard, REFRESH_INTERVAL_MS);
