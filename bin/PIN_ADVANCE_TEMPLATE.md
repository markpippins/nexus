# Pin-Advance PR Template — landed-tool-pins.json

Use this when a pinned tool's bytes **legitimately changed** on main (an
attested PR landed that intentionally modified the file) and the pin must
advance to the new landed digest. Copy the PR-body skeleton at the bottom.

**Never** use this flow to explain away unexplained drift: if the guard
posts DRIFT and no PR that touched the file landed, that is a parity
incident — investigate the squash merge first (see guard DRIFT wording).

## When a pin needs advancing

- Guard verdict flips to `QUEUED` ("landed digest == queued PR #N digest;
  pin advance pending") — the expected post-landing state.
- Or `bin/digest_drift_report.py` posts a DRIFT/MISSING incident and the
  diff is a reviewed, attested change that landed by PR.

## Pre-flight checklist (do these in order)

1. **Identify the landing.** `git log origin/main --oneline -5 -- <path>`
   — note the PR number and squash SHA. If no PR landed that touched the
   path, STOP: this is drift, not a pin advance.
2. **Capture the digest from the blob, never the working tree:**
   ```bash
   git -C /home/codex/dev/nexus cat-file -e "origin/main:<path>" && \
     git -C /home/codex/dev/nexus show "origin/main:<path>" | sha256sum
   ```
   The `cat-file -e` existence check is mandatory (epoch-0 lesson: an
   inverted existence check mislabeled a landed tool).
3. **Re-run the tool's own suite from main** (not from a worktree) and
   record the count in the PR body, e.g. `python3 bin/tests/<suite>.py`.
4. **Diff the change deliberately:** `git diff <old-pinned-landing>..<new-landing> -- <path>`
   — the PR body must summarize WHY the bytes changed (PR # + intent).
5. **Update the registry entry:**
   - `digest`: the new blob sha256 (full 64 hex, lowercase).
   - `provenance`: `PIN ADVANCE <date>: PR #N landed (squash <sha>) at its
     queued digest — <one-line verification evidence>`. Keep the old
     digest's short prefix in the line for lineage.
   - `queued_prs`: remove the landed entry (queue must end empty).
6. **Update the repo-registry test** (`bin/tests/test_landed_digest_guard.py`,
   `test_repo_registry_wellformed_and_pinned_digests_stable`) with the new
   digest so the test pins what the registry claims.
7. **Run both suites hermetically:**
   ```bash
   python3 bin/tests/test_landed_digest_guard.py && \
   python3 -m pytest bin/tests/test_landed_digest_guard.py bin/tests/test_digest_drift_report.py -q
   ```
8. **Verify the guard reads ALL_MATCH on the post-squash main:**
   ```bash
   python3 bin/landed_digest_guard.py --repo /home/codex/dev/nexus --rev origin/main
   ```

## PR requirements

- Tests for the registry/guard change included; suites green in both
  runners. Tester attestation required before merge (cite `CI run <id>`
  lines in the BODY — tags are invisible to the gate parser).
- While the pin-advance PR is OPEN, register it in the affected pin's
  `queued_prs` (pr/head/digest/provenance) so the guard live-verifies the
  queued head every tick — a force-push after capture flips QUEUED→DRIFT.

## PR-body skeleton

```markdown
## Pin advance: <path>

**Landing:** PR #<N> (squash <sha>) intentionally modified this tool.
**Why the bytes changed:** <one paragraph>.
**Old pin:** <first8>… (provenance: <old line>)
**New pin:** <full digest>, captured via `git show origin/main:<path> | sha256sum`
at rev `origin/main` <rev7> (existence verified first).

## Verification
- <tool>'s suite from main: <n/n> passed.
- Guard: `landed_digest_guard.py --repo … --rev origin/main` → ALL_MATCH
  exit 0 at the post-advance registry.
- Both suites hermetic green (direct + pytest).

## Registry diff
<the one-pin diff, old → new digest + provenance + queue emptying>
```

## Tier-2 tooling captured 2026-10-01 (this template's provenance)

Tier-2 pins added in the same PR as this template: `record_hygiene_sweep.py`,
`post-agent-record.py`, `post-change-log.sh`, `pgie-evidence.py` — each
captured from an `origin/main ead7ff59a` blob after a positive existence
check. Excluded from tier-2 (churn or scope): `check-inbox.sh` and
`run_bin_tests.py` were considered and left out — `check-inbox.sh` has
absorbed repeated behavioral fixes (#661, #638, #76) and `run_bin_tests.py`
is a test-discovery guard whose user is this repo's own CI, not the
attestation chain; revisit if their drift profile changes.
