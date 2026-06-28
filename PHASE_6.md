# Phase 6 — Contextual Backtest Browser Extension

**Status:** Planning  
**Prerequisites:** Phase 5 (dashboard + archive infrastructure)  
**Ambition level:** High — first cross-boundary integration between the web and the backtester

---

## Objective

Turn the backtester into something you can use *in context* — while reading about a market crash, a Fed announcement, or an earnings shock, a browser overlay surfaces what the model would have done during that event and shows you the result without leaving the page.

The idea: you're on Bloomberg reading about the 2020 COVID crash. You click the extension. It reads the page, extracts the event (March 2020, equities selling off), finds relevant tickers, runs a windowed backtest over that period, and displays the equity curve and key metrics as a sidebar overlay — all in under 10 seconds.

**Why this matters:** Backtesting in isolation is abstract. Anchoring it to real events you're reading about makes it immediately legible — you can ask "would this strategy have survived that?" and get an answer while the context is still in your head.

---

## Architecture Overview

```
Browser (Chrome Extension)
  │
  │  POST /api/context   { url, raw_text }
  ▼
FastAPI Bridge  (research/api/)
  │
  ├── Context Extractor  →  tickers, date_range, event_label
  │     (research/context/extractor.py)
  │
  ├── Event Database     →  canonical date windows for known events
  │     (research/context/events.py)
  │
  └── Backtest Runner    →  calls existing C++ backtester or run_pipeline.py
        (research/api/runner.py)
  │
  │  JSON { metrics, equity_curve, trades, event_label }
  ▼
Extension Popup / Page Overlay
  (extension/  — Manifest V3, vanilla JS + Plotly CDN)
```

The FastAPI bridge is the only new long-running process. The extension is a thin client — all intelligence lives server-side.

---

## Task Breakdown

### 6.1 FastAPI Bridge

**New directory:** `research/api/`

```
research/api/
  __init__.py
  app.py          ← FastAPI entry point
  runner.py       ← wraps run_pipeline.py / backtester as callable
  schemas.py      ← Pydantic request/response models
  cors.py         ← permissive localhost-only CORS for extension
```

**Endpoints:**

```
POST /api/context
  body:  { url: str, raw_text: str | null }
  → 200: { event_label, tickers, date_start, date_end, confidence }
  → 422: extraction failed

POST /api/backtest
  body:  { tickers: list[str], date_start: str, date_end: str,
           skip_train: bool = true }
  → 200: { metrics, equity: [{date, equity}], trades: [...] }
  → 400: no data for those tickers/dates

GET  /api/events
  → 200: list of known events the extension can offer as quick-picks

GET  /api/health
  → 200: { status: "ok", model_loaded: bool }
```

**`runner.py`** wraps the existing pipeline — if the model is already trained it calls the C++ backtester directly via subprocess; if not it offers a `skip_train=true` path. The API must return in < 15 seconds for extension UX to feel responsive. Caches the last N backtests in memory (LRU) so repeated clicks on the same page are instant.

**Docker:** add `api` service to `docker-compose.yml` on port `8502`.

---

### 6.2 Context Extraction

**New directory:** `research/context/`

```
research/context/
  __init__.py
  extractor.py    ← main extraction pipeline
  entities.py     ← ticker NER, date parsing
  scraper.py      ← fetch + clean page text from URL
  events.py       ← curated event database
```

**`scraper.py`** — fetches the URL server-side (avoids extension needing broad permissions), strips boilerplate with `trafilatura` (best-in-class article extractor), returns clean article text. Falls back to raw text sent from the extension if the server can't reach the URL.

**`extractor.py`** — two-pass pipeline:

```
Pass 1 — Rule-based (fast, no network):
  - Ticker extraction: regex for $AAPL / AAPL (all-caps 1-5 chars) filtered
    against a known-tickers set (S&P 500 + NASDAQ 100)
  - Date extraction: dateparser on phrases like "March 2020", "Q3 2008",
    "last October", "the 1987 crash"
  - Event keyword matching against events.py database

Pass 2 — LLM fallback (only when Pass 1 confidence < 0.6):
  - Send 500-char excerpt to Claude Haiku 4.5 with structured output schema
  - Returns: { tickers, event_label, date_start, date_end }
  - Claude is the fallback, not the primary — keeps latency low and costs near-zero
    for clear articles
```

