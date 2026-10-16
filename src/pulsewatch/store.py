"""SQLite storage for check results and alert history.

Everything is written with WAL journaling so a long poll loop never blocks on a
reader (the HTTP status endpoint), and every schema change goes through an
explicit migration list.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 4

_MIGRATIONS: list[str] = [
    # v1: base tables
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target TEXT NOT NULL,
        fetched_at REAL NOT NULL,
        status INTEGER,
        etag TEXT,
        last_modified TEXT,
        digest TEXT,
        token_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_target ON snapshots(target, fetched_at DESC);

    CREATE TABLE IF NOT EXISTS changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target TEXT NOT NULL,
        detected_at REAL NOT NULL,
        added TEXT NOT NULL,
        removed TEXT NOT NULL,
        from_digest TEXT,
        to_digest TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_changes_target ON changes(target, detected_at DESC);

    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target TEXT NOT NULL,
        change_id INTEGER NOT NULL,
        sent_at REAL NOT NULL,
        kind TEXT NOT NULL,
        ok INTEGER NOT NULL,
        detail TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_alerts_target ON alerts(target, sent_at DESC);
    """,
    # v2: notification bookkeeping for cooldown/dedupe rules
    """
    ALTER TABLE snapshots ADD COLUMN latency_ms REAL;
    ALTER TABLE alerts ADD COLUMN fingerprint TEXT;
    CREATE INDEX IF NOT EXISTS idx_alerts_fingerprint ON alerts(fingerprint, sent_at DESC);
    """,
    # v3: keep the fetched body so the next check can diff against it
    """
    ALTER TABLE snapshots ADD COLUMN body TEXT;
    """,
    # v4: record why a check failed, so the status API can explain an outage
    """
    ALTER TABLE snapshots ADD COLUMN error TEXT;
    """,
]


@dataclass(frozen=True)
class Change:
    target: str
    detected_at: float
    added: tuple[str, ...]
    removed: tuple[str, ...]
    from_digest: str | None
    to_digest: str | None
    id: int | None = None


@dataclass(frozen=True)
class CheckResult:
    target: str
    status: int
    digest: str
    token_count: int
    latency_ms: float
    from_cache: bool
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    change: Change | None = None
    body: str = ""


class Store:
    """Thin, explicit data access. No ORM, no magic."""

    def __init__(self, path: str | Path = ":memory:",
                 clock: Callable[[], float] = time.time) -> None:
        self.path = str(path)
        self.clock = clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # ---------------------------------------------------------------- schema
    def migrate(self) -> int:
        current = self._version()
        for index in range(current, len(_MIGRATIONS)):
            self.conn.executescript(_MIGRATIONS[index])
            self.conn.execute("INSERT INTO meta (version) VALUES (?)", (index + 1,))
        return self._version()

    def _has_meta(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        return row is not None

    def _version(self) -> int:
        if not self._has_meta():
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (version INTEGER NOT NULL)")
            return 0
        row = self.conn.execute("SELECT MAX(version) AS v FROM meta").fetchone()
        return int(row["v"] or 0)

    # --------------------------------------------------------------- writes
    def record_check(self, result: CheckResult) -> None:
        self.conn.execute(
            """
            INSERT INTO snapshots (target, fetched_at, status, etag, last_modified,
                                   digest, token_count, latency_ms, body, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.target,
                self.clock(),
                result.status,
                result.etag,
                result.last_modified,
                result.digest,
                result.token_count,
                result.latency_ms,
                result.body,
                result.error,
            ),
        )

    def record_change(self, change: Change) -> Change:
        cur = self.conn.execute(
            """
            INSERT INTO changes (target, detected_at, added, removed, from_digest, to_digest)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                change.target,
                change.detected_at,
                json.dumps(list(change.added)),
                json.dumps(list(change.removed)),
                change.from_digest,
                change.to_digest,
            ),
        )
        return Change(**{**change.__dict__, "id": int(cur.lastrowid)})

    def record_alert(self, target: str, change_id: int, kind: str, ok: bool,
                     fingerprint: str, detail: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO alerts (target, change_id, sent_at, kind, ok, detail, fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (target, change_id, self.clock(), kind, int(ok), detail, fingerprint),
        )

    # ---------------------------------------------------------------- reads
    def latest_snapshot(self, target: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE target = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
            (target,),
        ).fetchone()

    def last_change_at(self, target: str) -> float | None:
        row = self.conn.execute(
            "SELECT detected_at FROM changes WHERE target = ? ORDER BY detected_at DESC LIMIT 1",
            (target,),
        ).fetchone()
        return float(row["detected_at"]) if row else None

    def recent_changes(self, target: str | None = None, limit: int = 20) -> list[Change]:
        sql = "SELECT * FROM changes"
        args: list[Any] = []
        if target:
            sql += " WHERE target = ?"
            args.append(target)
        sql += " ORDER BY detected_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_change(r) for r in self.conn.execute(sql, args)]

    def alert_history(self, target: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        sql = "SELECT * FROM alerts"
        args: list[Any] = []
        if target:
            sql += " WHERE target = ?"
            args.append(target)
        sql += " ORDER BY sent_at DESC LIMIT ?"
        args.append(limit)
        return list(self.conn.execute(sql, args))

    def fingerprint_seen(self, fingerprint: str, within_seconds: float) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE fingerprint = ? AND sent_at >= ? LIMIT 1",
            (fingerprint, self.clock() - within_seconds),
        ).fetchone()
        return row is not None

    def counts(self) -> dict[str, int]:
        return {
            name: int(self.conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"])
            for name in ("snapshots", "changes", "alerts")
        }

    # -------------------------------------------------------------- cleanup
    def purge(self, retention_days: int) -> dict[str, int]:
        cutoff = self.clock() - retention_days * 86400
        removed: dict[str, int] = {}
        for table in ("snapshots", "changes", "alerts"):
            cur = self.conn.execute(f"DELETE FROM {table} WHERE " + _time_col(table) + " < ?", (cutoff,))
            removed[table] = cur.rowcount
        return removed

    def close(self) -> None:
        self.conn.close()

    def _row_to_change(self, row: sqlite3.Row) -> Change:
        return Change(
            id=int(row["id"]),
            target=row["target"],
            detected_at=float(row["detected_at"]),
            added=tuple(json.loads(row["added"])),
            removed=tuple(json.loads(row["removed"])),
            from_digest=row["from_digest"],
            to_digest=row["to_digest"],
        )


def _time_col(table: str) -> str:
    return {"snapshots": "fetched_at", "changes": "detected_at", "alerts": "sent_at"}[table]


def open_store(path: str | Path = ":memory:") -> Store:
    return Store(path)


def iter_targets(targets: Iterable[Any]) -> list[Any]:
    return [t for t in targets if getattr(t, "enabled", True)]