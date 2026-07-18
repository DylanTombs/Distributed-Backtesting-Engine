/**
 * settings.js — TradingTransformer extension settings page controller.
 *
 * Reads apiBase, apiKey, and dashboardBase from chrome.storage.sync on load,
 * validates them (validation.js, P1-5) and writes them back on Save.
 * "Test Connection" calls GET {apiBase}/api/health with the X-API-Key header
 * and reports the result inline without saving.
 */

const DEFAULTS = {
  apiBase: "http://localhost:8502",
  apiKey: "",
  dashboardBase: "http://localhost:8501",
};

const els = {
  apiBase: document.getElementById("api-base"),
  apiKey: document.getElementById("api-key"),
  dashboardBase: document.getElementById("dashboard-base"),
  save: document.getElementById("save"),
  test: document.getElementById("test"),
  status: document.getElementById("status"),
};

chrome.storage.sync.get(DEFAULTS, ({ apiBase, apiKey, dashboardBase }) => {
  els.apiBase.value = apiBase;
  els.apiKey.value = apiKey;
  els.dashboardBase.value = dashboardBase;
});

function showStatus(message, ok) {
  els.status.textContent = message;
  els.status.className = ok ? "ok" : "error";
}

/** Validate both URL fields; returns normalised values or null. */
function readAndValidate() {
  els.apiBase.classList.remove("invalid");
  els.dashboardBase.classList.remove("invalid");

  const api = validateBaseUrl(els.apiBase.value);
  if (!api.ok) {
    els.apiBase.classList.add("invalid");
    showStatus(`API Base URL: ${api.error}`, false);
    return null;
  }

  const dash = validateBaseUrl(els.dashboardBase.value);
  if (!dash.ok) {
    els.dashboardBase.classList.add("invalid");
    showStatus(`Dashboard Base URL: ${dash.error}`, false);
    return null;
  }

  return {
    apiBase: api.url,
    apiKey: els.apiKey.value.trim(),
    dashboardBase: dash.url,
  };
}

els.save.addEventListener("click", () => {
  const settings = readAndValidate();
  if (!settings) return;

  chrome.storage.sync.set(settings, () => {
    els.apiBase.value = settings.apiBase;
    els.dashboardBase.value = settings.dashboardBase;
    showStatus("Saved.", true);
    setTimeout(() => { els.status.textContent = ""; }, 2000);
  });
});

els.test.addEventListener("click", async () => {
  const settings = readAndValidate();
  if (!settings) return;

  els.test.disabled = true;
  showStatus("Testing connection…", true);
  try {
    const headers = settings.apiKey ? { "X-API-Key": settings.apiKey } : {};
    const resp = await fetch(`${settings.apiBase}/api/health`, { headers });
    if (resp.status === 401) {
      showStatus("✗ Server reachable but the API key was rejected (401).", false);
    } else if (!resp.ok) {
      showStatus(`✗ Server responded with HTTP ${resp.status}.`, false);
    } else {
      const body = await resp.json();
      showStatus(
        body.model_loaded
          ? "✓ Connected — model loaded"
          : "✓ Connected — server up, but no model is loaded",
        body.model_loaded,
      );
    }
  } catch (err) {
    showStatus(`✗ Could not reach the server: ${err.message}`, false);
  } finally {
    els.test.disabled = false;
  }
});
