"""Per-IP rate limiting for the hosted deployment (Phase 7.2).

Limits keep the hosted endpoint free to use during normal browsing while
bounding abuse and Anthropic spend (completes P1-7):

- /api/backtest: binary invocations are expensive → 10/minute
- /api/context:  may trigger a paid LLM call        → 30/minute
- /api/health:   exempt (uptime monitors poll it)
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

BACKTEST_LIMIT = "10/minute"
CONTEXT_LIMIT = "30/minute"
