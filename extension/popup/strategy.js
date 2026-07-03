/**
 * strategy.js — strategy template metadata and spec building (Phase 8.4).
 *
 * Pure logic, no DOM: loaded by popup.html before popup.js and evaluated
 * directly by the Node test harness. Mirrors the API's bounded schema
 * (ADR-046) so obviously invalid specs fail client-side with a friendly
 * message instead of a 422 round-trip.
 */

const STRATEGY_INDICATORS = ["PRICE", "SMA", "EMA", "RSI", "HIGH_N", "LOW_N"];
const STRATEGY_OPS = ["<", ">", "crosses_above", "crosses_below"];
const MAX_RULES_PER_SIDE = 8;
const MIN_PERIOD = 2;
const MAX_PERIOD = 250;

/**
 * Template metadata drives the params UI. `params` entries render as number
 * inputs; ranges mirror the server-side bounds.
 */
const STRATEGY_TEMPLATES = [
  { id: "buy_hold", label: "Buy & Hold vs benchmark", params: [] },
  {
    id: "ma_cross", label: "MA Crossover",
    params: [
      { key: "fast", label: "Fast MA", def: 10, min: 2, max: 250 },
      { key: "slow", label: "Slow MA", def: 50, min: 2, max: 250 },
    ],
  },
  {
    id: "rsi_reversion", label: "RSI Mean-Reversion",
    params: [
      { key: "period",     label: "RSI period", def: 14, min: 2, max: 250 },
      { key: "buy_below",  label: "Buy below",  def: 30, min: 1, max: 99 },
      { key: "sell_above", label: "Sell above", def: 70, min: 1, max: 99 },
    ],
  },
  {
    id: "breakout", label: "Channel Breakout",
    params: [
      { key: "lookback", label: "Lookback", def: 20, min: 2, max: 250 },
    ],
  },
];

const ML_TEMPLATE = {
  id: "ml_transformer",
  label: "ML Transformer (experimental)",
};

/**
 * Validate one custom-rule row (plain object from the UI):
 *   { indicator, period, op, rhsKind: "value"|"indicator",
 *     value, otherIndicator, otherPeriod }
 * Returns { ok: true, condition } (API-shaped) or { ok: false, error }.
 */
function buildCondition(row) {
  if (!STRATEGY_INDICATORS.includes(row.indicator)) {
    return { ok: false, error: `Unknown indicator ${row.indicator}` };
  }
  if (!STRATEGY_OPS.includes(row.op)) {
    return { ok: false, error: `Unknown operator ${row.op}` };
  }

  const cond = { indicator: row.indicator, op: row.op };

  if (row.indicator !== "PRICE") {
    const p = Number(row.period);
    if (!Number.isInteger(p) || p < MIN_PERIOD || p > MAX_PERIOD) {
      return { ok: false,
               error: `${row.indicator} period must be ${MIN_PERIOD}–${MAX_PERIOD}` };
    }
    cond.period = p;
  }

  const isCross = row.op === "crosses_above" || row.op === "crosses_below";
  if (row.rhsKind === "indicator") {
    if (!STRATEGY_INDICATORS.includes(row.otherIndicator)) {
      return { ok: false, error: "Pick an indicator to compare against" };
    }
    cond.other_indicator = row.otherIndicator;
    if (row.otherIndicator !== "PRICE") {
      const op = Number(row.otherPeriod);
      if (!Number.isInteger(op) || op < MIN_PERIOD || op > MAX_PERIOD) {
        return { ok: false,
                 error: `${row.otherIndicator} period must be ${MIN_PERIOD}–${MAX_PERIOD}` };
      }
      cond.other_period = op;
    }
  } else {
    if (isCross) {
      return { ok: false,
               error: "Cross conditions compare two indicators — pick one on the right" };
    }
    const v = Number(row.value);
    if (!Number.isFinite(v)) {
      return { ok: false, error: "Comparison value must be a number" };
    }
    cond.value = v;
  }

  return { ok: true, condition: cond };
}

/**
 * Build the API `strategy` field from UI state.
 *
 * state:
 *   { kind: "template", template, params }            — template mode
 *   { kind: "custom", entryRows, exitRows, name }     — rule builder mode
 *   { kind: "ml" }                                    — experimental
 *
 * Returns { ok: true, strategy } — strategy === null means "server default"
 * (plain buy & hold) — or { ok: false, error }.
 */
function buildStrategyPayload(state) {
  if (!state || (state.kind === "template" && state.template === "buy_hold")) {
    return { ok: true, strategy: null };
  }

  if (state.kind === "ml") {
    return { ok: true, strategy: { template: "ml_transformer" } };
  }

  if (state.kind === "template") {
    const meta = STRATEGY_TEMPLATES.find((t) => t.id === state.template);
    if (!meta) return { ok: false, error: `Unknown template ${state.template}` };

    const params = {};
    for (const p of meta.params) {
      const raw = state.params?.[p.key];
      const v = Number(raw ?? p.def);
      if (!Number.isFinite(v) || v < p.min || v > p.max) {
        return { ok: false, error: `${p.label} must be ${p.min}–${p.max}` };
      }
      params[p.key] = v;
    }
    if (state.template === "ma_cross" && params.fast >= params.slow) {
      return { ok: false, error: "Fast MA must be smaller than Slow MA" };
    }
    if (state.template === "rsi_reversion" &&
        params.buy_below >= params.sell_above) {
      return { ok: false, error: "'Buy below' must be less than 'Sell above'" };
    }
    return { ok: true, strategy: { template: state.template, params } };
  }

  if (state.kind === "custom") {
    const entryRows = state.entryRows ?? [];
    const exitRows = state.exitRows ?? [];
    if (entryRows.length === 0) {
      return { ok: false, error: "Add at least one entry rule" };
    }
    if (entryRows.length > MAX_RULES_PER_SIDE ||
        exitRows.length > MAX_RULES_PER_SIDE) {
      return { ok: false, error: `At most ${MAX_RULES_PER_SIDE} rules per side` };
    }

    const entry = [];
    for (const row of entryRows) {
      const r = buildCondition(row);
      if (!r.ok) return { ok: false, error: `Entry rule: ${r.error}` };
      entry.push(r.condition);
    }
    const exit = [];
    for (const row of exitRows) {
      const r = buildCondition(row);
      if (!r.ok) return { ok: false, error: `Exit rule: ${r.error}` };
      exit.push(r.condition);
    }

    const strategy = { rules: { entry, exit } };
    const name = (state.name ?? "").trim();
    if (name) strategy.name = name.slice(0, 64);
    return { ok: true, strategy };
  }

  return { ok: false, error: "Unknown strategy mode" };
}

// Node test harness support; ignored inside the extension page.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    STRATEGY_TEMPLATES, ML_TEMPLATE, STRATEGY_INDICATORS, STRATEGY_OPS,
    MAX_RULES_PER_SIDE, buildCondition, buildStrategyPayload,
  };
}
