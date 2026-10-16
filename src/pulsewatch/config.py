"""Configuration objects and target loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ALERT_KINDS = ("telegram", "webhook", "console")


@dataclass(frozen=True)
class Retry:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("invalid delay bounds")

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff for ``attempt`` (1-based), capped at ``max_delay``."""
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)


@dataclass(frozen=True)
class Selector:
    """CSS-ish extraction, kept dependency free.

    Only ``text`` is used by the built-in extractor; the field exists so targets
    can record *what* they look at and later runs can explain a match.
    """

    text: str | None = None
    contains: str | None = None


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    interval: float = 300.0
    selector: Selector | None = None
    json_path: str | None = None
    cooldown: float = 900.0
    enabled: bool = True
    as_html: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("target name is required")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"target {self.name!r} needs an http(s) url")
        if self.interval <= 0:
            raise ValueError(f"target {self.name!r} interval must be positive")
        if self.cooldown < 0:
            raise ValueError(f"target {self.name!r} cooldown cannot be negative")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Target":
        selector = data.get("selector")
        return cls(
            name=data["name"],
            url=data["url"],
            interval=float(data.get("interval", 300.0)),
            selector=Selector(**selector) if selector else None,
            json_path=data.get("json_path"),
            cooldown=float(data.get("cooldown", 900.0)),
            enabled=bool(data.get("enabled", True)),
            as_html=bool(data.get("as_html", False)),
            headers=dict(data.get("headers", {})),
            tags=tuple(data.get("tags", ())),
        )


@dataclass(frozen=True)
class Settings:
    retry: Retry = field(default_factory=Retry)
    jitter: float = 0.15
    timeout: float = 20.0
    user_agent: str = "pulsewatch/0.1 (+https://github.com/Suprit-U/PulseWatch)"
    retention_days: int = 30
    alert_kinds: tuple[str, ...] = ("console",)
    min_tokens: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter is a fraction between 0 and 1")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.retention_days < 1:
            raise ValueError("retention_days must be >= 1")
        unknown = set(self.alert_kinds) - set(ALERT_KINDS)
        if unknown:
            raise ValueError(f"unknown alert kinds: {sorted(unknown)}")
        if self.min_tokens < 1:
            raise ValueError("min_tokens must be >= 1")

    def with_retries(self, attempts: int) -> "Settings":
        return replace(self, retry=replace(self.retry, attempts=attempts))


def load_targets(path: str | Path) -> list[Target]:
    """Load targets from a JSON list or a ``{"targets": [...]}`` object."""
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("targets", [])
    if not isinstance(data, list):
        raise ValueError("targets file must hold a list")
    return [Target.from_dict(item) for item in data]