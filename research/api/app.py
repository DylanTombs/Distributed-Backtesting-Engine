"""FastAPI application — contextual backtest bridge.

Start with:
    uvicorn research.api.app:app --port 8502 --reload

Endpoints:
    POST /api/context    — extract event, tickers, date range from page text
    POST /api/backtest   — run a windowed backtest and return results
    GET  /api/events     — list all known events (for the Quick Picks dropdown)
    GET  /api/health     — liveness check

Note: no ``from __future__ import annotations`` here — slowapi's decorator
wrapper cannot resolve postponed (string) annotations for the request
models, which breaks FastAPI's schema generation.
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .auth import require_api_key
from .cors import add_cors
from .rate_limit import BACKTEST_LIMIT, CONTEXT_LIMIT, limiter
from .runner import BacktestInputError, is_model_loaded, run_backtest, warmup_cache
from .schemas import (
    BacktestRequest,
    BacktestResponse,
    ContextRequest,
    ContextResponse,
    EventSummary,
    HealthResponse,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    t = threading.Thread(target=warmup_cache, daemon=True, name="cache-warmup")
    t.start()
    yield


app = FastAPI(
    title="TradingTransformer Contextual Backtest API",
    version="0.1.0",
    description="Bridge between the browser extension and the backtesting pipeline.",
    lifespan=lifespan,
)
add_cors(app)

# Per-IP rate limiting (Phase 7.2). /api/health stays exempt for monitors.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Reject oversized bodies before deserialisation (P1-8). 2 MB comfortably
# exceeds the largest legitimate payload (MAX_RAW_TEXT_CHARS of UTF-8 text).
MAX_BODY_BYTES = 2_000_000


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", model_loaded=is_model_loaded())


# ---------------------------------------------------------------------------
# GET /api/events
# ---------------------------------------------------------------------------

@app.get("/api/events", response_model=list[EventSummary])
def list_events() -> list[EventSummary]:
    from research.context.events import EVENTS
    return [
        EventSummary(
            key=key,
            label=r.label,
            date_start=r.date_start,
            date_end=r.date_end,
            tickers=r.tickers,
            description=r.description,
            sector=r.sector,
        )
        for key, r in EVENTS.items()
    ]


# ---------------------------------------------------------------------------
# POST /api/context
# ---------------------------------------------------------------------------

@app.post(
    "/api/context",
    response_model=ContextResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(CONTEXT_LIMIT)
def extract_context(request: Request, req: ContextRequest) -> ContextResponse:
    if not req.has_content():
        raise HTTPException(status_code=422, detail="Provide url or raw_text")

    from research.context.scraper import clean_raw_text, fetch_article
    from research.context.extractor import extract

    # Unexpected extraction failures must surface as a generic 500 with full
    # detail server-side only — adversarial page text must not 500 with a
    # stack-derived message (P2-2).
    try:
        # Resolve text: raw_text from extension takes priority (handles paywalls)
        if req.raw_text:
            text = clean_raw_text(req.raw_text)
        else:
            text = fetch_article(req.url)
            if not text:
                raise HTTPException(
                    status_code=422,
                    detail="Could not fetch article text. Send raw_text instead.",
                )

        result = extract(text)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Context extraction failed")
        raise HTTPException(
            status_code=500, detail="Context extraction failed unexpectedly."
        ) from exc

    if result.confidence < 0.15:
        raise HTTPException(
            status_code=422,
            detail="No financial context detected on this page.",
        )

    return ContextResponse(
        event_label=result.event_label,
        event_key=result.event_key,
        tickers=result.tickers,
        date_start=result.date_start,
        date_end=result.date_end,
        confidence=result.confidence,
        source=result.source,
    )


# ---------------------------------------------------------------------------
# POST /api/backtest
# ---------------------------------------------------------------------------

@app.post(
    "/api/backtest",
    response_model=BacktestResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(BACKTEST_LIMIT)
def trigger_backtest(request: Request, req: BacktestRequest) -> BacktestResponse:
    # The model gate applies only to the experimental ML strategy — the
    # transparent rule strategies need no model artefact (Phase 8, ADR-047).
    wants_ml = req.strategy is not None and req.strategy.template == "ml_transformer"
    if wants_ml and not is_model_loaded():
        raise HTTPException(
            status_code=400,
            detail=(
                "The experimental ML strategy is not available on this "
                "server (no trained model deployed)."
            ),
        )

    # Error taxonomy (P2-1/P2-2): client-input problems return 422 with a
    # sanitised message; every server fault — including non-RuntimeError
    # surprises like a wrong-arch binary (OSError) or a corrupt CSV — returns
    # a generic 500. Full detail is logged server-side only.
    try:
        return run_backtest(
            tickers=req.tickers,
            date_start=req.date_start,
            date_end=req.date_end,
            strategy=req.strategy,
        )
    except BacktestInputError as exc:
        logger.info("Backtest rejected: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Backtest failed")
        raise HTTPException(
            status_code=500,
            detail="Backtest execution failed. See server logs for details.",
        ) from exc
