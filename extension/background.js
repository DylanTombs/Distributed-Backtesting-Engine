/**
 * background.js — service worker
 *
 * Manages all API calls to localhost:8502 so popup and content scripts
 * never need host_permissions themselves.
 *
 * Message protocol (from popup.js or content.js → background.js):
 *   { type: "EXTRACT_CONTEXT", payload: { url, rawText } }
 *   { type: "RUN_BACKTEST",    payload: { tickers, dateStart, dateEnd, skipTrain } }
 *   { type: "LIST_EVENTS" }
 *   { type: "HEALTH_CHECK" }
 *   { type: "GET_CACHED_RESULT", payload: { tabId } }
 *   { type: "SET_CACHED_RESULT", payload: { tabId, result } }
 */

const API_BASE = "http://localhost:8502";

// ---------------------------------------------------------------------------
// Message router
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id;

  switch (msg.type) {
    case "HEALTH_CHECK":
      fetchApi("/api/health")
        .then(sendResponse)
        .catch((err) => sendResponse({ error: err.message }));
      return true;  // keep channel open for async

    case "LIST_EVENTS":
      fetchApi("/api/events")
        .then(sendResponse)
        .catch((err) => sendResponse({ error: err.message }));
      return true;

    case "EXTRACT_CONTEXT":
      fetchApi("/api/context", {
        method: "POST",
        body: JSON.stringify({
          url: msg.payload.url ?? null,
          raw_text: msg.payload.rawText ?? null,
        }),
      })
        .then(sendResponse)
        .catch((err) => sendResponse({ error: err.message }));
      return true;

    case "RUN_BACKTEST":
      fetchApi("/api/backtest", {
        method: "POST",
        body: JSON.stringify({
          tickers: msg.payload.tickers,
          date_start: msg.payload.dateStart,
          date_end: msg.payload.dateEnd,
          skip_train: msg.payload.skipTrain ?? true,
        }),
      })
        .then((result) => {
          // Cache per tab so re-opening popup is instant
          if (tabId) {
            chrome.storage.session.set({ [`result_${tabId}`]: result });
          }
          sendResponse(result);
        })
        .catch((err) => sendResponse({ error: err.message }));
      return true;

    case "GET_CACHED_RESULT":
      chrome.storage.session.get([`result_${msg.payload.tabId}`], (items) => {
        sendResponse(items[`result_${msg.payload.tabId}`] ?? null);
      });
      return true;

    case "SET_CACHED_RESULT":
      chrome.storage.session.set({
        [`result_${msg.payload.tabId}`]: msg.payload.result,
      });
      sendResponse({ ok: true });
      return true;

    case "OPEN_POPUP":
      // chrome.action.openPopup() is only available in Chrome 127+.
      // Fall back gracefully if unavailable (older Chrome / non-focused window).
      if (chrome.action?.openPopup) {
        chrome.action.openPopup().catch(() => {});
      }
      sendResponse({ ok: true });
      return true;

    default:
      sendResponse({ error: `Unknown message type: ${msg.type}` });
  }
});

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function fetchApi(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    ...options,
  });

  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? detail;
    } catch (_) {}
    throw new Error(`API error ${resp.status}: ${detail}`);
  }

  return resp.json();
}
