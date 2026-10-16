from __future__ import annotations

import pytest

from pulsewatch.config import Retry, Settings, Target, load_targets
from pulsewatch.fetcher import HttpFetcher, FetchError, jittered_interval
from pulsewatch.monitor import Monitor, summarise

from conftest import FakeClock, FakeFetcher, RecordingAlerter

SEATS_V1 = "<html><body><p>Seats available: 0</p><p>Applications closed</p></body></html>"
SEATS_V2 = "<html><body><p>Seats available: 12</p><p>Applications closed</p></body></html>"


def test_first_check_stores_a_baseline(store, monitor_factory, target):
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1]})
    outcome = monitor_factory(fetcher).check_once(target)
    assert outcome.reason == "baseline stored"
    assert outcome.changed is False
    assert store.latest_snapshot("seats") is not None


def test_identical_content_is_not_modified(store, monitor_factory, target, clock):
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1, SEATS_V1]})
    monitor = monitor_factory(fetcher, clock=clock)
    monitor.check_once(target)
    outcome = monitor.check_once(target)
    assert outcome.reason == "not modified"
    assert outcome.changed is False


def test_changed_content_alerts_once(store, monitor_factory, target, clock):
    alerter = RecordingAlerter()
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1, SEATS_V2, SEATS_V2]})
    monitor = Monitor(store, fetcher, [alerter], monitor_factory(fetcher).settings, clock=clock)
    monitor.check_once(target)
    outcome = monitor.check_once(target)
    assert outcome.changed is True and outcome.alerted is True
    assert len(alerter.messages) == 1
    added, removed = alerter.messages[0][2], alerter.messages[0][3]
    assert "Seats available: 12" in added[0] or any("12" in line for line in added)
    assert any("0" in line for line in removed)


def test_cooldown_suppresses_a_second_alert(store, monitor_factory, target, clock):
    alerter = RecordingAlerter()
    v1 = "<p>seats alpha beta gamma</p>"
    v2 = "<p>seats alpha beta delta</p>"
    v3 = "<p>seats alpha beta epsilon</p>"
    fetcher = FakeFetcher({"https://example.edu/seats": [v1, v2, v3]})
    monitor = Monitor(store, fetcher, [alerter], monitor_factory(fetcher).settings, clock=clock)
    monitor.check_once(target)
    assert monitor.check_once(target).alerted is True
    clock.advance(10)  # still inside the 60s cooldown
    second = monitor.check_once(target)
    assert second.changed is True and second.alerted is False
    assert "cooldown" in second.reason


def test_alert_resumes_after_cooldown_expires(store, monitor_factory, target, clock):
    alerter = RecordingAlerter()
    bodies = ["<p>seats alpha beta gamma</p>", "<p>seats alpha beta delta</p>",
              "<p>seats alpha beta epsilon</p>"]
    fetcher = FakeFetcher({"https://example.edu/seats": bodies})
    monitor = Monitor(store, fetcher, [alerter], monitor_factory(fetcher).settings, clock=clock)
    monitor.check_once(target)
    monitor.check_once(target)
    clock.advance(120)
    third = monitor.check_once(target)
    assert third.alerted is True
    assert len(alerter.messages) == 2


def test_fetch_failure_is_recorded_not_raised(store, monitor_factory, target):
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1]}, fail_times=1)
    outcome = monitor_factory(fetcher).check_once(target)
    assert outcome.error is not None
    assert outcome.reason == "fetch failed"
    snapshot = store.latest_snapshot("seats")
    assert snapshot["status"] == 0 and snapshot["error"]


def test_disabled_target_is_skipped(store, monitor_factory, clock, target):
    from dataclasses import replace
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1]})
    disabled = replace(target, enabled=False)
    outcome = monitor_factory(fetcher, clock=clock).check_once(disabled)
    assert outcome.reason == "target disabled"
    assert fetcher.urls == []


def test_force_checks_a_disabled_target(store, monitor_factory, target, clock):
    from dataclasses import replace
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1, SEATS_V2]})
    disabled = replace(target, enabled=False)
    monitor = monitor_factory(fetcher, clock=clock)
    assert monitor.check_once(disabled).reason == "target disabled"
    monitor.check_once(disabled, force=True)
    assert monitor.check_once(disabled, force=True).changed is True


def test_json_target_compares_only_the_selected_path(store, monitor_factory, clock):
    target = Target(name="api", url="https://api.example/status", json_path="data.seats")
    bodies = ['{"data": {"seats": 0, "noise": "a"}}', '{"data": {"seats": 5, "noise": "a"}}']
    fetcher = FakeFetcher({target.url: bodies})
    monitor = Monitor(store, fetcher, [RecordingAlerter()], monitor_factory(fetcher).settings, clock=clock)
    monitor.check_once(target)
    outcome = monitor.check_once(target)
    assert outcome.changed is True


def test_run_forever_respects_max_cycles(store, monitor_factory, clock, target):
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1]})
    cycles = monitor_factory(fetcher, clock=clock).run_forever([target], max_cycles=3)
    assert cycles == 3


def test_summarise_counts_outcomes(monitor_factory, target, clock):
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1]})
    monitor = monitor_factory(fetcher, clock=clock)
    outcomes = [monitor.check_once(target), monitor.check_once(target)]
    stats = summarise(outcomes)
    assert stats == {"checked": 2, "changed": 0, "alerted": 0, "errors": 0}


def test_purge_removes_old_rows(store, monitor_factory, target, clock):
    fetcher = FakeFetcher({"https://example.edu/seats": [SEATS_V1, SEATS_V2]})
    monitor = Monitor(store, fetcher, [RecordingAlerter()], monitor_factory(fetcher).settings, clock=clock)
    monitor.check_once(target)
    monitor.check_once(target)
    assert store.counts()["snapshots"] == 2
    clock.advance(40 * 86400)
    removed = monitor.purge()
    assert removed["snapshots"] == 2 and store.counts()["snapshots"] == 0