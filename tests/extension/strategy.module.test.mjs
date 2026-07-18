/**
 * strategy.module.test.mjs — unit tests for the popup strategy module
 * (extension/popup/strategy.js, Phase 8.4).
 *
 * Run with:  node --test tests/extension/strategy.module.test.mjs
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const {
  STRATEGY_TEMPLATES,
  MAX_RULES_PER_SIDE,
  buildCondition,
  buildStrategyPayload,
} = require(path.join(HERE, "..", "..", "extension", "popup", "strategy.js"));

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

test("buy_hold (and empty state) maps to the server default", () => {
  assert.deepEqual(
    buildStrategyPayload({ kind: "template", template: "buy_hold" }),
    { ok: true, strategy: null },
  );
  assert.deepEqual(buildStrategyPayload(null), { ok: true, strategy: null });
});

test("ma_cross builds template payload with numeric params", () => {
  const r = buildStrategyPayload({
    kind: "template", template: "ma_cross",
    params: { fast: "5", slow: "20" },   // UI inputs are strings
  });
  assert.equal(r.ok, true);
  assert.deepEqual(r.strategy, {
    template: "ma_cross", params: { fast: 5, slow: 20 },
  });
});

test("ma_cross rejects fast >= slow client-side", () => {
  const r = buildStrategyPayload({
    kind: "template", template: "ma_cross", params: { fast: 50, slow: 10 },
  });
  assert.equal(r.ok, false);
  assert.match(r.error, /smaller/);
});

test("rsi_reversion enforces buy_below < sell_above", () => {
  const r = buildStrategyPayload({
    kind: "template", template: "rsi_reversion",
    params: { period: 14, buy_below: 80, sell_above: 20 },
  });
  assert.equal(r.ok, false);
});

test("template params outside range are rejected", () => {
  const r = buildStrategyPayload({
    kind: "template", template: "breakout", params: { lookback: 999 },
  });
  assert.equal(r.ok, false);
});

test("missing params fall back to template defaults", () => {
  const r = buildStrategyPayload({ kind: "template", template: "breakout" });
  assert.equal(r.ok, true);
  assert.deepEqual(r.strategy.params, { lookback: 20 });
});

test("ml mode maps to the experimental template", () => {
  assert.deepEqual(buildStrategyPayload({ kind: "ml" }), {
    ok: true, strategy: { template: "ml_transformer" },
  });
});

// ---------------------------------------------------------------------------
// Custom rule rows
// ---------------------------------------------------------------------------

const RSI_ROW = {
  indicator: "RSI", period: "7", op: "<",
  rhsKind: "value", value: "25",
};

test("buildCondition maps a value row to the API shape", () => {
  const r = buildCondition(RSI_ROW);
  assert.deepEqual(r, {
    ok: true,
    condition: { indicator: "RSI", period: 7, op: "<", value: 25 },
  });
});

test("buildCondition maps an indicator row to the API shape", () => {
  const r = buildCondition({
    indicator: "SMA", period: "10", op: "crosses_above",
    rhsKind: "indicator", otherIndicator: "SMA", otherPeriod: "50",
  });
  assert.deepEqual(r.condition, {
    indicator: "SMA", period: 10, op: "crosses_above",
    other_indicator: "SMA", other_period: 50,
  });
});

test("PRICE rows omit the period", () => {
  const r = buildCondition({
    indicator: "PRICE", op: ">", rhsKind: "value", value: "0",
  });
  assert.deepEqual(r.condition, { indicator: "PRICE", op: ">", value: 0 });
});

test("cross against a plain number is rejected", () => {
  const r = buildCondition({
    indicator: "SMA", period: "5", op: "crosses_above",
    rhsKind: "value", value: "100",
  });
  assert.equal(r.ok, false);
  assert.match(r.error, /two indicators/);
});

test("period bounds are enforced", () => {
  for (const period of ["1", "251", "abc", "2.5"]) {
    const r = buildCondition({ ...RSI_ROW, period });
    assert.equal(r.ok, false, `period ${period} should fail`);
  }
});

test("custom payload requires an entry rule and caps sides", () => {
  assert.equal(
    buildStrategyPayload({ kind: "custom", entryRows: [], exitRows: [] }).ok,
    false,
  );
  const many = Array(MAX_RULES_PER_SIDE + 1).fill(RSI_ROW);
  assert.equal(
    buildStrategyPayload({ kind: "custom", entryRows: many, exitRows: [] }).ok,
    false,
  );
});

test("custom payload round-trips rows into API rules with optional name", () => {
  const r = buildStrategyPayload({
    kind: "custom",
    entryRows: [RSI_ROW],
    exitRows: [{ indicator: "RSI", period: "7", op: ">",
                 rhsKind: "value", value: "60" }],
    name: "  my dip buyer  ",
  });
  assert.equal(r.ok, true);
  assert.deepEqual(r.strategy, {
    rules: {
      entry: [{ indicator: "RSI", period: 7, op: "<", value: 25 }],
      exit: [{ indicator: "RSI", period: 7, op: ">", value: 60 }],
    },
    name: "my dip buyer",
  });
});

test("template metadata stays within server bounds", () => {
  for (const t of STRATEGY_TEMPLATES) {
    for (const p of t.params) {
      assert.ok(p.min >= 1 && p.max <= 250,
        `${t.id}.${p.key} range must stay within server bounds`);
    }
  }
});

// ---------------------------------------------------------------------------
// Spec v2: direction (Phase 9.2)
// ---------------------------------------------------------------------------

test("short custom payload includes rules.direction", () => {
  const r = buildStrategyPayload({
    kind: "custom", direction: "short",
    entryRows: [RSI_ROW], exitRows: [],
  });
  assert.equal(r.ok, true);
  assert.equal(r.strategy.rules.direction, "short");
});

test("long custom payload omits direction for stable cache hashes", () => {
  for (const direction of ["long", undefined]) {
    const r = buildStrategyPayload({
      kind: "custom", direction, entryRows: [RSI_ROW], exitRows: [],
    });
    assert.equal(r.ok, true);
    assert.ok(!("direction" in r.strategy.rules));
  }
});

test("invalid direction is rejected", () => {
  const r = buildStrategyPayload({
    kind: "custom", direction: "sideways",
    entryRows: [RSI_ROW], exitRows: [],
  });
  assert.equal(r.ok, false);
});
