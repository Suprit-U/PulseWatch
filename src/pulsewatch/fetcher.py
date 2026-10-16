"""Fetching with conditional requests, retries and latency measurement.

``FetchResponse`` is the seam that makes PulseWatch testable: the real fetcher
uses :mod:`urllib`, tests inject a fake and never touch the network.
"""

from __future__ import annotations

import gzip
import random
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Protocol

from .config import Retry


@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: str
    etag: str | None = None
    last_modified: str | None = None
    elapsed_ms: float = 0.0
    from_cache: bool = False
    headers: dict[str, str] = field(default_factory=dict)


class Fetcher(Protocol):
    def fetch(self, url: str, *, etag: str | None = None,
              last_modified: str | None = None,
              headers: dict[str, str] | None = None) -> FetchResponse:
        ...


class FetchError(RuntimeError):
    """Raised when every retry attempt failed."""

    def __init__(self, url: str, attempts: int, cause: str) -> None:
        super().__init__(f"{url} failed after {attempts} attempts: {cause}")
        self.url = url
        self.attempts = attempts
        self.cause = cause


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", errors="replace")


class HttpFetcher:
    """urllib-based fetcher with conditional GET and bounded exponential backoff."""

    def __init__(self, retry: Retry, timeout: float = 20.0, user_agent: str = "pulsewatch",
                 sleep=time.sleep) -> None:
        self.retry = retry
        self.timeout = timeout
        self.user_agent = user_agent
        self._sleep = sleep

    def fetch(self, url: str, *, etag: str | None = None,
              last_modified: str | None = None,
              headers: dict[str, str] | None = None) -> FetchResponse:
        request_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        request_headers.update(headers or {})
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified

        last_error = "unknown"
        for attempt in range(1, self.retry.attempts + 1):
            started = time.perf_counter()
            try:
                request = urllib.request.Request(url, headers=request_headers, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return FetchResponse(
                        status=response.status,
                        body=_decode(raw, response.headers.get("Content-Encoding")),
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                        headers={k.lower(): v for k, v in response.headers.items()},
                    )
            except urllib.error.HTTPError as exc:
                elapsed = (time.perf_counter() - started) * 1000
                if exc.code == 304:
                    return FetchResponse(
                        status=304,
                        body="",
                        etag=exc.headers.get("ETag") or etag,
                        last_modified=exc.headers.get("Last-Modified") or last_modified,
                        elapsed_ms=elapsed,
                        from_cache=True,
                    )
                last_error = f"HTTP {exc.code}"
                if exc.code < 500 and exc.code != 429:
                    break  # client errors are not worth retrying
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = type(exc).__name__ + f": {exc}"
            if attempt < self.retry.attempts:
                self._sleep(self.retry.delay_for(attempt))
        raise FetchError(url, self.retry.attempts, last_error)


def jittered_interval(interval: float, jitter: float, rng: random.Random | None = None) -> float:
    """Spread poll intervals so many targets do not stampede a host at once."""
    if jitter <= 0:
        return interval
    generator = rng or random
    spread = interval * jitter
    return max(1.0, interval + generator.uniform(-spread, spread))