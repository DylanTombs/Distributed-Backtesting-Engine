/**
 * popup.js — extension popup controller
 *
 * On open:
 *  1. Health-check the API
 *  2. Check session cache for a prior backtest result for this tab
 *  3. If none, extract context from the current tab's page text
 *  4. Populate the event dropdown, ticker chips, and date fields
 *  5. On Run: call the backtest API and render results
 */

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
const statusDot       = document.getElementById("status-dot");
const eventSelect     = document.getElementById("event-select");
const btnRedetect     = document.getElementById("btn-redetect");
const tickerChips     = document.getElementById("ticker-chips");
const dateStart       = document.getElementById("date-start");
const dateEnd         = document.getElementById("date-end");
const confidenceFill  = document.getElementById("confidence-fill");
const confidenceLabel = document.getElementById("confidence-label");
const btnRun          = document.getElementById("btn-run");
const panelResults    = document.getElementById("panel-results");
const metricsGrid     = document.getElementById("metrics-grid");
const linkDashboard   = document.getElementById("link-dashboard");
const msgBox          = document.getElementById("msg-box");
const equityCanvas    = document.getElementById("equity-chart");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentTickers = [];
let currentTabId   = null;
let allEvents      = [];     // EventSummary[] from GET /api/events

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTabId = tab?.id ?? null;

  // 1. Health check (show status dot colour)
  const health = await send("HEALTH_CHECK");
  if (health && !health.error) {
    statusDot.className = "header-status online";
    statusDot.title = `API online — model ${health.model_loaded ? "loaded" : "not found"}`;
  } else {
    statusDot.className = "header-status offline";
    statusDot.title = "API offline — run: uvicorn research.api.app:app --port 8502";
    showMsg("API is offline. Start it with:\nuvicorn research.api.app:app --port 8502 --reload", "error");
    return;
  }

  // 2. Populate Quick Picks from event database
  allEvents = await send("LIST_EVENTS") ?? [];
  populateEventDropdown(allEvents);

  // 3. Check session cache for this tab
  if (currentTabId) {
    const cached = await send("GET_CACHED_RESULT", { tabId: currentTabId });
    if (cached && !cached.error) {
      renderResults(cached);
    }
  }

  // 4. Extract context from page (sends raw text from the active tab)
  await detectContext(tab);
})();

// ---------------------------------------------------------------------------
// Context detection
// ---------------------------------------------------------------------------
async function detectContext(tab) {
  setConfidence(0, "Analysing page…");

  let rawText = null;
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.body?.innerText ?? "",
    });
    rawText = result;
  } catch (_) {
    // Can't inject into this page (e.g. chrome:// URL) — try URL only
  }

  const ctx = await send("EXTRACT_CONTEXT", {
    url: tab.url,
    rawText,
  });

  if (!ctx || ctx.error) {
    setConfidence(0, ctx?.error ?? "Could not extract context");
    return;
  }

  applyContext(ctx);
}

function applyContext(ctx) {
  // Match detected event to the dropdown
  if (ctx.event_key) {
    const opt = [...eventSelect.options].find((o) => o.value === ctx.event_key);
    if (opt) eventSelect.value = ctx.event_key;
  }

  // Tickers
  currentTickers = ctx.tickers ?? [];
  renderTickerChips(currentTickers);

  // Dates
  if (ctx.date_start) dateStart.value = ctx.date_start;
  if (ctx.date_end)   dateEnd.value   = ctx.date_end;

  // Confidence
  const pct = Math.round((ctx.confidence ?? 0) * 100);
  const label =
    pct >= 70 ? "high confidence" :
    pct >= 40 ? "medium confidence" : "low confidence";
  setConfidence(pct, `${label} (${ctx.source})`);

  btnRun.disabled = currentTickers.length === 0 || !dateStart.value;
}

// ---------------------------------------------------------------------------
// Event dropdown
// ---------------------------------------------------------------------------
function populateEventDropdown(events) {
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "— Quick pick event —";
  eventSelect.appendChild(placeholder);

  for (const ev of events) {
    const opt = document.createElement("option");
    opt.value = ev.key;
    opt.textContent = ev.label;
    eventSelect.appendChild(opt);
  }
}

eventSelect.addEventListener("change", () => {
  const key = eventSelect.value;
  const ev  = allEvents.find((e) => e.key === key);
  if (!ev) return;

  dateStart.value  = ev.date_start;
  dateEnd.value    = ev.date_end;
  currentTickers   = ev.tickers.slice(0, 5);
  renderTickerChips(currentTickers);
  setConfidence(90, "event selected manually");
  btnRun.disabled = false;
});

btnRedetect.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  panelResults.style.display = "none";
  clearMsg();
  await detectContext(tab);
});

// ---------------------------------------------------------------------------
// Ticker chips
// ---------------------------------------------------------------------------
function renderTickerChips(tickers) {
  tickerChips.innerHTML = "";
  for (const t of tickers) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = t;
    chip.title = `Click to remove ${t}`;
    chip.addEventListener("click", () => {
      currentTickers = currentTickers.filter((x) => x !== t);
      renderTickerChips(currentTickers);
      btnRun.disabled = currentTickers.length === 0;
    });
    tickerChips.appendChild(chip);
  }
}

