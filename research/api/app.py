"""FastAPI application — contextual backtest bridge.

Start with:
    uvicorn research.api.app:app --port 8502 --reload

Endpoints:
    POST /api/context    — extract event, tickers, date range from page text
    POST /api/backtest   — run a windowed backtest and return results
    GET  /api/events     — list all known events (for the Quick Picks dropdown)
    GET  /api/health     — liveness check
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from .cors import add_cors
from .runner import is_model_loaded, run_backtest
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

app = FastAPI(
    title="TradingTransformer Contextual Backtest API",
    version="0.1.0",
    description="Bridge between the browser extension and the backtesting pipeline.",
)
add_cors(app)


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

@app.post("/api/context", response_model=ContextResponse)
def extract_context(req: ContextRequest) -> ContextResponse:
    if not req.has_content():
        raise HTTPException(status_code=422, detail="Provide url or raw_text")

    from research.context.scraper import clean_raw_text, fetch_article
    from research.context.extractor import extract

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

@app.post("/api/backtest", response_model=BacktestResponse)
def trigger_backtest(req: BacktestRequest) -> BacktestResponse:
    if not is_model_loaded():
        raise HTTPException(
            status_code=400,
            detail=(
                "No trained model found. Run the pipeline first: "
                "python run_pipeline.py"
            ),
        )

    try:
        return run_backtest(
            tickers=req.tickers,
            date_start=req.date_start,
            date_end=req.date_end,
            skip_train=req.skip_train,
        )
    except RuntimeError as exc:
        logger.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
