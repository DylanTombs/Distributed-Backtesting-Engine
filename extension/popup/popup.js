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
const tickerInput     = document.getElementById("ticker-input");
const btnAddTicker    = document.getElementById("btn-add-ticker");
const strategySelect  = document.getElementById("strategy-select");
const btnDeleteSaved  = document.getElementById("btn-delete-saved");
const strategyParams  = document.getElementById("strategy-params");
const rulesBuilder    = document.getElementById("rules-builder");
const entryRulesEl    = document.getElementById("entry-rules");
const exitRulesEl     = document.getElementById("exit-rules");
const btnAddEntry     = document.getElementById("btn-add-entry");
const btnAddExit      = document.getElementById("btn-add-exit");
const strategyNameEl  = document.getElementById("strategy-name");
const btnSaveStrategy = document.getElementById("btn-save-strategy");
const strategyHint    = document.getElementById("strategy-hint");
const resultStrategy  = document.getElementById("result-strategy");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentTickers   = [];
let currentTabId     = null;
let allEvents        = [];   // EventSummary[] from GET /api/events
let savedStrategies  = {};   // name → API strategy object (storage.sync)

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

  // 2. Populate Quick Picks from event database.
  // A failed load is non-fatal, but must be visible (P2-5): warn and
  // continue with an empty list rather than silently rendering an empty
  // dropdown.
  const eventsResp = await send("LIST_EVENTS");
  if (Array.isArray(eventsResp)) {
    allEvents = eventsResp;
  } else {
    allEvents = [];
    showMsg("Could not load event list — Quick Picks unavailable", "error");
  }
  populateEventDropdown(allEvents);

  // 3. Strategy panel (templates + saved strategies from storage.sync)
  await initStrategyPanel();

  // 4. Check session cache for this tab
  if (currentTabId) {
    const cached = await send("GET_CACHED_RESULT", { tabId: currentTabId });
    if (cached && !cached.error) {
      renderResults(cached);
    }
  }

  // 5. Extract context from page (sends raw text from the active tab)
  await detectContext(tab);
})().catch((err) => {
  showMsg(`Initialisation error: ${err.message}`, "error");
});

// ---------------------------------------------------------------------------
// Context detection
// ---------------------------------------------------------------------------
function isHttpUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

async function detectContext(tab) {
  setConfidence(0, "Analysing page…");

  let rawText = null;
  let injectFailed = false;
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.body?.innerText ?? "",
    });
    rawText = result;
  } catch (_) {
    // Can't inject into this page (e.g. chrome:// URL) — try URL only
    injectFailed = true;
  }

  // If we could not read the page AND the URL is unusable (chrome://,
  // about:, edge://, file://, missing…), the EXTRACT_CONTEXT call is
  // doomed — tell the user instead of failing silently (P2-5).
  if (injectFailed && !isHttpUrl(tab?.url)) {
    setConfidence(0, "Cannot read this page — open a news article and try again");
    return;
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
  if (!Array.isArray(events)) return;
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
  const strat = currentStrategyPayload();
  if (!strat.ok) {
    setStrategyHint(strat.error, true);
    return;
  }
  setStrategyHint("");

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
    strategy:  strat.strategy,
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

  // Strategy line + experimental caveat (textContent only — API values are
  // untrusted, same rule as the metric cards)
  resultStrategy.textContent = "";
  const stratName = describeStrategy(result.strategy);
  if (stratName) {
    resultStrategy.textContent = `Strategy: ${stratName} `;
    if (result.experimental) {
      const tag = document.createElement("span");
      tag.className = "experimental-tag";
      tag.textContent = "(experimental — AAPL-trained model)";
      resultStrategy.appendChild(tag);
    }
  }

  // A data-substitution warning must be visible, not small print (8.6)
  if (result.warning) {
    showMsg(result.warning, "info");
  }

  // Metric cards — built with createElement/textContent only. API values are
  // untrusted; never interpolate them into innerHTML (XSS hardening, P1-4).
  const m = result.metrics ?? {};
  const days = Number.isFinite(Number(m.days)) && m.days != null && m.days !== ""
    ? String(Number(m.days))
    : "—";
  const cards = [
    { label: "Sharpe",  value: fmt(m.sharpe_ratio, 2),    pos: (m.sharpe_ratio  ?? 0) > 0 },
    { label: "Max DD",  value: fmt(m.max_drawdown_pct, 1) + "%", pos: false },
    { label: "Return",  value: fmt(m.total_return_pct, 1) + "%", pos: (m.total_return_pct ?? 0) > 0 },
    { label: "Win %",   value: fmt(m.win_rate_pct, 0) + "%",     pos: (m.win_rate_pct ?? 0) >= 50 },
    { label: "Days",    value: days,                        pos: null },
    { label: "Trades",  value: String(result.trades?.length ?? "—"), pos: null },
  ];

  for (const c of cards) {
    const card = document.createElement("div");
    card.className = "metric-card";

    const labelEl = document.createElement("div");
    labelEl.className = "metric-label";
    labelEl.textContent = c.label;

    const valueEl = document.createElement("div");
    valueEl.className =
      "metric-value" + (c.pos === true ? " pos" : c.pos === false ? " neg" : "");
    valueEl.textContent = c.value;

    card.appendChild(labelEl);
    card.appendChild(valueEl);
    metricsGrid.appendChild(card);
  }

  // Equity chart (canvas 2D — no external deps)
  drawEquityChart(result.equity ?? []);

  // Dashboard link
  if (result.run_id) {
    chrome.storage.sync.get({ dashboardBase: "http://localhost:8501" }, ({ dashboardBase }) => {
      linkDashboard.href = `${dashboardBase}?run_id=${result.run_id}`;
    });
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

// ---------------------------------------------------------------------------
// Ticker input (Phase 8.4 — user-supplied symbols)
// ---------------------------------------------------------------------------
const TICKER_RE = /^[A-Z0-9.\-]{1,7}$/;

function addTickerFromInput() {
  const t = tickerInput.value.trim().toUpperCase();
  if (!t) return;
  if (!TICKER_RE.test(t)) {
    showMsg(`"${t}" is not a valid ticker symbol`, "error");
    return;
  }
  clearMsg();
  if (!currentTickers.includes(t)) {
    currentTickers = [t, ...currentTickers];   // typed ticker takes priority
    renderTickerChips(currentTickers);
  }
  tickerInput.value = "";
  btnRun.disabled = !dateStart.value;
}

btnAddTicker.addEventListener("click", addTickerFromInput);
tickerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") addTickerFromInput();
});

