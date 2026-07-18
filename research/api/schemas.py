"""Pydantic request/response models for the FastAPI bridge."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional
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
# Strategy specification (Phase 8.2/8.3, ADR-046)
#
# Canonical schema, mirrored exactly by the C++ RuleSpec parser: whitelisted
# indicators/operators, ≤ 8 AND-ed conditions per side, periods 2–250,
# long-only. Never accepts code.
# ---------------------------------------------------------------------------

INDICATORS = ("PRICE", "SMA", "EMA", "RSI", "HIGH_N", "LOW_N")
RULE_OPS = ("<", ">", "crosses_above", "crosses_below")
MAX_CONDITIONS_PER_SIDE = 8
STRATEGY_TEMPLATES = ("buy_hold", "ma_cross", "rsi_reversion", "breakout",
                      "ml_transformer")


class RuleCondition(BaseModel):
    indicator: Literal["PRICE", "SMA", "EMA", "RSI", "HIGH_N", "LOW_N"]
    period: Optional[int] = Field(default=None, ge=2, le=250)
    op: Literal["<", ">", "crosses_above", "crosses_below"]
    value: Optional[float] = Field(default=None, ge=-1e9, le=1e9)
    other_indicator: Optional[
        Literal["PRICE", "SMA", "EMA", "RSI", "HIGH_N", "LOW_N"]
    ] = None
    other_period: Optional[int] = Field(default=None, ge=2, le=250)

    @model_validator(mode="after")
    def validate_shape(self) -> "RuleCondition":
        if self.indicator == "PRICE":
            if self.period is not None:
                raise ValueError("PRICE takes no period")
        elif self.period is None:
            raise ValueError(f"{self.indicator} requires a period")

        has_value = self.value is not None
        has_other = self.other_indicator is not None
        if has_value == has_other:
            raise ValueError(
                "exactly one of 'value' or 'other_indicator' is required"
            )
        if has_other:
            if self.other_indicator == "PRICE":
                if self.other_period is not None:
                    raise ValueError("PRICE takes no period")
            elif self.other_period is None:
                raise ValueError(f"{self.other_indicator} requires a period")
        if self.op in ("crosses_above", "crosses_below") and not has_other:
            raise ValueError("cross operators require 'other_indicator'")
        return self


class StrategyRules(BaseModel):
    entry: list[RuleCondition] = Field(min_length=1,
                                       max_length=MAX_CONDITIONS_PER_SIDE)
    exit: list[RuleCondition] = Field(default_factory=list,
                                      max_length=MAX_CONDITIONS_PER_SIDE)
    # Spec v2 (Phase 9.2, ADR-049): entry conditions open a short position
    # instead of a long one; exit covers. Long-only remains the default.
    direction: Literal["long", "short"] = "long"


class StrategySpec(BaseModel):
    template: Optional[
        Literal["buy_hold", "ma_cross", "rsi_reversion", "breakout",
                "ml_transformer"]
    ] = None
    params: dict[str, float] = Field(default_factory=dict)
    rules: Optional[StrategyRules] = None
    name: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def template_xor_rules(self) -> "StrategySpec":
        if (self.template is None) == (self.rules is None):
            raise ValueError("provide exactly one of 'template' or 'rules'")
        if self.rules is not None and self.params:
            raise ValueError("'params' only applies to templates")
        if len(self.params) > 8:
            raise ValueError("too many template params")
        return self


# ---------------------------------------------------------------------------
# POST /api/backtest
# ---------------------------------------------------------------------------

class BacktestRequest(BaseModel):
    tickers: list[str] = Field(max_length=MAX_TICKERS)
    date_start: str    # YYYY-MM-DD
    date_end: str      # YYYY-MM-DD
    skip_train: bool = True
    strategy: Optional[StrategySpec] = None   # None → buy_hold default

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
    strategy: Optional[dict] = None      # resolved strategy echo (8.3)
    experimental: bool = False           # True only for the ML path


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
