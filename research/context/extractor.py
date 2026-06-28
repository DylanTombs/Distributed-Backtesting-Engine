"""Two-pass context extraction pipeline.

Pass 1 — rule-based (fast, no network):
  - Keyword match against events database
  - Ticker regex over allow-list
  - Date parsing via dateparser

Pass 2 — Claude Haiku fallback (only when confidence < CONFIDENCE_THRESHOLD):
  - Sends a short excerpt to Claude Haiku 4.5 with a structured output schema

Returns an ExtractionResult with a confidence score so the API can surface
"high / medium / unsure" in the UI.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from .entities import extract_tickers, extract_date_range
from .events import search_events

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6


@dataclass
class ExtractionResult:
    event_label: Optional[str]
    event_key: Optional[str]
    tickers: list[str]
    date_start: Optional[str]
    date_end: Optional[str]
    confidence: float          # 0.0–1.0
    source: str                # "rules" | "llm" | "rules+llm"


def extract(text: str) -> ExtractionResult:
    """Run the two-pass extraction pipeline over ``text``.

    ``url`` is used only for logging/debugging.
    """
    result = _rule_pass(text)
    logger.debug("Rule-pass confidence=%.2f event=%s", result.confidence, result.event_key)

    if result.confidence < CONFIDENCE_THRESHOLD:
        llm_result = _llm_pass(text)
        if llm_result is not None:
            # Merge: prefer LLM event/dates, keep rule tickers if LLM returns none
            result = ExtractionResult(
                event_label=llm_result.event_label or result.event_label,
                event_key=llm_result.event_key or result.event_key,
                tickers=llm_result.tickers or result.tickers,
                date_start=llm_result.date_start or result.date_start,
                date_end=llm_result.date_end or result.date_end,
                confidence=max(result.confidence, llm_result.confidence),
                source="rules+llm",
            )

    return result


# ---------------------------------------------------------------------------
# Pass 1 — rule-based
# ---------------------------------------------------------------------------

def _rule_pass(text: str) -> ExtractionResult:
    event_key: Optional[str] = None
    event_label: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    confidence = 0.0

    # Event keyword matching
    matches = search_events(text)
    if matches:
        key, record, match_count = matches[0]
        event_key = key
        event_label = record.label
        date_start = record.date_start
        date_end = record.date_end
        # Confidence grows with match count, caps at 0.9 for rule pass
        confidence = min(0.9, 0.4 + match_count * 0.15)

    # Ticker extraction
    tickers = extract_tickers(text)

    # Date extraction (only if no event-derived dates)
    if not date_start:
        date_start, date_end = extract_date_range(text)
        if date_start and not event_key:
            confidence = max(confidence, 0.3)

    # If we have event + tickers, boost confidence
    if event_key and tickers:
        confidence = min(confidence + 0.1, 0.95)

    # If no event found but tickers + dates present, still useful
    if not event_key and tickers and date_start:
        confidence = 0.35

    return ExtractionResult(
        event_label=event_label,
        event_key=event_key,
        tickers=tickers[:10],  # cap at 10
        date_start=date_start,
        date_end=date_end,
        confidence=confidence,
        source="rules",
    )


# ---------------------------------------------------------------------------
# Pass 2 — Claude Haiku 4.5 fallback
# ---------------------------------------------------------------------------

_LLM_SYSTEM = (
    "You are a financial market event extractor. Given a text excerpt, extract:\n"
    "1. event_label: short descriptive label (e.g. 'COVID-19 crash')\n"
    "2. tickers: list of stock ticker symbols mentioned or implied\n"
    "3. date_start: ISO date (YYYY-MM-DD) when the event began\n"
    "4. date_end: ISO date (YYYY-MM-DD) when the event ended\n\n"
    "Return ONLY valid JSON with keys: event_label, tickers, date_start, date_end.\n"
    "If unsure, set fields to null. Never invent dates."
)


def _llm_pass(text: str) -> Optional[ExtractionResult]:
    """Call Claude Haiku 4.5 with the text excerpt; return parsed result or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("ANTHROPIC_API_KEY not set — skipping LLM fallback")
        return None

    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=api_key)
        excerpt = text[:1500]  # keep token cost minimal

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": excerpt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM extraction failed: %s", exc)
        return None

    tickers = data.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]

    confidence = 0.4
    if data.get("event_label"):
        confidence += 0.10
    if data.get("date_start"):
        confidence += 0.15
    if data.get("date_end"):
        confidence += 0.05
    if tickers:
        confidence += 0.10
    # cap at 0.80 for LLM pass (rules+llm can exceed this in the merge)
    confidence = min(confidence, 0.80)

    return ExtractionResult(
        event_label=data.get("event_label"),
        event_key=None,
        tickers=[t.upper() for t in tickers][:10],
        date_start=data.get("date_start"),
        date_end=data.get("date_end"),
        confidence=confidence,
        source="llm",
    )
