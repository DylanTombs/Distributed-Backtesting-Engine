"""Pydantic request/response models for the FastAPI bridge."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, ConfigDict, field_validator


# ---------------------------------------------------------------------------
# POST /api/context
# ---------------------------------------------------------------------------

class ContextRequest(BaseModel):
    url: Optional[str] = None
    raw_text: Optional[str] = None

    @field_validator("url", "raw_text", mode="before")
    @classmethod
    def at_least_one(cls, v):
        return v  # cross-field check is in has_content()

    def has_content(self) -> bool:
        return bool(self.url or self.raw_text)


class ContextResponse(BaseModel):
    event_label: Optional[str]
    event_key: Optional[str]
    tickers: list[str]
    date_start: Optional[str]
    date_end: Optional[str]
    confidence: float
    source: str


# ---------------------------------------------------------------------------
# POST /api/backtest
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    tickers: list[str]
    date_start: str    # YYYY-MM-DD
    date_end: str      # YYYY-MM-DD
    skip_train: bool = True

    @field_validator("tickers")
    @classmethod
    def tickers_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("tickers must not be empty")
        return [t.upper() for t in v]


class EquityPoint(BaseModel):
    date: str
    equity: float


class BacktestResponse(BaseModel):
    run_id: Optional[str]
    metrics: dict
    equity: list[EquityPoint]
    trades: list[dict]
    cached: bool = False
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# GET /api/events
# ---------------------------------------------------------------------------

class EventSummary(BaseModel):
    key: str
    label: str
    date_start: str
    date_end: str
    tickers: list[str]
    description: str
    sector: Optional[str]


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_loaded: bool
    api_version: str = "0.1.0"
