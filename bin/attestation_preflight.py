#!/usr/bin/env python3
"""attestation_preflight.py — check a draft attestation against gate 3 BEFORE posting.

Why this exists: a tester row cannot be edited after posting, only superseded
(Decision 24), and gate 3 refusals cost a full re-attest cycle. On 2026-11-05
three substantively sound rows were refused in a row for wording reasons the
author could have seen in advance:

  * `f2949cbf` (#872) and `4214d9ca` (#873) — a later sentence mentioning the
    declaration label followed by a non-token outranked the row's own approval
    under the pre-fix parser (ATT_VERDICT_INVALID);
  * `35922581` (#873) — the same shape, quoting the test literal.

The tool imports the gate's own pure functions (`attestation_verdict`,
`attestation_policy`, `is_attestation_record`, `extract_ci_run_ids`,
`extract_check_count`) rather than re-implementing the
parsing rules, so "the preflight said PASS" and "the gate agrees" cannot drift
apart. It performs NO network calls and NO writes: freshness (the row's
createdAt vs the head commit) and the run-id lookups are the gate's own
business, and faking them here would be exactly the self-attestation this gate
exists to replace.

Usage:
    python3 bin/attestation_preflight.py --draft draft.json --pr 873 --head 8b6fc65 [--checks 76]
    python3 bin/attestation_preflight.py --body body.md --title "..." --tags a,b,c --pr 873
    python3 bin/attestation_preflight.py --draft draft.json --json

Exit codes:
    0  PASS or PASS-with-warnings (nothing the gate would refuse offline)
    1  FAIL — the gate would refuse this row on the evidence checked here
    2  usage / unreadable input
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BIN = Path(__file__).resolve().parent

# The gate's verdict vocabulary, imported from the gate itself. If the gate adds
# a token, the preflight inherits it instead of silently disagreeing.
_spec = importlib.util.spec_from_file_location("merge_pr", BIN / "merge_pr.py")
merge_pr = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("merge_pr", merge_pr)
_spec.loader.exec_module(merge_pr)

# The label scan mirrors the gate's own declared-label pattern, so "is there a
# competing declaration?" is answered with the same regex that decides it.
_LABEL_RE = merge_pr._VERDICT_DECLARED_RE

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


def classify_verdict(verdict: Optional[str]) -> Tuple[str, str]:
    """(level, message) for the parsed token, using the gate's own sets."""
    if verdict is None:
        return FAIL, (
            "no declaration found — a policy-v2 row must declare one "
            "(ATT_VERDICT_MISSING). Write `Verdict: approved.` as its own line."
        )
    if verdict in merge_pr._APPROVING_VERDICTS:
        return PASS, f"declares '{verdict}' (approval)"
    if verdict in merge_pr._REJECTING_VERDICTS:
        return FAIL, (
            f"declares '{verdict}' — a rejection is never mergeable "
            "(ATT_VERDICT_REJECTED); address the finding and re-attest"
        )
    if verdict in merge_pr._HOLD_VERDICTS:
        return FAIL, (
            f"declares '{verdict}' — a hold is author action, not a merge "
            "(ATT_VERDICT_HOLD)"
        )
    if verdict in merge_pr._RULE_VERDICTS:
        return WARN, (
            f"declares '{verdict}' — the gate re-proves the vendor rule's four "
            "conditions (ATT_VENDOR_RULE_UNVERIFIED if it cannot)"
        )
    return FAIL, (
        f"declares '{verdict}', which the gate cannot read "
        "(ATT_VERDICT_INVALID). Expected one of: "
        + ", ".join(sorted(merge_pr._APPROVING_VERDICTS | merge_pr._REJECTING_VERDICTS))
    )


def competing_declarations(text: str) -> List[Tuple[str, str]]:
    """Every label hit in the body as (token, surrounding snippet).

    The parser takes the last RECOGNIZED one; a body with more than one
    declaration still merges, but the reader cannot tell which the tester meant,
    and under any future parser change the last hit is what counts. Worth a
    warning rather than a refusal.
    """
    out: List[Tuple[str, str]] = []
    for m in _LABEL_RE.finditer(text):
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 40)
        out.append((merge_pr._normalize_verdict(m.group(1)), text[start:end].replace("\n", " ")))
    return out


