"""Optional HTTP status API.

Read-only views over the same SQLite file the poller writes, so the endpoint can
be scraped by anything without PulseWatch needing a dashboard. Imported lazily
so the base install keeps zero dependencies.
"""

from __future__ import annotations

from typing import Any

from .config import load_targets
from .monitor import Monitor
from .store import Store, open_store


def build_app(targets_path: str = "targets.json", db: str = "pulsewatch.db"):
    """Return a FastAPI app. Raises ImportError with a useful message if absent."""
    try:
        from fastapi import FastAPI, HTTPException, Query
    except ImportError as error:  # pragma: no cover
        raise ImportError("the HTTP API needs fastapi and uvicorn: pip install 'pulsewatch[server]'") from error

    app = FastAPI(title="PulseWatch", version="0.1.0",
                  description="Status and change history for monitored targets.")

    @app.get("/health")
    def health() -> dict[str, Any]:
        store = open_store(db)
        try:
            counts = store.counts()
        finally:
            store.close()
        return {"status": "ok", "counts": counts}

    @app.get("/targets")
    def targets() -> list[dict[str, Any]]:
        return [{"name": t.name, "url": t.url, "interval": t.interval, "enabled": t.enabled,
                 "cooldown": t.cooldown, "tags": list(t.tags)} for t in load_targets(targets_path)]

    @app.get("/changes")
    def changes(target: str | None = Query(default=None), limit: int = Query(default=20, ge=1, le=200)):
        store = open_store(db)
        try:
            rows = store.recent_changes(target, limit)
        finally:
            store.close()
        return [{"id": c.id, "target": c.target, "detected_at": c.detected_at,
                 "added": list(c.added), "removed": list(c.removed)} for c in rows]

    @app.get("/alerts")
    def alerts(target: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500)):
        store = open_store(db)
        try:
            rows = store.alert_history(target, limit)
        finally:
            store.close()
        return [{"target": r["target"], "kind": r["kind"], "ok": bool(r["ok"]),
                 "sent_at": r["sent_at"], "change_id": r["change_id"]} for r in rows]

    @app.get("/metrics")
    def metrics() -> str:
        """Prometheus text exposition so the monitor can be scraped."""
        store = open_store(db)
        try:
            counts = store.counts()
            rows = store.alert_history(None, 500)
        finally:
            store.close()
        lines = [
            "# HELP pulsewatch_snapshots_total Check results recorded.",
            "# TYPE pulsewatch_snapshots_total counter",
            f"pulsewatch_snapshots_total {counts['snapshots']}",
            "# HELP pulsewatch_changes_total Changes detected.",
            "# TYPE pulsewatch_changes_total counter",
            f"pulsewatch_changes_total {counts['changes']}",
            "# HELP pulsewatch_alerts_total Alerts attempted.",
            "# TYPE pulsewatch_alerts_total counter",
            f"pulsewatch_alerts_total {counts['alerts']}",
        ]
        failures = sum(1 for r in rows if not r["ok"])
        lines += [
            "# HELP pulsewatch_alert_failures_total Alerts that failed to deliver.",
            "# TYPE pulsewatch_alert_failures_total counter",
            f"pulsewatch_alert_failures_total {failures}",
        ]
        return "\n".join(lines) + "\n"

    return app


def serve(targets_path: str = "targets.json", db: str = "pulsewatch.db",
          host: str = "127.0.0.1", port: int = 8080) -> None:  # pragma: no cover
    try:
        import uvicorn
    except ImportError as error:
        raise ImportError("serving needs uvicorn: pip install 'pulsewatch[server]'") from error
    uvicorn.run(build_app(targets_path, db), host=host, port=port)


__all__ = ["build_app", "serve", "Monitor", "Store"]