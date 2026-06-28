"""CORS middleware configuration.

Allows requests only from the Chrome extension runtime and localhost origins.
The extension's ``fetch()`` calls arrive from a ``chrome-extension://`` origin
covered by ``_ALLOWED_ORIGIN_REGEX``.  The ``*`` wildcard is intentionally
avoided — the API binds to localhost only in development.
"""
from __future__ import annotations

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

_ALLOWED_ORIGIN_REGEX = r"chrome-extension://.*"


def add_cors(app: FastAPI) -> None:
    """Register CORS middleware on ``app``."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_origin_regex=_ALLOWED_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept"],
    )
