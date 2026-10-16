"""Normalisation and token-level diffing.

The point of PulseWatch is not "did bytes change" but "what a human would call
changed". These functions turn a fetched document into comparable tokens and
report added/removed lines with context.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+#.-]*")
_STOPWORDS = frozenset("""
a about after all also an and any are as at be because been before being between both but by can did do does
doing done for from further had has have having he her here hers him his how i if in into is it its itself
me more most my no nor not of off on once only or other our ours out over own same she should so some such
than that the their theirs them then there these they this those through to too under until up very was we
were what when where which while who whom why will with you your yours
""".split())


def strip_html(raw: str) -> str:
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


def get_json_path(document: Any, path: str) -> Any:
    """Read ``a.b[0].c`` style paths without a JSONPath dependency."""
    current = document
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("[") and part.endswith("]"):
            current = current[int(part[1:-1])]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


def normalize(raw: str, *, json_path: str | None = None, as_html: bool = False) -> list[str]:
    """Return comparable lines for a document.

    HTML gets tags stripped, JSON is pretty-printed through ``json_path``, then
    everything is whitespace-collapsed and blank lines are dropped.
    """
    text = raw
    if as_html:
        text = strip_html(text)
    if json_path:
        try:
            value = get_json_path(json.loads(raw), json_path)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            value = None
        if value is not None:
            text = json.dumps(value, indent=2, sort_keys=True)
    lines = []
    for line in text.splitlines():
        cleaned = _WS_RE.sub(" ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def digest(lines: list[str]) -> str:
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class Diff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    ratio: float
    tokens_changed: int

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def diff_lines(before: list[str], after: list[str], *, min_ratio: float = 0.0) -> Diff:
    """Line-level diff with a similarity guard against pure reformatting."""
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(before[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(after[j1:j2])
    ratio = matcher.ratio()
    if ratio >= 1.0 - min_ratio:
        return Diff((), (), ratio, 0)
    return Diff(tuple(added), tuple(removed), ratio, changed_tokens(before, after))


def changed_tokens(before: list[str], after: list[str]) -> int:
    """Tokens that differ between the two documents.

    Counting every token in the changed lines makes a one-word edit look large.
    Aligning tokens instead reports what actually changed, which is what the
    noise gate should be judging.
    """
    left = tokenize(" ".join(before))
    right = tokenize(" ".join(after))
    aligner = SequenceMatcher(a=left, b=right, autojunk=False)
    total = 0
    for tag, i1, i2, j1, j2 in aligner.get_opcodes():
        if tag != "equal":
            total += (i2 - i1) + (j2 - j1)
    return total


def fingerprint(target: str, change_added: tuple[str, ...], change_removed: tuple[str, ...]) -> str:
    """Stable identity for a change, used to suppress duplicate alerts."""
    payload = target + "\n" + "\n".join(sorted(change_added)) + "\n--\n" + "\n".join(sorted(change_removed))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def should_alert(change: Diff, *, min_tokens: int, last_alert_at: float | None,
                 cooldown: float, now: float) -> tuple[bool, str]:
    """Cooldown plus noise gate. Returns ``(send, reason)``."""
    if not change.changed:
        return False, "no change"
    if change.tokens_changed < min_tokens:
        return False, f"below noise floor ({change.tokens_changed} < {min_tokens} tokens)"
    if last_alert_at is not None and now - last_alert_at < cooldown:
        return False, "inside cooldown window"
    return True, "change is significant"