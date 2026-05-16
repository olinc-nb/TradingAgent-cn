const form = document.querySelector("#visual-form");
const submitBtn = document.querySelector("#submit-btn");
const statusEl = document.querySelector("#status");
const statusText = statusEl.querySelector(".status-text");
const runtimeEl = document.querySelector("#runtime");
const runtimeText = runtimeEl.querySelector(".runtime-text");
const themeToggle = document.querySelector("#theme-toggle");
const chartTitle = document.querySelector("#chart-title");
const chartSubtitle = document.querySelector("#chart-subtitle");
const trendSubtitle = document.querySelector("#trend-subtitle");
const chartProvider = document.querySelector("#chart-provider");
const debateTitle = document.querySelector("#debate-title");
const debateSubtitle = document.querySelector("#debate-subtitle");
const debateFlow = document.querySelector("#debate-flow");
const klineCanvas = document.querySelector("#kline-chart");
const trendCanvas = document.querySelector("#trend-chart");

initTheme();
loadRuntimeStatus();
loadVisualization();

themeToggle.addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("ta-theme", next); } catch (_) {}
  loadVisualization(false);
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadVisualization();
});

window.addEventListener("resize", () => {
  const payload = window.__lastVisualizationPayload;
  if (!payload) return;
  drawKline(klineCanvas, payload.kline || []);
  drawTrend(trendCanvas, payload.trend || []);
});

