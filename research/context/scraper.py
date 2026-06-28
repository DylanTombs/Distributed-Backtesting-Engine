"""URL → clean article text.

Fetches the URL server-side (so the extension needs no broad host permissions)
and strips boilerplate using trafilatura. If the URL is unreachable or behind
a paywall, callers should fall back to the raw page text sent from the
extension's content script.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Max characters to pass downstream — avoids overloading LLM context or regex
MAX_CHARS = 8_000


def fetch_article(url: str, timeout: float = 8.0) -> Optional[str]:
    """Fetch ``url`` and return clean article text, or ``None`` on failure.

    Attempts trafilatura first (best-in-class boilerplate removal), then falls
    back to a raw httpx GET and returns the first ``MAX_CHARS`` of the body.
    """
    try:
        import trafilatura  # type: ignore
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False,
                                       include_tables=False)
            if text:
                return text[:MAX_CHARS]
    except ImportError:
        logger.debug("trafilatura not installed — falling back to httpx")
    except Exception as exc:  # noqa: BLE001
        logger.warning("trafilatura failed for %s: %s", url, exc)

    # httpx fallback (plain HTML — useful for open pages)
    try:
        import httpx  # type: ignore
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "TradingTransformer/1.0"})
        resp.raise_for_status()
        return resp.text[:MAX_CHARS]
    except ImportError:
        logger.debug("httpx not installed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("httpx fallback failed for %s: %s", url, exc)

    return None


def clean_raw_text(text: str) -> str:
    """Strip excess whitespace from text already received from the extension."""
    import re
    text = re.sub(r'\s+', ' ', text)
    return text[:MAX_CHARS].strip()
