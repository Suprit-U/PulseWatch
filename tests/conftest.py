from __future__ import annotations

import itertools

import pytest

from pulsewatch.config import Retry, Settings, Target
from pulsewatch.fetcher import FetchResponse
from pulsewatch.monitor import Monitor


class FakeClock:
    """Deterministic clock so cooldown behaviour is testable without sleeping."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeFetcher:
    """Serves queued bodies per URL and records the conditional headers it saw.

    A queued body of ``None`` is returned as a 304 Not Modified response, which is what a
    real server sends when the conditional headers say the client already has the document.
    """

    def __init__(self, pages: dict[str, list[str]], *, etag: str | None = None,
                 last_modified: str | None = None, fail_times: int = 0) -> None:
        self._pages = {url: itertools.cycle(bodies) for url, bodies in pages.items()}
        self._etag = etag
        self._last_modified = last_modified
        self._fail_times = fail_times
        self.calls: list[dict] = []
        self.urls: list[str] = []

    def fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None,
              headers: dict | None = None) -> FetchResponse:
        self.calls.append({"url": url, "etag": etag, "last_modified": last_modified,
                           "headers": dict(headers or {})})
        self.urls.append(url)
        if self._fail_times > 0:
            self._fail_times -= 1
            from pulsewatch.fetcher import FetchError
            raise FetchError(url, 3, "HTTP 503")
        body = next(self._pages[url])
        if body is None:
            # A queued ``None`` models a 304 Not Modified: the server asserts the document
            # is unchanged, so the body is empty and ``from_cache`` is set.
            return FetchResponse(status=304, body="", etag=self._etag,
                                 last_modified=self._last_modified, elapsed_ms=4.0,
                                 from_cache=True)
        return FetchResponse(status=200, body=body, etag=self._etag,
                             last_modified=self._last_modified, elapsed_ms=12.0)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(retry=Retry(attempts=1), jitter=0.0, min_tokens=2, retention_days=30)


@pytest.fixture
def store():
    from pulsewatch.store import open_store
    store = open_store(":memory:")
    yield store
    store.close()


@pytest.fixture
def monitor_factory(store, settings):
    def build(fetcher, alerters=None, **kwargs):
        return Monitor(store, fetcher, alerters or [], settings, **kwargs)
    return build


@pytest.fixture
def target() -> Target:
    return Target(name="seats", url="https://example.edu/seats", interval=30.0, cooldown=60.0)


class RecordingAlerter:
    name = "recording"

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, tuple, tuple]] = []

    def send(self, target_name: str, url: str, diff) -> bool:
        self.messages.append((target_name, url, diff.added, diff.removed))
        return True