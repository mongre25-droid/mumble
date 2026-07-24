"""Shared HTTP transport for all AI providers.

Features:
  - Browser User-Agent for Cloudflare compatibility (Cerebras 403s without it)
  - Automatic retry with exponential backoff on 429 / 5xx
  - Rate-limit header parsing (Retry-After, x-ratelimit-reset-*)
  - Per-provider timeout configuration
  - JSON request/response helpers
"""

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime


# ---- User-Agent (required by Cerebras / Cloudflare) --------------------------
# Cerebras's API sits behind Cloudflare, which 403s (error 1010) the default
# "Python-urllib/x.y" User-Agent.  A normal browser UA is required on EVERY
# request.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _validate_http_url(url):
    """Return a validated HTTP(S) URL, rejecting local-file/custom schemes."""
    value = str(url or "").strip()
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise ValueError
        # Accessing .port validates malformed/non-numeric port components.
        parsed.port
    except (ValueError, TypeError):
        raise ValueError("API URL must use http:// or https:// with a valid host") \
            from None
    return value


def _build_headers(api_key, json_body=True):
    """Build a standard ``Authorization: Bearer`` headers dict.

    Args:
        api_key: the provider API key (required).
        json_body: if True, add ``Content-Type: application/json``.

    Returns:
        dict of HTTP headers.

    Raises:
        ValueError if api_key is empty.
    """
    if not (api_key or "").strip():
        raise ValueError("API key required — set one in Settings → Pro Mode")
    h = {
        "Authorization": "Bearer " + (api_key or "").strip(),
        "User-Agent": USER_AGENT,
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _build_anthropic_headers(api_key, json_body=True):
    """Build Anthropic-native auth headers (x-api-key + anthropic-version).

    Anthropic uses its own auth scheme, NOT ``Authorization: Bearer``.
    """
    if not (api_key or "").strip():
        raise ValueError("API key required — set one in Settings → Pro Mode")
    h = {
        "x-api-key": (api_key or "").strip(),
        "anthropic-version": "2023-06-01",
        "User-Agent": USER_AGENT,
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def headers_for(api_key, url=None, json_body=True):
    """Return the correct headers dict for a given endpoint.

    If *url* is an Anthropic endpoint, ``x-api-key`` auth is used;
    otherwise the standard ``Bearer`` scheme applies.
    """
    ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
    if url in (ANTHROPIC_URL, ANTHROPIC_MODELS_URL):
        return _build_anthropic_headers(api_key, json_body=json_body)
    return _build_headers(api_key, json_body=json_body)


# ---- Retry / backoff ---------------------------------------------------------

def _parse_retry_after(headers):
    """Extract a retry-delay (seconds) from HTTP response headers.

    Checks ``Retry-After``, ``retry-after``, ``x-ratelimit-reset-tokens``,
    and ``x-ratelimit-reset-requests``.  Returns a float (seconds) or None.

    Values larger than 3600 (likely Unix timestamps) are rejected.
    """
    ra = (
        headers.get("Retry-After")
        or headers.get("retry-after")
        or headers.get("x-ratelimit-reset-tokens")
        or headers.get("x-ratelimit-reset-requests")
        or ""
    )
    ra = str(ra).strip()
    if not ra:
        return None
    try:
        val = float(ra)
    except ValueError:
        stripped = ra.rstrip("s")
        try:
            val = float(stripped)
        except ValueError:
            # Retry-After also permits an RFC 7231 HTTP date.
            try:
                val = parsedate_to_datetime(ra).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                return None
    if not math.isfinite(val) or val < 0 or val > 3600:
        return None  # likely a Unix timestamp, not seconds
    return val


def retry_with_backoff(
    fn,
    args=(),
    kwargs=None,
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    backoff_factor=2.0,
):
    """Call *fn(*args, **kwargs)* with retry + exponential backoff.

    Retries on:
      - ``urllib.error.HTTPError`` with status 429 or 5xx
      - ``urllib.error.URLError`` (network blips)
      - ``OSError`` (low-level socket errors)

    When a 429 response includes a ``Retry-After`` header, that value is used
    as the delay (capped at *max_delay*) instead of the computed backoff.

    Args:
        fn: callable to invoke.
        args: positional args for *fn*.
        kwargs: keyword args for *fn*.
        max_retries: maximum number of retry attempts (default 3).
        base_delay: initial delay in seconds (default 1.0).
        max_delay: maximum delay in seconds (default 30.0).
        backoff_factor: multiplier for successive delays (default 2.0).

    Returns:
        The return value of *fn*.

    Raises:
        The last exception if all retries are exhausted.
    """
    kwargs = dict(kwargs or {})
    try:
        max_retries = int(max_retries)
        base_delay = float(base_delay)
        max_delay = float(max_delay)
        backoff_factor = float(backoff_factor)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid retry/backoff configuration") from None
    if (max_retries < 0 or not math.isfinite(base_delay)
            or not math.isfinite(max_delay)
            or not math.isfinite(backoff_factor)
            or base_delay < 0 or max_delay < 0 or backoff_factor < 1):
        raise ValueError("invalid retry/backoff configuration")
    last_exc = None
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except urllib.error.HTTPError as e:
            last_exc = e
            if attempt >= max_retries:
                break
            # Only retry on 429 (rate limit) and 5xx (server errors)
            if e.code != 429 and not (500 <= e.code < 600):
                raise
            # Use server-supplied Retry-After when available
            server_delay = _parse_retry_after(dict(e.headers or {}))
            wait = min(delay if server_delay is None else server_delay,
                       max_delay)
            if server_delay is not None:
                print(f"[transport] HTTP {e.code} — Retry-After={server_delay}s "
                      f"(attempt {attempt + 1}/{max_retries + 1})")
            else:
                print(f"[transport] HTTP {e.code} — backing off {wait:.1f}s "
                      f"(attempt {attempt + 1}/{max_retries + 1})")
            time.sleep(wait)
            delay = min(delay * backoff_factor, max_delay)
        except (urllib.error.URLError, OSError) as e:
            last_exc = e
            if attempt >= max_retries:
                break
            print(f"[transport] network error: {e} — retrying "
                  f"(attempt {attempt + 1}/{max_retries + 1})")
            time.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)
    raise last_exc  # all retries exhausted


# ---- JSON helpers ------------------------------------------------------------

def _json_request(url, data=None, headers=None, timeout=60, method=None):
    """Send a JSON request and return the parsed response.

    Args:
        url: endpoint URL.
        data: dict to JSON-encode as the request body (or None for GET).
        headers: dict of HTTP headers.
        timeout: request timeout in seconds.
        method: HTTP method (defaults to POST when *data* is set, else GET).

    Returns:
        Parsed JSON response (dict or list).

    Raises:
        RuntimeError on HTTP errors or network failures.
    """
    url = _validate_http_url(url)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    else:
        body = None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    if method:
        req.method = method
    def _perform_request():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    try:
        # Keep retrying below the exception-translation boundary.  Previously
        # providers called this helper directly, which immediately converted
        # HTTP/network failures to RuntimeError; the advertised retry policy was
        # therefore never reached by any production request.
        return retry_with_backoff(_perform_request)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            detail = str(getattr(e, "reason", "HTTP error"))[:200]
        raise RuntimeError(
            f"API HTTP {e.code}: {detail}"
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from None
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response: {e}") from None
    except (TimeoutError, OSError) as e:
        raise RuntimeError(f"Network error: {e}") from None
