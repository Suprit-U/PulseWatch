"""The check loop: fetch one target, diff it against the stored body, decide.

Synchronous and injectable on purpose. Tests drive it with a fake fetcher and a
clock they control, so nothing in the suite touches the network or sleeps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .alerts import Alerter
from .config import Settings, Target
from .diffing import diff_lines, digest, fingerprint, normalize, should_alert
from .fetcher import Fetcher, FetchError, jittered_interval
from .store import Change, CheckResult, Store


@dataclass
class CheckOutcome:
    target: str
    changed: bool = False
    alerted: bool = False
    reason: str = ""
    ratio: float = 0.0
    latency_ms: float = 0.0
    change_id: int | None = None
    error: str | None = None


class Monitor:
    def __init__(self, store: Store, fetcher: Fetcher, alerters: list[Alerter],
                 settings: Settings | None = None, *, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.fetcher = fetcher
        self.alerters = alerters
        self.settings = settings or Settings()
        self.clock = clock
        self.sleeper = sleeper
        # One clock for the whole run, otherwise retention and cooldown windows
        # would be measured against a different clock than the scheduler.
        store.clock = clock

    # ------------------------------------------------------------- internals
    def _last_alert_at(self, target: str) -> float | None:
        rows = self.store.alert_history(target, limit=1)
        return float(rows[0]["sent_at"]) if rows else None

    def _persist(self, target: Target, *, status: int, body: str, digest_value: str,
                 token_count: int, latency_ms: float, etag: str | None,
                 last_modified: str | None, error: str | None = None) -> None:
        self.store.record_check(CheckResult(
            target=target.name, status=status, digest=digest_value, token_count=token_count,
            latency_ms=latency_ms, from_cache=status == 304, etag=etag,
            last_modified=last_modified, error=error, body=body,
        ))

    # ----------------------------------------------------------------- checks
    def check_once(self, target: Target, *, force: bool = False) -> CheckOutcome:
        """Check one target. Network problems come back as ``error``, never raise.

        ``force`` checks a target even when it is marked disabled; it still diffs
        against the stored body.
        """
        if not target.enabled and not force:
            return CheckOutcome(target.name, reason="target disabled")

        snapshot = self.store.latest_snapshot(target.name)
        etag = snapshot["etag"] if snapshot else None
        last_modified = snapshot["last_modified"] if snapshot else None

        started = self.clock()
        try:
            response = self.fetcher.fetch(target.url, etag=etag, last_modified=last_modified,
                                          headers=dict(target.headers))
        except FetchError as error:
            latency_ms = (self.clock() - started) * 1000
            self._persist(target, status=0, body="", digest_value="", token_count=0,
                          latency_ms=latency_ms, etag=None, last_modified=None, error=str(error))
            return CheckOutcome(target.name, reason="fetch failed", latency_ms=latency_ms,
                                error=str(error))

        latency_ms = response.elapsed_ms or (self.clock() - started) * 1000
        lines = normalize(response.body, json_path=target.json_path, as_html=target.as_html)
        new_digest = digest(lines)
        token_count = sum(1 for line in lines for _ in line.split())

        if snapshot is None:
            self._persist(target, status=response.status, body=response.body, digest_value=new_digest,
                          token_count=token_count, latency_ms=latency_ms, etag=response.etag,
                          last_modified=response.last_modified)
            return CheckOutcome(target.name, reason="baseline stored", latency_ms=latency_ms)

        self._persist(target, status=response.status, body=response.body, digest_value=new_digest,
                      token_count=token_count, latency_ms=latency_ms, etag=response.etag,
                      last_modified=response.last_modified)

        if response.from_cache or new_digest == snapshot["digest"]:
            return CheckOutcome(target.name, reason="not modified", latency_ms=latency_ms)

        before_lines = normalize(snapshot["body"] or "", json_path=target.json_path,
                                 as_html=target.as_html)
        diff = diff_lines(before_lines, lines)
        if not diff.changed:
            return CheckOutcome(target.name, reason="whitespace-only change", latency_ms=latency_ms)

        change = self.store.record_change(Change(
            target=target.name, detected_at=self.clock(), added=diff.added, removed=diff.removed,
            from_digest=snapshot["digest"], to_digest=new_digest,
        ))

        mark = fingerprint(target.name, diff.added, diff.removed)
        send, reason = should_alert(diff, min_tokens=self.settings.min_tokens,
                                    last_alert_at=self._last_alert_at(target.name),
                                    cooldown=target.cooldown, now=self.clock())
        if send and self.store.fingerprint_seen(mark, target.cooldown):
            send, reason = False, "duplicate of an already alerted change"

        if send:
            for alerter in self.alerters:
                ok = alerter.send(target.name, target.url, diff)
                self.store.record_alert(target.name, change.id, alerter.name, ok, mark)

        return CheckOutcome(target.name, changed=True, alerted=send,
                            reason=f"{reason} (change #{change.id})", ratio=diff.ratio,
                            latency_ms=latency_ms, change_id=change.id)

    def check_all(self, targets: list[Target], *, force: bool = False) -> list[CheckOutcome]:
        return [self.check_once(target, force=force) for target in targets]

    def run_forever(self, targets: list[Target], *, max_cycles: int | None = None,
                    max_seconds: float | None = None) -> int:
        """Poll loop. ``max_cycles``/``max_seconds`` bound smoke runs and tests."""
        started = self.clock()
        cycle = 0
        next_due = {t.name: self.clock() for t in targets}
        while True:
            if max_cycles is not None and cycle >= max_cycles:
                break
            if max_seconds is not None and self.clock() - started >= max_seconds:
                break
            for target in targets:
                if self.clock() < next_due.get(target.name, 0.0):
                    continue
                self.check_once(target)
                next_due[target.name] = self.clock() + jittered_interval(
                    target.interval, self.settings.jitter)
            cycle += 1
            if max_cycles is None:
                self.sleeper(1.0)
        return cycle

    def purge(self) -> dict[str, int]:
        return self.store.purge(self.settings.retention_days)


def summarise(outcomes: list[CheckOutcome]) -> dict[str, Any]:
    return {
        "checked": len(outcomes),
        "changed": sum(1 for o in outcomes if o.changed),
        "alerted": sum(1 for o in outcomes if o.alerted),
        "errors": sum(1 for o in outcomes if o.error),
    }