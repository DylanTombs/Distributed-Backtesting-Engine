# Phase 7 — Web Store Submission & Hosted Deployment

**Status:** Planning
**Prerequisites:** Phase 6 (contextual backtest browser extension)
**Ambition level:** High — first public release; converts a developer tool into a consumer product

---

## Objective

Phase 6 produced a fully functional browser extension that reads a financial news page,
extracts a market event, and returns backtest results in under 15 seconds. The limitation
is that it requires the user to run a local FastAPI server, have the C++ binary compiled,
and load the extension in Developer Mode — a setup that is inaccessible to anyone outside
the project.

Phase 7 removes every one of those constraints. A hosted API on Fly.io serves model
predictions from the server, so the user's machine needs nothing more than Chrome. The
extension ships with a settings page that accepts an API base URL and key, pointing either
at the hosted service or at a self-hosted instance. Rate limiting and API key
authentication protect the hosted endpoint, which calls the Anthropic API and runs the
transformer model on behalf of every user. The phase concludes with a Chrome Web Store
submission, making the extension publicly installable from a URL rather than a zip file.

**Why this matters:** A developer tool that requires compilation and a running local server
is only ever used by the developer who built it. Web Store distribution, a hosted backend,
and a settings page are the three steps that turn TradingTransformer into something a
financial analyst, journalist, or retail trader can install in 30 seconds and use
immediately.

---

## Architecture Overview

```
User's Browser (Chrome — installed from Web Store)
  │
  │  reads chrome.storage.sync → { apiBase, apiKey }
  │
  ├── Dev path ──► http://localhost:8502     (no auth, no rate limit)
  │
  └── Prod path ─► https://api.tradingtransformer.com   (Fly.io)
                     │
                     ├── nginx reverse proxy
                     │     └── rate limit: 10 req/min (backtest)
                     │                    30 req/min (context)
                     │
                     └── FastAPI (uvicorn)
                           │
                           ├── X-API-Key middleware  →  401 on missing/invalid key
                           │
                           ├── POST /api/context     →  extractor.py
                           │
                           └── POST /api/backtest    →  runner.py
                                                           │
                                                     ┌─────┴──────────────────┐
                                                     │  ml_backtest binary     │
                                                     │  transformer.pt         │
                                                     │  (on Fly.io volume)     │
                                                     └─────────────────────────┘
```

The critical structural change from Phase 6: the model binary and artefacts live on the
server. Extension users never need Python, CMake, or LibTorch on their own machine. The
per-run temporary working directory (7.3) resolves the fixed-output-path limitation noted
in ADR-027, enabling concurrent backtest requests on the server.

---

## Task Breakdown

### 7.1 Extension Settings Page & Configurable API Base

**New directory:** `extension/settings/`

```
extension/settings/
  settings.html     ← options page (chrome.runtime.openOptionsPage)
  settings.js       ← reads/writes chrome.storage.sync
  settings.css      ← matches popup dark theme
```

**`manifest.json` changes:**

```json
{
  "options_ui": {
    "page": "settings/settings.html",
    "open_in_tab": true
  },
  "host_permissions": ["<all_urls>"],
  "optional_host_permissions": []
}
```

The fixed `http://localhost:8502/*` host permission is replaced by `<all_urls>` so the
extension can reach whichever `apiBase` the user configures. `<all_urls>` requires CWS
reviewers to confirm the extension does not inject content on all pages — it does not;
`content.js` still only injects the FAB on financial pages (ADR-030 is unchanged).

**`settings.html`** — single-page form:

```
┌──────────────────────────────────────────────────────┐
│  TradingTransformer Settings                          │
├──────────────────────────────────────────────────────┤
│  API Base URL                                         │
│  [ https://api.tradingtransformer.com        ]        │
│                                                       │
│  API Key                                              │
│  [ ••••••••••••••••••••••••••••••••••        ]        │
│                                                       │
│  [Test Connection]          [Save]                    │
│                                                       │
│  Status: ✓ Connected — model loaded                   │
└──────────────────────────────────────────────────────┘
```

`settings.js`:

- On save: writes `{ apiBase, apiKey }` to `chrome.storage.sync`.
- On "Test Connection": fetches `${apiBase}/api/health` with the `X-API-Key` header and
  displays the response status inline.
- Exports `getSettings()` — a one-liner returning `chrome.storage.sync.get(...)` — used
  by `background.js` before every API call.

**`background.js` changes:**

All `fetch()` calls are updated to:

```js
const { apiBase, apiKey } = await getSettings();
const headers = apiKey ? { "X-API-Key": apiKey } : {};
fetch(`${apiBase}/api/backtest`, { method: "POST", headers, body: ... });
```

No other logic changes in `background.js`.

