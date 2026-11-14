# absorb deploy units

    cp absorb-hourly.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload && systemctl --user enable --now absorb-hourly.timer

Hourly cadence (spec: operator direction 2026-08-22):
1. `absorb run chat-export-markdown` — watermark-safe; ingests only new/changed files
   (changed files re-enter via content-hash identity)
2. `absorb_candidates consume --limit 5 --backend opencode --probe-seconds 30` —
   extracts Specification Candidates from the FIVE MOST RECENT unconsumed
   documents, attaching them to existing harvests. `--probe-seconds 30` makes one
   cheap call first, so a dead route (quota, credentials, host down) exits 2 with a
   `BACKEND UNAVAILABLE` banner in ~30s instead of burning 300s per chunk. A route
   that answers small prompts but stalls on real chunks aborts after two consecutive
   timeouts (`BACKEND STALLED`).

Transient failures (LLM truncation E_TRANSIENT_LLM_TRUNCATED, network) leave documents
unconsumed so the next cycle retries. Permanent failures consume to keep the queue clean.
