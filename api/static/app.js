const form = document.querySelector("#analysis-form");
const submitBtn = document.querySelector("#submit-btn");
const statusEl = document.querySelector("#status");
const statusText = statusEl.querySelector(".status-text");
const statusElapsed = document.querySelector("#status-elapsed");
const runtimeEl = document.querySelector("#runtime");
const runtimeText = runtimeEl.querySelector(".runtime-text");
const reportTitle = document.querySelector("#report-title");
const reportSubtitle = document.querySelector("#report-subtitle");
const reportContent = document.querySelector("#report-content");
const decisionTitle = document.querySelector("#decision-title");
const decisionSubtitle = document.querySelector("#decision-subtitle");
const decisionContent = document.querySelector("#decision-content");
const themeToggle = document.querySelector("#theme-toggle");
const analysisVisual = document.querySelector("#analysis-visual");
const chartTitle = document.querySelector("#chart-title");
const chartSubtitle = document.querySelector("#chart-subtitle");
const trendSubtitle = document.querySelector("#trend-subtitle");
const chartProvider = document.querySelector("#chart-provider");
const chartTicker = document.querySelector("#chart-ticker");
const tickClose = document.querySelector("#tick-close");
const tickChange = document.querySelector("#tick-change");
const tickPct = document.querySelector("#tick-pct");
const tickHigh = document.querySelector("#tick-high");
const tickLow = document.querySelector("#tick-low");
const tickVolume = document.querySelector("#tick-volume");
const debateTitle = document.querySelector("#debate-title");
const debateSubtitle = document.querySelector("#debate-subtitle");
const debateFlow = document.querySelector("#debate-flow");
const klineCanvas = document.querySelector("#kline-chart");
const trendCanvas = document.querySelector("#trend-chart");
const clockTime = document.querySelector("#clock-time");
const clockLabel = document.querySelector("#clock-label");
const marketClock = document.querySelector("#market-clock");
const symbolInput = document.querySelector("#symbol");
const hotSectorsEl = document.querySelector("#hot-sectors");
const hotSectorsGrid = document.querySelector("#hot-sectors-grid");
const resultGridEl = document.querySelector("#result-grid");
const landingEl = document.querySelector("#landing");
const brandEl = document.querySelector(".brand");
let progressStages = [];
let analysisStartedAt = 0;
let elapsedTimer = null;
let activeStageStartedAt = 0;
let nodeTickerTimer = null;
let hotSectorsState = [];
let hotSectorsTimer = null;

initTheme();
loadRuntimeStatus();
startClock();
initHotSectors();
clearCanvas(klineCanvas, "等待分析");
clearCanvas(trendCanvas, "等待分析");

themeToggle.addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("ta-theme", next); } catch (_) {}
  const payload = window.__lastInlineVisualizationPayload;
  if (payload) {
    drawKline(klineCanvas, payload.kline || []);
    drawTrend(trendCanvas, payload.trend || []);
  }
});

document.querySelectorAll(".symbol-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const value = chip.getAttribute("data-symbol");
    if (!value) return;
    symbolInput.value = value;
    symbolInput.focus();
  });
});