// ---------------------------------------------------------------------------
// Strategy panel (Phase 8.4)
// ---------------------------------------------------------------------------
async function initStrategyPanel() {
  savedStrategies = await new Promise((resolve) => {
    chrome.storage.sync.get({ savedStrategies: {} },
      ({ savedStrategies: s }) => resolve(s ?? {}));
  });
  rebuildStrategySelect();
  strategySelect.value = "buy_hold";
  onStrategySelectChange();
}

function rebuildStrategySelect(selectValue) {
  strategySelect.innerHTML = "";

  const groupTemplates = document.createElement("optgroup");
  groupTemplates.label = "Templates";
  for (const t of STRATEGY_TEMPLATES) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.label;
    groupTemplates.appendChild(opt);
  }
  strategySelect.appendChild(groupTemplates);

  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "Custom rules…";
  strategySelect.appendChild(custom);

  const names = Object.keys(savedStrategies).sort();
  if (names.length) {
    const groupSaved = document.createElement("optgroup");
    groupSaved.label = "Saved";
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = `saved:${name}`;
      opt.textContent = name;
      groupSaved.appendChild(opt);
    }
    strategySelect.appendChild(groupSaved);
  }

  const groupExp = document.createElement("optgroup");
  groupExp.label = "Experimental";
  const ml = document.createElement("option");
  ml.value = "ml";
  ml.textContent = ML_TEMPLATE.label;
  groupExp.appendChild(ml);
  strategySelect.appendChild(groupExp);

  if (selectValue) strategySelect.value = selectValue;
}

function onStrategySelectChange() {
  const v = strategySelect.value;
  const isCustom = v === "custom";
  const isSaved = v.startsWith("saved:");

  rulesBuilder.style.display = isCustom ? "block" : "none";
  btnDeleteSaved.style.display = isSaved ? "block" : "none";
  strategyParams.innerHTML = "";
  setStrategyHint("");

  if (isCustom) {
    if (!entryRulesEl.children.length) addRuleRow(entryRulesEl);
    return;
  }
  if (v === "ml") {
    setStrategyHint(
      "Runs the transformer model — experimental; results reflect its AAPL training data.");
    return;
  }
  if (isSaved) {
    setStrategyHint("Saved rule strategy — runs as configured.");
    return;
  }

  const meta = STRATEGY_TEMPLATES.find((t) => t.id === v);
  if (!meta) return;
  for (const p of meta.params) {
    const field = document.createElement("div");
    field.className = "param-field";
    const label = document.createElement("label");
    label.textContent = p.label;
    const input = document.createElement("input");
    input.type = "number";
    input.min = p.min;
    input.max = p.max;
    input.value = p.def;
    input.dataset.param = p.key;
    field.appendChild(label);
    field.appendChild(input);
    strategyParams.appendChild(field);
  }
}

strategySelect.addEventListener("change", onStrategySelectChange);