async function loadVisualization(showBusy = true) {
  const formData = new FormData(form);
  const params = new URLSearchParams({
    symbol: formData.get("symbol") || "",
    depth: formData.get("depth") || "quick",
    include_decision: formData.get("include_decision") === "on" ? "true" : "false",
    model: (document.getElementById("model")?.value || "gpt-5.4"),
  });

  if (showBusy) {
    setStatus("正在加载真实 K 线、趋势和辩论链路...", "loading");
    setLoading(true);
    debateFlow.className = "debate-flow";
    debateFlow.innerHTML = skeletonHTML();
  }

  try {
    const response = await fetch(`/cn/visualization?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data?.detail?.message || "可视化数据加载失败");
    }
    window.__lastVisualizationPayload = data;
    renderVisualization(data);
    setStatus(`完成 · ${data.symbol} ${data.name || ""} · ${data.kline.length} 根 K 线`, "success");
  } catch (error) {
    setStatus(error.message || "请求失败", "error");
    clearCanvas(klineCanvas, "K 线数据加载失败");
    clearCanvas(trendCanvas, "趋势数据加载失败");
    debateFlow.className = "debate-flow empty";
    debateFlow.innerHTML = placeholderHTML("暂无辩论链路");
  } finally {
    setLoading(false);
  }
}

function renderVisualization(data) {
  chartTitle.textContent = `${data.symbol} ${data.name || ""}`.trim();
  chartSubtitle.textContent = `${data.kline[0]?.date || "-"} 至 ${data.kline.at(-1)?.date || "-"} · OHLC + Volume`;
  trendSubtitle.textContent = "收盘价、MA5、MA20 与单日动量同步展示。";
  chartProvider.textContent = `数据源 ${data.data_provider || "-"}`;
  debateTitle.textContent = `${data.symbol} · LLM 思考辩论可视化`;
  debateSubtitle.textContent = data.debate?.summary || "展示每个角色的立场、证据与最终流向。";
  drawKline(klineCanvas, data.kline || []);
  drawTrend(trendCanvas, data.trend || []);
  renderDebate(data.debate?.stages || []);
}

function drawKline(canvas, bars) {
  const ctx = setupCanvas(canvas);
  if (!bars.length) {
    drawEmpty(ctx, canvas, "暂无 K 线数据");
    return;
  }
  const styles = chartStyles();
  const box = plotBox(canvas, 48, 24, 70, 34);
  const maxHigh = Math.max(...bars.map((bar) => bar.high));
  const minLow = Math.min(...bars.map((bar) => bar.low));
  const maxVolume = Math.max(...bars.map((bar) => bar.volume), 1);
  const scaleY = makeScale(minLow, maxHigh, box.y + box.h, box.y);
  const volumeTop = box.y + box.h + 22;
  const volumeHeight = 46;
  const candleWidth = Math.max(isNarrowViewport() ? 2 : 3, Math.min(12, box.w / bars.length * 0.58));

  drawGrid(ctx, box, styles);
  bars.forEach((bar, index) => {
    const x = box.x + (index + 0.5) * (box.w / bars.length);
    const openY = scaleY(bar.open);
    const closeY = scaleY(bar.close);
    const highY = scaleY(bar.high);
    const lowY = scaleY(bar.low);
    const rising = bar.close >= bar.open;
    ctx.strokeStyle = rising ? styles.bull : styles.bear;
    ctx.fillStyle = rising ? styles.bullSoft : styles.bearSoft;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(2, Math.abs(closeY - openY));
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    ctx.strokeRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);

    const volumeHeightPx = Math.max(1, (bar.volume / maxVolume) * volumeHeight);
    ctx.globalAlpha = 0.38;
    ctx.fillRect(x - candleWidth / 2, volumeTop + volumeHeight - volumeHeightPx, candleWidth, volumeHeightPx);
    ctx.globalAlpha = 1;
  });
  drawAxisLabels(ctx, box, minLow, maxHigh, bars, styles);
}

function drawTrend(canvas, trend) {
  const ctx = setupCanvas(canvas);
  if (!trend.length) {
    drawEmpty(ctx, canvas, "暂无趋势数据");
    return;
  }
  const styles = chartStyles();
  const box = plotBox(canvas, 48, 24, 36, 34);
  const values = trend.flatMap((item) => [item.close, item.ma5, item.ma20]).filter((value) => Number.isFinite(value));
  const scaleY = makeScale(Math.min(...values), Math.max(...values), box.y + box.h, box.y);
  drawGrid(ctx, box, styles);
  drawLine(ctx, box, trend, "close", scaleY, styles.info, 2.2);
  drawLine(ctx, box, trend, "ma5", scaleY, styles.warn, 1.8);
  drawLine(ctx, box, trend, "ma20", scaleY, styles.accent, 1.8);
  drawAxisLabels(ctx, box, Math.min(...values), Math.max(...values), trend, styles);
  drawLegend(ctx, box, [
    ["Close", styles.info],
    ["MA5", styles.warn],
    ["MA20", styles.accent],
  ]);
}

function renderDebate(stages) {
  if (!stages.length) {
    debateFlow.className = "debate-flow empty";
    debateFlow.innerHTML = placeholderHTML("本次未开启辩论链路");
    return;
  }
  debateFlow.className = "debate-flow";
  debateFlow.innerHTML = stages.map((stage, index) => {
    const tone = stanceTone(stage.stance);
    const evidence = (stage.evidence || []).slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    return `
      <article class="debate-node tone-${tone}">
        <div class="node-index">${index + 1}</div>
        <div class="node-body">
          <div class="node-head">
            <span class="node-phase">${escapeHtml(stage.phase || "")}</span>
            <span class="badge ${tone}">${escapeHtml(stage.stance || "思考")}</span>
          </div>
          <h3>${escapeHtml(agentDisplayName(stage.agent))}</h3>
          <p>${escapeHtml(stage.summary || "")}</p>
          ${evidence ? `<ul class="list">${evidence}</ul>` : ""}
        </div>
      </article>
    `;
  }).join("");
}

function isNarrowViewport() {
  return window.innerWidth <= 640;
}

function effectiveCanvasHeight(canvas) {
  const baseHeight = Number(canvas.getAttribute("height"));
  if (!isNarrowViewport()) return baseHeight;
  const id = canvas.id || "";
  if (id === "kline-chart") return Math.min(baseHeight, 360);
  if (id === "trend-chart") return Math.min(baseHeight, 260);
  return Math.min(baseHeight, 280);
}

function effectiveCanvasWidth(canvas) {
  const parentWidth = canvas.parentElement.clientWidth;
  if (isNarrowViewport()) return Math.max(parentWidth, 480);
  return parentWidth;
}

function setupCanvas(canvas) {
  const cssWidth = effectiveCanvasWidth(canvas);
  const cssHeight = effectiveCanvasHeight(canvas);
  const dpr = window.devicePixelRatio || 1;
  if (isNarrowViewport() && cssWidth > canvas.parentElement.clientWidth) {
    canvas.style.width = `${cssWidth}px`;
  } else {
    canvas.style.width = "100%";
  }
  canvas.style.height = `${cssHeight}px`;
  canvas.width = Math.max(320, Math.floor(cssWidth * dpr));
  canvas.height = Math.floor(cssHeight * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);
  return ctx;
}

function plotBox(canvas, left, top, bottom, right) {
  const width = effectiveCanvasWidth(canvas);
  const height = effectiveCanvasHeight(canvas);
  if (isNarrowViewport()) {
    left = Math.max(34, left - 12);
    right = Math.max(18, right - 12);
    top = Math.max(18, top - 4);
    bottom = Math.max(40, bottom - 12);
  }
  return { x: left, y: top, w: Math.max(100, width - left - right), h: Math.max(120, height - top - bottom) };
}

function makeScale(min, max, outMin, outMax) {
  const pad = Math.max((max - min) * 0.08, 0.01);
  const lo = min - pad;
  const hi = max + pad;
  return (value) => outMin + ((value - lo) / (hi - lo)) * (outMax - outMin);
}

function drawGrid(ctx, box, styles) {
  ctx.strokeStyle = styles.grid;
  ctx.lineWidth = 1;
  ctx.font = "12px system-ui, sans-serif";
  for (let i = 0; i <= 4; i += 1) {
    const y = box.y + (box.h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(box.x, y);
    ctx.lineTo(box.x + box.w, y);
    ctx.stroke();
  }
}

function drawLine(ctx, box, points, key, scaleY, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  let started = false;
  points.forEach((point, index) => {
    const value = point[key];
    if (!Number.isFinite(value)) return;
    const x = box.x + (index + 0.5) * (box.w / points.length);
    const y = scaleY(value);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
}

function drawAxisLabels(ctx, box, min, max, points, styles) {
  ctx.fillStyle = styles.muted;
  ctx.font = `${isNarrowViewport() ? 10 : 12}px system-ui, sans-serif`;
  ctx.textAlign = "right";
  ctx.fillText(max.toFixed(2), box.x - 8, box.y + 4);
  ctx.fillText(min.toFixed(2), box.x - 8, box.y + box.h);
  ctx.textAlign = "left";
  ctx.fillText(points[0]?.date || "", box.x, box.y + box.h + 22);
  ctx.textAlign = "right";
  ctx.fillText(points.at(-1)?.date || "", box.x + box.w, box.y + box.h + 22);
}

function drawLegend(ctx, box, items) {
  ctx.font = "12px system-ui, sans-serif";
  let x = box.x;
  items.forEach(([label, color]) => {
    ctx.fillStyle = color;
    ctx.fillRect(x, box.y - 16, 18, 3);
    ctx.fillText(label, x + 24, box.y - 12);
    x += 80;
  });
}

function clearCanvas(canvas, message) {
  const ctx = setupCanvas(canvas);
  drawEmpty(ctx, canvas, message);
}

function drawEmpty(ctx, canvas, message) {
  const styles = chartStyles();
  ctx.fillStyle = styles.muted;
  ctx.font = "14px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(message, canvas.parentElement.clientWidth / 2, Number(canvas.getAttribute("height")) / 2);
}

function chartStyles() {
  const css = getComputedStyle(document.documentElement);
  return {
    accent: css.getPropertyValue("--accent").trim(),
    bull: css.getPropertyValue("--bull").trim(),
    bear: css.getPropertyValue("--bear").trim(),
    warn: css.getPropertyValue("--warn").trim(),
    info: css.getPropertyValue("--info").trim(),
    muted: css.getPropertyValue("--muted").trim(),
    grid: css.getPropertyValue("--line").trim(),
    bullSoft: css.getPropertyValue("--bull-soft").trim(),
    bearSoft: css.getPropertyValue("--bear-soft").trim(),
  };
}

function agentDisplayName(name) {
  const names = {
    "Market Analyst": "市场分析师",
    "Fundamentals Analyst": "基本面分析师",
    "News Analyst": "新闻分析师",
    "Sentiment Analyst": "情绪分析师",
    "Policy Analyst": "政策分析师",
    "Hot Money Tracker": "资金/游资追踪员",
    "Lockup Watcher": "解禁监控员",
    "Bull Researcher": "多方研究员",
    "Bear Researcher": "空方研究员",
    "Aggressive Risk Analyst": "进取型风控分析师",
    "Neutral Risk Analyst": "中性风控分析师",
    "Conservative Risk Analyst": "保守型风控分析师",
    "Research Manager": "研究经理",
    "Portfolio Manager": "组合经理",
    "Trader": "交易员",
  };
  return names[name] || name || "智能体";
}

function stanceTone(stance) {
  const text = String(stance || "").toLowerCase();
  if (/(多|bull|buy|买|强|流入|positive|建议买入)/.test(text)) return "bull";
  if (/(空|bear|sell|卖|弱|流出|negative|暂不|回避)/.test(text)) return "bear";
  if (/(谨慎|保守|风险|等待|观察|hold|持有)/.test(text)) return "warn";
  return "info";
}

function setStatus(message, state) {
  statusText.textContent = message;
  statusEl.classList.remove("status-idle", "status-loading", "status-success", "status-error");
  statusEl.classList.add(`status-${state || "idle"}`);
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  submitBtn.classList.toggle("loading", isLoading);
  submitBtn.querySelector(".btn-label").textContent = isLoading ? "加载中" : "刷新图表";
}

async function loadRuntimeStatus() {
  try {
    const response = await fetch("/cn/status");
    const status = await response.json();
    const llm = status.llm_enabled ? "LLM 已开启" : "LLM 未开启";
    const backend = status.backend_url_configured ? "自定义网关" : "官方接口";
    runtimeText.textContent = `${llm} · ${status.llm_provider}/${status.quick_model} · ${backend} · 数据源 ${status.data_provider}`;
    runtimeEl.classList.remove("warn", "error");
    runtimeEl.classList.add(status.llm_enabled ? "ready" : "warn");
  } catch (error) {
    runtimeText.textContent = "运行配置读取失败";
    runtimeEl.classList.remove("ready", "warn");
    runtimeEl.classList.add("error");
  }
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("ta-theme"); } catch (_) {}
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", saved || (prefersDark ? "dark" : "light"));
}

function skeletonHTML() {
  return `
    <div class="skeleton">
      <div class="skeleton-row short"></div>
      <div class="skeleton-row long"></div>
      <div class="skeleton-row block"></div>
      <div class="skeleton-row long"></div>
    </div>
  `;
}

function placeholderHTML(text) {
  return `
    <div class="placeholder">
      <span class="placeholder-icon" aria-hidden="true"></span>
      <p>${escapeHtml(text)}</p>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
