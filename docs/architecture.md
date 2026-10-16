# PulseWatch architecture

## The problem

Almost every "track this page for me" tool is a diff tool. It tells you the bytes changed.
That is not the question. The question is *did anything I care about change*, asked often
enough to be useful and rarely enough to stay worth paying attention to.

PulseWatch is the boring machinery around that question: fetch cheaply, diff honestly,
decide whether the difference is signal, remember enough to answer "why didn't it alert?",
and deliver the answer to somewhere the human already looks.

## Pipeline

```
targets.json
    |
    v
  Scheduler          jittered interval per target, honours enabled=false, max_cycles for tests
    |
    v
  HttpFetcher        conditional GET (ETag / Last-Modified), retry with exponential backoff,
    |               no retry on 4xx except 429, records elapsed_ms
    v
  normalise          HTML -> stripped text, or JSON -> json_path -> pretty-printed, then
    |               whitespace collapsed and blank lines dropped
    v
  digest             sha256 of the normalised lines (first 16 hex chars)
    |
    |  digest unchanged -> store the check, return "not modified"
    v
  diff_lines         SequenceMatcher over lines -> added / removed / ratio
    |
    v
  changed_tokens     token-level SequenceMatcher -> how much actually changed
    |
    v
  should_alert       noise floor, cooldown window, fingerprint de-duplication
    |
    v
  Alerters           console / webhook / telegram  (stdlib urllib, each returns bool)
    |
    v
  Store              SQLite WAL: snapshots, changes, alerts
```

Every arrow writes something to `Store`. If a page changed and no alert went out, the reason
is in the database, not in a log buffer that rotated away overnight.

## Why these choices

**Conditional GET before anything else.** A `304` costs a few hundred bytes and no parsing.
Polling a page every five minutes should mostly return `304`, so `etag` and `last_modified`
from the previous check are always sent back.

**Normalise before comparing.** Raw HTML changes on every render — injected timestamps,
CSRF tokens, reordered class attributes. Stripping tags and collapsing whitespace turns a
noisy byte comparison into a comparison of what the page says.

**Token-level diff, not line length.** The first version counted every token in a changed
line, so editing one word in a long sentence looked like a large change. `changed_tokens`
compares token sequences, so the count reflects the edit.

**Fingerprint on the diff, not the target.** Deduplicating by target would drop a second,
genuinely different change that happened to arrive inside the cooldown. Hashing the added
and removed lines means the same change is suppressed and a different one is not, even
across restarts.

**SQLite in WAL mode.** One file, no server, concurrent readers while a check writes, and
`retention_days` cleanup that is a single `DELETE` with an index. This is the right size of
tool for tens of targets.

## Storage schema

| Table | Purpose | Notable columns |
| :--- | :--- | :--- |
| `meta` | migration bookkeeping | `version` (currently 4) |
| `snapshots` | one row per check | `digest`, `etag`, `last_modified`, `token_count`, `latency_ms`, `body`, `error` |
| `changes` | one row per detected change | `added`, `removed`, `ratio`, `tokens_changed`, `fingerprint` |
| `alerts` | one row per delivery attempt | `kind`, `ok`, `detail`, `fingerprint` |

`snapshots.body` holds the last normalised body per target so the next check has a baseline
to diff against without a second fetch.

## Migrations

Migrations are a numbered list in `store.py`. `migrate()` creates `meta` if it is missing,
applies every migration above the recorded version, and updates the version.

Rules that are not negotiable:

- **Never edit a shipped migration.** Add a new one.
- Each step must be safe to run on an existing database, and safe to re-run.
- `PRAGMA user_version` is not used; `meta.version` is the single source of truth, because
  WAL mode and `user_version` interact badly across process restarts in older SQLite builds.

Version history: `v1` core tables, `v2` latency + alert fingerprint, `v3` snapshot body,
`v4` snapshot error.

## Reproducing the README transcript

```bash
mkdir -p /tmp/pw-demo && cd /tmp/pw-demo
cat > index.html <<'HTML'
<html><head><title>Seat Allotment</title></head><body>
<h1>VTU Exam Seat Allotment</h1>
<table><tr><th>USN</th><th>Centre</th><th>Status</th></tr>
<tr><td>1CS21IS001</td><td>Bangalore</td><td>Available</td></tr>
</table></body></html>
HTML

python -m http.server 8901 --bind 127.0.0.1 --directory /tmp/pw-demo &

cat > /tmp/targets.json <<'JSON'
[{"name": "seat-allotment", "url": "http://127.0.0.1:8901/index.html",
  "as_html": true, "interval": 2, "cooldown": 0, "tags": ["demo"]}]
JSON

pip install -e .
pulsewatch check --targets /tmp/targets.json --db /tmp/pw.db --alerts console   # baseline
# edit index.html: flip one row to Allocated, add another row
pulsewatch check --targets /tmp/targets.json --db /tmp/pw.db --alerts console   # alert
pulsewatch check --targets /tmp/targets.json --db /tmp/pw.db --alerts console   # not modified

sqlite3 /tmp/pw.db 'select target, added, removed from changes;'
```

## Non-goals

- **Not a scraping framework.** No CSS selectors, no headless browser, no rendering. If a page
  needs JavaScript, watch the API it calls.
- **Not an alerting platform.** No on-call rotation, no escalation, no deduplication across
  services. Send to the webhook of the system that does that.
- **Not multi-tenant.** One config file, one database, one process.
- **Not a scheduler.** No cron expression parser; intervals are seconds.