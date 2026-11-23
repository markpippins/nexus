"""Hermetic tests for bin/metadata_namespace_normalize.py (ranking item 6).

No database, no HTTP: the scan is a list of Row objects, and the write path
takes injected `fetch` / `write` callables, so every refusal class, the
read-back verification and the no-op rerun are exercised deterministically.

    python3 -m pytest bin/tests/test_metadata_namespace_normalize.py -q
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metadata_namespace_normalize import (  # noqa: E402
    LEGACY_TO_CANONICAL,
    Row,
    apply_plans,
    build_plans,
    classify_row,
    normalize_metadata,
    report_tsv,
    summarize,
)

RETIRED = {"at": "2026-11-23T06:00:00Z", "by": "tester", "note": "superseded"}


def row(metadata, rid="11111111-1111-1111-1111-111111111111", role="tester",
        title="T", content="body text"):
    return Row(id=rid, role=role, metadata=metadata, title=title, content=content)


def plan_one(metadata):
    return classify_row(row(metadata))


# ── mapping ──────────────────────────────────────────────────────────────

def test_maps_a_legacy_lifecycle_key_and_preserves_the_rest():
    p = plan_one({"retired": RETIRED, "pr": 1049, "branch": "item6"})
    assert [(i.key, i.canonical, i.verdict, i.reason) for i in p.planned] == [
        ("retired", "record.retired", "plan", "map")
    ]
    assert {i.reason for i in p.residue} == {"free_form"}
    assert p.blocked == []
    out = normalize_metadata(p)
    assert out["record.retired"] == RETIRED
    assert "retired" not in out
    assert out["pr"] == 1049 and out["branch"] == "item6"


def test_maps_several_legacy_keys_in_one_write():
    meta = {"retired": RETIRED, "corrections": [{"claim": "x"}], "residue_closed": True}
    p = plan_one(meta)
    assert {i.canonical for i in p.planned} == {
        "record.retired", "correction.claims", "correction.residue_closed"
    }
    out = normalize_metadata(p)
    assert set(out) == {"record.retired", "correction.claims", "correction.residue_closed"}


def test_maps_migration_and_repair_blocks():
    meta = {"_migrated_from": "a/b.md", "_dirname": "a", "_size_bytes": 12,
            "hollow_content_repair": True, "repaired_from": "x", "restored_content_chars": 9}
    out = normalize_metadata(plan_one(meta))
    assert out["migration.origin_path"] == "a/b.md"
    assert out["migration.dirname"] == "a"
    assert out["migration.size_bytes"] == 12
    assert out["repair.hollow_content"] is True
    assert out["repair.repaired_from"] == "x"
    assert out["repair.restored_chars"] == 9
    assert not [k for k in out if k.startswith("_")]


def test_conditional_snapshot_keys_map_only_with_their_anchor():
    with_anchor = {"snapshot_purpose": "pre-fk-migration-evidence", "source_table": "work_requests",
                   "source_id": "739d22f9", "fk_column": "plan_id", "fk_value": "1076"}
    out = normalize_metadata(plan_one(with_anchor))
    assert out["snapshot.purpose"] == "pre-fk-migration-evidence"
    assert out["snapshot.source_table"] == "work_requests"
    assert out["snapshot.source_id"] == "739d22f9"
    assert out["snapshot.fk_column"] == "plan_id"
    assert out["snapshot.fk_value"] == "1076"
    assert "source_id" not in out

    lone = plan_one({"source_id": "someone-elses-id"})
    assert lone.planned == []
    assert [(i.key, i.reason) for i in lone.blocked] == [("source_id", "ambiguous_owner")]


def test_every_registered_legacy_key_has_a_canonical_name():
    assert LEGACY_TO_CANONICAL["retired"] == "record.retired"
    assert LEGACY_TO_CANONICAL["snapshot_purpose"] == "snapshot.purpose"
    assert len(set(LEGACY_TO_CANONICAL.values())) == len(LEGACY_TO_CANONICAL)


# ── refusal / residue ────────────────────────────────────────────────────

def test_free_form_keys_are_left_untouched_and_enumerated():
    meta = {"pr": 1049, "model": "x", "role": "tester", "verdict": "approved"}
    p = plan_one(meta)
    assert p.planned == []
    assert {i.reason for i in p.residue} == {"free_form"}
    assert p.blocked == []
    assert normalize_metadata(p) == meta


def test_conflict_refuses_the_whole_row_without_writing():
    meta = {"retired": RETIRED, "record.retired": {"at": "different"}}
    p = plan_one(meta)
    assert [(i.key, i.reason) for i in p.blocked] == [("retired", "conflict")]
    assert p.planned == []
    assert normalize_metadata(p) == meta  # nothing rewritten


def test_duplicate_value_drops_the_legacy_copy_only():
    meta = {"retired": RETIRED, "record.retired": RETIRED}
    p = plan_one(meta)
    assert [(i.key, i.reason) for i in p.items] == [("retired", "duplicate_value")]
    out = normalize_metadata(p)
    assert out == {"record.retired": RETIRED}


def test_already_canonical_rows_plan_nothing():
    p = plan_one({"record.retired": RETIRED, "pr": 1})
    assert p.planned == []
    assert [i.reason for i in p.residue] == ["free_form"]
    canonical_only = plan_one({"record.retired": RETIRED, "snapshot.purpose": "x"})
    assert canonical_only.items == []


# ── summary / report ─────────────────────────────────────────────────────

def test_summary_arithmetic_covers_every_key_occurrence():
    rows = [
        row({"retired": RETIRED, "pr": 1}, rid="a"),
        row({"source_id": "x", "_migrated_from": "p"}, rid="b"),
        row({"record.retired": RETIRED}, rid="c"),
    ]
    plans = build_plans(rows)
    summary = summarize(plans)
    assert summary["rows_scanned"] == 3
    assert summary["rows_with_legacy_keys"] == 2
    assert summary["rows_to_write"] == 2
    assert summary["keys_to_move"] == 2          # retired, _migrated_from
    # only the blocking refusal is a refused row; free-form prose does not
    # stop the rest of the row from being normalized
    assert summary["rows_refused"] == 1          # b (ambiguous_owner)
    total = sum(summary["occurrences"].values())
    # every non-canonical key occurrence is classified exactly once
    assert total == sum(1 for r in rows for k in r.metadata if not k.startswith(
        ("record.", "correction.", "snapshot.", "migration.", "repair.", "harness.")))
    assert summary["occurrences"]["free_form"] == 1
    assert summary["occurrences"]["ambiguous_owner"] == 1
    assert summary["occurrences"]["map"] == 2


def test_report_lists_one_line_per_occurrence_with_its_reason():
    plans = build_plans([row({"retired": RETIRED, "pr": 9, "source_id": "s"})])
    lines = report_tsv(plans).strip().splitlines()
    assert lines[0].startswith("record_id\trole\tlegacy_key")
    body = lines[1:]
    assert len(body) == 3
    assert any("\tretired\trecord.retired\tplan\tmap" in ln for ln in body)
    assert any("\tpr\t\trefuse\tfree_form" in ln for ln in body)
    assert any("\tsource_id\tsnapshot.source_id\trefuse\tambiguous_owner" in ln for ln in body)


# ── apply: verification, staleness, bounds ───────────────────────────────

class FakeNebula:
    def __init__(self, rows, *, corrupt=None, status=200, mutate_after=None):
        self.rows = {r.id: {"id": r.id, "metadata": json.loads(json.dumps(r.metadata)),
                            "content": r.content, "title": r.title} for r in rows}
        self.corrupt = corrupt      # callable(metadata) before read-back
        self.status = status
        self.mutate_after = mutate_after
        self.writes = 0

    def fetch(self, _base, rid):
        return json.loads(json.dumps(self.rows[rid]))

    def write(self, _base, rid, metadata):
        self.writes += 1
        if self.status != 200:
            return self.status, {"error": "nope"}
        rec = self.rows[rid]
        if self.mutate_after:
            rec["metadata"] = self.mutate_after(rec["metadata"])
        rec["metadata"] = json.loads(json.dumps(metadata))
        if self.corrupt:
            rec["metadata"] = self.corrupt(rec["metadata"])
        return 200, {"id": rid}


def test_apply_verifies_each_write_and_leaves_bystanders_intact():
    r = row({"retired": RETIRED, "pr": 3})
    neb = FakeNebula([r])
    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert out["verified"] == [r.id] and out["failed"] == []
    assert neb.rows[r.id]["metadata"] == {"record.retired": RETIRED, "pr": 3}


def test_apply_fails_when_read_back_drops_the_canonical_key():
    r = row({"retired": RETIRED})
    neb = FakeNebula([r], corrupt=lambda md: {})
    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert out["verified"] == []
    assert "record.retired missing" in out["failed"][0]["error"]


def test_apply_fails_when_a_bystander_key_changes():
    r = row({"retired": RETIRED, "pr": 3})
    neb = FakeNebula([r], corrupt=lambda md: {**md, "pr": 999})
    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert out["verified"] == []
    assert "bystander" in out["failed"][0]["error"]


def test_apply_fails_when_the_legacy_key_survives():
    r = row({"retired": RETIRED})
    neb = FakeNebula([r], corrupt=lambda md: {**md, "retired": RETIRED})
    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert out["failed"] and "still present" in out["failed"][0]["error"]


def test_apply_fails_when_the_body_changed():
    r = row({"retired": RETIRED})
    neb = FakeNebula([r])
    real_fetch = neb.fetch

    def fetch(_base, rid):
        rec = real_fetch(_base, rid)
        if neb.writes:
            rec["content"] = "rewritten body"
        return rec

    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=fetch, write=neb.write)
    assert out["failed"] and "body changed" in out["failed"][0]["error"]


def test_apply_refuses_a_stale_row_instead_of_clobbering_it():
    r = row({"retired": RETIRED, "pr": 3})
    neb = FakeNebula([r])
    # a concurrent writer lands between the scan and our pre-write read
    neb.rows[r.id]["metadata"] = {"retired": RETIRED, "pr": 42}
    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0
    assert out["failed"] and "stale" in out["failed"][0]["error"]
    assert neb.rows[r.id]["metadata"]["pr"] == 42  # the other writer's value survives


def test_apply_records_an_http_refusal():
    r = row({"retired": RETIRED})
    neb = FakeNebula([r], status=422)
    out = apply_plans(build_plans([r]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert out["verified"] == []
    assert "HTTP 422" in out["failed"][0]["error"]


def test_apply_is_bounded_and_writes_nothing_when_the_bound_is_exceeded():
    rows = [row({"retired": RETIRED}, rid=f"{i:08d}-0000-0000-0000-000000000000") for i in range(3)]
    neb = FakeNebula(rows)
    with pytest.raises(RuntimeError, match="exceed --max-apply"):
        apply_plans(build_plans(rows), "http://x", max_apply=2,
                    fetch=neb.fetch, write=neb.write)
    assert neb.writes == 0


def test_conflicting_rows_are_never_written():
    bad = row({"retired": RETIRED, "record.retired": {"at": "other"}}, rid="bad")
    good = row({"retired": RETIRED}, rid="good")
    neb = FakeNebula([bad, good])
    out = apply_plans(build_plans([bad, good]), "http://x", max_apply=10,
                      fetch=neb.fetch, write=neb.write)
    assert out["verified"] == ["good"]
    assert neb.writes == 1


# ── idempotency ──────────────────────────────────────────────────────────

def test_rerun_after_apply_is_a_verified_no_op():
    rows = [
        row({"retired": RETIRED, "pr": 1}, rid="a"),
        row({"snapshot_purpose": "p", "source_id": "s"}, rid="b"),
        row({"_migrated_from": "x"}, rid="c"),
    ]
    neb = FakeNebula(rows)
    first = apply_plans(build_plans(rows), "http://x", max_apply=10,
                        fetch=neb.fetch, write=neb.write)
    assert len(first["verified"]) == 3

    rescan = [Row(id=k, role="tester", metadata=neb.rows[k]["metadata"],
                  title=neb.rows[k]["title"], content=neb.rows[k]["content"])
              for k in neb.rows]
    plans = build_plans(rescan)
    assert all(p.planned == [] for p in plans)
    assert sum(len(p.blocked) for p in plans) == 0
    second = apply_plans(plans, "http://x", max_apply=10,
                         fetch=neb.fetch, write=neb.write)
    assert second == {"planned": 0, "verified": [], "failed": []}
    assert neb.writes == 3  # nothing written on the rerun


def test_apply_paces_writes_to_stay_under_the_service_rate_limit():
    rows = [row({"retired": RETIRED}, rid=f"{i:08d}-0000-0000-0000-000000000000") for i in range(3)]
    neb = FakeNebula(rows)
    naps: list[float] = []
    apply_plans(build_plans(rows), "http://x", max_apply=10,
                fetch=neb.fetch, write=neb.write, pace=0.7, sleep=naps.append)
    assert naps == [0.7, 0.7]  # one pause between rows, never before the first


class _FakeResponse:
    status = 200

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_retries_a_429_and_honours_retry_after(monkeypatch):
    import io
    import urllib.error
    import metadata_namespace_normalize as mod

    calls = {"n": 0}
    naps: list[float] = []

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                req.full_url, 429, "limited", {"Retry-After": "1"}, io.BytesIO(b'{"error":"limit"}'))
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod, "_sleep", naps.append)
    status, body = mod._http("PATCH", "http://x/api/agent-records/1", {"metadata": {}})
    assert (status, body) == (200, {"ok": True})
    assert calls["n"] == 3
    assert naps == [1.0, 1.0]


def test_http_gives_up_after_the_attempt_budget(monkeypatch):
    import io
    import urllib.error
    import metadata_namespace_normalize as mod

    calls = {"n": 0}

    def always_429(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 429, "limited", {},
                                     io.BytesIO(b'{"error":"limit"}'))

    monkeypatch.setattr(mod.urllib.request, "urlopen", always_429)
    monkeypatch.setattr(mod, "_sleep", lambda _s: None)
    status, body = mod._http("PATCH", "http://x", {})
    assert status == 429 and body == {"error": "limit"}
    assert calls["n"] == mod.MAX_ATTEMPTS


def test_http_does_not_retry_a_refusal():
    import io
    import urllib.error
    import metadata_namespace_normalize as mod

    calls = {"n": 0}

    def refuse(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 422, "bad", {},
                                     io.BytesIO(b'{"error":"legacy key"}'))

    original = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = refuse
    try:
        status, body = mod._http("POST", "http://x", {})
    finally:
        mod.urllib.request.urlopen = original
    assert status == 422 and calls["n"] == 1


def test_digest_is_order_independent():
    a = row({"retired": RETIRED, "pr": 1})
    b = row({"pr": 1, "retired": RETIRED})
    assert a.digest() == b.digest()
    assert a.digest() == hashlib.sha256(
        json.dumps(a.metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
