"""Resolve ADBTuner / FruitDeepLinks dynamic and lane deeplink URLs."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

# Defaults match GlobalOptions.dynamic_url_timeout / dynamic_url_attempts.
# Lane resolvers (yttv-epg /whatson, FruitDeepLinks) can stall on first Docker
# hairpin to a LAN IP. Fail hung connects quickly and retry on this tuner
# instead of burning Channels' ~30s window across three boxes.
DEFAULT_TIMEOUT = 15.0
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 0.25


class DynamicUrlError(Exception):
    """Raised when a dynamic / lane URL cannot be resolved to a deeplink."""


def looks_like_dynamic_url(url: str) -> bool:
    """True when channel.url should be fetched before launch (not used as intent data)."""
    u = (url or "").strip()
    if not u:
        return False
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return False
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "dynamic_url_json_key" in qs:
        return True
    path = (parsed.path or "").lower()
    if "/lanes/" in path or "/whatson/" in path:
        return True
    fmt = (qs.get("format") or [None])[0]
    if fmt and str(fmt).lower() in ("json", "text", "txt"):
        # Lane-style resolvers often use format= without dynamic_url_json_key.
        if "deeplink" in path or "/api/" in path:
            return True
    return False


def _strip_query_key(url: str, key: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop(key, None)
    # Flatten single-value lists for cleaner URLs.
    flat: list[tuple[str, str]] = []
    for k, values in qs.items():
        for v in values:
            flat.append((k, v))
    new_query = urlencode(flat)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def _dig(data: Any, key: str) -> Any:
    if not isinstance(data, dict):
        return None
    if key in data:
        return data[key]
    # Nested common wrappers.
    for wrap in ("data", "result", "deeplinks"):
        nested = data.get(wrap)
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return None


def _http_timeout(timeout: float) -> httpx.Timeout:
    try:
        total = float(timeout)
    except (TypeError, ValueError):
        total = DEFAULT_TIMEOUT
    if not math.isfinite(total):
        total = DEFAULT_TIMEOUT
    total = max(1.0, total)
    connect = min(DEFAULT_CONNECT_TIMEOUT, total)
    return httpx.Timeout(total, connect=connect)


def _fetch_error_text(exc: BaseException, timeout: float) -> str:
    detail = str(exc).strip()
    if isinstance(exc, httpx.TimeoutException):
        return detail or f"timed out after {timeout:g}s"
    return detail or type(exc).__name__


async def resolve_dynamic_url(
    url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
) -> str:
    """Fetch a lane / dynamic URL and return the concrete deeplink string."""
    raw = (url or "").strip()
    if not looks_like_dynamic_url(raw):
        return raw

    parsed = urlparse(raw)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    json_key = (qs.get("dynamic_url_json_key") or [None])[0]
    fmt = (qs.get("format") or [None])[0]
    fmt_l = str(fmt).lower() if fmt else ""

    fetch_url = _strip_query_key(raw, "dynamic_url_json_key") if json_key else raw

    tries = max(1, int(attempts))
    owns_client = client is None
    http: Optional[httpx.AsyncClient] = client
    timeout_cfg = _http_timeout(timeout)
    resp: Optional[httpx.Response] = None
    try:
        for attempt in range(1, tries + 1):
            if owns_client:
                http = httpx.AsyncClient(
                    timeout=timeout_cfg, follow_redirects=True
                )
            assert http is not None
            try:
                resp = await http.get(fetch_url, timeout=timeout_cfg)
                break
            except httpx.TransportError as exc:
                reason = _fetch_error_text(exc, timeout)
                if owns_client:
                    await http.aclose()
                    http = None
                if attempt < tries:
                    logger.warning(
                        "Dynamic URL fetch failed (%s); retrying %s/%s %s",
                        reason,
                        attempt + 1,
                        tries,
                        fetch_url,
                    )
                    await asyncio.sleep(RETRY_PAUSE_SECONDS)
                    continue
                raise DynamicUrlError(
                    f"Failed to fetch dynamic URL {fetch_url!r}: {reason}"
                ) from exc
    finally:
        if owns_client and http is not None:
            await http.aclose()

    if resp is None:
        raise DynamicUrlError(f"Failed to fetch dynamic URL {fetch_url!r}")

    if resp.status_code >= 400:
        raise DynamicUrlError(
            f"Dynamic URL {fetch_url!r} returned HTTP {resp.status_code}"
        )

    body = (resp.text or "").strip()
    if not body:
        raise DynamicUrlError(
            f"no event on this lane ({fetch_url!r} returned empty body)"
        )

    # Prefer JSON when requested or when the body looks like JSON.
    want_json = bool(json_key) or fmt_l == "json" or body.startswith("{") or body.startswith("[")
    if want_json:
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            if json_key or fmt_l == "json":
                raise DynamicUrlError(
                    f"Dynamic URL {fetch_url!r} did not return JSON"
                ) from exc
            data = None
        if data is not None:
            if isinstance(data, dict) and data.get("ok") is False:
                raise DynamicUrlError(
                    f"no event on this lane ({fetch_url!r})"
                )
            keys_to_try = []
            if json_key:
                keys_to_try.append(str(json_key))
            keys_to_try.extend(["deeplink", "deeplink_url", "url"])
            for key in keys_to_try:
                found = _dig(data, key)
                if found is None:
                    continue
                text = str(found).strip()
                if text and text.lower() not in ("none", "null"):
                    logger.info("Resolved dynamic URL via JSON key %s", key)
                    return text
            raise DynamicUrlError(
                f"no event on this lane ({fetch_url!r} JSON missing deeplink; "
                f"tried {keys_to_try})"
            )

    # Plain-text deeplink body.
    if body.lower() in ("none", "null"):
        raise DynamicUrlError(
            f"no event on this lane ({fetch_url!r} returned no deeplink)"
        )
    # Some resolvers return JSON-as-text without content-type; already handled.
    first_line = body.splitlines()[0].strip()
    if not first_line:
        raise DynamicUrlError(f"Dynamic URL {fetch_url!r} returned empty deeplink")
    logger.info("Resolved dynamic URL as text (%d chars)", len(first_line))
    return first_line
