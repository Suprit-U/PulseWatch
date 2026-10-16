# Contributing to PulseWatch

Thanks for looking at PulseWatch. Issues and pull requests are welcome.

## Getting set up

```bash
git clone https://github.com/Suprit-U/PulseWatch.git
cd PulseWatch
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the checks

```bash
make test    # pytest
make eval    # none yet; see README
```

Everything must pass before a pull request is merged:

- `pytest -q` — no new failures, no skipped tests without a reason.
- New behaviour needs a test that fails without your change.
- Public functions get type hints and a docstring explaining the non-obvious part.

## Reporting a bug

Include the target configuration (redact tokens), what you expected, what happened,
and the relevant log lines. A failing `pulsewatch check --json` output is ideal.

## Design rules

- **Zero required runtime dependencies.** Anything else goes behind the `server` extra.
- **A check must be safe to run twice.** Idempotency matters more than raw speed.
- **Never send an alert you cannot justify.** The noise floor and cooldown exist for a reason;
  loosening them is a behaviour change and needs a reason in the pull request.
- **Storage changes need a migration.** Add a new entry to `_MIGRATIONS`; never edit an existing one.

## Security

Do not commit tokens or webhook URLs. Use environment variables
(`PULSEWATCH_TELEGRAM_TOKEN`, `PULSEWATCH_TELEGRAM_CHAT_ID`, `PULSEWATCH_WEBHOOK_URL`).
See `SECURITY.md` for reporting a vulnerability.
