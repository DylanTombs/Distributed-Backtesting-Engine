/**
 * background.router.test.mjs — unit tests for the extension service-worker
 * message router (extension/background.js).
 *
 * Zero-dependency harness: node:test + node:assert + node:vm. The script is
 * evaluated inside a vm context whose globals provide a stubbed `chrome` API
 * and a stubbed `fetch`, then the captured onMessage listener is driven
 * directly with messages.
 *
 * Run with:  node --test tests/extension/
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BACKGROUND_PATH = path.join(HERE, "..", "..", "extension", "background.js");
const BACKGROUND_SOURCE = fs.readFileSync(BACKGROUND_PATH, "utf8");

const DEFAULT_FETCH_BODY = { ok: true };

/**
 * Build a fresh vm context running background.js with stubbed globals.
 * Returns handles for driving the router and inspecting stub calls.
 *
 * options:
 *   openPopupBehavior: "resolve" | "reject" | "missing"
 *   fetchBody:         object returned by resp.json()
 */
function loadBackground(options = {}) {
  const {
    openPopupBehavior = "resolve",
    fetchBody = DEFAULT_FETCH_BODY,
  } = options;

  const calls = {
    fetch: [],          // { url, options }
    sessionSet: [],     // objects passed to storage.session.set
    badgeText: [],      // objects passed to action.setBadgeText
    title: [],          // objects passed to action.setTitle
    openPopup: 0,       // number of openPopup invocations
  };

  let capturedListener = null;
  const sessionStore = new Map();

  const chromeStub = {
    runtime: {
      onMessage: {
        addListener(fn) { capturedListener = fn; },
      },
    },
    storage: {
      sync: {
        // Mirrors chrome behaviour: returns stored values merged over the
        // defaults object. Nothing stored in tests, so defaults come back.
        get(defaults, cb) { cb({ ...defaults }); },
      },
      session: {
        set(items, cb) {
          calls.sessionSet.push(items);
          for (const [k, v] of Object.entries(items)) sessionStore.set(k, v);
          if (cb) cb();
        },
        get(keys, cb) {
          const out = {};
          for (const k of keys) {
            if (sessionStore.has(k)) out[k] = sessionStore.get(k);
          }
          cb(out);
        },
      },
    },
    action: {
      // structuredClone: vm-realm objects get foreign prototypes, which
      // breaks deepStrictEqual in the assertions below.
      setBadgeText(details) {
        calls.badgeText.push(structuredClone(details));
        return Promise.resolve();
      },
      setTitle(details) {
        calls.title.push(structuredClone(details));
        return Promise.resolve();
      },
    },
  };

  if (openPopupBehavior !== "missing") {
    chromeStub.action.openPopup = () => {
      calls.openPopup += 1;
      return openPopupBehavior === "reject"
        ? Promise.reject(new Error("popup unavailable"))
        : Promise.resolve();
    };
  }

  const fetchStub = (url, opts = {}) => {
    calls.fetch.push({ url, options: opts });
    return Promise.resolve({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => structuredClone(fetchBody),
    });
  };

  const sandbox = {
    chrome: chromeStub,
    fetch: fetchStub,
    // Badge-clear timer: fire callbacks immediately so the clear path is
    // exercised without real waiting.
    setTimeout: (fn) => { fn(); return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(BACKGROUND_SOURCE, sandbox, { filename: "background.js" });

  assert.ok(capturedListener, "background.js must register an onMessage listener");

  /** Drive the router with one message; resolves with the sendResponse value. */
  function dispatch(msg, sender = {}) {
    return new Promise((resolve, reject) => {
      const timer = global.setTimeout(
        () => reject(new Error(`sendResponse never called for ${msg.type}`)),
        1000,
      );
      const returned = capturedListener(msg, sender, (resp) => {
        global.clearTimeout(timer);
        // Objects created inside the vm realm have foreign prototypes, which
        // breaks deepStrictEqual — clone them into the host realm first.
        resolve(resp == null ? resp : structuredClone(resp));
      });
      assert.equal(returned, true, `listener must return true for ${msg.type}`);
    });
  }

  return { dispatch, calls, sessionStore };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("HEALTH_CHECK fetches /api/health and relays the body", async () => {
  const body = { status: "ok", model_loaded: true };
  const { dispatch, calls } = loadBackground({ fetchBody: body });

  const resp = await dispatch({ type: "HEALTH_CHECK" });

  assert.equal(calls.fetch.length, 1);
  assert.equal(calls.fetch[0].url, "http://localhost:8502/api/health");
  assert.deepEqual(resp, body);
});

test("LIST_EVENTS fetches /api/events", async () => {
  const body = [{ key: "cpi_2024_06", label: "CPI June 2024" }];
  const { dispatch, calls } = loadBackground({ fetchBody: body });

  const resp = await dispatch({ type: "LIST_EVENTS" });

  assert.equal(calls.fetch[0].url, "http://localhost:8502/api/events");
  assert.deepEqual(resp, body);
});

test("RUN_BACKTEST maps camelCase payload to snake_case body and caches under payload tabId", async () => {
  const body = { run_id: "r1", metrics: { sharpe_ratio: 1.2 } };
  const { dispatch, calls, sessionStore } = loadBackground({ fetchBody: body });

  const resp = await dispatch(
    {
      type: "RUN_BACKTEST",
      payload: {
        tickers: ["AAPL", "MSFT"],
        dateStart: "2024-06-01",
        dateEnd: "2024-06-30",
        skipTrain: true,
        tabId: 42, // popup messages have sender.tab === undefined
      },
    },
    {}, // no sender.tab — must fall back to payload.tabId
  );

  assert.equal(calls.fetch.length, 1);
  assert.equal(calls.fetch[0].url, "http://localhost:8502/api/backtest");
  assert.equal(calls.fetch[0].options.method, "POST");

  const sent = JSON.parse(calls.fetch[0].options.body);
  assert.deepEqual(sent, {
    tickers: ["AAPL", "MSFT"],
    date_start: "2024-06-01",
    date_end: "2024-06-30",
    skip_train: true,
  });

  assert.deepEqual(resp, body);
  assert.equal(calls.sessionSet.length, 1);
  assert.deepEqual(sessionStore.get("result_42"), body);
});

test("GET_CACHED_RESULT returns a previously cached result, null otherwise", async () => {
  const body = { run_id: "r2" };
  const { dispatch } = loadBackground({ fetchBody: body });

  const miss = await dispatch({ type: "GET_CACHED_RESULT", payload: { tabId: 7 } });
  assert.equal(miss, null);

  await dispatch({ type: "SET_CACHED_RESULT", payload: { tabId: 7, result: body } });
  const hit = await dispatch({ type: "GET_CACHED_RESULT", payload: { tabId: 7 } });
  assert.deepEqual(hit, body);
});

test("unknown message type responds with an error", () => {
  // The default branch calls sendResponse synchronously and does not return
  // true, so drive the raw listener directly instead of using dispatch().
  const { listener } = loadBackgroundRaw();

  let resp = null;
  listener({ type: "NOT_A_REAL_TYPE" }, {}, (r) => { resp = r; });

  assert.ok(resp && typeof resp.error === "string");
  assert.match(resp.error, /Unknown message type/);
});

test("OPEN_POPUP resolves ok:true when openPopup succeeds", async () => {
  const { dispatch, calls } = loadBackground({ openPopupBehavior: "resolve" });

  const resp = await dispatch({ type: "OPEN_POPUP" }, { tab: { id: 3 } });

  assert.equal(calls.openPopup, 1);
  assert.deepEqual(resp, { ok: true });
  // No badge fallback on success (the immediate-fire setTimeout stub means
  // any badge set would also have been cleared — assert none was set at all).
  assert.equal(calls.badgeText.length, 0);
});

test("OPEN_POPUP falls back to badge and ok:false when openPopup rejects", async () => {
  const { dispatch, calls } = loadBackground({ openPopupBehavior: "reject" });

  const resp = await dispatch({ type: "OPEN_POPUP" }, { tab: { id: 3 } });

  assert.deepEqual(resp, { ok: false, fallback: "badge" });
  assert.equal(calls.openPopup, 1);

  // Badge "▶" set on the sender's tab, then cleared by the (immediate) timer.
  assert.ok(calls.badgeText.length >= 1);
  assert.deepEqual(calls.badgeText[0], { tabId: 3, text: "▶" });
  assert.equal(calls.title.length, 1);
  assert.equal(calls.title[0].tabId, 3);
  assert.match(calls.title[0].title, /toolbar icon/i);
});

test("OPEN_POPUP falls back to badge when openPopup API is missing (Chrome < 127)", async () => {
  const { dispatch, calls } = loadBackground({ openPopupBehavior: "missing" });

  const resp = await dispatch({ type: "OPEN_POPUP" }, { tab: { id: 9 } });

  assert.deepEqual(resp, { ok: false, fallback: "badge" });
  assert.equal(calls.openPopup, 0);
  assert.deepEqual(calls.badgeText[0], { tabId: 9, text: "▶" });
});

// ---------------------------------------------------------------------------
// Raw-listener loader for the synchronous default branch
// ---------------------------------------------------------------------------

function loadBackgroundRaw() {
  let listener = null;
  const sandbox = {
    chrome: {
      runtime: { onMessage: { addListener(fn) { listener = fn; } } },
      storage: {
        sync: { get(defaults, cb) { cb({ ...defaults }); } },
        session: { set() {}, get(_k, cb) { cb({}); } },
      },
      action: {
        setBadgeText: () => Promise.resolve(),
        setTitle: () => Promise.resolve(),
        openPopup: () => Promise.resolve(),
      },
    },
    fetch: () => Promise.resolve({ ok: true, json: async () => ({}) }),
    setTimeout: (fn) => { fn(); return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(BACKGROUND_SOURCE, sandbox, { filename: "background.js" });
  return { listener };
}