function addRuleRow(container) {
  if (container.children.length >= MAX_RULES_PER_SIDE) {
    setStrategyHint(`At most ${MAX_RULES_PER_SIDE} rules per side`, true);
    return;
  }
  const row = document.createElement("div");
  row.className = "rule-row";

  const ind = makeSelect(STRATEGY_INDICATORS, "SMA");
  const period = makeNumber(14);
  const op = makeSelect(STRATEGY_OPS, "<");
  const rhsInd = makeSelect(["value", ...STRATEGY_INDICATORS], "value");
  const rhsVal = makeNumber(30);   // doubles as rhs period when rhs is indicator

  const sync = () => {
    period.style.visibility = ind.value === "PRICE" ? "hidden" : "visible";
    rhsVal.style.visibility =
      rhsInd.value === "PRICE" ? "hidden" : "visible";
  };
  ind.addEventListener("change", sync);
  rhsInd.addEventListener("change", sync);

  const remove = document.createElement("button");
  remove.className = "rule-remove";
  remove.textContent = "✕";
  remove.title = "Remove rule";
  remove.addEventListener("click", () => row.remove());

  for (const el of [ind, period, op, rhsInd, rhsVal, remove]) row.appendChild(el);
  container.appendChild(row);
  sync();
}

function makeSelect(options, value) {
  const s = document.createElement("select");
  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o;
    opt.textContent = o === "value" ? "number…" : o;
    s.appendChild(opt);
  }
  s.value = value;
  return s;
}

function makeNumber(value) {
  const i = document.createElement("input");
  i.type = "number";
  i.value = value;
  return i;
}

function readRuleRows(container) {
  return [...container.children].map((row) => {
    const [ind, period, op, rhsInd, rhsVal] = row.querySelectorAll("select, input");
    const usesValue = rhsInd.value === "value";
    return {
      indicator: ind.value,
      period: period.value,
      op: op.value,
      rhsKind: usesValue ? "value" : "indicator",
      value: rhsVal.value,
      otherIndicator: usesValue ? null : rhsInd.value,
      otherPeriod: rhsVal.value,
    };
  });
}

/** Build the API strategy payload from the current panel state. */
function currentStrategyPayload() {
  const v = strategySelect.value;
  if (v.startsWith("saved:")) {
    const saved = savedStrategies[v.slice(6)];
    return saved
      ? { ok: true, strategy: saved }
      : { ok: false, error: "Saved strategy not found" };
  }
  if (v === "ml") return buildStrategyPayload({ kind: "ml" });
  if (v === "custom") {
    return buildStrategyPayload({
      kind: "custom",
      entryRows: readRuleRows(entryRulesEl),
      exitRows: readRuleRows(exitRulesEl),
      name: strategyNameEl.value,
    });
  }
  const params = {};
  for (const input of strategyParams.querySelectorAll("input")) {
    params[input.dataset.param] = input.value;
  }
  return buildStrategyPayload({ kind: "template", template: v, params });
}

btnAddEntry.addEventListener("click", () => addRuleRow(entryRulesEl));
btnAddExit.addEventListener("click", () => addRuleRow(exitRulesEl));

btnSaveStrategy.addEventListener("click", () => {
  const name = strategyNameEl.value.trim();
  if (!name) {
    setStrategyHint("Give the strategy a name to save it", true);
    return;
  }
  const built = currentStrategyPayload();
  if (!built.ok) {
    setStrategyHint(built.error, true);
    return;
  }
  savedStrategies = { ...savedStrategies, [name]: built.strategy };
  chrome.storage.sync.set({ savedStrategies }, () => {
    rebuildStrategySelect(`saved:${name}`);
    onStrategySelectChange();
    setStrategyHint(`Saved "${name}" — available on any article.`);
  });
});

btnDeleteSaved.addEventListener("click", () => {
  const v = strategySelect.value;
  if (!v.startsWith("saved:")) return;
  const name = v.slice(6);
  const { [name]: _removed, ...rest } = savedStrategies;
  savedStrategies = rest;
  chrome.storage.sync.set({ savedStrategies }, () => {
    rebuildStrategySelect("buy_hold");
    onStrategySelectChange();
  });
});

function setStrategyHint(text, isError = false) {
  strategyHint.textContent = text ?? "";
  strategyHint.className = "strategy-hint" + (isError ? " error" : "");
}

function describeStrategy(strategy) {
  if (!strategy) return "Buy & Hold vs benchmark";
  if (strategy.template) {
    const meta = STRATEGY_TEMPLATES.find((t) => t.id === strategy.template);
    if (meta) return meta.label;
    if (strategy.template === "ml_transformer") return ML_TEMPLATE.label;
    return strategy.template;
  }
  return strategy.name || "Custom rules";
}
