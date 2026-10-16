# PulseWatch

**Change detection for things that matter and change quietly.**

PulseWatch watches pages and JSON endpoints you care about, works out whether the change is
*real*, and tells you once — instead of every time a footer year changes.

Built for the jobs nobody has time for: exam seat availability going from `Available` to
`Allocated`, a scholarship deadline moving, a GPU rental price dropping, a TLS certificate
about to expire.

- **Zero required runtime dependencies** — Python standard library only.
- **Conditional GET** — ETag / `Last-Modified` on every request, so an unchanged page costs one `304`.
- **Noise gates** — token-level diff plus a noise floor, a cooldown window and fingerprint
  de-duplication, so you are not paged for a timestamp.
- **Durable state** — SQLite in WAL mode with versioned migrations and retention.
- **Alert where you already look** — Telegram, a generic webhook, or the console.

---

## Measured behaviour

Every number below came from running the CLI against a local HTTP server and reading the
resulting SQLite database. Reproduce it yourself with the transcript in
[docs/architecture.md](docs/architecture.md).

```
=== run 1 (baseline) ===
[     ok] seat-allotment: baseline stored (5 ms)
checked=1 changed=0 alerted=0 errors=0

=== the page changes: one seat flips to Allocated, one new row appears ===
=== run 2 (real change) ===
[pulsewatch] seat-allotment changed
URL: http://127.0.0.1:8901/index.html
+ 1CS21IS001 Bangalore Allocated
+ 1CS21IS045 Mysuru Available
- 1CS21IS001 Bangalore Available
[  ALERT] seat-allotment: change is significant (change #1) (3 ms)
checked=1 changed=1 alerted=1 errors=0

=== run 3 (nothing changed) ===
[     ok] seat-allotment: not modified (2 ms)
checked=1 changed=0 alerted=0 errors=0
```

| Measure | Value |
| :--- | :--- |
| Tests | **56 passing** (`pytest -q`) |
| Mean check latency, local server | **3.4 ms** |
| Python versions in CI | 3.10, 3.11, 3.12, 3.13 |
| Runtime dependencies | 0 |

---

## Install

```bash
git clone https://github.com/Suprit-U/PulseWatch.git
cd PulseWatch
pip install -e .              # runtime only
pip install -e ".[server]"    # + FastAPI status API
pip install -e ".[dev]"       # + pytest
```

Python 3.10 or newer. Or use Docker:

```bash
cp examples/targets.json targets.json
echo "PULSEWATCH_TELEGRAM_TOKEN=..." > .env     # optional
docker compose up -d
```

---

## Configure

`targets.json` is the only thing you need to edit.

```json
[
  {
    "name": "vtu-exam-seat-availability",
    "url": "https://www.vtu.ac.in/exam-seat-allotment",
    "as_html": true,
    "interval": 300,
    "cooldown": 900,
    "tags": ["education", "exam"]
  },
  {
    "name": "gpu-cloud-pricing",
    "url": "https://api.example-cloud.com/v1/pricing/gpu",
    "json_path": "offers",
    "interval": 600,
    "cooldown": 1800,
    "tags": ["cloud", "pricing"]
  }
]
```

| Field | Meaning |
| :--- | :--- |
| `name` | Identifier in the database and in alerts. Must be unique. |
| `url` | What to fetch. `http` and `https`. |
| `interval` | Seconds between checks, plus up to 15% jitter so many targets do not stampede a host. |
| `as_html` | Strip `<script>`/`<style>`/tags before diffing. |
| `json_path` | Compare only this part of a JSON document, e.g. `data.items.0`. |
| `cooldown` | Minimum seconds between two alerts for the same target. |
| `enabled` | `false` pauses the target without deleting its history. |
| `headers` | Extra request headers, e.g. an auth token. Prefer environment variables in real deployments. |
| `tags` | Free-form labels carried through to the API and metrics. |

---

## Use

```bash
pulsewatch targets --targets targets.json            # validate and list
pulsewatch check   --targets targets.json            # check every target once
pulsewatch check   --targets targets.json --json     # machine-readable
pulsewatch check   --targets targets.json --force    # check even if disabled
pulsewatch run     --targets targets.json            # poll forever
pulsewatch run     --targets targets.json --max-cycles 5
pulsewatch serve   --targets targets.json --port 8080 # status API
```

`check` exits non-zero when a fetch fails, so cron and CI notice.

### Status API

| Endpoint | Returns |
| :--- | :--- |
| `GET /health` | Liveness plus target and database counts. |
| `GET /targets` | Configured targets with their live state. |
| `GET /changes` | Recent diffs, newest first. |
| `GET /alerts` | Alert delivery log, including failures. |
| `GET /metrics` | Prometheus text format. |

### Alerts

```bash
export PULSEWATCH_TELEGRAM_TOKEN=...
export PULSEWATCH_TELEGRAM_CHAT_ID=...
pulsewatch run --targets targets.json --alerts console,telegram
```

`--alerts` takes `console`, `webhook` (via `PULSEWATCH_WEBHOOK_URL`) and `telegram`.
A kind whose configuration is missing is skipped with a warning rather than crashing the loop.

---

## How it decides an alert is worth sending

1. **Nothing retrieved** — the fetch succeeded but produced no comparable content. No alert.
2. **Conditional GET** — a `304` means nothing was re-sent, so nothing is diffed or stored.
3. **Token-level diff** — `diff_lines` reports added and removed lines; `changed_tokens` counts
   only the tokens that actually differ, so a one-word edit is 2 tokens, not the length of the line.
4. **Noise floor** (`--min-tokens`, default 2) — smaller changes are recorded but not sent.
   Raise it for large pages where a copyright year should not wake you.
5. **Cooldown** (`cooldown`, per target) — suppresses repeats inside the window.
6. **Fingerprint** — the same change seen twice inside the window is not re-sent, even
   across restarts, because the fingerprint is derived from the diff itself.

Every check, change and alert attempt is written to SQLite, so "why did it not alert?" is
answerable after the fact instead of being a guess.

---

## Design notes

See [docs/architecture.md](docs/architecture.md) for the storage schema, the migration
policy and the deliberate non-goals.

---

## Limitations

- The noise floor is a token count, not a semantic judgement. A large page where the only
  change is two tokens needs a higher `--min-tokens`.
- JS-rendered pages return their shell. Use the JSON endpoint the page calls, if there is one.
- One process, one SQLite file. It is built for tens of targets, not thousands.
- No distributed locking: run one PulseWatch per database.
- Rate limiting is per target interval plus jitter, not a global budget against a host.

## Licence

MIT — see [LICENSE](LICENSE).