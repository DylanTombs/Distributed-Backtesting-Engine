"""Pydantic request/response models for the FastAPI bridge."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Boundary size caps (P1-8): fail fast on oversized payloads before any
# processing. raw_text far exceeds the 8 000 chars used downstream but stays
# well below anything that could pin CPU or memory.
MAX_RAW_TEXT_CHARS = 200_000
MAX_URL_CHARS = 2_048
MAX_TICKERS = 20


# ---------------------------------------------------------------------------
# POST /api/context
# ---------------------------------------------------------------------------

class ContextRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=MAX_URL_CHARS)
    raw_text: Optional[str] = Field(default=None, max_length=MAX_RAW_TEXT_CHARS)

    @field_validator("url")
    @classmethod
    def url_scheme_must_be_http(cls, v: Optional[str]) -> Optional[str]:
        # Boundary check only — deep SSRF validation (IP ranges, redirects)
        # happens in research/context/scraper.py.
        if v is not None and not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must use http or https")
        return v

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
    tickers: list[str] = Field(max_length=MAX_TICKERS)
    date_start: str    # YYYY-MM-DD
    date_end: str      # YYYY-MM-DD
    skip_train: bool = True

    @field_validator("tickers")
    @classmethod
    def tickers_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("tickers must not be empty")
        upcased = [t.upper() for t in v]
        for t in upcased:
            if not re.fullmatch(r'[A-Z0-9.\-]{1,7}', t):
                raise ValueError(f"Invalid ticker symbol: {t!r}")
        return upcased

    @model_validator(mode="after")
    def validate_dates(self) -> "BacktestRequest":
        try:
            start_dt = datetime.fromisoformat(self.date_start)
        except ValueError:
            raise ValueError(
                f"date_start {self.date_start!r} is not a valid ISO 8601 date"
            )
        try:
            end_dt = datetime.fromisoformat(self.date_end)
        except ValueError:
            raise ValueError(
                f"date_end {self.date_end!r} is not a valid ISO 8601 date"
            )
        if start_dt > end_dt:
            raise ValueError("date_start must be before date_end")
        return self


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
