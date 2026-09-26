# Write-queue buffer drainer — scheduled deployment (systemd timer)

> Companion to PR #587 (`bin/drain_write_queue_buffer.py`). The drainer
> closes the `buffered_local` gap — intents the Java producer
> (`WriteQueueProducer.bufferLocally`) appends to
> `$NEXUS_CORE_WRITEQUEUE_DIR/write-queue-buffer.jsonl` while NATS is
> unreachable were durable on disk only and never reconciled. This doc
> turns the manual "run it after an outage" procedure into a schedule:
> a user-level systemd timer fires the drainer every 15 minutes, with
> post-outage catch-up.

## What ships

| File | Role |
|------|------|
| `bin/write-queue-buffer-drain.service` | oneshot service running `bin/drain_write_queue_buffer.py` with the deployment environment |
| `bin/write-queue-buffer-drain.timer` | `OnCalendar=*:0/15`, `Persistent=true`, `RandomizedDelaySec=30` |

Both are **user-level** units (like the in-tree
`python/cascade/write-queue-reconciler.service` precedent): no root
needed, they live under `~/.config/systemd/user/`.

## Why this schedule

- **15 minutes** (`*:0/15`): the buffer only grows while NATS is down;
  a quarter-hour replay bound keeps the reconciliation lag of
  offline-buffered intents small without any steady-state cost — a tick
  with an empty buffer (the overwhelmingly common case) exits 0 in well
  under a second.
- **`Persistent=true`**: if titanium is down across a tick (or the timer
  is stopped), the missed activation is replayed at the next boot/start —
  exactly the post-outage catch-up behavior the manual procedure relied
  on a human to remember.
- **`RandomizedDelaySec=30`**: avoids head-on collision with other
  `:00/:15` scheduled jobs.
- **Tick during a still-down outage**: costs nothing and is loud in the
  journal — the drainer's stream gate (provision check) refuses with
  exit 1 before touching the buffer. No retry storm, no masking.

## Install (titanium, user `codex`)

```bash
# 1. Units into the user unit dir (they are also tracked in-tree at bin/)
mkdir -p ~/.config/systemd/user
cp /home/codex/dev/nexus/bin/write-queue-buffer-drain.service \
   /home/codex/dev/nexus/bin/write-queue-buffer-drain.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload

# 2. Enable + start the TIMER (never enable the service directly; the
#    timer owns its schedule, and `systemctl --user start <service>` is
#    the way to force an immediate drain by hand).
systemctl --user enable --now write-queue-buffer-drain.timer
```

The service `WorkingDirectory` is `/home/codex/dev/nexus` — the same
checkout the units ship in. If the repo moves, update `WorkingDirectory`
and the `ExecStart` path together.

## Verify

```bash
# Schedule visible (next trigger ~:00/:15/:30/:45 ±30s):
systemctl --user list-timers write-queue-buffer-drain.timer

# Last run + result (exit codes surface here):
systemctl --user status write-queue-buffer-drain.service

# Journal of past runs:
journalctl --user -t write-queue-buffer-drain -n 50

# Force an immediate drain (manual override, e.g. right after NATS recovery):
systemctl --user start write-queue-buffer-drain.service

# Pure rehearsal without touching anything:
NATS_URL=nats://192.168.1.82:4222 \
  python3 /home/codex/dev/nexus/bin/drain_write_queue_buffer.py --dry-run
```

## Failure semantics (what the journal will show)

| Exit | Meaning | Timer behavior |
|------|---------|----------------|
| 0 | drained, or nothing to do | next tick as scheduled |
| 1 | stream gate refused (NATS down / stream provisioning failed) | buffer untouched; next tick retries; this is the expected state DURING an outage |
| 2 | publish failure mid-drain | aborted **before any file operation** — buffer byte-identical; next tick replays (at-least-once; the reconciler dedups on `write_id`) |
| 3 | another drain holds the flock | cannot happen on a schedule (single timer) — indicates a manual run racing a tick; harmless |

Every non-zero exit is visible in `systemctl --user status` and the
journal; none are masked as success. Consecutive exit-1s during an
outage are normal and self-resolve when NATS returns.

## Design notes

- **Overlap safety**: the drainer takes an exclusive `flock` on
  `<buffer>.drain.lock`; a manual run racing a tick exits 3 rather than
  interleaving.
- **Producer needs nothing**: `WriteQueueProducer` appends to the path
  fresh on every write; the drainer's rotate model makes that
  race-free with zero producer coordination.
- **Dedup**: replayed lines are reconciler-deduped via the staging
  `write_id` PK — at-least-once is safe by construction.
- **The drainer never touches PG** — hence the service unit carries only
  `NATS_URL` (unlike `write-queue-reconciler.service`).

## Rollback

```bash
systemctl --user disable --now write-queue-buffer-drain.timer
rm ~/.config/systemd/user/write-queue-buffer-drain.{service,timer}
systemctl --user daemon-reload
```

Manual drain remains available at any time (see Verify above).

## Deployment status

Units ship in-tree in PR #587. **They are not installed on titanium by
this PR** — installation is an explicit, separately-approved step
(Step: Install above). Until then, post-outage drains remain manual.
