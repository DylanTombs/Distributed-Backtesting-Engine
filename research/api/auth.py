"""API key authentication for the hosted deployment (Phase 7.2).

Keys live in the ``API_KEYS`` environment variable as a comma-separated
list, so adding or revoking a key is a secret update — no image rebuild.
When ``API_KEYS`` is empty or unset (local development), all requests are
allowed, preserving the frictionless dev workflow.
"""
from __future__ import annotations

import logging
import os

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


def _load_keys() -> frozenset[str]:
    return frozenset(
        k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()
    )


API_KEYS: frozenset[str] = _load_keys()


def require_api_key(x_api_key: str = Header(default="")) -> str:
    """FastAPI dependency: validate the X-API-Key header.

    Dev mode (no keys configured) allows every request. Rejections are
    logged without the presented key — logging attacker-supplied secrets
    verbatim invites log-injection and accidental disclosure.
    """
    if not API_KEYS:
        return ""
    if x_api_key not in API_KEYS:
        logger.info("Rejected request with missing or invalid API key")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key
