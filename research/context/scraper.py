"""URL → clean article text.

Fetches the URL server-side (so the extension needs no broad host permissions)
and strips boilerplate using trafilatura. If the URL is unreachable or behind
a paywall, callers should fall back to the raw page text sent from the
extension's content script.

SSRF defence in depth: every fetch goes through a single validated helper
that only permits http(s) URLs whose host resolves exclusively to globally
routable addresses, and never follows redirects. trafilatura is never allowed
to fetch on its own — it only receives HTML we downloaded ourselves. The API
schema layer performs its own boundary checks; this module is the deep
defence behind it.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Max characters to pass downstream — avoids overloading LLM context or regex
MAX_CHARS = 8_000

# Pre-clean bound for clean_raw_text: slice before running the whitespace
# regex so a multi-megabyte payload cannot pin the CPU. Whitespace collapse
# only shrinks text, so 4x MAX_CHARS leaves ample room for the final slice.
_PRE_CLEAN_CAP = MAX_CHARS * 4

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_public_http_url(url: str) -> bool:
    """Return True when ``url`` is http(s) and resolves only to global IPs.

    Rejects: non-http(s) schemes, URLs without a hostname, and any hostname
    (or literal IP) where ANY resolved address is non-global — loopback,
    private, link-local, reserved, multicast, or unspecified. Rejections are
    logged at WARNING with no more than the scheme and host.

    Residual risk (accepted): DNS rebinding between this resolution and the
    actual connect (TOCTOU). Mitigating that would require pinning the
    resolved address inside a custom HTTP transport.
    """
    try:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
    except ValueError:
        logger.warning("Rejected malformed URL")
        return False

    if scheme not in _ALLOWED_SCHEMES:
        logger.warning("Rejected URL: disallowed scheme %r (host=%s)", scheme, host)
        return False
    if not host:
        logger.warning("Rejected URL: missing hostname (scheme=%s)", scheme)
        return False

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        logger.warning("Rejected URL: cannot resolve host %s (%s)", host, exc)
        return False

    if not infos:
        logger.warning("Rejected URL: no addresses resolved for host %s", host)
        return False

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            logger.warning("Rejected URL: unparseable address for host %s", host)
            return False
        if not ip.is_global:
            logger.warning(
                "Rejected URL: host %s resolves to a non-global address", host
            )
            return False
    return True


def _fetch_validated(url: str, timeout: float) -> Optional[str]:
    """GET ``url`` after SSRF validation. Redirects are treated as failure.

    Returns the response body text, or ``None`` on validation failure,
    redirect, HTTP error, or network error.
    """
    if not _is_public_http_url(url):
        return None

    try:
        import httpx  # type: ignore
    except ImportError:
        logger.debug("httpx not installed")
        return None

    host = urlsplit(url).hostname
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=False,
                         headers={"User-Agent": "TradingTransformer/1.0"})
        if 300 <= resp.status_code < 400:
            logger.warning("Refusing redirect (%s) from host %s",
                           resp.status_code, host)
            return None
        resp.raise_for_status()
        return resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetch failed for host %s: %s", host, exc)
        return None


def fetch_article(url: str, timeout: float = 8.0) -> Optional[str]:
    """Fetch ``url`` and return clean article text, or ``None`` on failure.

    The page is downloaded exactly once via the validated helper; trafilatura
    only performs boilerplate removal on that HTML. When trafilatura is
    unavailable or extracts nothing, the raw body (truncated) is returned.
    """
    html = _fetch_validated(url, timeout)
    if html is None:
        return None

    try:
        import trafilatura  # type: ignore
        text = trafilatura.extract(html, include_comments=False,
                                   include_tables=False)
        if text:
            return text[:MAX_CHARS]
    except ImportError:
        logger.debug("trafilatura not installed — returning raw body")
    except Exception as exc:  # noqa: BLE001
        logger.warning("trafilatura extract failed: %s", exc)

    return html[:MAX_CHARS]


def clean_raw_text(text: str) -> str:
    """Strip excess whitespace from text already received from the extension.

    The input is sliced to a bounded pre-clean cap before the regex runs so a
    multi-megabyte payload cannot pin the CPU, then truncated to MAX_CHARS.
    """
    text = re.sub(r'\s+', ' ', text[:_PRE_CLEAN_CAP])
    return text[:MAX_CHARS].strip()
