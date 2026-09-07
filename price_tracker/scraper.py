"""HTTP fetching with manners.

Responsibilities:
  * check robots.txt before touching a host, and cache the result
  * send a honest, identifying User-Agent
  * retry on transient failures (HTTP 429 and 5xx) with exponential back-off
  * surface a single, catchable exception type (``FetchError``) to callers

Politeness *between* different product URLs (the base delay + jitter) is the
caller's job - see ``core.run_check`` - because only the caller knows how many
URLs are in the batch.
"""

from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from .config import settings


class FetchError(Exception):
    """Raised when a URL cannot be retrieved after all retries."""


class RobotsDisallowed(FetchError):
    """Raised when robots.txt forbids fetching a URL."""


# host -> RobotFileParser, built lazily and reused for the whole run.
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _robots_for(url: str) -> urllib.robotparser.RobotFileParser:
    parts = urlparse(url)
    host = f"{parts.scheme}://{parts.netloc}"
    if host in _robots_cache:
        return _robots_cache[host]

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{host}/robots.txt")
    try:
        parser.read()
    except Exception:
        # If robots.txt cannot be read we choose to *allow* (fail-open), which
        # matches how most crawlers behave. A missing file means "no rules".
        parser.parse([])
    _robots_cache[host] = parser
    return parser


def is_allowed(url: str) -> bool:
    """Return True if our User-Agent may fetch ``url`` per robots.txt."""
    parser = _robots_for(url)
    # can_fetch wants the UA token; the product name portion is ignored by the
    # stdlib parser, so passing the full string is fine.
    return parser.can_fetch(settings.user_agent, url)


def fetch(url: str, *, session: requests.Session | None = None) -> str:
    """Fetch ``url`` and return the response body as text.

    Raises ``RobotsDisallowed`` if robots.txt forbids it, or ``FetchError`` if
    the request keeps failing.
    """
    if not is_allowed(url):
        raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

    sess = session or requests.Session()
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.8",
    }

    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            resp = sess.get(url, headers=headers, timeout=settings.request_timeout)
        except requests.RequestException as exc:
            last_error = exc
            _backoff(attempt, reason=str(exc))
            continue

        if resp.status_code == 200:
            return resp.text

        # 429 = rate limited, 5xx = server hiccup -> worth retrying.
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = _retry_after_seconds(resp)
            last_error = FetchError(f"HTTP {resp.status_code} for {url}")
            _backoff(attempt, reason=f"HTTP {resp.status_code}", floor=retry_after)
            continue

        # 4xx other than 429 (404, 403, ...) - retrying will not help.
        raise FetchError(f"HTTP {resp.status_code} for {url}")

    raise FetchError(f"gave up on {url} after {settings.max_retries} attempts: {last_error}")


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """Honour a numeric ``Retry-After`` header if the server sent one."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _backoff(attempt: int, *, reason: str, floor: float | None = None) -> None:
    """Sleep for an exponentially growing interval before the next attempt."""
    delay = 2.0 ** (attempt - 1)  # 1s, 2s, 4s, ...
    if floor is not None:
        delay = max(delay, floor)
    print(f"    retry {attempt}/{settings.max_retries} in {delay:.0f}s ({reason})")
    time.sleep(delay)
