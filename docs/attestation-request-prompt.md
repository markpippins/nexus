# Attestation Request — engineer → tester prompt template

The canonical shape of the message an engineer sends when it needs a PR
attested. Copy this into the request; do not improvise the requirements list.

**Why this file exists.** On 2026-11-06 a tester posted a correct attestation at
the right head, citing four verified CI runs, and the merge gate still refused
it (`ATT_CI_COVERAGE`, record `31bf9ad4`): the row did not state the size of the
rollup it had read. The rule is documented — the `attestation-authoring`
procedure card §5 says a v2 row must state `Rollup at <head>: N checks, all
success` — and the tester's own prompt already tells it to load that card. What
was missing was the *request*: the engineer's ad-hoc prompt asked for CI run
ids and said nothing about the card or the rollup, so the tester satisfied the
letter of what it was asked. The cost was a full re-verify and re-attest cycle
(a second harness job and a Decision 24 supersession).

The lesson: the gate reads a record's properties, so the request must name
every property the gate checks.

## Authority

The card is the authority, not this template. Before attesting, the tester
loads it:

```bash
curl -s -X POST http://localhost:3400/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_get_procedure","arguments":{"slug":"attestation-authoring"}}}'
```

If the card and this template disagree, the card wins and this file is wrong.

## What the request must require

1. **Load the card first** (`attestation-authoring`), and use its "current"
   record shape: `recordType: engineering_log` + tag `type:attestation`.
2. **Full tag set**: `type:attestation`, `pr:<N>`, `policy:attestation/v2`,
   `verdict:<token>`, `status:done`, `head:<sha>`.
3. **The rollup line, with the number counted at that head**:
   `Rollup at <head8>: <N> checks, all success`. The tester must derive `N`
   from the live rollup (`gh pr view <N> --json statusCheckRollup`) — not from
   the number of runs it happens to cite, and not from the request. A count
   the engineer supplies is a hint the tester must verify, never a substitute
   for reading the head.
4. **Real CI runs at this head**, cited as `CI run <id>: <name> success`, each
   verified `conclusion == success` with `head_sha` matching the PR head.
5. **Preflight before posting**: `bin/attestation_preflight.py` must print
   `PASS` (it parses exactly what gate 3 parses). Iterate on the draft until it
   does; do not post a row that has only been eyeballed.
6. **Head minted, never hand-typed**: `python3 bin/pgie-evidence.py mint-head --pr <N>`.
7. **Model attribution**: pass `--model "$NEXUS_AGENT_MODEL"` to
   `post-agent-record.py` so the row records which model executed the check.
8. **No merge, no edits, no push.** The tester verifies and attests; merging is
   `bin/merge_pr.py` / the janitor. A declined row with a stated reason is a
   correct outcome, and a failing local check means no row at all.

## Request prompt (copy from here)

```text
Attestation request from engineer to tester for PR #<N>.

Repo markpippins/nexus, PR #<N>, head <sha7>, branch <branch>.
Worktree: /home/codex/dev/nexus-worktrees/<topic>

Change under review: <one paragraph — what changed and why, so the tester can
judge whether the tests cover the behaviour rather than merely exist>.

FIRST load your procedure card — it is the authority on record shape, not this
message:
  curl -s -X POST http://localhost:3400/ -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_get_procedure","arguments":{"slug":"attestation-authoring"}}}'

Verify independently (record exact exit codes; do not edit anything):
1. git -C <worktree> log -1 --format=%H      (must begin <sha7>)
2. <the project's typecheck command>, then <the project's test command>
3. cd /home/codex/dev/nexus && gh pr checks <N>
4. gh pr view <N> --json statusCheckRollup   (count it; confirm every
   conclusion is SUCCESS/NEUTRAL/SKIPPED)

If and only if those pass, write the body and attest:
- Body opens with the exact line `Verdict: approved.`
- Body contains, on its own line, the rollup you counted:
  `Rollup at <sha7>: <N> checks, all success`
- Body states the head sha, the changed files, what you actually ran with exit
  codes, and says what you did NOT run.
- Cite CI as `CI run <id>: <name> success` for runs whose conclusion is success
  and whose head_sha is this head.

Preflight the draft and iterate until it prints PASS:
  cd /home/codex/dev/nexus && python3 bin/attestation_preflight.py \
    --body <body.md> --title "<title>" \
    --tags "type:attestation,pr:<N>,policy:attestation/v2,verdict:approved,status:done,head:<sha7>" \
    --record-type engineering_log --pr <N> --head <sha7> --checks <N>

Post it:
  cd /home/codex/dev && nexus/bin/post-agent-record.py -r tester \
    -t "<title>" -c "$(cat <body.md>)" \
    --tags '["type:attestation","pr:<N>","policy:attestation/v2","verdict:approved","status:done","head:<sha7>"]' \
    --record-type engineering_log --level 2 --model "$NEXUS_AGENT_MODEL"

Report the record id and the rollup line you used.

Do NOT merge, push, or edit any repository file. If a check fails, post nothing
and report the failure.
```

## If the gate refuses the row

Read the code before responding — `ATT_CI_COVERAGE` means the rollup line is
missing or does not match the head, not that the tests are in doubt. Do not
push (a new commit invalidates the attestation). Have the tester re-verify and
post a corrected row, then retire the refused one via Decision 24:

```bash
bash bin/supersede-record.sh supersede --old <refused id> --new <corrected id> \
  --reason "<gate code>: <what the corrected row states>" --role tester
```

Today's worked instance: refused `31bf9ad4` (`ATT_CI_COVERAGE`) → corrected
`952a8493` (stating `Rollup at b9a819bd: 66 checks, all success`) → superseded,
archive `1b907ea5` → gate passed all three conditions and PR #901 merged.

## Related

- procedure card `attestation-authoring` (authority on the row itself)
- procedure card `pr-protocol` §4 (the merge gate and its exceptions)
- `bin/merge_pr.py` (the gate), `bin/attestation_preflight.py` (dry run of it)
- AGENTS.md R8 (no author self-attestation; tester attests with evidence)
