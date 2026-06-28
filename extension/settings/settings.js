/**
 * settings.js — TradingTransformer extension settings page controller.
 *
 * Reads apiBase and dashboardBase from chrome.storage.sync on load and
 * writes them back when the user clicks Save.
 */

const DEFAULTS = { apiBase: "http://localhost:8502", dashboardBase: "http://localhost:8501" };

chrome.storage.sync.get(DEFAULTS, ({ apiBase, dashboardBase }) => {
  document.getElementById("api-base").value       = apiBase;
  document.getElementById("dashboard-base").value = dashboardBase;
});

document.getElementById("save").addEventListener("click", () => {
  const apiBase       = document.getElementById("api-base").value.trim();
  const dashboardBase = document.getElementById("dashboard-base").value.trim();
  chrome.storage.sync.set({ apiBase, dashboardBase }, () => {
    const status = document.getElementById("status");
    status.textContent = "Saved.";
    setTimeout(() => { status.textContent = ""; }, 2000);
  });
});
