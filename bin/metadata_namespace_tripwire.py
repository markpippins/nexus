#!/usr/bin/env python3
"""metadata_namespace_tripwire.py — shout if a legacy tool-owned metadata key
reappears in `nebula.agent_records`.

The write path is guarded: `typescript/nebula-srv/src/metadata-namespace.ts`
returns 422 when a POST/PATCH carries a legacy spelling such as `retired` or
`snapshot_purpose`. Nothing guards the *table*. A migration, a repair script,
`psql`, or any future service writing SQL directly can put the old dialect
back, and the API would never notice — the corpus would quietly grow a second
spelling of the same contract again.

This tripwire watches the corpus itself. Every legacy key it finds is a
finding: the record id, the role, the legacy key, and the canonical name the
producer should have used. Exit 1 and a loud stderr block make it fail the
unit; `--post` also files one change-log entry per new evidence set (deduped by
evidence digest so a persisting violation does not spam).

Registry single-sourced: the legacy->canonical map is imported from
`bin/metadata_namespace_normalize.py`, the same one the normalizer applied.
Only the *enforced* set is watched (the keys the API guard refuses). Generic
keys the guard deliberately lets through — `source_id`, `source_table`,
`fk_*` — are not flagged on their own, because a record author may legitimately
use those and no anchor proves ownership.

The control lives in `bin/tests/test_metadata_namespace_tripwire.py`: a
synthetic row carrying each legacy key must be detected, a clean corpus must
pass, and the JSON/exit-code contract is pinned. A live control was also run
against the real corpus with a probe row (see the PR body).

Usage:
  metadata_namespace_tripwire.py                 # scan, exit 1 on findings
  metadata_namespace_tripwire.py --json          # machine-readable verdict
  metadata_namespace_tripwire.py --post          # + one change-log entry on a new episode
  metadata_namespace_tripwire.py --dry-run       # no state, no posts, no heartbeat

Exit codes: 0 clean; 1 legacy keys present; 2 tool/environment error (nothing
concluded).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN_DIR))

from metadata_namespace_normalize import DIRECT  # noqa: E402  (single registry)

DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
STATE_DIR = Path(os.environ.get(
    "METADATA_TRIPWIRE_STATE", str(Path.home() / ".cache" / "metadata-namespace-tripwire")))

# The enforced set: what the API guard refuses. Conditional generic keys
# (source_id/source_table/fk_*) are deliberately NOT here — see module docstring.
LEGACY_ENFORCED: Dict[str, str] = {legacy: canonical for canonical, legacy in DIRECT.items()}


def find_legacy(rows: Iterable[Tuple[str, str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Pure scan: one finding per (record, legacy key) present.

    `rows` is (id, role, metadata). Canonical keys and free-form keys are
    ignored by construction: only the enforced legacy spellings produce a
    finding.
    """
    findings: List[Dict[str, Any]] = []
    for rid, role, metadata in rows:
        for key in sorted((metadata or {}).keys()):
            canonical = LEGACY_ENFORCED.get(key)
            if canonical is None:
                continue
            findings.append({
                "record_id": rid,
                "role": role or "",
                "legacy_key": key,
                "canonical_key": canonical,
            })
    return findings


