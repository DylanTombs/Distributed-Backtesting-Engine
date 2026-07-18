# Phase 10 — Launch & Observability

**Status:** Planning
**Prerequisites:** Phases 7–9 merged to main (PRs #21/#22/#23)
**Ambition level:** High-stakes rather than high-code — the phase where the system meets real users. Most remaining risk is operational, not technical.

---

## Objective

Every layer is code-complete: the engine runs user-defined long/short
strategies on on-demand data, the API is hardened and rate-limited, the
extension is packaged, and the deployment scaffolding exists. What does not
yet exist is a **running public service** and any way to answer, once it runs:
*is it healthy, who is using it, and what is it costing?*

Phase 10 executes the two procedural launches (Fly.io deploy, Chrome Web
Store submission), and adds the minimum observability and key-lifecycle
tooling needed to operate a free public API responsibly. The bar is not an
enterprise monitoring stack — it is that a single operator can detect and
diagnose a problem from logs and one status endpoint within minutes.

---

## Task Breakdown

### 10.1 Hosted Deployment Execution (carries out 7.4)

Procedural, with a written verification checklist executed in order:

1. `flyctl` app + volume `model_artefacts` created; secrets set
   (`API_KEYS`, `ANTHROPIC_API_KEY`; `ALLOWED_EXTENSION_IDS` after 10.4).
2. `scripts/deploy_api.sh` — builds the linux/amd64 `backtest` binary
   pinned to the deploy SHA, deploys, verifies `/api/health`.
3. **Deploy-time verifications deferred from earlier phases:**
   - [ ] Cold start serves `/api/health` in < 5 s (7.4 exit criterion)
   - [ ] Cold-ticker fetch completes end-to-end in < 10 s (8.5 budget)
   - [ ] OHLCV cache at `/app/models/ohlcv` survives a redeploy (9.3)
   - [ ] Wrong-arch binary failure mode produces the sanitised 500 +
     server-side log, not a crash loop (P2-2 verification)
   - [ ] Rate limits and 401s behave correctly from a real remote IP
4. Custom domain (optional) and TLS via Fly defaults.

**Relevant components:** `fly.toml`, `Dockerfile.api`, `scripts/deploy_api.sh` — all exist; this task runs them.

### 10.2 Observability (the code component of this phase)

- **Structured logging:** JSON log lines (timestamp, level, request id,
  endpoint, status, duration ms, cache hit/miss, strategy kind, ticker) via
  a logging formatter — no new dependency. Request id generated per request
  and echoed in error responses so a user report can be joined to logs.
- **Status endpoint:** extend `GET /api/health` (or add `/api/status`,
  admin-key-gated) with: engine binary version/SHA, uptime, LRU cache
  hit/size counters, OHLCV cache ticker count, warm-up completion state,
  per-endpoint request/error counters since boot.
- **Spend guardrails:** counter for Anthropic fallback invocations per day;
  WARNING log when it crosses a configured threshold (`LLM_DAILY_BUDGET`).
- **Log retention:** stay within the privacy policy — request-level only,
  7-day TTL (Fly's default log retention already complies; document it).

### 10.3 API Key Lifecycle

- Runbook (`docs/operations.md`): issue a key (append to `API_KEYS` secret),
  revoke, rotate; incident response for a leaked key or abusive client.
- Per-key request counters exposed on the admin status endpoint (keys are
  hashed in logs and metrics — raw keys never logged, consistent with the
  Phase 7 auth design).
- Decide and document the issuance policy for strangers (manual on request
  via GitHub issue template, initially).

### 10.4 Chrome Web Store Submission (carries out 7.6)

1. CWS developer account ($5, verify email).
2. Capture the four 1280×800 screenshots per
   `extension/store/screenshots/README.md` (against the live hosted API).
3. Host `privacy_policy.html` at a stable public URL.
4. `scripts/pack_extension.sh` → upload → listing copy from
   `extension/store/description.txt` → submit.
5. On approval: record the extension ID, set `ALLOWED_EXTENSION_IDS`
   (ADR-043), add the install badge + link to README.
6. Rejection playbook (prepared in PHASE_7.md 7.6): `<all_urls>`
   justification, no-remote-code confirmation, privacy policy URL.

### 10.5 Post-Launch Feedback Loop

- GitHub issue templates: bug report, strategy-request (routes rules-v3
  demand), data-coverage gap (routes provider issues).
- README: install instructions for both store and self-hosted paths;
  honest "what this is / isn't" framing (backtests are hypothetical, not
  advice — mirrors the store listing).
- One week post-launch: review logs/counters, write a short retro into this
  file, and triage the Phase 11 backlog against actual usage.

---

## Exit Criteria

- [ ] Public API serving at a stable URL; all five 10.1 deploy-time
  verifications checked off
- [ ] Extension installable from the Chrome Web Store on a clean profile;
  `ALLOWED_EXTENSION_IDS` pinned to the published ID
- [ ] Every request traceable: request id in logs and error responses;
  status endpoint answers health, cache, usage, and spend questions
- [ ] LLM spend counter with a configured daily budget threshold and
  WARNING alerting in logs
- [ ] `docs/operations.md` covers deploy, rollback, key lifecycle, and
  incident basics — executable by someone who didn't write the code
- [ ] Python coverage stays ≥ 80%; observability code unit-tested
  (counters, request-id propagation, budget threshold)

---

## Open Questions / Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CWS review rejects `<all_urls>` or takes multiple cycles | Medium | High | Prepared justifications (7.6); fallback to `optional_host_permissions` |
| Stooq rate-limits or blocks the Fly egress IP | Medium | Medium | Cache-forever makes each ticker one fetch; pre-warm curated tickers from a dev machine via volume; fallback provider decision as a follow-up ADR |
| Anthropic spend spikes from legitimate traffic | Low | Medium | 10.2 budget counter + rate limits already bound it; extractor's 40-char floor and cache help |
| Fly shared-cpu-1x too small under real load | Low | Low | `MAX_CONCURRENT_BACKTESTS` semaphore degrades gracefully to queueing; scale VM if p95 suffers |
| Operator (you) is a team of one | Certain | Medium | Everything in 10.2/10.3 exists precisely to make 15-minute diagnosis realistic |

---

## Phase 11 Candidates (triage after launch, not before)

- **Rules v3:** OR-condition groups, stop-loss/take-profit exits, volume/ATR
  indicators, per-strategy sizing (spec `version: 3`; migration mechanism
  established by ADR-049)
- **Strategy comparison:** run 2–3 strategies side-by-side on one event in
  the popup (API already supports it via parallel requests; UI work)
- **ML scoreboard:** the evaluation harness from the original Phase 8 draft —
  revisit only if the experimental track earns attention
- **Provider redundancy:** second OHLCV source behind the fetcher interface

---

## Definition of Done

A stranger finds the extension on the Chrome Web Store, installs it, requests
a key via the documented path, and runs a custom short strategy on a stock
they read about this morning — while the operator can see that request in the
logs, knows what it cost, and could roll back the deployment in five minutes
if it had failed. The project stops being a repository and becomes a service.
