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

const DEFAULTS = {
  apiBase:       "http://localhost:8502",
  dashboardBase: "http://localhost:8501",
};

async function getSettings() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(DEFAULTS, resolve);
  });
}

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
          // Cache per tab so re-opening popup is instant.
          // Popup messages arrive with sender.tab === undefined, so we fall
          // back to the tabId included in the payload by popup.js.
          const cacheTabId = msg.payload.tabId ?? tabId;
          if (cacheTabId) {
            chrome.storage.session.set({ [`result_${cacheTabId}`]: result });
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
      // chrome.action.openPopup() is only available in Chrome 127+ and can
      // reject even there (e.g. window not focused). Never fail silently:
      // if we cannot open the popup, surface a toolbar badge on the sender's
      // tab so the FAB click has a visible effect, and tell the caller.
      openPopupOrBadge(tabId)
        .then(sendResponse)
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;  // keep channel open for async

    default:
      sendResponse({ error: `Unknown message type: ${msg.type}` });
  }
});

// ---------------------------------------------------------------------------
// Popup opener with visible badge fallback (P2-4)
// ---------------------------------------------------------------------------

const BADGE_CLEAR_MS = 5000;

async function openPopupOrBadge(tabId) {
  if (chrome.action?.openPopup) {
    try {
      await chrome.action.openPopup();
      return { ok: true };
    } catch (_) {
      // Fall through to the badge fallback below.
    }
  }
  await showBadgeFallback(tabId);
  return { ok: false, fallback: "badge" };
}

async function showBadgeFallback(tabId) {
  // Scope to the sender's tab when known so other tabs are unaffected.
  const target = tabId != null ? { tabId } : {};
  await chrome.action.setBadgeText({ ...target, text: "▶" });
  await chrome.action.setTitle({
    ...target,
    title: "Click the toolbar icon to open the backtest popup",
  });
  // Best-effort clear. The MV3 service worker may be suspended before this
  // fires, in which case the badge simply persists until the next open —
  // acceptable, since it still points the user at the toolbar icon.
  setTimeout(() => {
    Promise.resolve(chrome.action.setBadgeText({ ...target, text: "" }))
      .catch(() => {});
  }, BADGE_CLEAR_MS);
}

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function fetchApi(path, options = {}) {
  const { apiBase } = await getSettings();
  const url = `${apiBase}${path}`;
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
