#!/usr/bin/env python3
"""attestation_residue_sweep.py — retire duplicate live tester attestations.

Motivation: the tester harness files a new attestation row on every retry.
Only the NEWEST row for a given (pr, head) is ever the merge gate's answer
(bin/merge_pr.py select_attestation + duplicate_attestations), so the older
rows are residue: they stay status:done forever, and any live-attestation
count over the corpus is inflated by them. PR #1046 closed the creation side
(write-time idempotency in nebula-srv) and made the gate retire duplicates
on every accepted run. This tool is the repair side: it finds the residue
that predates those two changes and retires it under the correct surviving
successor row.

## The rule (one rule, borrowed — never invented here)

A group is a set of live tester attestation rows sharing a PR number
(`pr:<N>` / `gate:<N>` tag) and a head binding (`head:<sha>` tag, 7-40 hex,
matched by prefix either direction — the gate's Decision 10/15 rule). The
survivor is the NEWEST row in the group (createdAt, then id, for
determinism); every other row is retired under it. This is exactly what
bin/merge_pr.py does on a gate pass (duplicate_attestations + the #1046
bookkeeping) and exactly what nebula-srv's POST /api/agent-records does at
write time, so the corpus has ONE retirement dialect.

Retirement is Decision 24 Amendment 1 (architect record cfcc9d65): every
`status:*` tag drops, `status:retired` is added, the title is prefixed
`[RETIRED <date>]` once, `metadata.retired` records the successor and the
reason, and the BODY IS BYTE-IDENTICAL. Nothing else is touched. The patch
shape is parity-tested against the canonical implementation in
typescript/nebula-srv/src/attestation-idempotency.ts (retirementPatch); see
bin/tests/test_attestation_residue_sweep.py.

## What it refuses to touch (the item-4 guard)

A row is only ever a candidate if it carries BOTH a PR tag AND a well-formed
`head:` tag. Head-less legacy attestations are UNKEYABLE: there is no head
binding to prove two rows answer the same commit, and the corpus contains
pairs that legitimately share several PR numbers (c79223b8 / 8e65c047 are
tagged pr:679 AND pr:681 — one is a partial, one its completion, and they
are not duplicates of anything). Collapsing them would destroy records, so
the sweep counts them, reports them as protected, and never selects them —
neither as survivor nor as stray. Also outside scope: withdrawn rows
(status:retired/status:superseded — already terminal), non-tester rows, and
rows that are not attestation-shaped (recordType=assessment + type:approval
+ status:done, or the legacy type:attestation marker). A status:refused row
IS a stray: it is attestation-shaped and live, and it is not the answer.

## Modes and safety

  attestation_residue_sweep.py            # DRY RUN: report only, zero writes
  attestation_residue_sweep.py --apply    # retire the planned strays
  attestation_residue_sweep.py --json     # machine-readable plan/result

--apply is bounded (--max-retire, default 25) and verifies every write:
read the point record, hash the body, PATCH, read back, and check that
(1) the body hash is unchanged, (2) status:retired is present, (3) the tag set
is identical except for status:*, (4) the successor is still live. A failed
verification is reported, never claimed as done. Re-running --apply after a
successful pass plans zero rows (idempotent), and the dry run then reports
the same group set it retired, as retired.

Every --apply that retires at least one row posts a change-log entry
(Assembly :3107, canonical wrapper bin/post-change-log.sh) naming every
retired row and its successor — the audit trail lives outside the records.

Exit codes: 0 ok (dry run, or apply fully verified); 1 one or more writes
failed verification; 2 hard error (nebula unreachable — nothing written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

BIN_DIR = Path(__file__).resolve().parent
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_POST_CHANGELOG = BIN_DIR / "post-change-log.sh"

PAGE_LIMIT = 500
MAX_PAGES = 40                      # 20k records — a hard stop, not an expectation
DEFAULT_MAX_RETIRE = 25
TESTER_ROLE = "tester"

WITHDRAWN_STATUS = frozenset({"status:retired", "status:superseded"})
HEAD_RE = re.compile(r"^[0-9a-f]{7,40}$")
PR_TAG_RE = re.compile(r"^(?:pr|gate):(\d+)$")
RETIRED_TITLE_PREFIX = "[RETIRED "
CONVENTION = "Decision 24 (cfcc9d65) Amendment 1"
PERFORMED_BY = "attestation-residue-sweep"


# ── row predicates (mirror bin/merge_pr.py + the TS write path) ───────────

def head_tag(tags: Optional[Iterable[Any]]) -> Optional[str]:
    """The row's head:<sha> value, lowercased; None when absent/blank."""
    for tag in tags or []:
        s = str(tag or "")
        if s.lower().startswith("head:"):
            return s.split(":", 1)[1].strip().lower() or None
    return None


