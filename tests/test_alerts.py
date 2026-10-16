from __future__ import annotations

import io
import json

from pulsewatch.alerts import (
    AlerterConfig,
    ConsoleAlerter,
    TelegramAlerter,
    WebhookAlerter,
    build_alerters,
    format_change,
)
from pulsewatch.diffing import diff_lines

DIFF = diff_lines(["seats: 0"], ["seats: 12", "note: open now"])


class FakeOpener:
    """Minimal stand-in for urllib's opener, capturing the request."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        body = io.BytesIO(b"{}")
        body.status = self.status  # type: ignore[attr-defined]
        return body

    @property
    def last_body(self):
        request, _ = self.requests[-1]
        return json.loads(request.data.decode("utf-8"))


def test_format_change_marks_added_and_removed():
    message = format_change("seats", "https://example.edu", DIFF)
    assert "+ seats: 12" in message
    assert "- seats: 0" in message
    assert message.startswith("[pulsewatch] seats changed")


def test_format_change_truncates_long_line_lists():
    diff = diff_lines(["a"], [f"line{i}" for i in range(20)])
    message = format_change("t", "https://x.example", diff, max_lines=3)
    assert "more line(s)" in message
    assert sum(1 for line in message.splitlines() if line.startswith("+")) == 3


def test_console_alerter_writes_to_the_given_stream():
    stream = io.StringIO()
    assert ConsoleAlerter(stream).send("seats", "https://x.example", DIFF) is True
    assert "[pulsewatch] seats changed" in stream.getvalue()


def test_webhook_alerter_posts_json():
    opener = FakeOpener()
    alerter = WebhookAlerter("https://hooks.example/x", opener=opener)
    assert alerter.send("seats", "https://x.example", DIFF) is True
    payload = opener.last_body
    assert payload["target"] == "seats"
    assert payload["added"] == list(DIFF.added)
    assert payload["removed"] == list(DIFF.removed)


def test_webhook_alerter_reports_failure():
    opener = FakeOpener(status=500)
    assert WebhookAlerter("https://hooks.example/x", opener=opener).send("t", "u", DIFF) is False


def test_telegram_alerter_uses_the_bot_endpoint():
    opener = FakeOpener()
    alerter = TelegramAlerter("TOKEN", "42", opener=opener)
    assert alerter.send("seats", "https://x.example", DIFF) is True
    request, timeout = opener.requests[-1]
    assert "sendMessage" in request.full_url
    assert opener.last_body["chat_id"] == "42"


def test_build_alerters_skips_unconfigured_kinds():
    alerters = build_alerters(AlerterConfig(kinds=("console", "telegram", "webhook"),
                                            telegram_token=None, webhook_url=None))
    assert [a.name for a in alerters] == ["console"]


def test_build_alerters_creates_each_configured_kind():
    alerters = build_alerters(AlerterConfig(kinds=("console", "telegram", "webhook"),
                                            telegram_token="T", telegram_chat_id="C",
                                            webhook_url="https://hooks.example"))
    assert sorted(a.name for a in alerters) == ["console", "telegram", "webhook"]