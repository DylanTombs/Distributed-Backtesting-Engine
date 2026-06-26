/**
 * content.js — injected into financial news pages
 *
 * 1. Detects whether the current page looks financial (≥2 known tickers in body).
 * 2. Injects a floating "Backtest this →" button if so.
 * 3. On click, opens the extension popup (the button is cosmetic — the real
 *    UI is in popup.js; this just signals the user that the page is relevant).
 *
 * Runs at document_idle on the allowed-URL list in manifest.json.
 */

// Representative S&P 500 / NASDAQ 100 tickers to scan for
const KNOWN_TICKERS = new Set([
  "AAPL","MSFT","AMZN","NVDA","GOOGL","META","TSLA","JPM","XOM","CVX",
  "WMT","PG","UNH","LLY","AVGO","MA","V","HD","MRK","ABBV","COST","PEP",
  "BAC","MCD","CSCO","NFLX","DIS","ACN","TMO","ORCL","KO","TXN","QCOM",
  "NKE","GS","MS","C","WFC","INTC","IBM","AMD","BABA","CRM","ADBE","PYPL",
  "GE","CAT","DE","HON","RTX","LMT","NOC","BA","UAL","AAL","DAL","CCL",
  "SPY","QQQ","DIA","IWM","TLT","GLD","USO","EEM",
]);

const MIN_TICKERS = 2;
const BUTTON_ID   = "tt-backtest-btn";

function countTickersInPage() {
  const text = document.body.innerText.toUpperCase();
  let count = 0;
  for (const ticker of KNOWN_TICKERS) {
    // Word-boundary check: surrounded by non-alpha chars
    const re = new RegExp(`(?<![A-Z])${ticker}(?![A-Z])`);
    if (re.test(text)) {
      count++;
      if (count >= MIN_TICKERS) return count;
    }
  }
  return count;
}

function injectButton() {
  if (document.getElementById(BUTTON_ID)) return;

  const btn = document.createElement("button");
  btn.id = BUTTON_ID;
  btn.textContent = "Backtest this →";
  btn.title = "TradingTransformer — run a contextual backtest on this article";

  Object.assign(btn.style, {
    position:     "fixed",
    bottom:       "24px",
    right:        "24px",
    zIndex:       "2147483647",
    padding:      "10px 16px",
    background:   "#0f172a",
    color:        "#f8fafc",
    border:       "none",
    borderRadius: "8px",
    fontSize:     "13px",
    fontFamily:   "system-ui, sans-serif",
    cursor:       "pointer",
    boxShadow:    "0 4px 12px rgba(0,0,0,0.4)",
    transition:   "opacity 0.2s",
    opacity:      "0.92",
  });

  btn.addEventListener("mouseenter", () => { btn.style.opacity = "1"; });
  btn.addEventListener("mouseleave", () => { btn.style.opacity = "0.92"; });

  // Clicking just focuses the extension icon — the popup does the real work
  btn.addEventListener("click", () => {
    btn.textContent = "Opening…";
    btn.disabled = true;
    // Signal background that the user wants to open the popup for this tab.
    // Chrome doesn't allow programmatic popup opening, so we just flash the
    // badge to draw attention to the extension icon.
    chrome.runtime.sendMessage({ type: "HEALTH_CHECK" }, (resp) => {
      if (resp && !resp.error) {
        btn.textContent = "Click the extension icon ↗";
      } else {
        btn.textContent = "API offline — start uvicorn on :8502";
        btn.style.background = "#7f1d1d";
      }
      setTimeout(() => {
        btn.textContent = "Backtest this →";
        btn.disabled = false;
      }, 3000);
    });
  });

  document.body.appendChild(btn);
}

// Run after DOM is ready
if (countTickersInPage() >= MIN_TICKERS) {
  injectButton();
}