def pr_number(tags: Optional[Iterable[Any]]) -> Optional[int]:
    """The PR number from a pr:<N> / gate:<N> tag, else None."""
    for tag in tags or []:
        m = PR_TAG_RE.match(str(tag or "").strip())
        if m:
            return int(m.group(1))
    return None


def is_withdrawn(row: Dict[str, Any]) -> bool:
    tags = {str(t or "").lower() for t in (row.get("tags") or [])}
    return bool(tags & WITHDRAWN_STATUS)


def is_attestation_shaped(row: Dict[str, Any]) -> bool:
    """merge_pr.is_attestation_record(): canonical or legacy marker."""
    tags = {str(t or "").lower() for t in (row.get("tags") or [])}
    if "type:attestation" in tags:
        return True
    return (
        str(row.get("recordType") or "").lower() == "assessment"
        and "type:approval" in tags
        and "status:done" in tags
    )


def is_live_attestation(row: Dict[str, Any]) -> bool:
    return (
        str(row.get("role") or "").lower() == TESTER_ROLE
        and is_attestation_shaped(row)
        and not is_withdrawn(row)
    )


def heads_match(a: Optional[str], b: Optional[str]) -> bool:
    """Prefix comparison either direction, 7-40 hex on both sides."""
    x = str(a or "").strip().lower()
    y = str(b or "").strip().lower()
    if not HEAD_RE.match(x) or not HEAD_RE.match(y):
        return False
    return x.startswith(y) or y.startswith(x)


def keyable(row: Dict[str, Any]) -> Optional[Tuple[int, str]]:
    """(pr, head) for a live attestation that has BOTH tags, else None.

    None is the protection boundary: an unkeyable row is never a survivor
    and never a stray, so the head-less legacy rows (including the 679/681
    pair) cannot be collapsed by this tool.
    """
    if not is_live_attestation(row):
        return None
    pr = pr_number(row.get("tags"))
    head = head_tag(row.get("tags"))
    if pr is None or not head or not HEAD_RE.match(head):
        return None
    return pr, head


def created_ms(row: Dict[str, Any]) -> int:
    """createdAt as epoch-ms; unparseable/absent sorts oldest (-1)."""
    raw = row.get("createdAt")
    if raw is None:
        raw = row.get("created_at")
    if isinstance(raw, bool) or raw is None:
        return -1
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return -1
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return -1


# ── grouping + plan (pure) ────────────────────────────────────────────────

