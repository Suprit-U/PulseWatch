"""PulseWatch - a small change-detection monitor with alerting.

Polls a list of URLs, stores what it last saw, and tells you when something a
human would call a change actually changed.
"""

from __future__ import annotations

from .alerts import Alerter, AlerterConfig, ConsoleAlerter, TelegramAlerter, WebhookAlerter, build_alerters
from .config import Retry, Selector, Settings, Target, load_targets
from .diffing import Diff, diff_lines, digest, fingerprint, normalize, should_alert, tokenize
from .fetcher import FetchError, FetchResponse, Fetcher, HttpFetcher, jittered_interval
from .monitor import CheckOutcome, Monitor, summarise
from .store import Change, CheckResult, Store, open_store
from .version import __version__

__all__ = [
    "Alerter", "AlerterConfig", "Change", "CheckOutcome", "CheckResult", "ConsoleAlerter",
    "Diff", "FetchError", "FetchResponse", "Fetcher", "HttpFetcher", "Monitor", "Retry",
    "Selector", "Settings", "Store", "Target", "TelegramAlerter", "WebhookAlerter",
    "build_alerters", "diff_lines", "digest", "fingerprint", "jittered_interval",
    "load_targets", "normalize", "open_store", "should_alert", "summarise", "tokenize",
    "__version__",
]