// ---------------------------------------------------------------------------
// Run backtest
// ---------------------------------------------------------------------------
btnRun.addEventListener("click", async () => {
  btnRun.disabled = true;
  btnRun.classList.add("loading");
  btnRun.textContent = "▶ Running";
  clearMsg();

  const result = await send("RUN_BACKTEST", {
    tickers:   currentTickers,
    dateStart: dateStart.value,
    dateEnd:   dateEnd.value,
    skipTrain: true,
    tabId:     currentTabId,
  });

  btnRun.classList.remove("loading");
  btnRun.textContent = "▶ Run Backtest";
  btnRun.disabled = false;

  if (!result || result.error) {
    showMsg(result?.error ?? "Backtest failed", "error");
    return;
  }

  renderResults(result);
});

// ---------------------------------------------------------------------------
// Render results
// ---------------------------------------------------------------------------
function renderResults(result) {
  panelResults.style.display = "block";
  metricsGrid.innerHTML = "";

  // Metric cards
  const m = result.metrics ?? {};
  const cards = [
    { label: "Sharpe",  value: fmt(m.sharpe_ratio, 2),    pos: (m.sharpe_ratio  ?? 0) > 0 },
    { label: "Max DD",  value: fmt(m.max_drawdown_pct, 1) + "%", pos: false },
    { label: "Return",  value: fmt(m.total_return_pct, 1) + "%", pos: (m.total_return_pct ?? 0) > 0 },
    { label: "Win %",   value: fmt(m.win_rate_pct, 0) + "%",     pos: (m.win_rate_pct ?? 0) >= 50 },
    { label: "Days",    value: String(m.days ?? "—"),      pos: null },
    { label: "Trades",  value: String(result.trades?.length ?? "—"), pos: null },
  ];

  for (const c of cards) {
    const card = document.createElement("div");
    card.className = "metric-card";
    card.innerHTML = `
      <div class="metric-label">${c.label}</div>
      <div class="metric-value ${c.pos === true ? "pos" : c.pos === false ? "neg" : ""}">${c.value}</div>
    `;
    metricsGrid.appendChild(card);
  }

  // Equity chart (canvas 2D — no external deps)
  drawEquityChart(result.equity ?? []);

  // Dashboard link
  if (result.run_id) {
    linkDashboard.href = `http://localhost:8501?run_id=${result.run_id}`;
    linkDashboard.style.display = "block";
  }
}

function fmt(v, decimals) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toFixed(decimals);
}

// ---------------------------------------------------------------------------
// Mini equity chart (canvas 2D — no external library needed)
// ---------------------------------------------------------------------------
function drawEquityChart(equity) {
  const ctx = equityCanvas.getContext("2d");
  const W   = equityCanvas.width  = equityCanvas.offsetWidth || 372;
  const H   = equityCanvas.height = 140;

  if (!equity.length) { ctx.clearRect(0, 0, W, H); return; }

  const values = equity.map((p) => p.equity);
  const minV   = Math.min(...values);
  const maxV   = Math.max(...values);
  const range  = maxV - minV || 1;

  const pad = { t: 8, r: 4, b: 24, l: 48 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;

  ctx.clearRect(0, 0, W, H);

  // Grid lines
  ctx.strokeStyle = "#334155";
  ctx.lineWidth   = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (plotH / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
  }

  // Y-axis labels
  ctx.fillStyle  = "#64748b";
  ctx.font       = "9px system-ui";
  ctx.textAlign  = "right";
  for (let i = 0; i <= 4; i++) {
    const v = maxV - (range / 4) * i;
    const y = pad.t + (plotH / 4) * i;
    ctx.fillText(fmtK(v), pad.l - 4, y + 3);
  }

  // Equity line
  const positive = values[values.length - 1] >= values[0];
  ctx.beginPath();
  ctx.lineWidth   = 2;
  ctx.strokeStyle = positive ? "#4ade80" : "#f87171";
  ctx.lineJoin    = "round";

  equity.forEach((p, i) => {
    const x = pad.l + (i / (equity.length - 1 || 1)) * plotW;
    const y = pad.t + (1 - (p.equity - minV) / range) * plotH;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill under line
  ctx.lineTo(pad.l + plotW, pad.t + plotH);
  ctx.lineTo(pad.l, pad.t + plotH);
  ctx.closePath();
  ctx.fillStyle = positive ? "rgba(74,222,128,0.08)" : "rgba(248,113,113,0.08)";
  ctx.fill();
}

function fmtK(v) {
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (Math.abs(v) >= 1_000)     return (v / 1_000).toFixed(1) + "K";
  return v.toFixed(0);
}

// ---------------------------------------------------------------------------
// Confidence bar helpers
// ---------------------------------------------------------------------------
function setConfidence(pct, label) {
  confidenceFill.style.width = `${pct}%`;
  confidenceFill.className = "confidence-fill " +
    (pct >= 70 ? "high" : pct >= 40 ? "medium" : "low");
  confidenceLabel.textContent = label;
}

// ---------------------------------------------------------------------------
// Message box helpers
// ---------------------------------------------------------------------------
function showMsg(text, type = "") {
  msgBox.textContent = text;
  msgBox.className   = "msg-box" + (type ? ` ${type}` : "");
  msgBox.style.display = "block";
}

function clearMsg() {
  msgBox.style.display = "none";
  msgBox.textContent   = "";
}

// ---------------------------------------------------------------------------
// Send message to background service worker
// ---------------------------------------------------------------------------
function send(type, payload = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, payload }, (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ error: chrome.runtime.lastError.message });
      } else {
        resolve(resp);
      }
    });
  });
}