**Relevant components:**
- `extension/manifest.json` — version bump to `1.0.0`, add `options_ui`
- `extension/background.js` — read `apiBase` + `apiKey` from storage before every request
- `extension/settings/` — new directory (3 files)

---

### 7.2 API Key Authentication & Rate Limiting

**New files:**

```
research/api/
  auth.py        ← FastAPI dependency: validates X-API-Key header
  rate_limit.py  ← slowapi limiter instance and per-endpoint decorators
```

**`auth.py`** — dependency injected into protected routes:

```python
API_KEYS: frozenset[str] = frozenset(
    k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()
)

def require_api_key(x_api_key: str = Header(default="")) -> str:
    if not API_KEYS:           # dev mode: no keys configured → allow all
        return ""
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key
```

`API_KEYS` is a comma-separated environment variable, making it trivial to add or revoke
keys without redeploying. When `API_KEYS` is empty (local development), all requests are
allowed — preserving the frictionless dev workflow.

**`rate_limit.py`** — `slowapi` limiter with per-endpoint limits:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
BACKTEST_LIMIT  = "10/minute"
CONTEXT_LIMIT   = "30/minute"
```

Limits are chosen to keep the hosted endpoint free-to-use for normal browsing while
preventing abuse. The `/api/health` endpoint is exempt.

**`app.py` changes:**

- Register `limiter` as middleware (`app.state.limiter`)
- Add `require_api_key` as a dependency on `/api/context` and `/api/backtest`
- Add `@limiter.limit(BACKTEST_LIMIT)` / `@limiter.limit(CONTEXT_LIMIT)` decorators

**New environment variables:**

| Variable | Purpose | Example |
|----------|---------|---------|
| `API_KEYS` | Comma-separated valid API keys | `key_abc123,key_def456` |
| `ANTHROPIC_API_KEY` | Claude Haiku for context extraction | (existing) |

**Relevant components:**
- `research/api/auth.py` — new file
- `research/api/rate_limit.py` — new file
- `research/api/app.py` — register middleware, inject dependency

---

### 7.3 Per-Run Output Directories (Resolves ADR-027)

ADR-027 notes that `runner.py` uses a `threading.Lock` to serialise binary invocations
because the binary writes to a fixed output path (`ml_equity.csv`, `ml_trades.csv` in
CWD). On a hosted server with any non-trivial traffic, a global lock creates a sequential
queue. Per-run temporary directories resolve this without modifying the C++ binary.

**`runner.py` changes:**

```python
import tempfile, shutil

def _execute(tickers, date_start, date_end, skip_train):
    run_dir = tempfile.mkdtemp(prefix="tt_run_")
    try:
        # Symlink read-only artefacts; binary writes ml_equity.csv etc. into run_dir
        _symlink_inputs(run_dir)
        result = subprocess.run(
            [BINARY_PATH, ...],
            cwd=run_dir,          # binary CWD = isolated temp dir
            capture_output=True,
            timeout=90,
        )
        return _parse_outputs(run_dir)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
```

The global `threading.Lock` is removed. Concurrent requests each get an isolated
directory; there is no shared mutable state between them.

**Relevant components:**
- `research/api/runner.py` — remove `threading.Lock`, add `tempfile.mkdtemp` per invocation
- `research/api/` — no new files; pure internal change

---

### 7.4 Hosted API Deployment on Fly.io

**New files:**

```
Dockerfile.api          ← slim Python image, no C++ build stage
fly.toml                ← Fly.io app configuration
nginx/api.conf          ← rate limiting + reverse proxy (optional; slowapi may be enough)
```

**`Dockerfile.api`:**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY research/ ./research/
COPY backtester/ml_backtest ./backtester/ml_backtest   # pre-compiled for linux/amd64
COPY backtest_config.yaml .

CMD ["uvicorn", "research.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

The compiled `ml_backtest` binary (linux/amd64, produced by the existing C++ CI job) is
bundled directly into the image. Model artefacts (`transformer.pt`, scaler CSVs) are
stored on a Fly.io volume so they survive redeployment without being in the image.

**`fly.toml`:**

```toml
app = "tradingtransformer-api"
primary_region = "lhr"

[build]
  dockerfile = "Dockerfile.api"

[http_service]
  internal_port = 8080
  force_https   = true

  [http_service.concurrency]
    type       = "requests"
    hard_limit = 25
    soft_limit = 20

[[mounts]]
  source      = "model_artefacts"
  destination = "/app/models"

[[vm]]
  size = "shared-cpu-1x"

[env]
  PROJECT_ROOT = "/app"
```

**`docker-compose.yml` changes:**

Add an `api-hosted` profile so local development continues to use the existing `api`
service on port 8502, while `docker compose --profile hosted up api-hosted` simulates the
production image:

```yaml
  api-hosted:
    build:
      context: .
      dockerfile: Dockerfile.api
    profiles: [hosted]
    ports: ["8080:8080"]
    volumes:
      - ./models:/app/models:ro
    environment:
      - API_KEYS=${API_KEYS}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