def duplicate_groups(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(groups with >1 live row, unkeyable live attestations).

    A group has: pr, head (the survivor's head tag), survivor, strays.
    Rows join a group when the PR numbers match and the head tags match by
    prefix — the gate's binding rule, so 7-hex and 40-hex tags for one commit
    land in one group. Ordering is (createdAt desc, id) so the survivor is
    deterministic and ties never flip between runs.
    """
    keyed: List[Tuple[int, str, Dict[str, Any]]] = []
    unkeyable: List[Dict[str, Any]] = []
    for row in records or []:
        if not is_live_attestation(row):
            continue
        key = keyable(row)
        if key is None:
            unkeyable.append(row)
            continue
        keyed.append((key[0], key[1], row))

    keyed.sort(key=lambda t: (-created_ms(t[2]), str(t[2].get("id") or "")))

    by_pr: Dict[int, List[Dict[str, Any]]] = {}
    for pr, head, row in keyed:
        clusters = by_pr.setdefault(pr, [])
        for cluster in clusters:
            if heads_match(cluster["head"], head):
                cluster["rows"].append(row)
                break
        else:
            clusters.append({"pr": pr, "head": head, "rows": [row]})

    groups: List[Dict[str, Any]] = []
    for pr, clusters in sorted(by_pr.items()):
        for cluster in clusters:
            rows = cluster["rows"]
            if len(rows) < 2:
                continue
            survivor = rows[0]
            groups.append({
                "pr": pr,
                "head": cluster["head"],
                "survivor": survivor,
                "strays": rows[1:],
            })
    return groups, unkeyable


def retirement_patch(
    row: Dict[str, Any],
    pr: int,
    head: str,
    successor_id: str,
    at_iso: str,
) -> Optional[Dict[str, Any]]:
    """Decision 24 Amendment 1 patch — parity with the TS retirementPatch.

    Drops every status:* (a row that is status:done AND status:retired still
    reads as live to a tag query), adds exactly one status:retired, prefixes
    the title once, preserves every other tag and all other metadata, and
    records metadata.retired naming the successor. The body is NOT part of the
    patch: retirement is a status transition, not a rewrite.
    """
    if is_withdrawn(row):
        return None
    kept = [str(t) for t in (row.get("tags") or []) if not str(t).lower().startswith("status:")]
    prefix = f"{RETIRED_TITLE_PREFIX}{at_iso[:10]}] "
    title = str(row.get("title") or "")
    metadata = dict(row.get("metadata") or {})
    succ = str(successor_id or "")[:8]
    metadata["retired"] = {
        "at": at_iso,
        "note": (
            f"duplicate attestation for PR #{pr} at head {head[:8]} "
            f"superseded by {succ} (attestation-residue-sweep)"
        ),
        "by": "tester",
        "performed_by": PERFORMED_BY,
        "convention": CONVENTION,
    }
    return {
        "title": title if title.startswith(prefix) else prefix + title,
        "tags": kept + ["status:retired"],
        "metadata": metadata,
    }


def plan(groups: List[Dict[str, Any]], at_iso: str) -> List[Dict[str, Any]]:
    """Flatten groups into retire actions (pure)."""
    actions: List[Dict[str, Any]] = []
    for group in groups:
        survivor_id = str(group["survivor"].get("id") or "")
        for stray in group["strays"]:
            patch = retirement_patch(
                stray, group["pr"], group["head"], survivor_id, at_iso)
            if patch is None:
                continue
            actions.append({
                "id": str(stray.get("id") or ""),
                "pr": group["pr"],
                "head": group["head"],
                "successor": survivor_id,
                "title_before": str(stray.get("title") or ""),
                "patch": patch,
            })
    return actions


# ── I/O ───────────────────────────────────────────────────────────────────

def _http_json(url: str, payload: Optional[Dict[str, Any]] = None,
               method: Optional[str] = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}


def fetch_tester_records(base: str) -> List[Dict[str, Any]]:
    """Every tester record, paginated. Raises on unreachable/!200."""
    out: List[Dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        data = _http_json(
            f"{base}/api/agent-records?role={TESTER_ROLE}&limit={PAGE_LIMIT}&page={page}")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("unexpected /api/agent-records shape (no items[])")
        out.extend(items)
        if len(items) < PAGE_LIMIT:
            return out
    raise RuntimeError(f"pagination exceeded {MAX_PAGES} pages — refusing to guess")


def fetch_point(base: str, record_id: str) -> Dict[str, Any]:
    return _http_json(f"{base}/api/agent-records/{record_id}")


def patch_record(base: str, record_id: str, patch: Dict[str, Any]) -> Any:
    return _http_json(f"{base}/api/agent-records/{record_id}", patch, "PATCH")


def _body_sha(row: Dict[str, Any]) -> str:
    return hashlib.sha256(str(row.get("content") or "").encode()).hexdigest()


def _tags_without_status(row: Dict[str, Any]) -> List[str]:
    return [str(t) for t in (row.get("tags") or []) if not str(t).lower().startswith("status:")]


def apply_actions(
    actions: List[Dict[str, Any]],
    fetch_point: Callable[[str], Dict[str, Any]],
    patch: Callable[[str, Dict[str, Any]], Any],
    is_live: Callable[[Dict[str, Any]], bool],
    max_retire: int,
) -> List[Dict[str, Any]]:
    """Execute + verify each retirement. Never raises; reports per row."""
    results: List[Dict[str, Any]] = []
    for action in actions[:max_retire]:
        rid = action["id"]
        result = {"id": rid, "pr": action["pr"], "head": action["head"],
                  "successor": action["successor"], "ok": False, "error": None,
                  "body_sha": None, "body_unchanged": None}
        try:
            before = fetch_point(rid)
            if not before or before.get("id") != rid:
                result["error"] = "row unreadable before retirement"
                results.append(result)
                continue
            if is_withdrawn(before):
                result["error"] = "already withdrawn before retirement"
                results.append(result)
                continue
            sha_before = _body_sha(before)
            result["body_sha"] = sha_before
            tags_before_nostatus = _tags_without_status(before)

            patch(rid, action["patch"])

            after = fetch_point(rid)
            tags_after = [str(t) for t in (after.get("tags") or [])]
            lowered = {t.lower() for t in tags_after}
            problems: List[str] = []
            if _body_sha(after) != sha_before:
                problems.append("body changed")
            if "status:retired" not in lowered:
                problems.append("status:retired missing")
            if [t for t in tags_after if t.lower().startswith("status:")] != ["status:retired"]:
                problems.append("status tags not exactly [status:retired]")
            if _tags_without_status(after) != tags_before_nostatus:
                problems.append("non-status tags changed")
            if not str(after.get("title") or "").startswith(RETIRED_TITLE_PREFIX):
                problems.append("title not prefixed")
            successor_live = False
            try:
                succ_row = fetch_point(action["successor"])
                successor_live = bool(succ_row) and is_live(succ_row)
            except Exception as exc:  # verification only
                problems.append(f"successor unreadable: {exc!r}"[:160])
            if not successor_live:
                problems.append("successor is not live")
            result["body_unchanged"] = _body_sha(after) == sha_before
            if problems:
                result["error"] = "; ".join(problems)
            else:
                result["ok"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        results.append(result)
    return results


def post_changelog(script: Path, title: str, body: str,
                   runner: Callable[..., Any] = subprocess.run) -> bool:
    try:
        proc = runner([str(script), "--title", title, "--body", body],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


# ── reporting ─────────────────────────────────────────────────────────────

def _short(rid: Any) -> str:
    return str(rid or "")[:8]


def _iso(ms: int) -> str:
    if ms < 0:
        return "unknown"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_report(scanned: int, live: List[Dict[str, Any]], groups: List[Dict[str, Any]],
                  unkeyable: List[Dict[str, Any]], actions: List[Dict[str, Any]],
                  results: Optional[List[Dict[str, Any]]], applied: bool) -> str:
    lines: List[str] = []
    mode = "APPLY" if applied else "DRY RUN (no writes)"
    lines.append(f"attestation residue sweep — {mode}")
    lines.append(
        f"scanned {scanned} tester records; {len(live)} live attestations; "
        f"{len(groups)} duplicate group(s) for a (pr, well-formed head) pair; "
        f"{len(actions)} stray row(s) planned")
    if groups:
        lines.append("")
        for group in groups:
            sv = group["survivor"]
            lines.append(
                f"GROUP PR #{group['pr']} head {group['head'][:9]} — survivor "
                f"{_short(sv.get('id'))} ({_iso(created_ms(sv))}, "
                f"{sv.get('recordType') or 'unknown'})")
            for stray in group["strays"]:
                lines.append(
                    f"  retire {_short(stray.get('id'))} ({_iso(created_ms(stray))}, "
                    f"{stray.get('recordType') or 'unknown'}) under {_short(sv.get('id'))}")
    if unkeyable:
        ids = ", ".join(_short(r.get("id")) for r in unkeyable[:8])
        more = "" if len(unkeyable) <= 8 else f" … (+{len(unkeyable) - 8})"
        lines.append("")
        lines.append(
            f"protected: {len(unkeyable)} head-less live attestation row(s) — no head "
            f"binding, never candidates (e.g. {ids}{more}). Includes the shared "
            f"679/681 legacy pair.")
    if results is not None:
        lines.append("")
        ok = [r for r in results if r["ok"]]
        bad = [r for r in results if not r["ok"]]
        lines.append(f"applied: {len(ok)} retired and verified, {len(bad)} failed")
        for r in ok:
            lines.append(f"  ok    {_short(r['id'])} -> {_short(r['successor'])} "
                         f"(PR #{r['pr']}, body sha256 {str(r['body_sha'])[:12]})")
        for r in bad:
            lines.append(f"  FAIL  {_short(r['id'])}: {r['error']}")
        if bad:
            lines.append("failed rows were NOT claimed as retired; re-run after resolving")
    if not groups and applied:
        lines.append("")
        lines.append("nothing to do — rerun is a no-op")
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────

def run(records: List[Dict[str, Any]], apply: bool, at_iso: str,
        fetch_point: Optional[Callable[[str], Dict[str, Any]]] = None,
        patch: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        post: Optional[Callable[[str, str], bool]] = None,
        max_retire: int = DEFAULT_MAX_RETIRE) -> Tuple[int, Dict[str, Any]]:
    live = [r for r in records or [] if is_live_attestation(r)]
    groups, unkeyable = duplicate_groups(records)
    actions = plan(groups, at_iso)
    results: Optional[List[Dict[str, Any]]] = None
    exit_code = 0
    if apply and actions and fetch_point and patch:
        results = apply_actions(
            actions, fetch_point, patch,
            is_live=lambda row: is_live_attestation(row) and not is_withdrawn(row),
            max_retire=max_retire)
        if any(not r["ok"] for r in results):
            exit_code = 1
        retired = [r for r in results if r["ok"]]
        if retired and post:
            body = "\n".join(
                [f"attestation-residue-sweep retired {len(retired)} duplicate live "
                 f"tester attestation(s) under their surviving successor "
                 f"(Decision 24 Amendment 1: status:* dropped, status:retired added, "
                 f"title prefixed, metadata.retired names the successor, body "
                 f"byte-identical).", ""]
                + [f"- {_short(r['id'])} -> {_short(r['successor'])} (PR #{r['pr']}, "
                   f"head {r['head'][:9]}, body sha256 {str(r['body_sha'])[:12]})"
                   for r in retired])
            ok = post("attestation-residue-sweep: retired duplicate tester attestations",
                      body)
            for r in results:
                r["changelog_posted"] = bool(ok)
    summary = {
        "scanned": len(records or []),
        "live_attestations": len(live),
        "groups": [
            {"pr": g["pr"], "head": g["head"],
             "survivor": _short(g["survivor"].get("id")),
             "strays": [_short(s.get("id")) for s in g["strays"]]}
            for g in groups
        ],
        "actions": [{"id": a["id"], "pr": a["pr"], "head": a["head"],
                     "successor": a["successor"]} for a in actions],
        "protected_headless": len(unkeyable),
        "results": results,
        "applied": bool(apply and actions),
    }
    return exit_code, summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Retire duplicate live tester attestations (see module docstring).")
    ap.add_argument("--apply", action="store_true",
                    help="retire the planned strays (default: dry run, zero writes)")
    ap.add_argument("--json", action="store_true", help="print the machine-readable summary")
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--max-retire", type=int, default=DEFAULT_MAX_RETIRE,
                    help=f"cap rows retired per run (default {DEFAULT_MAX_RETIRE})")
    ap.add_argument("--no-changelog", action="store_true",
                    help="skip the change-log audit post on --apply")
    ap.add_argument("--post-changelog-path", default=str(DEFAULT_POST_CHANGELOG))
    args = ap.parse_args(argv)

    base = args.nebula_url.rstrip("/")
    try:
        records = fetch_tester_records(base)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        print(f"attestation-residue-sweep: ERROR — nebula unreachable or unreadable "
              f"({exc}); nothing written", file=sys.stderr)
        return 2

    at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    exit_code, summary = run(
        records,
        apply=args.apply,
        at_iso=at_iso,
        fetch_point=lambda rid: fetch_point(base, rid),
        patch=lambda rid, payload: patch_record(base, rid, payload),
        post=(None if args.no_changelog else
              lambda title, body: post_changelog(Path(args.post_changelog_path), title, body)),
        max_retire=max(1, args.max_retire),
    )

    if args.json:
        print(json.dumps(summary, indent=1))
    else:
        print(format_report(
            summary["scanned"], [r for r in records if is_live_attestation(r)],
            *duplicate_groups(records), summary["actions"], summary["results"],
            summary["applied"]))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
