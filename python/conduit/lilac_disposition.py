#!/usr/bin/env python3
"""Legacy receipt disposition classifier (Lilac plan 8261670, AC3/AC4).

Minimal, deterministic, append-only. Operates on supplied row dicts — it
opens no database connection itself, so classification can be unit-tested
hermetically and the live databases are never touched by this module.

Dispositions: ``backfill`` (row is importable into the canonical
resolution surface with explicit lineage), ``discard`` (synthetic/test
residue with no canonical value), ``retire-with-evidence`` (historical
row kept read-only with an evidence pointer), ``unclassifiable`` (could
not be placed; counted explicitly, never silently dropped).

Append-only ledger: :func:`record_dispositions` writes one JSON line per
row id into a JSONL ledger. Re-running with the same rows produces the
identical summary and leaves existing ledger entries byte-identical —
entries are never rewritten.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

DISPOSITIONS = ("backfill", "discard", "retire-with-evidence", "unclassifiable")

# Legacy receipt-bearing surfaces covered by the W1 audit. Anything outside
# this set is unclassifiable by construction (counted, not dropped).
LEGACY_RECEIPT_TABLES = frozenset({
    ("vision", "receipts"),
    ("vision", "receipts_history"),
    ("execution", "receipts"),
    ("wind", "receipts"),
    ("kernel", "receipts"),
    ("nebula", "receipts_unified"),
})

_SYNTHETIC_MARKERS = ("test", "synthetic", "probe", "seed", "fixture")


def classify_legacy_receipt(row: Dict[str, Any]) -> str:
    """Return the disposition for one legacy receipt row.

    Every input maps to exactly one disposition — there is no ``None``
    outcome, so a summary over N rows always accounts for N rows.
    """
    schema = str(row.get("source_schema") or "").strip()
    table = str(row.get("source_table") or "").strip()
    if (schema, table) not in LEGACY_RECEIPT_TABLES:
        return "unclassifiable"
    plan_id = str(row.get("plan_id") or "").strip()
    receipt_id = str(row.get("receipt_id") or row.get("id") or "").strip()
    summary = str(row.get("summary") or "").lower()
    if not receipt_id:
        return "unclassifiable"
    if any(m in summary or m in receipt_id.lower() for m in _SYNTHETIC_MARKERS):
        return "discard"
    if plan_id:
        return "backfill"
    return "retire-with-evidence"


def summarize_dispositions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize dispositions with 100% accounting.

    The returned mapping always satisfies
    ``sum(counts.values()) == len(rows)`` — unclassifiable rows are
    counted, never dropped.
    """
    counts = {d: 0 for d in DISPOSITIONS}
    for row in rows:
        counts[classify_legacy_receipt(row)] += 1
    return {"total": len(rows), "counts": counts}


def record_dispositions(ledger_path: str,
                       rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Append dispositions to a JSONL ledger without rewriting history.

    Existing entries (matched by row id) are left byte-identical; only
    unseen ids are appended. Returns the summary plus ``appended`` count.
    """
    seen: Dict[str, str] = {}
    if os.path.exists(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                seen[str(entry["id"])] = line
    appended = 0
    with open(ledger_path, "a", encoding="utf-8") as fh:
        for row in rows:
            row_id = str(row.get("receipt_id") or row.get("id") or "")
            if not row_id or row_id in seen:
                continue
            entry = {"id": row_id,
                     "disposition": classify_legacy_receipt(row),
                     "source_schema": row.get("source_schema"),
                     "source_table": row.get("source_table"),
                     "plan_id": row.get("plan_id")}
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            appended += 1
    summary = summarize_dispositions(rows)
    summary["appended"] = appended
    return summary
