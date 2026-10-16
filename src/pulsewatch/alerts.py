"""Alerters: turn a recorded change into a message someone actually receives.

Only the console alerter has no dependencies. Telegram and generic webhook both
use ``urllib.request`` from the standard library so PulseWatch stays
installable without extra packages.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .diffing import Diff

MAX_SNIPPET = 6
MAX_LINE_CHARS = 160


def format_change(target_name: str, url: str, diff: Diff, *, max_lines: int = MAX_SNIPPET) -> str:
    """Render a change as a short plain-text message."""
    lines = [f"[pulsewatch] {target_name} changed", f"URL: {url}"]
    for line in diff.added[:max_lines]:
        lines.append(f"+ {line[:MAX_LINE_CHARS]}")
    for line in diff.removed[:max_lines]:
        lines.append(f"- {line[:MAX_LINE_CHARS]}")
    hidden = (len(diff.added) + len(diff.removed)) - 2 * max_lines
    if hidden > 0:
        lines.append(f"... {hidden} more line(s)")
    return "\n".join(lines)


class Alerter(Protocol):
    name: str

    def send(self, target_name: str, url: str, diff: Diff) -> bool:
        """Return True when the alert was delivered."""


class ConsoleAlerter:
    """Prints to stdout. The default so `pulsewatch run` works out of the box."""

    name = "console"

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream

    def send(self, target_name: str, url: str, diff: Diff) -> bool:
        message = format_change(target_name, url, diff)
        stream = self._stream
        if stream is None:
            print(message, flush=True)
        else:
            stream.write(message + "\n")
        return True


class WebhookAlerter:
    """POSTs JSON to any endpoint. Meant for Slack/Discord/n8n style hooks."""

    name = "webhook"

    def __init__(self, url: str, *, timeout: float = 10.0, headers: dict[str, str] | None = None,
                 opener: Any = None) -> None:
        self.url = url
        self.timeout = timeout
        self.headers = headers or {}
        self._opener = opener or urllib.request.build_opener()

    def _payload(self, target_name: str, url: str, diff: Diff) -> bytes:
        return json.dumps({
            "target": target_name,
            "url": url,
            "added": list(diff.added[:MAX_SNIPPET]),
            "removed": list(diff.removed[:MAX_SNIPPET]),
            "ratio": round(diff.ratio, 4),
        }).encode("utf-8")

    def send(self, target_name: str, url: str, diff: Diff) -> bool:
        request = urllib.request.Request(
            self.url,
            data=self._payload(target_name, url, diff),
            headers={"Content-Type": "application/json", **self.headers},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return 200 <= getattr(response, "status", 200) < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False


class TelegramAlerter:
    """Sends through the Bot API ``sendMessage`` endpoint."""

    name = "telegram"

    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str, *, timeout: float = 10.0,
                 api_base: str | None = None, opener: Any = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.api_base = api_base or "https://api.telegram.org"
        self._opener = opener or urllib.request.build_opener()

    def send(self, target_name: str, url: str, diff: Diff) -> bool:
        body = json.dumps({
            "chat_id": self.chat_id,
            "text": format_change(target_name, url, diff),
            "disable_web_page_preview": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.API.format(token=self.token).replace("https://api.telegram.org", self.api_base),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return 200 <= getattr(response, "status", 200) < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False


@dataclass
class AlerterConfig:
    kinds: tuple[str, ...] = ("console",)
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None


def build_alerters(config: AlerterConfig, *, opener: Any = None) -> list[Alerter]:
    """Instantiate the alerters named in config, skipping misconfigured ones."""
    alerters: list[Alerter] = []
    for kind in config.kinds:
        if kind == "console":
            alerters.append(ConsoleAlerter())
        elif kind == "webhook" and config.webhook_url:
            alerters.append(WebhookAlerter(config.webhook_url, opener=opener))
        elif kind == "telegram" and config.telegram_token and config.telegram_chat_id:
            alerters.append(TelegramAlerter(config.telegram_token, config.telegram_chat_id, opener=opener))
    return alerters