**Confidence scoring:** each extracted entity gets a confidence float. The API returns the aggregate confidence to the extension so the UI can show "high / medium / unsure" and let the user correct before running.

**`entities.py`** — ticker allow-list (S&P 500 CSV bundled with the extension), date normalisation to `YYYY-MM-DD`.

---

### 6.3 Event Database

**`research/context/events.py`** — a curated dictionary of ~50 major market events with canonical date windows, description, and affected tickers/sectors:

```python
EVENTS: dict[str, EventRecord] = {
    "dot_com_crash": EventRecord(
        label="Dot-com crash",
        keywords=["dot-com", "dotcom", "tech bubble", "nasdaq crash 2000"],
        date_start="2000-03-10",
        date_end="2002-10-09",
        tickers=["MSFT", "INTC", "CSCO", "AAPL"],
        description="NASDAQ peak to trough, -78%",
    ),
    "gfc_2008": EventRecord(
        label="Global Financial Crisis",
        keywords=["financial crisis", "2008 crash", "lehman", "subprime"],
        date_start="2007-10-09",
        date_end="2009-03-09",
        tickers=["GS", "JPM", "BAC", "C"],
        description="S&P 500 peak to trough, -57%",
    ),
    "covid_crash": EventRecord(
        label="COVID-19 crash",
        keywords=["covid", "coronavirus", "pandemic crash", "march 2020"],
        date_start="2020-02-19",
        date_end="2020-03-23",
        tickers=["AAPL", "MSFT", "AMZN", "TSLA"],
        description="S&P 500 fastest -34% in history",
    ),
    # ... ~47 more: Flash Crash 2010, Fed hike cycles, oil shocks,
    # earnings shocks (NFLX Q4 2022, META Q3 2022), geopolitical events ...
}
```

The extension's "Quick Picks" dropdown is populated from `GET /api/events` — so users can jump straight to a known event without needing to be on a relevant page.

---

### 6.4 Browser Extension

**New directory:** `extension/`

```
extension/
  manifest.json         ← Manifest V3
  background.js         ← service worker, manages API calls
  content.js            ← injected into pages, detects financial context
  popup/
    popup.html
    popup.js
    popup.css
  icons/
    icon16.png  icon48.png  icon128.png
```

**`manifest.json`** — Manifest V3, permissions: `activeTab`, `storage`. Host permission: `http://localhost:8502/*` only. No broad web permissions — the server does the fetching.

**`content.js`** — lightweight: watches for `DOMContentLoaded`, checks if the page looks financial (domain list: bloomberg.com, reuters.com, ft.com, wsj.com, cnbc.com, marketwatch.com, plus any page containing ≥2 S&P 500 tickers). If yes, injects a small floating button ("Backtest this →") in the bottom-right corner.

**`popup.html`** — 400×600px sidebar panel:

```
┌─────────────────────────────────┐
│  TradingTransformer  ⚙          │
├─────────────────────────────────┤
│  Detected event:                │
│  [COVID-19 crash       ▼]  ✏    │
│                                 │
│  Tickers:  AAPL  MSFT  AMZN    │
│  Window:   2020-02-19 → 03-23  │
│  Confidence: ████░ high         │
├─────────────────────────────────┤
│  [▶ Run Backtest]               │
├─────────────────────────────────┤
│  (equity chart renders here)    │
│                                 │
│  Sharpe  │ Max DD  │ Return     │
│   0.42   │ -12.3%  │  +8.1%    │
│                                 │
│  [Open in Dashboard →]          │
└─────────────────────────────────┘
```

The equity chart is a mini Plotly chart rendered inside the popup (Plotly loaded from extension bundle, not CDN, for offline use).

**`background.js`** — handles all `fetch()` calls to `localhost:8502`. Stores last result per tab in `chrome.storage.session` so re-opening the popup is instant.

---

### 6.5 Results Overlay

Two display modes, user-selectable in settings:

**Mode A — Popup panel** (default): results appear in the extension popup, no page modification.

**Mode B — Page overlay**: a floating panel injected into the page DOM by `content.js`. Draggable, closeable. Shows the equity curve and metric cards. Useful on sites with wide layouts (Bloomberg terminal-style).

The overlay uses a Shadow DOM so it's isolated from the host page's CSS.

For both modes, "Open in Dashboard →" passes the run's archive path as a query parameter to the Streamlit dashboard (`?run_id=<timestamp>`) so you can do deeper analysis without re-running.

---

### 6.6 Packaging & Distribution

**Development install:**
```bash
# 1. Start the API bridge
uvicorn research.api.app:app --port 8502 --reload

# 2. Load extension in Chrome
#    chrome://extensions → Developer mode → Load unpacked → ./extension/

# 3. Navigate to any financial news page
```

**Docker:**
```yaml
  api:
    build: { context: ., dockerfile: Dockerfile.python }
    command: uvicorn research.api.app:app --host 0.0.0.0 --port 8502
    ports: ["8502:8502"]
    volumes:
      - ./models:/app/models:ro
      - ./output:/app/output
      - ./backtest_config.yaml:/app/backtest_config.yaml:ro
    depends_on: [pipeline]
```

**Packaging for wider use (stretch goal):** Chrome Web Store submission requires the API URL to be user-configurable (not hardcoded to localhost) so others can point it at a hosted instance.

---

## New Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi>=0.111` | API bridge |
| `uvicorn[standard]>=0.30` | ASGI server |
| `trafilatura>=1.10` | Article text extraction |
| `dateparser>=1.2` | Natural language date parsing |
| `httpx>=0.27` | Async HTTP client for scraper |
| `anthropic>=0.28` | Claude Haiku fallback for entity extraction |

No new C++ dependencies. The extension is vanilla JS — no build step (no npm/webpack required for MVP).

---

## Exit Criteria

- [ ] `POST /api/context` correctly extracts tickers + date range from a Bloomberg or Reuters article URL in < 3 s
- [ ] `POST /api/backtest` returns equity curve + metrics for a known event window in < 15 s (model pre-loaded)
- [ ] Extension popup opens, shows extracted context, and runs a backtest without leaving the page
- [ ] "Quick Picks" dropdown lists all events from the event database
- [ ] Rule-based extraction handles ≥ 3 of: COVID crash, GFC, dot-com, a Fed rate decision, a single-stock earnings event — without LLM fallback
- [ ] LLM fallback (Claude Haiku) activates and returns structured output when rule-based confidence < 0.6
- [ ] "Open in Dashboard →" link lands on the correct run in the Streamlit dashboard
- [ ] Page overlay (Mode B) renders without breaking the host page's layout
- [ ] `docker compose up` brings up `api` service and extension can reach it
- [ ] All extraction and API logic covered by unit tests (≥ 80%); extension JS not counted

---

## Open Questions / Risks

| Risk | Mitigation |
|------|-----------|
| Paywalled sites (WSJ, FT) block server-side fetch | Extension sends raw `document.body.innerText` as fallback; server never needs to re-fetch |
| 15 s backtest latency feels slow | Cache last 20 backtests LRU; pre-warm common event windows on API startup |
| Chrome extension review for localhost permissions | Manifest V3 optional host permissions sidestep store restrictions; dev mode always works |
| Claude API cost at scale | Haiku is ~$0.0008 per extraction; only triggered on low-confidence; set a monthly cap |
| C++ backtester requires Docker on user machine | Offer a pure-Python fallback mode (slower, uses existing `run_pipeline.py`) |

---

## Definition of Done

Phase 6 is complete when a developer can:

1. Start `docker compose up` (or `uvicorn research.api.app:app --port 8502`)
2. Load the unpacked extension in Chrome
3. Navigate to any article about a historical market event
4. Click the extension icon, see the event auto-detected, click Run
5. See an equity curve and Sharpe ratio for that exact event window
6. Click "Open in Dashboard" and land on the full Phase 5 dashboard with that run loaded

The backtester stops being a CLI tool and becomes something you actually use while reading the news.
