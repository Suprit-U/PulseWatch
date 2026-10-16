from __future__ import annotations

import urllib.error

import pytest

from pulsewatch.config import Retry
from pulsewatch.fetcher import FetchError, FetchResponse, HttpFetcher, jittered_interval
from pulsewatch.store import Store, open_store


def test_store_migrates_to_the_latest_schema():
    store = Store(":memory:")
    version = store.migrate()
    assert version == 4
    columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(snapshots)")}
    assert {"etag", "digest", "latency_ms", "body", "error"} <= columns


def test_store_persists_to_disk(tmp_path):
    path = tmp_path / "nested" / "pulsewatch.db"
    store = open_store(path)
    assert path.exists()
    store.close()


def test_counts_start_at_zero():
    store = Store(":memory:")
    assert store.counts() == {"snapshots": 0, "changes": 0, "alerts": 0}
    store.close()


def test_jittered_interval_stays_inside_the_band():
    interval = jittered_interval(100, 0.15)
    assert 85 <= interval <= 115
    assert jittered_interval(100, 0.0) == 100


def test_jittered_interval_is_never_below_one_second():
    assert jittered_interval(1.2, 0.5) >= 1.0


class _Response:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int) -> "urllib.error.HTTPError":
    """urllib raises HTTPError for 4xx/5xx; the fetcher branches on that type."""
    return urllib.error.HTTPError("https://x.example", code, "test", {}, None)


def test_http_fetcher_returns_decoded_body(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: _Response(body=b"hello"))
    fetcher = HttpFetcher(Retry(attempts=1))
    response = fetcher.fetch("https://x.example")
    assert response.body == "hello" and response.status == 200


def test_http_fetcher_sends_conditional_headers(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured.update(req.headers)
        return _Response(body=b"")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    HttpFetcher(Retry(attempts=1)).fetch("https://x.example", etag='W/"abc"',
                                         last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    lowered = {k.lower(): v for k, v in captured.items()}
    assert lowered["if-none-match"] == 'W/"abc"'
    assert lowered["if-modified-since"].startswith("Wed, 01 Jan 2025")


def test_http_fetcher_treats_304_as_cached(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(304)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    response = HttpFetcher(Retry(attempts=1)).fetch("https://x.example")
    assert response.from_cache is True and response.status == 304


def test_http_fetcher_does_not_retry_client_errors(monkeypatch):
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        raise _http_error(404)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(FetchError):
        HttpFetcher(Retry(attempts=3, base_delay=0.01)).fetch("https://x.example")
    assert len(attempts) == 1


def test_http_fetcher_retries_server_errors(monkeypatch):
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(FetchError):
        HttpFetcher(Retry(attempts=3, base_delay=0.01)).fetch("https://x.example")
    assert len(attempts) == 3


def test_fetch_response_defaults():
    response = FetchResponse(status=200, body="x")
    assert response.from_cache is False and response.etag is None and response.headers == {}