if (brandEl) {
  brandEl.style.cursor = "pointer";
  brandEl.setAttribute("role", "button");
  brandEl.setAttribute("aria-label", "返回首页");
  brandEl.addEventListener("click", () => {
    if (submitBtn.disabled) return;
    if (landingEl) landingEl.hidden = false;
    if (analysisVisual) {
      analysisVisual.hidden = true;
      analysisVisual.classList.remove("is-active");
    }
    if (resultGridEl) resultGridEl.hidden = true;
    setStatus("输入标的代码后开始分析，系统按最新交易日拉取公开数据。", "idle");
    if (statusElapsed) statusElapsed.hidden = true;
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
  const payload = {
    symbol: formData.get("symbol"),
    analysis_date: null,
    depth: formData.get("depth"),
    include_decision: formData.get("include_decision") === "on",
    model: (document.getElementById("model")?.value || "gpt-5.4"),
  };

  setStatus("分析中，正在按最新时间汇总公开信息、投研辩论和交易决策...", "loading");
  setLoading(true);
  startElapsedTimer();
  showInlineVisualization(payload);
  showSkeleton(reportContent);
  if (payload.include_decision) {
    showSkeleton(decisionContent);
  } else {
    decisionContent.className = "content empty";
    decisionContent.innerHTML = placeholderHTML("本次请求未开启决策板块");
  }

  try {
    const data = await consumeAnalysisStream(payload);
    setStatus(`完成 · ${data.report.symbol} ${data.report.name || ""}`, "success");
  } catch (error) {
    setStatus(error.message || "请求失败", "error");
    reportContent.className = "content empty";
    reportContent.innerHTML = placeholderHTML("暂无报告");
    decisionContent.className = "content empty";
    decisionContent.innerHTML = placeholderHTML("暂无决策");
  } finally {
    setLoading(false);
    stopElapsedTimer();
    stopNodeTicker();
  }
});

window.addEventListener("resize", () => {
  const payload = window.__lastInlineVisualizationPayload;
  if (!payload) return;
  drawKline(klineCanvas, payload.kline || []);
  drawTrend(trendCanvas, payload.trend || []);
});

function renderReport(report) {
  reportTitle.textContent = `${report.symbol} ${report.name || ""}`.trim() || "中性公开信息报告";
  reportSubtitle.textContent = `生成时间 ${formatTime(report.generated_at)}`;
  reportContent.className = "content";
  reportContent.innerHTML = [
    section("公司概览", report.company_overview),
    section("市场数据", report.market_data_summary),
    section("财务摘要", report.financial_summary),
    section("公告与新闻", joinText([report.announcement_summary, report.news_summary])),
    section("技术指标", report.technical_indicator_explanation),
    listSection("风险因素", report.risk_factors),
    listSection("数据限制", report.data_limitations),
    section("中性结论", report.neutral_summary),
    listSection(
      "运行记录",
      report.compliance_notes && report.compliance_notes.length
        ? report.compliance_notes
        : ["当前版本未启用合规改写。"]
    ),
    section("声明", report.disclaimer, "small"),
  ].join("");
}

function renderDecision(decision) {
  if (!decision) {
    decisionTitle.textContent = "未开启交易决策";
    decisionSubtitle.textContent = "勾选「包含交易决策」后再次分析即可查看。";
    decisionContent.className = "content empty";
    decisionContent.innerHTML = placeholderHTML("本次请求未开启决策板块");
    return;
  }

  const tone = actionTone(decision.simulated_action || decision.trade_decision);
  decisionTitle.textContent = `${decision.symbol} · ${decision.investment_recommendation || decision.simulated_action || "交易决策"}`;
  decisionSubtitle.textContent = `生成时间 ${formatTime(decision.generated_at)}`;
  decisionContent.className = "content";
  decisionContent.innerHTML = [
    decisionHero(decision, tone),
    decisionSummary(decision, tone),
    pegSection(decision.peg_metrics),
    agentGroup("分析师团队", decision.analyst_reports),
    debateSection(decision.investment_debate),
    section("交易员计划", decision.trader_plan),
    riskSection(decision.risk_debate),
    listSection("关键驱动", decision.key_drivers),
    listSection("风控约束", decision.risk_controls),
    listSection("决策依据", decision.decision_basis),
    section("声明", decision.disclaimer, "small"),
  ].join("");
}

function pegSection(peg) {
  if (!peg || typeof peg !== "object") return "";
  const has = (key) => peg[key] !== null && peg[key] !== undefined && peg[key] !== "";
  if (!has("peg") && !has("forward_pe") && !has("cagr_pct")) {
    const notes = (peg.notes || []).slice(0, 3).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
    if (!notes) return "";
    return `<div class="section"><h3>PEG 估值（彼得·林奇）</h3><ul class="muted-list">${notes}</ul></div>`;
  }
  const fmt = (v, suffix = "", digits = 2) =>
    v === null || v === undefined || v === "" || Number.isNaN(Number(v))
      ? "—"
      : Number(v).toFixed(digits) + suffix;
  const ratingZone = peg.rating_zone || "";
  const tone = ratingZone === "deep-value" || ratingZone === "undervalued"
    ? "bull"
    : ratingZone === "rich"
    ? "warn"
    : ratingZone === "overvalued"
    ? "bear"
    : "";
  const notesHtml = (peg.notes || []).slice(0, 4).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  return `
    <div class="section">
      <h3>PEG 估值（彼得·林奇）</h3>
      <div class="decision-summary">
        <div class="metric ${tone}"><span>PEG</span><strong>${fmt(peg.peg)}</strong></div>
        <div class="metric"><span>评级</span><strong>${escapeHtml(peg.rating || "—")}</strong></div>
        <div class="metric"><span>前瞻 PE</span><strong>${fmt(peg.forward_pe, "×")}</strong></div>
        <div class="metric"><span>净利润 CAGR</span><strong>${fmt(peg.cagr_pct, "%")}</strong></div>
        <div class="metric"><span>消化年限 (锚 30×)</span><strong>${fmt(peg.digestion_years, " 年")}</strong></div>
        <div class="metric"><span>一致预期 EPS (${escapeHtml(peg.consensus_eps_year || "—")})</span><strong>${fmt(peg.consensus_eps)}</strong></div>
      </div>
      ${notesHtml ? `<ul class="muted-list">${notesHtml}</ul>` : ""}
    </div>
  `;
}

function decisionHero(decision, tone) {
  const action = decision.simulated_action || decision.trade_decision || "-";
  const recommendation = decision.investment_recommendation || "-";
  const confidence = decision.confidence || "-";
  const horizon = decision.horizon || "-";
  const confPct = parseConfidence(confidence);
  const confDisplay = confPct != null ? `${confPct}%` : escapeHtml(confidence);
  return `
    <div class="decision-hero ${tone}">
      <div class="decision-hero-main">
        <div class="decision-hero-label">组合经理模拟动作</div>
        <div class="decision-hero-value">${escapeHtml(action)}</div>
        <div class="decision-hero-meta">
          <span class="decision-hero-meta-item">投资建议 <strong>${escapeHtml(recommendation)}</strong></span>
          <span class="decision-hero-meta-item">周期 <strong>${escapeHtml(horizon)}</strong></span>
        </div>
      </div>
      <div class="decision-hero-side">
        <div class="confidence-row">
          <div class="confidence-label">
            <span>决策置信度</span>
            <strong>${confDisplay}</strong>
          </div>
          <div class="confidence-bar">
            <div class="confidence-fill" style="width: ${confPct != null ? confPct : 50}%"></div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function parseConfidence(value) {
  if (value == null) return null;
  const text = String(value).trim();
  const pct = text.match(/(\d+(?:\.\d+)?)\s*%/);
  if (pct) return Math.min(100, Math.round(Number(pct[1])));
  const decimal = text.match(/^0?\.(\d+)/);
  if (decimal) return Math.min(100, Math.round(parseFloat("0." + decimal[1]) * 100));
  if (/(高|强|强烈|high)/i.test(text)) return 78;
  if (/(中|中等|medium|moderate)/i.test(text)) return 55;
  if (/(低|弱|low|weak)/i.test(text)) return 28;
  return null;
}

function decisionSummary(decision, tone) {
  const items = [
    ["投资建议", decision.investment_recommendation, tone],
    ["交易动作", decision.trade_decision, tone],
    ["目标区间", decision.target_price_range || "-", ""],
    ["仓位建议", decision.position_suggestion || "-", ""],
    ["止损参考", decision.stop_loss || "-", "bear"],
    ["止盈参考", decision.take_profit || "-", "bull"],
  ];
  return `<div class="decision-summary">${items
    .map(([label, value, mod]) => {
      const isPlaceholder = !value || value === "-";
      const modClass = isPlaceholder ? "" : mod;
      return `<div class="metric ${modClass}"><span>${label}</span><strong>${escapeHtml(value || "-")}</strong></div>`;
    })
    .join("")}</div>`;
}

function agentGroup(title, agents) {
  if (!agents || !agents.length) return "";
  const cards = agents.map((a) => agentCard(a, stanceTone(a.stance))).join("");
  return `<div class="section"><h3>${escapeHtml(title)}</h3><div class="agents-group">${cards}</div></div>`;
}

function debateSection(debate) {
  if (!debate) return "";
  const bull = agentCard(debate.bull_case, "bull");
  const bear = agentCard(debate.bear_case, "bear");
  const synth = debate.manager_synthesis
    ? `<div class="debate-synthesis">${escapeHtml(debate.manager_synthesis)}</div>`
    : "";
  return `<div class="section"><h3>投研辩论</h3><div class="debate-grid">${bull}${bear}</div>${synth}</div>`;
}

function riskSection(risk) {
  if (!risk) return "";
  const items = [
    agentCard(risk.aggressive_view, "bear"),
    agentCard(risk.neutral_view, "info"),
    agentCard(risk.conservative_view, "warn"),
  ].join("");
  const decision = risk.portfolio_manager_decision
    ? `<div class="debate-synthesis">${escapeHtml(risk.portfolio_manager_decision)}</div>`
    : "";
  return `<div class="section"><h3>风控委员会</h3><div class="risk-grid">${items}</div>${decision}</div>`;
}

function agentCard(agent, tone = "") {
  if (!agent) return "";
  const evidence = agent.evidence && agent.evidence.length
    ? `<ul class="list">${agent.evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : "";
  const badgeTone = tone || stanceTone(agent.stance);
  return `
    <div class="agent tone-${badgeTone}">
      <div class="agent-title">
        <span>${escapeHtml(agentDisplayName(agent.agent))}</span>
        <span class="badge ${badgeTone}">${escapeHtml(agent.stance || "-")}</span>
      </div>
      <p>${escapeHtml(agent.summary || "")}</p>
      ${evidence}
    </div>
  `;
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
    "PEG Analyst": "PEG 估值分析师",
    "Bull Researcher": "多方研究员",
    "Bear Researcher": "空方研究员",
    "Aggressive Risk Analyst": "进取型风控分析师",
    "Neutral Risk Analyst": "中性风控分析师",
    "Conservative Risk Analyst": "保守型风控分析师",
    "Research Manager": "研究经理",
    "Portfolio Manager": "组合经理",
    "Trader": "交易员",
  };
  return names[name] || name || "分析师";
}

function actionTone(action) {
  const text = String(action || "").toLowerCase();
  if (/(buy|bull|加仓|买入|做多|看多|增持)/.test(text)) return "bull";
  if (/(sell|bear|减仓|卖出|做空|看空|减持|清仓)/.test(text)) return "bear";
  if (/(hold|观望|持有|中性|不变)/.test(text)) return "warn";
  return "info";
}

function stanceTone(stance) {
  const text = String(stance || "").toLowerCase();
  if (/(多|bull|bullish|看多|买|乐观|positive)/.test(text)) return "bull";
  if (/(空|bear|bearish|看空|卖|悲观|negative)/.test(text)) return "bear";
  if (/(中性|neutral|观望|hold)/.test(text)) return "info";
  if (/(保守|conservative|谨慎|cautious)/.test(text)) return "warn";
  if (/(进取|激进|aggressive)/.test(text)) return "bear";
  return "info";
}

function section(title, body, className = "") {
  const text = escapeHtml(body || "").replaceAll("\n", "<br>");
  return `<div class="section ${className}"><h3>${escapeHtml(title)}</h3><p>${text}</p></div>`;
}

function listSection(title, items) {
  const list = (items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `<div class="section"><h3>${escapeHtml(title)}</h3><ul class="list">${list}</ul></div>`;
}

function joinText(parts) {
  return (parts || []).filter((p) => p && String(p).trim()).join("\n\n");
}

function setStatus(message, state) {
  statusText.textContent = message;
  statusEl.classList.remove("status-idle", "status-loading", "status-success", "status-error");
  statusEl.classList.add(`status-${state || "idle"}`);
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  submitBtn.classList.toggle("loading", isLoading);
  submitBtn.querySelector(".btn-label").textContent = isLoading ? "分析中" : "开始分析";
}

function showSkeleton(target) {
  target.className = "content";
  target.innerHTML = `
    <div class="skeleton">
      <div class="skeleton-row short"></div>
      <div class="skeleton-row long"></div>
      <div class="skeleton-row long"></div>
      <div class="skeleton-row mid"></div>
      <div class="skeleton-row block"></div>
      <div class="skeleton-row long"></div>
      <div class="skeleton-row mid"></div>
      <div class="skeleton-row block"></div>
    </div>
  `;
}

function showInlineVisualization(payload) {
  if (landingEl) landingEl.hidden = true;
  if (resultGridEl) resultGridEl.hidden = false;
  if (analysisVisual) analysisVisual.hidden = false;
  analysisVisual.classList.add("is-active");
  chartTitle.textContent = `${payload.symbol || ""} · K 线图`;
  chartSubtitle.textContent = "正在拉取行情数据并准备 OHLC 图表。";
  trendSubtitle.textContent = "正在计算收盘价、MA5、MA20。";
  chartProvider.textContent = "行情加载中";
  clearCanvas(klineCanvas, "K 线加载中");
  clearCanvas(trendCanvas, "趋势加载中");
  if (payload.include_decision) {
    resetProgressStages();
    renderProgressFlow("等待后端流式事件");
  } else {
    resetProgressStages(false);
    renderProgressFlow("本次未开启交易决策");
  }
}

async function consumeAnalysisStream(payload) {
  const params = new URLSearchParams({
    symbol: payload.symbol || "",
    depth: payload.depth || "quick",
    include_decision: payload.include_decision ? "true" : "false",
    model: payload.model || "gpt-5.4",
  });
  const response = await fetch(`/cn/analyze/stream?${params.toString()}`);
  if (!response.ok) {
    throw new Error("流式分析启动失败");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      const handled = handleStreamEvent(event);
      if (event.event === "done") finalPayload = handled;
      if (event.event === "error") throw new Error(event.label || "分析失败");
    }
  }
  if (!finalPayload) throw new Error("分析未返回最终结果");
  return finalPayload;
}

function handleStreamEvent(event) {
  if (event.event === "market_data_done" && event.visualization) {
    renderInlineCharts(event.visualization);
  }
  if (event.report) {
    renderReport(event.report);
  }
  if (event.decision) {
    renderDecision(event.decision);
  }
  updateProgressFromEvent(event);
  if (event.event === "done") {
    return { report: event.report, decision: event.decision };
  }
  return null;
}

function renderInlineCharts(data) {
  window.__lastInlineVisualizationPayload = data;
  chartTitle.textContent = `${data.symbol} ${data.name || ""}`.trim();
  chartSubtitle.textContent = `${data.kline[0]?.date || "-"} 至 ${data.kline.at(-1)?.date || "-"} · OHLC + Volume`;
  trendSubtitle.textContent = "收盘价、MA5、MA20 与单日动量同步展示。";
  chartProvider.textContent = `数据源 ${data.data_provider || "-"}`;
  drawKline(klineCanvas, data.kline || []);
  drawTrend(trendCanvas, data.trend || []);
  renderTicker(data.kline || []);
}

function renderTicker(bars) {
  if (!bars || !bars.length) {
    chartTicker.hidden = true;
    return;
  }
  chartTicker.hidden = false;
  const last = bars[bars.length - 1];
  const prev = bars.length > 1 ? bars[bars.length - 2] : last;
  const change = last.close - prev.close;
  const pct = prev.close ? (change / prev.close) * 100 : 0;
  const tone = change > 0 ? "bull" : change < 0 ? "bear" : "muted";
  const sign = change > 0 ? "+" : "";
  tickClose.textContent = formatPrice(last.close);
  tickClose.className = `tick-value ${tone}`;
  tickChange.textContent = `${sign}${formatPrice(change)}`;
  tickChange.className = `tick-value ${tone}`;
  tickPct.textContent = `${sign}${pct.toFixed(2)}%`;
  tickPct.className = `tick-value ${tone}`;
  tickHigh.textContent = formatPrice(last.high);
  tickLow.textContent = formatPrice(last.low);
  tickVolume.textContent = formatVolume(last.volume);
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

function formatVolume(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(0);
}

function resetProgressStages(includeDecision = true) {
  progressStages = [
    { id: "market", phase: "行情", agent: "Market Data", label: "等待行情数据", status: "pending", elapsed: null },
    { id: "report", phase: "报告", agent: "Neutral Report", label: "等待中性报告", status: "pending", elapsed: null },
    { id: "decision_data", phase: "决策数据", agent: "Decision Context", label: "等待决策上下文", status: includeDecision ? "pending" : "skip", elapsed: null },
    { id: "peg", phase: "PEG 估值", agent: "PEG Analyst", label: "等待 PEG 数据", status: includeDecision ? "pending" : "skip", elapsed: null },
    { id: "analysts", phase: "分析师 LLM", agent: "8 Analysts", label: "等待 quick 模型", status: includeDecision ? "pending" : "skip", elapsed: null },
    { id: "portfolio", phase: "组合经理 LLM", agent: "Portfolio Manager", label: "等待 deep 模型", status: includeDecision ? "pending" : "skip", elapsed: null },
    { id: "final", phase: "完成", agent: "Result", label: "等待最终结果", status: "pending", elapsed: null },
  ];
}

function updateProgressFromEvent(event) {
  const map = {
    market_data_start: ["market", "active"],
    market_data_done: ["market", "done"],
    report_start: ["report", "active"],
    report_done: ["report", "done"],
    decision_data_start: ["decision_data", "active"],
    decision_data_done: ["decision_data", "done"],
    peg_start: ["peg", "active"],
    peg_done: ["peg", "done"],
    analysts_llm_start: ["analysts", "active"],
    analysts_llm_done: ["analysts", "done"],
    portfolio_llm_full_start: ["portfolio", "active"],
    portfolio_llm_compact_retry: ["portfolio", "warn"],
    portfolio_llm_compact_done: ["portfolio", "active"],
    portfolio_llm_done: ["portfolio", "done"],
    llm_decision_fallback: ["portfolio", "error"],
    decision_done: ["final", "active"],
    done: ["final", "done"],
    error: ["final", "error"],
  };
  const change = map[event.event];
  if (!change) return;
  const [id, status] = change;
  const stage = progressStages.find((item) => item.id === id);
  if (!stage) return;
  const prevStatus = stage.status;
  stage.status = status;
  stage.label = event.label || stage.label;
  stage.elapsed = event.elapsed;
  if (status === "active" && prevStatus !== "active") {
    stage.startedAt = Date.now();
  }
  if (status === "done" || status === "error") {
    stage.startedAt = null;
  }
  if (event.event === "portfolio_llm_compact_retry") {
    stage.detail = "完整上下文被网关断开，已切换为压缩上下文继续 LLM 综合。";
  }
  if (event.event === "llm_decision_fallback") {
    stage.detail = event.error
      ? `LLM 决策层失败，已使用确定性决策回退。错误：${event.error}`
      : "LLM 决策层失败，已使用确定性决策回退。";
  }
  renderProgressFlow(event.label || "分析进行中");
  ensureNodeTicker();
}

function renderProgressFlow(summary) {
  debateTitle.textContent = "LLM 思考辩论过程";
  debateSubtitle.textContent = summary || "流式展示后端实际执行阶段，详细观点在下方交易决策模块展示。";
  debateFlow.className = "debate-flow compact progress-flow";
  debateFlow.innerHTML = progressStages.map((stage, index) => {
    const tone = progressTone(stage.status);
    const elapsedDisplay = formatStageElapsed(stage);
    return `
      <article class="debate-node tone-${tone} state-${progressStateClass(stage.status)}" data-stage-id="${escapeHtml(stage.id)}">
        <div class="node-index">${stage.status === "active" ? "" : index + 1}</div>
        <div class="node-body">
          <div class="node-head">
            <span class="node-phase">${escapeHtml(stage.phase)}</span>
            <span class="badge ${tone}">${escapeHtml(progressLabel(stage.status))}</span>
          </div>
          <h3>${escapeHtml(stage.agent)}</h3>
          <p>${escapeHtml(stage.label)}</p>
          <span class="node-elapsed" data-elapsed-for="${escapeHtml(stage.id)}">${elapsedDisplay}</span>
          ${stage.detail ? `<ul class="list"><li>${escapeHtml(stage.detail)}</li></ul>` : ""}
        </div>
      </article>
    `;
  }).join("");
}

function formatStageElapsed(stage) {
  if (stage.status === "active" && stage.startedAt) {
    const seconds = (Date.now() - stage.startedAt) / 1000;
    return `${seconds.toFixed(1)}s 进行中`;
  }
  if (stage.elapsed != null) {
    return `${Number(stage.elapsed).toFixed(1)}s`;
  }
  if (stage.status === "pending") return "等待事件";
  if (stage.status === "skip") return "已跳过";
  if (stage.status === "error") return "失败";
  return "—";
}

function ensureNodeTicker() {
  const hasActive = progressStages.some((s) => s.status === "active" && s.startedAt);
  if (hasActive && !nodeTickerTimer) {
    nodeTickerTimer = setInterval(() => {
      progressStages.forEach((stage) => {
        if (stage.status !== "active" || !stage.startedAt) return;
        const el = debateFlow.querySelector(`[data-elapsed-for="${stage.id}"]`);
        if (el) {
          const seconds = (Date.now() - stage.startedAt) / 1000;
          el.textContent = `${seconds.toFixed(1)}s 进行中`;
        }
      });
    }, 100);
  } else if (!hasActive && nodeTickerTimer) {
    stopNodeTicker();
  }
}

function stopNodeTicker() {
  if (nodeTickerTimer) {
    clearInterval(nodeTickerTimer);
    nodeTickerTimer = null;
  }
}

function progressTone(status) {
  if (status === "done") return "ok";
  if (status === "active") return "info";
  if (status === "warn") return "warn";
  if (status === "error") return "warn";
  return "info";
}

function progressStateClass(status) {
  if (status === "active") return "active";
  if (status === "done") return "done";
  if (status === "skip") return "pending";
  if (status === "warn") return "active";
  if (status === "error") return "done";
  return "pending";
}

function progressLabel(status) {
  const labels = {
    pending: "等待",
    active: "进行中",
    done: "完成",
    warn: "重试中",
    error: "失败",
    skip: "跳过",
  };
  return labels[status] || "等待";
}

async function loadInlineVisualization(payload) {
  const params = new URLSearchParams({
    symbol: payload.symbol || "",
    depth: payload.depth || "quick",
    include_decision: "false",
    model: payload.model || "gpt-5.4",
  });
  const response = await fetch(`/cn/visualization?${params.toString()}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail?.message || "可视化数据加载失败");
  }
  renderInlineCharts(data);
  return data;
}

function thinkingStages() {
  return [
    {
      phase: "行情",
      agent: "Market Analyst",
      stance: "读取中",
      summary: "拉取 K 线、成交量、均线和技术指标，建立市场上下文。",
      evidence: ["OHLC", "MA5 / MA20", "成交量"],
      state: "active",
    },
    {
      phase: "基本面",
      agent: "Fundamentals Analyst",
      stance: "排队中",
      summary: "汇总财务、估值和经营质量线索。",
      evidence: ["财务摘要", "估值字段", "现金流"],
      state: "pending",
    },
    {
      phase: "消息面",
      agent: "News Analyst",
      stance: "排队中",
      summary: "读取公告、新闻、政策和资金面事件。",
      evidence: ["公告", "新闻", "政策"],
      state: "pending",
    },
    {
      phase: "投研辩论",
      agent: "Research Manager",
      stance: "等待交锋",
      summary: "多方和空方研究员会围绕证据强弱进行交叉论证。",
      evidence: ["Bull Case", "Bear Case"],
      state: "pending",
    },
    {
      phase: "交易员",
      agent: "Trader",
      stance: "等待计划",
      summary: "结合 A 股 T+1、涨跌停和仓位约束生成交易计划。",
      evidence: ["交易动作", "仓位", "止盈止损"],
      state: "pending",
    },
    {
      phase: "风控",
      agent: "Portfolio Manager",
      stance: "等待决策",
      summary: "进取、中性、保守风控讨论后输出组合经理结论。",
      evidence: ["风险约束", "最终动作"],
      state: "pending",
    },
  ];
}

function decisionToDebateStages(decision) {
  const stages = [];
  (decision.analyst_reports || []).forEach((analyst) => stages.push({ phase: "分析师", ...analyst, state: "done" }));
  const debate = decision.investment_debate || {};
  if (debate.bull_case) stages.push({ phase: "多方研究", ...debate.bull_case, state: "done" });
  if (debate.bear_case) stages.push({ phase: "空方研究", ...debate.bear_case, state: "done" });
  if (debate.manager_synthesis) {
    stages.push({
      phase: "研究经理",
      agent: "Research Manager",
      stance: "综合",
      summary: debate.manager_synthesis,
      evidence: [],
      state: "done",
    });
  }
  if (decision.trader_plan) {
    stages.push({
      phase: "交易员",
      agent: "Trader",
      stance: decision.trade_decision || "",
      summary: decision.trader_plan,
      evidence: decision.key_drivers || [],
      state: "done",
    });
  }
  const risk = decision.risk_debate || {};
  if (risk.aggressive_view) stages.push({ phase: "进取风控", ...risk.aggressive_view, state: "done" });
  if (risk.neutral_view) stages.push({ phase: "中性风控", ...risk.neutral_view, state: "done" });
  if (risk.conservative_view) stages.push({ phase: "保守风控", ...risk.conservative_view, state: "done" });
  if (risk.portfolio_manager_decision) {
    stages.push({
      phase: "组合经理",
      agent: "Portfolio Manager",
      stance: decision.simulated_action || decision.investment_recommendation || "",
      summary: risk.portfolio_manager_decision,
      evidence: decision.risk_controls || [],
      state: "done",
    });
  }
  return stages;
}

function renderDebate(stages, summary) {
  debateTitle.textContent = "LLM 思考辩论过程";
  debateSubtitle.textContent = summary || "分析师、投研辩论、交易员、风控和组合经理的推进状态。";
  if (!stages.length) {
    debateFlow.className = "debate-flow empty";
    debateFlow.innerHTML = placeholderHTML("本次未开启辩论链路");
    return;
  }
  debateFlow.className = "debate-flow compact";
  debateFlow.innerHTML = stages.map((stage, index) => {
    const tone = stanceTone(stage.stance);
    const evidence = (stage.evidence || []).slice(0, 2).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    return `
      <article class="debate-node tone-${tone} state-${stage.state || "done"}">
        <div class="node-index">${stage.state === "active" ? "" : index + 1}</div>
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

function drawKline(canvas, bars) {
  const ctx = setupCanvas(canvas);
  if (!bars.length) {
    drawEmpty(ctx, canvas, "暂无 K 线数据");
    return;
  }
  const styles = chartStyles();
  const box = plotBox(canvas, 52, 28, 64, 24);
  const maxHigh = Math.max(...bars.map((bar) => bar.high));
  const minLow = Math.min(...bars.map((bar) => bar.low));
  const maxVolume = Math.max(...bars.map((bar) => bar.volume), 1);
  const scaleY = makeScale(minLow, maxHigh, box.y + box.h, box.y);
  const volumeTop = box.y + box.h + 18;
  const volumeHeight = 40;
  const candleWidth = Math.max(isNarrowViewport() ? 2 : 3, Math.min(11, box.w / bars.length * 0.62));
  drawGrid(ctx, box, styles, { vertical: true });
  bars.forEach((bar, index) => {
    const x = box.x + (index + 0.5) * (box.w / bars.length);
    const openY = scaleY(bar.open);
    const closeY = scaleY(bar.close);
    const highY = scaleY(bar.high);
    const lowY = scaleY(bar.low);
    const rising = bar.close >= bar.open;
    ctx.strokeStyle = rising ? styles.bull : styles.bear;
    ctx.fillStyle = rising ? styles.bull : styles.bear;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + 0.5, highY);
    ctx.lineTo(x + 0.5, lowY);
    ctx.stroke();
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(1, Math.abs(closeY - openY));
    if (rising) {
      ctx.fillStyle = styles.bgElev;
      ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
      ctx.strokeRect(x - candleWidth / 2 + 0.5, bodyTop + 0.5, candleWidth - 1, bodyHeight - 1);
    } else {
      ctx.fillStyle = styles.bear;
      ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    }
    ctx.globalAlpha = 0.4;
    ctx.fillStyle = rising ? styles.bull : styles.bear;
    const volumeHeightPx = Math.max(1, (bar.volume / maxVolume) * volumeHeight);
    ctx.fillRect(x - candleWidth / 2, volumeTop + volumeHeight - volumeHeightPx, candleWidth, volumeHeightPx);
    ctx.globalAlpha = 1;
  });
  const lastBar = bars[bars.length - 1];
  const lastY = scaleY(lastBar.close);
  ctx.strokeStyle = styles.muted;
  ctx.setLineDash([3, 3]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(box.x, lastY);
  ctx.lineTo(box.x + box.w, lastY);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = lastBar.close >= bars[0].open ? styles.bull : styles.bear;
  ctx.fillRect(box.x + box.w + 2, lastY - 9, 46, 18);
  ctx.fillStyle = "white";
  ctx.font = `600 11px ${styles.fontMono}`;
  ctx.textAlign = "center";
  ctx.fillText(lastBar.close.toFixed(2), box.x + box.w + 25, lastY + 4);
  drawAxisLabels(ctx, box, minLow, maxHigh, bars, styles);
  drawVolumeLabel(ctx, box, volumeTop, volumeHeight, styles);
}

function drawTrend(canvas, trend) {
  const ctx = setupCanvas(canvas);
  if (!trend.length) {
    drawEmpty(ctx, canvas, "暂无趋势数据");
    return;
  }
  const styles = chartStyles();
  const box = plotBox(canvas, 52, 36, 36, 24);
  const values = trend.flatMap((item) => [item.close, item.ma5, item.ma20]).filter((value) => Number.isFinite(value));
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const scaleY = makeScale(minV, maxV, box.y + box.h, box.y);
  drawGrid(ctx, box, styles, { vertical: true });
  drawArea(ctx, box, trend, "close", scaleY, styles.info);
  drawLine(ctx, box, trend, "close", scaleY, styles.info, 2);
  drawLine(ctx, box, trend, "ma5", scaleY, styles.warn, 1.4);
  drawLine(ctx, box, trend, "ma20", scaleY, styles.accent, 1.4);
  drawAxisLabels(ctx, box, minV, maxV, trend, styles);
  drawLegend(ctx, box, [["Close", styles.info], ["MA5", styles.warn], ["MA20", styles.accent]], styles);
}

function isNarrowViewport() {
  return window.innerWidth <= 640;
}

function effectiveCanvasHeight(canvas) {
  const baseHeight = Number(canvas.getAttribute("height"));
  if (!isNarrowViewport()) return baseHeight;
  const id = canvas.id || "";
  if (id === "kline-chart") return Math.min(baseHeight, 320);
  if (id === "trend-chart") return Math.min(baseHeight, 240);
  return Math.min(baseHeight, 280);
}

function effectiveCanvasWidth(canvas) {
  const parentWidth = canvas.parentElement.clientWidth - 32;
  if (isNarrowViewport()) return Math.max(parentWidth, 480);
  return parentWidth;
}

function setupCanvas(canvas) {
  const cssWidth = effectiveCanvasWidth(canvas);
  const cssHeight = effectiveCanvasHeight(canvas);
  const dpr = window.devicePixelRatio || 1;
  if (isNarrowViewport() && cssWidth > canvas.parentElement.clientWidth - 32) {
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
    left = Math.max(36, left - 12);
    right = Math.max(16, right - 8);
    top = Math.max(20, top - 6);
    bottom = Math.max(20, bottom - 8);
  }
  return { x: left, y: top, w: Math.max(120, width - left - right), h: Math.max(100, height - top - bottom) };
}

function makeScale(min, max, outMin, outMax) {
  const pad = Math.max((max - min) * 0.08, 0.01);
  const lo = min - pad;
  const hi = max + pad;
  return (value) => outMin + ((value - lo) / (hi - lo)) * (outMax - outMin);
}

function drawGrid(ctx, box, styles, opts = {}) {
  ctx.strokeStyle = styles.grid;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = Math.round(box.y + (box.h / 4) * i) + 0.5;
    ctx.beginPath();
    ctx.moveTo(box.x, y);
    ctx.lineTo(box.x + box.w, y);
    ctx.stroke();
  }
  if (opts.vertical) {
    for (let i = 1; i < 6; i += 1) {
      const x = Math.round(box.x + (box.w / 6) * i) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, box.y);
      ctx.lineTo(x, box.y + box.h);
      ctx.stroke();
    }
  }
}

function drawLine(ctx, box, points, key, scaleY, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
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

function drawArea(ctx, box, points, key, scaleY, color) {
  const gradient = ctx.createLinearGradient(0, box.y, 0, box.y + box.h);
  gradient.addColorStop(0, withAlpha(color, 0.22));
  gradient.addColorStop(1, withAlpha(color, 0));
  ctx.fillStyle = gradient;
  ctx.beginPath();
  let started = false;
  let lastX = box.x;
  points.forEach((point, index) => {
    const value = point[key];
    if (!Number.isFinite(value)) return;
    const x = box.x + (index + 0.5) * (box.w / points.length);
    const y = scaleY(value);
    lastX = x;
    if (!started) {
      ctx.moveTo(x, box.y + box.h);
      ctx.lineTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  if (started) {
    ctx.lineTo(lastX, box.y + box.h);
    ctx.closePath();
    ctx.fill();
  }
}

function withAlpha(color, alpha) {
  if (!color) return `rgba(0,0,0,${alpha})`;
  const c = color.trim();
  if (c.startsWith("#")) {
    const hex = c.slice(1);
    const r = parseInt(hex.length === 3 ? hex[0] + hex[0] : hex.slice(0, 2), 16);
    const g = parseInt(hex.length === 3 ? hex[1] + hex[1] : hex.slice(2, 4), 16);
    const b = parseInt(hex.length === 3 ? hex[2] + hex[2] : hex.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  if (c.startsWith("rgb")) {
    return c.replace(/rgba?\(([^)]+)\)/, (_, body) => {
      const parts = body.split(",").map((p) => p.trim());
      return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`;
    });
  }
  return c;
}

function drawAxisLabels(ctx, box, min, max, points, styles) {
  ctx.fillStyle = styles.muted;
  const labelSize = isNarrowViewport() ? 9 : 10.5;
  ctx.font = `500 ${labelSize}px ${styles.fontMono}`;
  ctx.textAlign = "right";
  const ticks = isNarrowViewport() ? 3 : 4;
  for (let i = 0; i <= ticks; i += 1) {
    const value = max - ((max - min) / ticks) * i;
    const y = box.y + (box.h / ticks) * i + 4;
    ctx.fillText(value.toFixed(2), box.x - 8, y);
  }
  ctx.textAlign = "left";
  ctx.fillText(points[0]?.date || "", box.x, box.y + box.h + 20);
  if (points.length > 2) {
    const mid = points[Math.floor(points.length / 2)]?.date || "";
    ctx.textAlign = "center";
    ctx.fillText(mid, box.x + box.w / 2, box.y + box.h + 20);
  }
  ctx.textAlign = "right";
  ctx.fillText(points.at(-1)?.date || "", box.x + box.w, box.y + box.h + 20);
}

function drawVolumeLabel(ctx, box, volumeTop, volumeHeight, styles) {
  ctx.fillStyle = styles.mutedSoft;
  ctx.font = `600 9px ${styles.fontMono}`;
  ctx.textAlign = "right";
  ctx.fillText("VOL", box.x - 8, volumeTop + volumeHeight / 2 + 3);
}

function drawLegend(ctx, box, items, styles) {
  ctx.font = `500 10.5px ${styles.fontMono}`;
  ctx.textBaseline = "middle";
  let x = box.x;
  const y = box.y - 16;
  items.forEach(([label, color]) => {
    ctx.fillStyle = color;
    ctx.fillRect(x, y - 1, 14, 2);
    ctx.fillStyle = styles.muted;
    ctx.fillText(label, x + 20, y);
    x += 64;
  });
  ctx.textBaseline = "alphabetic";
}

function clearCanvas(canvas, message) {
  const ctx = setupCanvas(canvas);
  drawEmpty(ctx, canvas, message);
}

function drawEmpty(ctx, canvas, message) {
  const styles = chartStyles();
  ctx.fillStyle = styles.mutedSoft;
  ctx.font = `500 12px ${styles.fontMono}`;
  ctx.textAlign = "center";
  ctx.fillText(message, (canvas.parentElement.clientWidth - 32) / 2, Number(canvas.getAttribute("height")) / 2);
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
    mutedSoft: css.getPropertyValue("--muted-soft").trim(),
    grid: css.getPropertyValue("--line").trim(),
    bullSoft: css.getPropertyValue("--bull-soft").trim(),
    bearSoft: css.getPropertyValue("--bear-soft").trim(),
    ok: css.getPropertyValue("--ok").trim(),
    bgElev: css.getPropertyValue("--bg-elev").trim(),
    fontMono: 'JetBrains Mono, ui-monospace, "SF Mono", Menlo, Consolas, monospace',
  };
}

function placeholderHTML(text) {
  return `
    <div class="placeholder">
      <span class="placeholder-icon" aria-hidden="true"></span>
      <p>${escapeHtml(text)}</p>
    </div>
  `;
}

function formatTime(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (isNaN(d.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
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
  const theme = saved || (prefersDark ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
}

function startClock() {
  function tick() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const hh = pad(now.getHours());
    const mm = pad(now.getMinutes());
    const ss = pad(now.getSeconds());
    if (clockTime) clockTime.textContent = `${hh}:${mm}:${ss}`;
    const status = marketStatus(now);
    if (marketClock) {
      marketClock.classList.remove("is-open", "is-break", "is-closed");
      marketClock.classList.add(`is-${status.state}`);
    }
    if (clockLabel) clockLabel.textContent = status.label;
  }
  tick();
  setInterval(tick, 1000);
}

function marketStatus(now) {
  const day = now.getDay();
  if (day === 0 || day === 6) return { state: "closed", label: "周末" };
  const minutes = now.getHours() * 60 + now.getMinutes();
  const morningStart = 9 * 60 + 30;
  const morningEnd = 11 * 60 + 30;
  const afternoonStart = 13 * 60;
  const afternoonEnd = 15 * 60;
  if (minutes >= morningStart && minutes < morningEnd) return { state: "open", label: "盘中" };
  if (minutes >= afternoonStart && minutes < afternoonEnd) return { state: "open", label: "盘中" };
  if (minutes >= morningEnd && minutes < afternoonStart) return { state: "break", label: "午间休市" };
  if (minutes < morningStart) return { state: "closed", label: "盘前" };
  return { state: "closed", label: "盘后" };
}

function startElapsedTimer() {
  analysisStartedAt = Date.now();
  statusElapsed.hidden = false;
  statusElapsed.textContent = "00.0s";
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => {
    const seconds = (Date.now() - analysisStartedAt) / 1000;
    statusElapsed.textContent = `${seconds.toFixed(1)}s`;
  }, 100);
}

function stopElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
  if (analysisStartedAt) {
    const seconds = (Date.now() - analysisStartedAt) / 1000;
    statusElapsed.textContent = `${seconds.toFixed(1)}s`;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/* ============ Hot sectors ============ */
async function initHotSectors() {
  if (!hotSectorsGrid) return;
  await refreshHotSectors();
  if (hotSectorsTimer) clearInterval(hotSectorsTimer);
  hotSectorsTimer = setInterval(refreshHotSectors, 30000);
}

async function refreshHotSectors() {
  if (!hotSectorsEl || hotSectorsEl.hidden) return;
  try {
    const response = await fetch("/cn/hot_sectors");
    if (!response.ok) throw new Error("hot_sectors http " + response.status);
    const data = await response.json();
    const incoming = (data.sectors || []).map((s) => normalizeSector(s));
    if (!incoming.length) return;
    if (hotSectorsState.length) {
      const previous = new Map(hotSectorsState.map((s) => [s.symbol, s]));
      incoming.forEach((s) => {
        const prev = previous.get(s.symbol);
        if (prev && Math.abs((prev.pct ?? 0) - (s.pct ?? 0)) > 0.001) s._bumped = true;
      });
    }
    hotSectorsState = incoming;
    renderHotSectors();
  } catch (error) {
    if (!hotSectorsState.length) {
      hotSectorsGrid.innerHTML = `
        <div class="hot-empty">
          <p>实时板块数据暂时不可用：${escapeHtml(error.message || "网络异常")}</p>
        </div>
      `;
    }
  }
}

function normalizeSector(s) {
  const pct = Number.isFinite(s.pct) ? Number(s.pct) : null;
  const spark = Array.isArray(s.spark) ? s.spark.filter((x) => Number.isFinite(x)) : [];
  return {
    name: s.name || s.symbol || "—",
    symbol: s.symbol || "",
    leader: s.leader || "—",
    pct,
    leaderPct: Number.isFinite(s.leader_pct) ? Number(s.leader_pct) : pct,
    price: Number.isFinite(s.price) ? Number(s.price) : null,
    volume: Number.isFinite(s.volume) ? Number(s.volume) : null,
    history: spark.length ? spark : [],
    _bumped: false,
  };
}

function renderHotSectors() {
  if (!hotSectorsGrid) return;
  hotSectorsGrid.innerHTML = hotSectorsState.map((sector) => {
    const pct = Number.isFinite(sector.pct) ? sector.pct : 0;
    const leaderPct = Number.isFinite(sector.leaderPct) ? sector.leaderPct : pct;
    const tone = pct > 0.05 ? "bull" : pct < -0.05 ? "bear" : "warn";
    const sign = pct > 0 ? "+" : "";
    const leaderSign = leaderPct > 0 ? "+" : "";
    const leaderTone = leaderPct > 0 ? "bull" : leaderPct < 0 ? "bear" : "muted";
    const bumped = sector._bumped ? "bumped" : "";
    sector._bumped = false;
    const volumeText = Number.isFinite(sector.volume) && sector.volume > 0
      ? `成交 ${sector.volume.toFixed(2)}亿`
      : "成交 —";
    const priceText = Number.isFinite(sector.price)
      ? `<span class="hot-price mono">${sector.price.toFixed(3)}</span>`
      : "";
    return `
      <article class="hot-card ${tone}" data-symbol="${escapeHtml(sector.symbol)}" data-name="${escapeHtml(sector.name)}" tabindex="0">
        <div class="hot-card-head">
          <span class="hot-name">${escapeHtml(sector.name)}</span>
          <span class="hot-pct ${tone} ${bumped}">${sign}${pct.toFixed(2)}%</span>
        </div>
        ${sparklineSvg(sector.history, tone)}
        <div class="hot-meta">
          <span>${volumeText}</span>
          <span>${escapeHtml(sector.symbol)}${priceText ? " · " + priceText : ""}</span>
        </div>
        <div class="hot-leader">
          <span>代表</span>
          <strong>${escapeHtml(sector.leader)}</strong>
          <span class="leader-pct" style="color: var(--${leaderTone === 'muted' ? 'muted' : leaderTone})">${leaderSign}${leaderPct.toFixed(2)}%</span>
        </div>
      </article>
    `;
  }).join("");
  hotSectorsGrid.querySelectorAll(".hot-card").forEach((card) => {
    card.addEventListener("click", () => {
      const sym = card.getAttribute("data-symbol");
      if (sym) {
        symbolInput.value = sym;
        symbolInput.focus();
        symbolInput.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        card.click();
      }
    });
  });
}

function sparklineSvg(points, tone) {
  if (!points || !points.length) return "";
  const w = 200;
  const h = 28;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const stepX = w / (points.length - 1);
  const path = points.map((p, i) => {
    const x = (i * stepX).toFixed(1);
    const y = (h - ((p - min) / range) * (h - 4) - 2).toFixed(1);
    return `${i === 0 ? "M" : "L"}${x},${y}`;
  }).join(" ");
  const fill = `${path} L${w},${h} L0,${h} Z`;
  const colorVar = tone === "bull" ? "--bull" : tone === "bear" ? "--bear" : "--warn";
  return `
    <svg class="hot-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="g-${colorVar}-${Math.floor(Math.random()*1e6)}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(${colorVar})" stop-opacity="0.3" />
          <stop offset="100%" stop-color="var(${colorVar})" stop-opacity="0" />
        </linearGradient>
      </defs>
      <path d="${fill}" fill="var(${colorVar})" fill-opacity="0.12" stroke="none" />
      <path d="${path}" fill="none" stroke="var(${colorVar})" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  `;
}