**Deployment script:** `scripts/deploy_api.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
# Build linux/amd64 binary in CI Docker container, copy to repo root, then deploy
docker build --platform linux/amd64 -f Dockerfile.backtester -t tt-backtester .
docker cp $(docker create tt-backtester):/app/ml_backtest backtester/ml_backtest
flyctl deploy --remote-only
```

**Relevant components:**
- `Dockerfile.api` — new file
- `fly.toml` — new file
- `docker-compose.yml` — add `api-hosted` profile
- `scripts/deploy_api.sh` — new file

---

### 7.5 Extension Packaging for Chrome Web Store

**New directories:**

```
extension/icons/
  icon16.svg     ← source SVG (candlestick chart motif, matches content.js FAB)
  icon16.png
  icon32.png
  icon48.png
  icon128.png

extension/store/
  description.txt          ← CWS short (132 chars) + long description
  privacy_policy.html      ← hosted at tradingtransformer.com/privacy
  screenshots/
    01_popup_event.png     ← 1280×800: popup showing COVID crash detection
    02_popup_results.png   ← 1280×800: equity chart + metric cards after run
    03_settings.png        ← 1280×800: settings page with API key field
    04_fab_injection.png   ← 1280×800: FAB on a Bloomberg-style financial page
```

**`extension/store/description.txt`** — short description (132 chars max):

```
Run a transformer model backtest on any market event you read about. One click from any
financial news page.
```

Long description (5 paragraphs) covers: what it does, how it works, setup for
self-hosting, privacy stance (no data stored beyond the session), and link to the GitHub
repository.

**Privacy policy requirements** (CWS mandatory for extensions that make network requests):

- No user data is stored on the server beyond the per-request session
- `chrome.storage.sync` stores only `apiBase` and `apiKey` (never transmitted to
  third parties)
- The extension does not use cookies or analytics
- Hosted API logs are request-level only (timestamp + endpoint + response code) with a
  7-day TTL

**`manifest.json` final state for submission:**

```json
{
  "manifest_version": 3,
  "name": "TradingTransformer",
  "version": "1.0.0",
  "description": "Run a transformer model backtest on any market event you read about.",
  "permissions": ["activeTab", "storage"],
  "host_permissions": ["<all_urls>"],
  "options_ui": {
    "page": "settings/settings.html",
    "open_in_tab": true
  },
  "background": { "service_worker": "background.js" },
  "content_scripts": [{
    "matches": ["<all_urls>"],
    "js": ["content.js"],
    "run_at": "document_idle"
  }],
  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": { "16": "icons/icon16.png", "48": "icons/icon48.png" }
  },
  "icons": {
    "16": "icons/icon16.png",
    "32": "icons/icon32.png",
    "48": "icons/icon48.png",
    "128": "icons/icon128.png"
  }
}
```

**Packaging script:** `scripts/pack_extension.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
VERSION=$(jq -r '.version' extension/manifest.json)
zip -r "tradingtransformer-v${VERSION}.zip" extension/ \
    --exclude "extension/store/*" \
    --exclude "extension/icons/*.svg"
echo "Packaged: tradingtransformer-v${VERSION}.zip"
```

**Relevant components:**
- `extension/icons/` — new directory (4 PNGs + source SVG)
- `extension/store/` — new directory (description, privacy policy, screenshots)
- `extension/manifest.json` — version bump, options_ui, consolidated host_permissions
- `scripts/pack_extension.sh` — new file

---

### 7.6 Chrome Web Store Submission

This task is procedural and not fully automatable, but it has concrete, verifiable
milestones:

1. **CWS developer account** — pay the one-time $5 registration fee at
   `https://chrome.google.com/webstore/devconsole`. Verify email.

2. **Privacy policy hosting** — deploy `extension/store/privacy_policy.html` to a
   publicly accessible URL (e.g. `https://github.com/DylanTombs/TradingTransformer/blob/main/extension/store/privacy_policy.html`
   or a custom domain). The URL must be stable and resolvable by CWS reviewers.

3. **Submit the package:**
   - Run `scripts/pack_extension.sh` to produce `tradingtransformer-v1.0.0.zip`
   - Upload to the CWS Developer Dashboard
   - Fill in: category (Productivity), language (English), store listing copy from
     `extension/store/description.txt`, four screenshots from `extension/store/screenshots/`
   - Set visibility to Public
   - Submit for review

4. **Review response protocol** — CWS review typically takes 1–3 business days. Common
   rejection reasons and prepared responses:
   - `<all_urls>` host permission flagged: justify as "user-configured API base URL;
     the extension fetches only the URL the user explicitly configures"
   - Remotely hosted code: confirm no CDN scripts; Plotly is bundled in the extension
   - Privacy policy missing: confirm the URL from step 2