def evidence_digest(findings: Sequence[Dict[str, Any]]) -> str:
    blob = json.dumps(sorted(findings, key=lambda f: (f["record_id"], f["legacy_key"])),
                      sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _connect(dsn: str):
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("psycopg2 is required to read the corpus") from exc
    return psycopg2.connect(dsn)


def load_rows(dsn: str = DSN, limit: Optional[int] = None) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Read only the keys and metadata of records that could carry a legacy key.

    The `?|` filter keeps the scan cheap on a 17k-row corpus: PostgreSQL has to
    inspect jsonb either way, but the row set crossing the wire is tiny.
    """
    keys = sorted(LEGACY_ENFORCED)
    sql = (
        "select id::text, coalesce(role,''), metadata "
        "from nebula.agent_records where metadata ?| %s::text[] "
        "order by created_at asc"
    )
    if limit:
        sql += " limit %s"
        params: Tuple[Any, ...] = (keys, limit)
    else:
        params = (keys,)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1], dict(r[2] or {})) for r in cur.fetchall()]


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def post_alert(findings: Sequence[Dict[str, Any]]) -> bool:
    """File one change-log entry naming the offenders. Returns True on success."""
    lines = [
        "Metadata namespace tripwire: legacy tool-owned keys are back in the corpus.",
        "",
        "The API guard refuses these spellings, so a writer bypassed it (direct SQL).",
        "",
        "| record | role | legacy key | canonical |",
        "|---|---|---|---|",
    ]
    for f in findings[:50]:
        lines.append(f"| `{f['record_id'][:8]}` | {f['role']} | `{f['legacy_key']}` | `{f['canonical_key']}` |")
    if len(findings) > 50:
        lines.append(f"\n(+{len(findings) - 50} more)")
    lines += ["", "Fix the producer (or normalize the rows), then re-run: clean is exit 0."]
    proc = subprocess.run(
        [str(BIN_DIR / "post-change-log.sh"),
         "--title", f"METADATA TRIPWIRE: {len(findings)} legacy tool-owned metadata key(s) reappeared",
         "--body", "\n".join(lines)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"tripwire: could not post alert: {proc.stderr.strip()[:200]}", file=sys.stderr)
    return proc.returncode == 0


def main(argv: Optional[Sequence[str]] = None, *,
         fetch=load_rows, post=post_alert) -> int:
    ap = argparse.ArgumentParser(description="Fail loudly if legacy tool-owned metadata keys reappear.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--post", action="store_true", help="file a change-log alert on a new episode")
    ap.add_argument("--dry-run", action="store_true", help="no state, no posts, no heartbeat")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--state-dir", default=str(STATE_DIR))
    args = ap.parse_args(argv)

    try:
        rows = fetch(args.dsn, args.limit) if args.limit else fetch(args.dsn)
    except Exception as exc:  # noqa: BLE001 — tool error, not a verdict
        print(f"tripwire: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    findings = find_legacy(rows)
    state_path = Path(args.state_dir) / "state.json"
    state = _read_state(state_path)
    digest = evidence_digest(findings) if findings else ""
    new_episode = bool(findings) and digest != state.get("last_evidence_digest")

    verdict = {
        "verdict": "LEGACY_KEYS_PRESENT" if findings else "CLEAN",
        "findings": len(findings),
        "records": len({f["record_id"] for f in findings}),
        "episode": "new" if new_episode else ("persisting" if findings else "none"),
        "detail": findings,
    }

    if args.json:
        print(json.dumps(verdict, indent=2))
    elif findings:
        print(f"LEGACY METADATA KEYS PRESENT — {len(findings)} key(s) across "
              f"{verdict['records']} record(s)", file=sys.stderr)
        print("the API guard refuses these spellings, so a writer bypassed it:", file=sys.stderr)
        for f in findings[:25]:
            print(f"  {f['record_id']} [{f['role']}] {f['legacy_key']} -> {f['canonical_key']}",
                  file=sys.stderr)
        if len(findings) > 25:
            print(f"  (+{len(findings) - 25} more)", file=sys.stderr)
        print("fix the producer or normalize the rows, then re-run.", file=sys.stderr)
    else:
        print("metadata namespace tripwire: CLEAN — no legacy tool-owned metadata keys "
              "in nebula.agent_records")

    if args.dry_run:
        return 1 if findings else 0

    if findings and args.post and new_episode:
        post(findings)

    _write_state(state_path, {
        "last_run": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                                   capture_output=True, text=True).stdout.strip(),
        "verdict": verdict["verdict"],
        "findings": len(findings),
        "last_evidence_digest": digest or state.get("last_evidence_digest", ""),
    })
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
