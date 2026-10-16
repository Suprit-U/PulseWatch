"""Command line interface: ``pulsewatch check|run|serve|targets``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .alerts import AlerterConfig, build_alerters
from .config import Retry, Settings, load_targets
from .fetcher import HttpFetcher
from .monitor import Monitor, summarise
from .store import open_store
from .version import __version__


def _alerter_config(args: argparse.Namespace) -> AlerterConfig:
    kinds = tuple(k.strip() for k in args.alerts.split(",") if k.strip())
    return AlerterConfig(
        kinds=kinds or ("console",),
        telegram_token=os.environ.get("PULSEWATCH_TELEGRAM_TOKEN"),
        telegram_chat_id=os.environ.get("PULSEWATCH_TELEGRAM_CHAT_ID"),
        webhook_url=os.environ.get("PULSEWATCH_WEBHOOK_URL"),
    )


def _build(args: argparse.Namespace) -> tuple[Monitor, list, Settings]:
    targets = load_targets(args.targets)
    settings = Settings(
        retry=Retry(attempts=args.retries),
        jitter=args.jitter,
        timeout=args.timeout,
        retention_days=args.retention_days,
        alert_kinds=_alerter_config(args).kinds,
        min_tokens=args.min_tokens,
    )
    store = open_store(args.db)
    fetcher = HttpFetcher(settings.retry, timeout=settings.timeout, user_agent=settings.user_agent)
    alerters = build_alerters(_alerter_config(args))
    return Monitor(store, fetcher, alerters, settings), targets, settings


def _cmd_targets(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets)
    if args.json:
        print(json.dumps([{"name": t.name, "url": t.url, "interval": t.interval,
                           "enabled": t.enabled, "cooldown": t.cooldown} for t in targets], indent=2))
    else:
        for target in targets:
            state = "enabled" if target.enabled else "disabled"
            print(f"{target.name:<24} {state:<9} every {target.interval:>7.0f}s  {target.url}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    monitor, targets, _ = _build(args)
    outcomes = monitor.check_all(targets, force=args.force)
    if args.json:
        print(json.dumps([o.__dict__ for o in outcomes], indent=2, default=str))
    else:
        for outcome in outcomes:
            flag = "ALERT" if outcome.alerted else ("changed" if outcome.changed else "ok")
            print(f"[{flag:>7}] {outcome.target}: {outcome.reason} ({outcome.latency_ms:.0f} ms)")
    stats = summarise(outcomes)
    if not args.json:
        print(f"checked={stats['checked']} changed={stats['changed']} "
              f"alerted={stats['alerted']} errors={stats['errors']}")
    return 1 if stats["errors"] else 0


def _cmd_run(args: argparse.Namespace) -> int:
    monitor, targets, settings = _build(args)
    print(f"pulsewatch {__version__}: watching {len(targets)} target(s), "
          f"db={args.db}, alerts={','.join(settings.alert_kinds)}", flush=True)
    cycles = monitor.run_forever(targets, max_cycles=args.max_cycles)
    removed = monitor.purge()
    print(f"stopped after {cycles} cycle(s); purged {removed}", flush=True)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        from .server import serve
    except ImportError as error:  # pragma: no cover - exercised only without extras
        print(f"the server needs the 'server' extra: pip install 'pulsewatch[server]' ({error})",
              file=sys.stderr)
        return 2
    serve(args.targets, db=args.db, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pulsewatch",
        description="Poll URLs, diff what changed, and alert on meaningful change.",
    )
    parser.add_argument("--version", action="version", version=f"pulsewatch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--targets", default="targets.json", help="path to the targets JSON file")
        sp.add_argument("--db", default="pulsewatch.db", help="SQLite database path")
        sp.add_argument("--alerts", default="console", help="comma separated: console,webhook,telegram")
        sp.add_argument("--timeout", type=float, default=20.0)
        sp.add_argument("--retries", type=int, default=3)
        sp.add_argument("--jitter", type=float, default=0.15)
        sp.add_argument("--retention-days", type=int, default=30)
        sp.add_argument("--min-tokens", type=int, default=3)

    check = sub.add_parser("check", help="check every target once and exit")
    add_common(check)
    check.add_argument("--force", action="store_true", help="ignore the stored baseline")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=_cmd_check)

    run = sub.add_parser("run", help="poll continuously")
    add_common(run)
    run.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles (smoke runs)")
    run.set_defaults(func=_cmd_run)

    serve = sub.add_parser("serve", help="expose status over HTTP")
    add_common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(func=_cmd_serve)

    targets = sub.add_parser("targets", help="show the configured targets")
    targets.add_argument("--targets", default="targets.json")
    targets.add_argument("--json", action="store_true")
    targets.set_defaults(func=_cmd_targets)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not Path(args.targets).exists():
        print(f"targets file not found: {args.targets}", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())