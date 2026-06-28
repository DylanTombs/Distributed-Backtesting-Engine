/**
 * content.js — injects a persistent floating action button on every page.
 *
 * Clicking the FAB opens the extension popup (Chrome doesn't allow scripts to
 * open popups directly, so we open it as a side panel via chrome.action.openPopup
 * in the background worker, triggered by a message from here).
 */

const FAB_ID = "tt-fab";

function injectFab() {
  if (document.getElementById(FAB_ID)) return;

  const fab = document.createElement("button");
  fab.id = FAB_ID;
  fab.title = "TradingTransformer — backtest this page";
  fab.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"
         width="22" height="22" style="display:block">
      <!-- candlestick chart icon -->
      <rect x="4"  y="9"  width="3" height="8"  rx="0.5" fill="#38bdf8"/>
      <line x1="5.5" y1="7"  x2="5.5" y2="9"  stroke="#38bdf8" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="5.5" y1="17" x2="5.5" y2="19" stroke="#38bdf8" stroke-width="1.5" stroke-linecap="round"/>

      <rect x="10.5" y="5"  width="3" height="10" rx="0.5" fill="#4ade80"/>
      <line x1="12"  y1="3"  x2="12"  y2="5"  stroke="#4ade80" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="12"  y1="15" x2="12"  y2="18" stroke="#4ade80" stroke-width="1.5" stroke-linecap="round"/>

      <rect x="17" y="11" width="3" height="6"  rx="0.5" fill="#f87171"/>
      <line x1="18.5" y1="9"  x2="18.5" y2="11" stroke="#f87171" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="18.5" y1="17" x2="18.5" y2="19" stroke="#f87171" stroke-width="1.5" stroke-linecap="round"/>
    </svg>
  `;

  Object.assign(fab.style, {
    position:     "fixed",
    bottom:       "28px",
    right:        "28px",
    zIndex:       "2147483647",
    width:        "48px",
    height:       "48px",
    borderRadius: "50%",
    background:   "#0f172a",
    border:       "2px solid #38bdf8",
    cursor:       "pointer",
    display:      "flex",
    alignItems:   "center",
    justifyContent: "center",
    boxShadow:    "0 4px 16px rgba(56,189,248,0.35)",
    transition:   "transform 0.15s, box-shadow 0.15s",
    padding:      "0",
  });

  fab.addEventListener("mouseenter", () => {
    fab.style.transform  = "scale(1.1)";
    fab.style.boxShadow  = "0 6px 20px rgba(56,189,248,0.55)";
  });
  fab.addEventListener("mouseleave", () => {
    fab.style.transform  = "scale(1)";
    fab.style.boxShadow  = "0 4px 16px rgba(56,189,248,0.35)";
  });

  // Tell background to open the popup
  fab.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "OPEN_POPUP" });
  });

  document.body.appendChild(fab);
}

// Inject as soon as body is available
if (document.body) {
  injectFab();
} else {
  document.addEventListener("DOMContentLoaded", injectFab);
}