5. **Post-approval:** record the CWS listing URL and update `README.md` with an install
   badge.

**Relevant components:**
- `extension/store/` — all listing assets (produced in 7.5)
- `scripts/pack_extension.sh` — produces the submission zip
- `README.md` — install link and CWS badge after approval

---

## New Dependencies

| Package / Tool | Purpose | Layer |
|----------------|---------|-------|
| `slowapi>=0.1.9` | FastAPI rate limiting (per-IP, per-minute) | Python API |
| `flyctl` (CLI) | Fly.io deployment CLI (`brew install flyctl`) | Deployment |
| `jq` (CLI) | Version extraction in `pack_extension.sh` | Tooling |

No new C++ dependencies. No new JavaScript libraries — the extension remains vanilla JS
with Plotly bundled from Phase 6.

---

## Exit Criteria

- [ ] Extension settings page opens from Chrome's extension management UI, accepts an
  API base URL and key, and persists both to `chrome.storage.sync`
- [ ] `background.js` reads `apiBase` and `apiKey` from `chrome.storage.sync` and sends
  `X-API-Key` in every API request
- [ ] "Test Connection" button in settings returns a green status when pointed at the
  hosted API with a valid key, and a red error when the key is invalid
- [ ] `POST /api/backtest` returns equity curve + metrics when `apiBase` is set to the
  Fly.io URL (not localhost) on a machine with no local Python environment
- [ ] `POST /api/backtest` returns `HTTP 401` when `X-API-Key` is absent or incorrect
  on the hosted endpoint
- [ ] Hosted API returns `HTTP 429` after more than 10 `/api/backtest` requests within
  one minute from the same IP
- [ ] `GET /api/health` on the Fly.io deployment responds within 5 seconds of a cold
  start (fresh machine scale-up from zero)
- [ ] `docker compose --profile hosted up api-hosted` on a fresh Ubuntu 22.04 machine
  with no local Python environment starts the API and the extension can run a full
  backtest through it
- [ ] Two concurrent `POST /api/backtest` requests complete without either returning
  stale results from the other's output files (per-run temp directory isolation verified)
- [ ] `scripts/pack_extension.sh` produces a zip that loads cleanly in Chrome via
  `chrome://extensions` (no manifest errors)
- [ ] Extension loads from the Chrome Web Store (not Developer Mode) on a clean Chrome
  profile with no pre-existing extension files
- [ ] CWS listing is publicly accessible at a `chrome.google.com/webstore` URL
- [ ] All new Python code (auth, rate limiting, settings plumbing) covered by unit tests;
  overall Python test coverage remains ≥ 80%

---

## Open Questions / Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CWS review rejects `<all_urls>` permission | Medium | High | Prepare written justification; as fallback, use `optional_host_permissions` with user-granted access per origin |
| Fly.io cold starts exceed 5 s with model load | Medium | Medium | Pre-warm via a `/api/health` cron ping every 5 min; set `min_machines_running = 1` if cold-start SLA is unacceptable |
| `ml_backtest` linux/amd64 binary in Docker image produces stale artefact | Low | High | Pin binary build to the same git SHA as the deployment; CI job uploads binary as an artefact keyed by SHA |
| `<all_urls>` triggers host page CORS issues | Low | Low | Extension fetches only the user-configured `apiBase`; no page content is exfiltrated |
| Fly.io volume is unavailable during deployment | Low | High | Volume persists across deploys by default; document recovery procedure (re-upload `transformer.pt` via `flyctl sftp`) |
| API key rotation requires manual secret update | Medium | Low | `API_KEYS` accepts multiple comma-separated keys; rotate by adding the new key, removing the old after clients update |
| CWS review takes > 7 days | Low | Low | Submit during a low-traffic period; no code change needed while waiting |

---

## Definition of Done

Phase 7 is complete when a user with no knowledge of the project can:

1. Search "TradingTransformer" on the Chrome Web Store and click Install.
2. After installation, open the extension settings (right-click toolbar icon → Options),
   paste the hosted API URL (`https://api.tradingtransformer.com`) and their API key,
   and click Save.
3. "Test Connection" turns green.
4. Navigate to any financial news article (Bloomberg, Reuters, CNBC, etc.).
5. Click the candlestick FAB in the bottom-right corner.
6. See a market event auto-detected, click Run Backtest.
7. Receive an equity curve and Sharpe/drawdown/return/win-rate cards within 15 seconds,
   with the backtest running entirely on the hosted server — no local binary, no local
   Python, no local model file.
8. Click "Open in Dashboard" to see the full tearsheet, if they also have the dashboard
   running locally.

The system stops being a local developer tool and becomes something anyone can install
from a link.
