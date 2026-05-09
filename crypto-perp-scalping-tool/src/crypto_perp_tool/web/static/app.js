const els = {
  symbol: document.getElementById("symbolSelect"),
  refresh: document.getElementById("refreshButton"),
  lastPrice: document.getElementById("lastPrice"),
  cumDelta: document.getElementById("cumDelta"),
  signals: document.getElementById("signals"),
  orders: document.getElementById("orders"),
  closed: document.getElementById("closed"),
  pnl: document.getElementById("pnl"),
  connection: document.getElementById("connection"),
  sourceLabel: document.getElementById("sourceLabel"),
  tradeCount: document.getElementById("tradeCount"),
  priceCanvas: document.getElementById("priceCanvas"),
  deltaCanvas: document.getElementById("deltaCanvas"),
  profile: document.getElementById("profileLevels"),
  tape: document.getElementById("tapeBody")
};

const colors = {
  grid: "#333333",
  text: "#aaa69b",
  price: "#4fb6d8",
  buy: "#36c98a",
  sell: "#ef5b5b",
  warn: "#e7b84b"
};

async function loadDashboard() {
  const response = await fetch(`/api/orderflow?symbol=${encodeURIComponent(els.symbol.value)}`);
  const data = await response.json();
  renderSummary(data.summary);
  drawPrice(els.priceCanvas, data.trades, data.markers);
  drawDelta(els.deltaCanvas, data.delta_series);
  renderProfile(data.profile_levels);
  renderTape(data.trades.slice(-12).reverse());
}

function renderSummary(summary) {
  els.lastPrice.textContent = formatNumber(summary.last_price);
  els.cumDelta.textContent = formatNumber(summary.cumulative_delta);
  els.signals.textContent = summary.signals;
  els.orders.textContent = summary.orders;
  els.closed.textContent = summary.closed_positions;
  els.pnl.textContent = formatNumber(summary.realized_pnl);
  els.pnl.className = summary.realized_pnl >= 0 ? "buy" : "sell";
  els.connection.textContent = summary.connection_status || summary.source || "csv";
  els.connection.title = summary.connection_message || "";
  els.sourceLabel.textContent = summary.source === "binance"
    ? "Binance Live Market / 币安实时行情"
    : "CSV Market Replay / CSV 行情回放";
  els.tradeCount.textContent = `${summary.trade_count} trades`;
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
    const x = scale.x(marker.index, trades.length);
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
  const colorFor = {
    POC: colors.warn,
    HVN: colors.buy,
    LVN: colors.sell,
    VAH: colors.price,
    VAL: colors.price
  };
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
      <td>${trade.timestamp}</td>
      <td class="${klass}">${trade.side}</td>
      <td>${formatNumber(trade.price)}</td>
      <td>${formatNumber(trade.quantity)}</td>
      <td class="${trade.delta >= 0 ? "buy" : "sell"}">${formatNumber(trade.delta)}</td>
    </tr>`;
  }).join("");
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(Number(canvas.getAttribute("height")) * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  canvas.width = rect.width;
  canvas.height = Number(canvas.getAttribute("height"));
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
window.addEventListener("resize", loadDashboard);
loadDashboard();