def build_report(
    row: Dict[str, Any],
    pr_number: Optional[int] = None,
    head: Optional[str] = None,
    checks: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate a draft row offline. Pure: no I/O, no clock, no network."""
    title = str(row.get("title") or "")
    content = str(row.get("content") or "")
    tags = [str(t) for t in (row.get("tags") or [])]
    text = f"{title}\n{content}"

    findings: List[Dict[str, str]] = []

    def add(level: str, check: str, message: str) -> None:
        findings.append({"level": level, "check": check, "message": message})

    # 1. Shape — the gate filters candidates on this before anything else.
    probe = dict(row)
    probe["tags"] = tags
    if merge_pr.is_attestation_record(probe):
        add(PASS, "shape", f"recordType={row.get('recordType')!r} with the attestation marker")
    else:
        add(FAIL, "shape", (
            "not an attestation record the gate will look at (ATT_SHAPE_UNSEEN): "
            "needs recordType engineering_log + tag type:attestation, or "
            "recordType assessment + tags type:approval,status:done"
        ))

    # 2. Contract.
    policy = merge_pr.attestation_policy(probe)
    if policy is None:
        add(WARN, "policy", (
            "no `policy:` line — the gate reads this as a legacy row and will not "
            "require a declaration. Declare `policy: attestation/v2`."
        ))
    elif policy not in merge_pr._SUPPORTED_POLICIES:
        add(FAIL, "policy", (
            f"declares '{policy}', which this gate cannot interpret "
            f"(ATT_POLICY_UNSUPPORTED); supported: "
            + ", ".join(sorted(merge_pr._SUPPORTED_POLICIES))
        ))
    else:
        add(PASS, "policy", f"declares {policy}")

    # 3. The declaration itself, read exactly as the gate reads it. A legacy row
    # (no `policy:`) is read under the pre-v2 contract, where an absent or
    # unreadable declaration is not a refusal — so those downgrade to warnings
    # rather than telling an author their good legacy row would be refused.
    verdict = merge_pr.attestation_verdict(probe)
    level, message = classify_verdict(verdict)
    if policy is None and level == FAIL:
        level = WARN
        message += " Legacy row (no `policy:` line): the gate reads it under the "
        message += "pre-v2 contract and will not refuse on this alone. Declaring "
        message += "`policy: attestation/v2` is still what the convention asks for."
    add(level, "declaration", message)

    # 4. Competing declarations — the trap that cost three rows on 2026-11-05.
    hits = competing_declarations(text)
    if len(hits) > 1:
        picked = verdict
        detail = "; ".join(f"'{tok}' … {snip.strip()[:70]}" for tok, snip in hits[:4])
        add(WARN, "label-in-prose", (
            f"{len(hits)} label hits in the body; the gate takes the last "
            f"RECOGNIZED one (currently '{picked}'). Hits: {detail}. Keep one "
            "declaration and call the rest *terminal state* / *decision*."
        ))
    elif len(hits) == 1:
        add(PASS, "label-in-prose", "exactly one declaration label in title+body")
    elif verdict is not None:
        # attestation_verdict falls back to the `verdict:` tag (and to a spaced
        # "approved by" form) when the body declares nothing. Reading the row
        # through the gate means inheriting that path — but an approval that
        # exists only in a tag is invisible to a human reading the record, so
        # say so instead of reporting a clean pass.
        add(WARN, "declaration-source", (
            "no `Verdict:` line in the body — the gate read this token from a tag "
            "(its own fallback). That is accepted, but a reader of the record "
            "cannot see the declaration; state it in the body."
        ))

    # 5. CI citation — the gate reads EVEN ONE missing-citation row as absent.
    run_ids = merge_pr.extract_ci_run_ids(text)
    if run_ids:
        add(PASS, "ci-citation", f"cites run id(s) {', '.join(run_ids)}")
    else:
        add(FAIL, "ci-citation", (
            "no `CI run <id>` line — tags are invisible to the gate parser, so "
            "the citation must be in the body (ATT_NO_CI_EVIDENCE). Cite the run "
            "you actually read: `CI run <id>`."
        ))

    # 6. Rollup figure — v2 requires the number the row claims to have read.
    if policy in merge_pr._SUPPORTED_POLICIES:
        stated = merge_pr.extract_check_count(text)
        if stated is None:
            add(FAIL, "rollup", (
                "policy v2 requires the rollup figure in the body "
                "(ATT_CI_COVERAGE), e.g. `Rollup at <head>: 76 checks, all success`"
            ))
        elif checks is not None and stated != checks:
            add(FAIL, "rollup", (
                f"states {stated} checks but the head has {checks} "
                "(ATT_CI_COVERAGE as soon as the gate compares them)"
            ))
        else:
            add(PASS, "rollup", f"states {stated} checks"
                + ("" if checks is not None else " (not compared — pass --checks)"))

    # 7. Head binding — a `head:` tag that names a different commit is refused.
    head_tag = None
    for t in tags:
        if t.lower().startswith("head:"):
            head_tag = t.split(":", 1)[1].strip().lower()
            break
    if head_tag and head is not None:
        if not head.strip().lower().startswith(head_tag) and not head_tag.startswith(
            head.strip().lower()[: len(head_tag)]
        ):
            add(FAIL, "head-binding", (
                f"tag head:{head_tag} does not match the PR head {head[:9]} "
                "(ATT_STALE_HEAD once the gate binds them)"
            ))
        else:
            add(PASS, "head-binding", f"head:{head_tag} matches the PR head")
    elif head is not None:
        add(WARN, "head-binding", (
            "no `head:<sha>` tag — fine, but the binding is one more witness that "
            "the row was written for this head"
        ))

    # 8. PR scope — a row that never mentions the PR number cannot be matched.
    if pr_number is not None:
        blob = f"{text} {' '.join(tags)}"
        if re.search(rf"(?<![\d]){pr_number}(?![\d])", blob):
            add(PASS, "pr-reference", f"mentions #{pr_number}")
        else:
            add(FAIL, "pr-reference", (
                f"never mentions the PR number {pr_number} — nor a `pr:{pr_number}` "
                "tag; the gate matches rows to PRs by that number (ATT_SHAPE_UNSEEN)"
            ))

    worst = FAIL if any(f["level"] == FAIL for f in findings) else (
        WARN if any(f["level"] == WARN for f in findings) else PASS
    )
    return {
        "result": worst,
        "verdict": verdict,
        "policy": policy,
        "findings": findings,
    }


def render(report: Dict[str, Any]) -> str:
    lines = [f"attestation preflight: {report['result']}"]
    for f in report["findings"]:
        lines.append(f"  [{f['level']:4}] {f['check']:15} {f['message']}")
    if report["result"] == FAIL:
        lines.append(
            "\nRefused checks above are what gate 3 would refuse offline. Fix the "
            "draft, or supersede an already-posted row (Decision 24) and re-attest."
        )
    return "\n".join(lines)


def _load_row(args: argparse.Namespace) -> Dict[str, Any]:
    if args.draft:
        return json.loads(Path(args.draft).read_text(encoding="utf-8"))
    body = Path(args.body).read_text(encoding="utf-8") if args.body else sys.stdin.read()
    return {
        "recordType": args.record_type,
        "title": args.title or "",
        "content": body,
        "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check a draft attestation against gate 3's parser before posting.")
    ap.add_argument("--draft", help="JSON file with recordType/title/content/tags")
    ap.add_argument("--body", help="markdown body file (default: stdin)")
    ap.add_argument("--title", default="", help="row title (with --body)")
    ap.add_argument("--tags", default="", help="comma-separated tags (with --body)")
    ap.add_argument("--record-type", default="engineering_log")
    ap.add_argument("--pr", type=int, help="PR number the row is for")
    ap.add_argument("--head", help="PR head sha (enables the head-binding check)")
    ap.add_argument("--checks", type=int, help="real check-run count at the head")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        row = _load_row(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"attestation-preflight: cannot read the draft: {exc}", file=sys.stderr)
        return 2

    report = build_report(row, pr_number=args.pr, head=args.head, checks=args.checks)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0 if report["result"] in (PASS, WARN) else 1


if __name__ == "__main__":
    sys.exit(main())
