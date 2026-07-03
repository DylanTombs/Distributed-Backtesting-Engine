"""CORS middleware configuration.

Allows requests only from the Chrome extension runtime and localhost origins.
The extension's ``fetch()`` calls arrive from a ``chrome-extension://`` origin
covered by the origin regex.  The ``*`` wildcard is intentionally avoided.

Extension-ID pinning (ADR-043, revisits ADR-035's localhost-only premise):
when ``ALLOWED_EXTENSION_IDS`` is set (comma-separated 32-char extension IDs),
only those extensions pass CORS — required on a hosted deployment, where
"any installed extension" is no longer shielded by a localhost-only bind.
When unset (local development, where sideloaded extension IDs churn), any
extension origin is accepted, matching ADR-035's original rationale.
"""
from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Origins allowed to call the API.  In production (Docker) the API is
# reachable only on localhost, so this is low-risk.
_ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:8501",   # Streamlit dashboard
    "http://localhost:8502",   # API self-calls
    "http://127.0.0.1:8501",
    "http://127.0.0.1:8502",
    # "null" removed: extension requests go through background.js (service
    # worker) which sends a chrome-extension:// origin, covered by the regex.
]


def _build_origin_regex() -> str:
    """Build the extension-origin regex, pinned to IDs when configured."""
    raw = os.environ.get("ALLOWED_EXTENSION_IDS", "")
    ids = [i.strip() for i in raw.split(",") if i.strip()]
    if not ids:
        return r"chrome-extension://.*"
    return r"chrome-extension://(" + "|".join(re.escape(i) for i in ids) + r")"


_ALLOWED_ORIGIN_REGEX = _build_origin_regex()


def add_cors(app: FastAPI) -> None:
    """Register CORS middleware on ``app``."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_origin_regex=_ALLOWED_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        # X-API-Key must be preflight-approved or the extension cannot
        # authenticate to the hosted API (P1-2).
        allow_headers=["Content-Type", "Accept", "X-API-Key"],
    )
