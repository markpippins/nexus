"""Hermetic tests for bin/supersession_pointers.py (ranking item 12, ruling aa7d33dd).

The control corpus is `bin/tests/fixtures/supersession_pointers_control.json`:
the live census of every distinct value in the three supersession families,
with the hit count each one produced against the 17,567-record corpus and the
reason code the ruling assigns. Every defect class the census found has a case
here, plus the synthetic cases the corpus does not contain (self-pointer,
2-cycle, a value that is both short and unique, uppercase hex).

The audit must account for every occurrence — 587 across the three families —
and must never resolve an ambiguous prefix or touch a record.

    python3 -m pytest bin/tests/test_supersession_pointers.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import supersession_pointers as sp  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "supersession_pointers_control.json"
CENSUS = json.loads(FIXTURE.read_text())
FAMILIES = CENSUS["families"]
DEFECTS = CENSUS["defects"]

RATIFIED = "superseded-by"
DEPRECATED = "supersedes-relation"
UNRATIFIED = "supersedes"


def values_of(family):
    return [(v["value"], v["count"]) for v in FAMILIES[family]["values"]]


def rows_from_families(classify=None):
    """One AuditRow per census occurrence, one pointer tag per row."""
    rows = []
    for family, data in FAMILIES.items():
        prefix = sp.PREFIX_RATIFIED if family == RATIFIED else (
            sp.PREFIX_DEPRECATED if family == DEPRECATED else sp.PREFIX_UNRATIFIED)
        for v in data["values"]:
            for i in range(v["count"]):
                rows.append(sp.AuditRow(
                    f"{family}-{v['value']}-{i}", f"{family}:{v['value']}"[:40],
                    [prefix + v["value"]]))
    return rows


def fixture_classifier():
    """PointerClass per (family, value) exactly as the live census measured."""
    by_key = {}
    for family, data in FAMILIES.items():
        for v in data["values"]:
            hits = v["hit_count"]
            by_key[(family, v["value"])] = sp.PointerClass(
                tag="", family=family, family_status=data["status"], value=v["value"],
                reason_code=v["expected_reason"], target=v["expected_target"],
                hits=hits, canonical_form=v["canonical_form"])
    def classify(family, value, _subject=None):
        return by_key[(family, value)]
    return classify


# ── family grammar (ruling §12.1, §12.5) ─────────────────────────────────

def test_only_three_families_are_recognised():
    assert set(sp.FAMILIES) == {"superseded-by:", "supersedes-relation:", "supersedes:"}
    assert sp.FAMILIES[sp.PREFIX_RATIFIED] == ("superseded-by", sp.FAMILY_RATIFIED)
    assert sp.FAMILIES[sp.PREFIX_DEPRECATED] == ("supersedes-relation", sp.FAMILY_DEPRECATED)
    assert sp.FAMILIES[sp.PREFIX_UNRATIFIED] == ("supersedes", sp.FAMILY_UNRATIFIED)


def test_freeform_and_unrelated_tags_are_not_pointers():
    for tag in ["supersedes-none", "supersedes-claim:non-reproducible", "superseded-by-merge",
                "supersede-lifecycle", "status:superseded", "type:superseded-reconstruction",
                "gate:1062", "pr:1062"]:
        assert sp.classify_tag(tag) is None, tag
        assert sp.pointer_families([tag]) == []


def test_supersedes_relation_is_resolved_but_flagged_deprecated():
    idx = sp.RecordIndex(["2b3e6f64-bdaf-490a-b51e-4f59cf8cc6cd"])
    c = sp.classify_pointer("supersedes-relation", "2b3e6f64", idx)
    assert c.family_status == sp.FAMILY_DEPRECATED
    assert c.reason_code == sp.R_DEPRECATED
    assert c.resolved is True and c.target.startswith("2b3e6f64")


def test_supersedes_is_unratified_but_still_resolved_for_information():
    idx = sp.RecordIndex(["570f00f0-1111-4111-8111-111111111111"])
    c = sp.classify_pointer("supersedes", "570f00f0", idx)
    assert c.family_status == sp.FAMILY_UNRATIFIED
    assert c.reason_code == sp.R_RESOLVED


# ── resolution semantics (ruling §12.2) ──────────────────────────────────

IDS = ["aaaaaaaa-1111-4111-8111-111111111111",
       "aaaaaaaa-2222-4222-8222-222222222222",
       "bbbbbbbb-3333-4333-8333-333333333333"]


def test_eight_char_prefix_resolves_to_one_record():
    idx = sp.RecordIndex(IDS)
    c = sp.classify_pointer("superseded-by", "bbbbbbbb", idx)
    assert c.reason_code == sp.R_RESOLVED
    assert c.target == IDS[2]
    assert c.canonical_form is True


def test_full_uuid_resolves_and_is_canonical():
    idx = sp.RecordIndex(IDS)
    c = sp.classify_pointer("superseded-by", IDS[0], idx)
    assert c.reason_code == sp.R_RESOLVED and c.target == IDS[0] and c.canonical_form


def test_prefix_collision_is_ambiguous_and_never_picks_one():
    idx = sp.RecordIndex(IDS)
    c = sp.classify_pointer("superseded-by", "aaaaaaaa", idx)
    assert c.reason_code == sp.R_AMBIGUOUS
    assert c.target is None and c.hits == 2


def test_short_prefix_collision_matches_the_corpus_case():
    # `supersedes:767` matched eight records in the live corpus; the shape of
    # the failure, not the number, is what the classifier guarantees.
    idx = sp.RecordIndex([f"767{i:05d}-1111-4111-8111-111111111111" for i in range(8)]
                         + [IDS[0]])
    c = sp.classify_pointer("supersedes", "767", idx)
    assert c.reason_code == sp.R_AMBIGUOUS and c.hits == 8 and c.target is None


def test_dangling_pointer_is_unresolved():
    idx = sp.RecordIndex(IDS)
    c = sp.classify_pointer("superseded-by", "deadbeef", idx)
    assert c.reason_code == sp.R_UNRESOLVED and c.hits == 0 and c.target is None


def test_prose_value_is_unresolved_not_malformed():
    for value in ["pr-669", "timestamp-binding", "branch-attestation-e9157edb",
                  "8d431a5d-storage-framing"]:
        c = sp.classify_pointer("supersedes", value, sp.RecordIndex(IDS))
        assert c.reason_code == sp.R_UNRESOLVED, value


def test_empty_value_is_malformed_and_never_enters_prefix_matching():
    idx = sp.RecordIndex(IDS)
    assert idx.resolve("") == [], "an empty prefix must match nothing"
    c = sp.classify_pointer("supersedes", "", idx)
    assert c.reason_code == sp.R_MALFORMED


def test_empty_time_guard_would_otherwise_match_every_record():
    big = sp.RecordIndex([f"{i:08x}-1111-4111-8111-111111111111" for i in range(500)])
    assert len(big) == 500
    assert big.resolve("") == []
    assert sp.classify_pointer("supersedes", "", big).hits == 0


@pytest.mark.parametrize("value", [" ", "  ", "superseded by", "abcdef1 ", " ABCDEF12"])
def test_whitespace_values_are_malformed(value):
    assert sp.classify_pointer("superseded-by", value, sp.RecordIndex(IDS)).reason_code == sp.R_MALFORMED


def test_uppercase_hex_is_malformed_not_folded():
    # Case is not folded: an upper-case id is a different byte string.
    assert sp.classify_pointer("superseded-by", "AAAAAAAA", sp.RecordIndex(IDS)).reason_code == sp.R_MALFORMED


def test_self_pointer_is_flagged():
    idx = sp.RecordIndex(IDS)
    c = sp.classify_pointer("superseded-by", "aaaaaaaa", idx, subject_id=IDS[0] + "")
    # prefix is ambiguous between two rows, so use the full id to isolate self
    c = sp.classify_pointer("superseded-by", IDS[0], idx, subject_id=IDS[0])
    assert c.reason_code == sp.R_SELF and c.target == IDS[0]


def test_non_canonical_length_still_resolves_but_is_not_canonical_form():
    idx = sp.RecordIndex(IDS)
    c = sp.classify_pointer("superseded-by", "bbbbbbb", idx)
    assert c.reason_code == sp.R_RESOLVED and c.canonical_form is False


def test_resolution_without_an_index_only_trusts_canonical_shape():
    assert sp.classify_pointer("superseded-by", "bbbbbbbb").reason_code == sp.R_RESOLVED
    assert sp.classify_pointer("superseded-by", "bbbbbbb").reason_code == sp.R_MALFORMED
    assert sp.classify_pointer("superseded-by", "pr-1").reason_code == sp.R_UNRESOLVED


# ── the invariant (ruling §12.3) and structure (§12.4) ───────────────────

def test_canonical_successor_refuses_multiplicity_and_foreign_families():
    idx = sp.RecordIndex(IDS)
    assert sp.canonical_successor(["superseded-by:bbbbbbbb"], idx) == IDS[2]
    assert sp.canonical_successor(["superseded-by:bbbbbbbb", "superseded-by:" + IDS[0]], idx) is None
    assert sp.canonical_successor(["supersedes-relation:bbbbbbbb"], idx) is None
    assert sp.canonical_successor(["supersedes:bbbbbbbb"], idx) is None
    assert sp.canonical_successor(["superseded-by:aaaaaaaa"], idx) is None  # ambiguous
    assert sp.canonical_successor([], idx) is None


def test_chain_resolves_to_a_terminal_record():
    ids = ["11111111-1111-4111-8111-111111111111",
           "22222222-2222-4222-8222-222222222222",
           "33333333-3333-4333-8333-333333333333"]
    tags = {ids[0]: ["superseded-by:22222222"],
            ids[1]: ["superseded-by:33333333"],
            ids[2]: ["status:done"]}
    terminal, steps, problem = sp.resolve_chain(tags, sp.RecordIndex(ids), ids[0])
    assert (terminal, steps, problem) == (ids[2], 2, None)


def test_self_pointer_and_cycle_are_reported_not_looped():
    ids = ["11111111-1111-4111-8111-111111111111",
           "22222222-2222-4222-8222-222222222222"]
    self_tags = {ids[0]: ["superseded-by:11111111-1111-4111-8111-111111111111"]}
    assert sp.resolve_chain(self_tags, sp.RecordIndex(ids), ids[0])[2] == sp.R_SELF
    cycle = {ids[0]: ["superseded-by:22222222"], ids[1]: ["superseded-by:11111111"]}
    assert sp.resolve_chain(cycle, sp.RecordIndex(ids), ids[0])[2] == "ptr_cycle"


def test_audit_reports_the_status_invariant_in_both_directions():
    ids = ["11111111-1111-4111-8111-111111111111",
           "22222222-2222-4222-8222-222222222222",
           "33333333-3333-4333-8333-333333333333"]
    rows = [
        sp.AuditRow(ids[0], "superseded without pointer", ["status:superseded"]),
        sp.AuditRow(ids[1], "pointer without status", ["superseded-by:33333333", "status:done"]),
        sp.AuditRow(ids[2], "terminal", ["status:done"]),
    ]
    audit = sp.audit_rows(rows, sp.RecordIndex(ids + [ids[1]]))
    assert [r["id"] for r in audit.status_without_pointer] == [ids[0]]
    assert [r["id"] for r in audit.pointer_without_status] == [ids[1]]
    # accepted exceptions: neither direction is a finding (§12.3)
    assert audit.ok is True


def test_audit_counts_multi_pointer_and_self_and_cycle_rows():
    ids = ["11111111-1111-4111-8111-111111111111",
           "22222222-2222-4222-8222-222222222222",
           "33333333-3333-4333-8333-333333333333",
           "44444444-4444-4444-8444-444444444444"]
    rows = [
        sp.AuditRow(ids[0], "two successors",
                    ["superseded-by:22222222", "superseded-by:33333333"]),
        sp.AuditRow(ids[1], "points at itself", ["superseded-by:" + ids[1]]),
        sp.AuditRow(ids[2], "cycle partner of 44444444", ["superseded-by:44444444"]),
        sp.AuditRow(ids[3], "cycle partner of 33333333", ["superseded-by:33333333"]),
    ]
    audit = sp.audit_rows(rows, sp.RecordIndex(ids))
    assert len(audit.multi_pointer_rows) == 1
    assert len(audit.self_pointers) == 1
    assert len(audit.cycles) >= 1


# ── the control corpus: every live value classifies as the ruling says ────

def fixture_index():
    """An id set whose prefix structure reproduces every measured hit count."""
    ids = []
    for data in FAMILIES.values():
        for v in data["values"]:
            ids.extend(v["hits"])
    return sp.RecordIndex(sorted(set(ids)))


def test_every_census_value_classifies_to_its_expected_reason():
    idx = fixture_index()
    for family, data in FAMILIES.items():
        for v in data["values"]:
            c = sp.classify_pointer(family, v["value"], idx)
            assert c.reason_code == v["expected_reason"], (family, v["value"])
            assert c.family_status == data["status"]
            if c.reason_code in (sp.R_RESOLVED, sp.R_DEPRECATED):
                assert c.target == v["expected_target"], (family, v["value"])
                assert c.canonical_form == v["canonical_form"], (family, v["value"])
            else:
                assert c.target is None


def test_census_family_totals_are_352_76_159():
    assert FAMILIES[RATIFIED]["occurrences"] == 352
    assert FAMILIES[RATIFIED]["rows"] == 350
    assert FAMILIES[UNRATIFIED]["occurrences"] == 76
    assert FAMILIES[UNRATIFIED]["rows"] == 70
    assert FAMILIES[DEPRECATED]["occurrences"] == 159
    assert FAMILIES[DEPRECATED]["rows"] == 159


def test_superseded_by_is_320_prefix_occurrences_and_32_full_uuids():
    values = FAMILIES[RATIFIED]["values"]
    prefix = sum(v["count"] for v in values
                 if v["shape"] == "hex" and len(v["value"]) == 8)
    uuid = sum(v["count"] for v in values if v["shape"] == "uuid")
    assert (prefix, uuid) == (320, 32)
    assert len(values) == 148
    assert all(v["expected_reason"] == sp.R_RESOLVED for v in values)


def test_supersedes_splits_into_66_resolved_8_prose_1_empty_1_ambiguous():
    reasons = FAMILIES[UNRATIFIED]["expected_reasons"]
    assert reasons == {sp.R_RESOLVED: 66, sp.R_UNRESOLVED: 8,
                       sp.R_MALFORMED: 1, sp.R_AMBIGUOUS: 1}
    empty = [v for v in FAMILIES[UNRATIFIED]["values"] if v["value"] == ""]
    assert len(empty) == 1 and empty[0]["hit_count"] == CENSUS["total_records"]
    collision = [v for v in FAMILIES[UNRATIFIED]["values"] if v["value"] == "767"]
    assert len(collision) == 1 and collision[0]["hit_count"] == 8


def test_supersedes_relation_is_159_deprecated_resolutions():
    reasons = FAMILIES[DEPRECATED]["expected_reasons"]
    assert reasons == {sp.R_DEPRECATED: 159}


def test_audit_over_the_control_corpus_reproduces_the_census():
    audit = sp.audit_rows(rows_from_families(), classifier=fixture_classifier())
    summary = audit.summary()
    assert summary["occurrences"] == 587
    assert summary["by_family"] == {RATIFIED: 352, UNRATIFIED: 76, DEPRECATED: 159}
    assert summary["reasons"] == {sp.R_RESOLVED: 418, sp.R_DEPRECATED: 159,
                                  sp.R_MALFORMED: 1, sp.R_AMBIGUOUS: 1,
                                  sp.R_UNRESOLVED: 8}
    assert summary["unclassified"] == 0
    assert summary["ok"] is True
    assert summary["checks"]["zero_unclassified"] is True
    assert summary["checks"]["zero_ratified_anomalies"] is True
    assert summary["deprecated_resolved"] == 159
    assert summary["unratified_nonid"] == 10        # 8 prose + 1 empty + 1 ambiguous
    assert summary["family_statuses"] == {sp.FAMILY_RATIFIED: 352,
                                          sp.FAMILY_DEPRECATED: 159,
                                          sp.FAMILY_UNRATIFIED: 76}


def test_strict_promotes_unratified_nonid_but_not_the_ratified_family():
    rows = rows_from_families()
    assert sp.audit_rows(rows, classifier=fixture_classifier()).summary()["ok"] is True
    strict = sp.audit_rows(rows, classifier=fixture_classifier()).summary(strict=True)
    assert strict["ok"] is False
    assert strict["checks"]["zero_unratified_nonid"] is False
    assert strict["checks"]["zero_ratified_anomalies"] is True


def test_report_writes_one_line_per_occurrence():
    audit = sp.audit_rows(rows_from_families(), classifier=fixture_classifier())
    lines = sp.report_tsv(audit).strip().splitlines()
    assert len(lines) == 1 + 587
    assert lines[0].startswith("record_id\tfamily\traw_tag\treason")


def test_fixture_records_the_measured_defect_counts():
    assert DEFECTS["status_superseded_rows"] == 163
    assert len(DEFECTS["status_superseded_without_pointer"]) == 3
    assert DEFECTS["superseded_by_rows"] == 350
    assert DEFECTS["superseded_by_without_status"] == 190
    assert len(DEFECTS["superseded_by_without_status_other_status"]) == 3
    assert len(DEFECTS["multi_pointer_rows"]) == 2
    assert DEFECTS["chains_count"] == 43
    assert DEFECTS["chains_exact_uuid"] == 17
    assert len(DEFECTS["freeform"]) == 13
    assert DEFECTS["self_pointer"] == [] and DEFECTS["cycles"] == []


def test_defect_rows_are_reproduced_from_the_fixture():
    no_ptr = [sp.AuditRow(r["id"], r["title"], r["tags"])
              for r in DEFECTS["status_superseded_without_pointer"]]
    other = [sp.AuditRow(r["id"], r["title"], r["tags"])
             for r in DEFECTS["superseded_by_without_status_other_status"]]
    multi = [sp.AuditRow(r["id"], r["title"], r["tags"])
             for r in DEFECTS["multi_pointer_rows"]]
    ids = {row.id for row in no_ptr + other + multi}
    ids |= {t.split(":", 1)[1] for row in no_ptr + other + multi
            for t in row.tags if t.startswith("superseded-by:")}
    audit = sp.audit_rows(no_ptr + other + multi, sp.RecordIndex(ids))
    assert len(audit.status_without_pointer) == 3
    # 3 rows carrying another status + the 2 multi-pointer rows: none of the
    # five carries status:superseded, and that is accepted, not a finding.
    assert len(audit.pointer_without_status) == 5
    assert {row.id for row in other} <= {e["id"] for e in audit.pointer_without_status}
    assert len(audit.multi_pointer_rows) == 2


def test_freeform_tags_are_inventoried_and_untouched():
    tags = DEFECTS["freeform"]
    rows = [sp.AuditRow(f"row-{i}", "freeform", [tag]) for i, tag in enumerate(tags)]
    audit = sp.audit_rows(rows, sp.RecordIndex([r.id for r in rows]))
    assert len(audit.freeform) == 13
    assert audit.occurrences == 0            # not pointers, not counted as such
    assert audit.ok is True


# ── writer contract (ruling §12.7) ───────────────────────────────────────

def test_require_pointer_for_write_normalises_to_the_canonical_prefix():
    idx = sp.RecordIndex(IDS)
    assert sp.require_pointer_for_write(IDS[2], idx) == "superseded-by:bbbbbbbb"


@pytest.mark.parametrize("value,reason", [
    ("", sp.R_MALFORMED),
    (" ", sp.R_MALFORMED),
    ("ABCDEF12", sp.R_MALFORMED),
    ("pr-669", sp.R_UNRESOLVED),
    ("deadbeef", sp.R_UNRESOLVED),
    ("aaaaaaaa", sp.R_AMBIGUOUS),
])
def test_require_pointer_for_write_refuses_instead_of_guessing(value, reason):
    idx = sp.RecordIndex(IDS)
    with pytest.raises(sp.PointerWriteRefused) as exc:
        sp.require_pointer_for_write(value, idx)
    assert exc.value.reason_code == reason


def test_require_pointer_for_write_refuses_a_self_pointer():
    idx = sp.RecordIndex(IDS)
    with pytest.raises(sp.PointerWriteRefused) as exc:
        sp.require_pointer_for_write(IDS[0], idx, subject_id=IDS[0])
    assert exc.value.reason_code == sp.R_SELF


# ── CLI ──────────────────────────────────────────────────────────────────

def test_cli_exit_codes(monkeypatch, capsys):
    # A resolved pointer, a pointerless status row, and (for the failing run)
    # one dangling pointer.
    ids = ["aaaa0000-1111-4111-8111-111111111111",
           "bbbb0000-2222-4222-8222-222222222222"]
    ok_rows = [
        sp.AuditRow(ids[0], "superseded by the other", ["superseded-by:bbbb0000"]),
        sp.AuditRow(ids[1], "terminal", ["status:done"]),
        sp.AuditRow("cccc0000-3333-4333-8333-333333333333", "pointerless status",
                    ["status:superseded"]),
    ]
    bad_rows = ok_rows + [sp.AuditRow("dddd0000-4444-4444-8444-444444444444",
                                     "dangling", ["superseded-by:deadbeef"])]
    all_ids = ids + ["cccc0000-3333-4333-8333-333333333333",
                     "dddd0000-4444-4444-8444-444444444444"]
    assert sp.main(["--audit"], load=lambda _d: ok_rows,
                   load_ids=lambda _d: sp.RecordIndex(all_ids)) == 0
    assert sp.main(["--audit", "--json"], load=lambda _d: bad_rows,
                   load_ids=lambda _d: sp.RecordIndex(all_ids)) == 1
    assert sp.main(["--audit", "--strict"], load=lambda _d: ok_rows,
                   load_ids=lambda _d: sp.RecordIndex(all_ids)) == 0
    assert sp.main(["--audit"], load=lambda _d: (_ for _ in ()).throw(RuntimeError("down")),
                   load_ids=lambda _d: sp.RecordIndex([])) == 2
    capsys.readouterr()


# ── live corpus (skipped when the database is unavailable) ───────────────

def _live_available() -> bool:
    try:
        sp.load_index().ids[:1]
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(os.environ.get("SKIP_LIVE_AUDIT") == "1" or not _live_available(),
                    reason="nebula corpus not reachable from this host")
def test_live_audit_reproduces_the_census():
    rows = sp.load_rows()
    index = sp.load_index()
    audit = sp.audit_rows(rows, index)
    summary = audit.summary()
    assert summary["occurrences"] == 587
    assert summary["by_family"][RATIFIED] == 352
    assert summary["by_family"][UNRATIFIED] == 76
    assert summary["by_family"][DEPRECATED] == 159
    assert summary["reasons"][sp.R_RESOLVED] == 418
    assert summary["reasons"][sp.R_DEPRECATED] == 159
    assert summary["reasons"][sp.R_UNRESOLVED] == 8
    assert summary["reasons"][sp.R_MALFORMED] == 1
    assert summary["reasons"][sp.R_AMBIGUOUS] == 1
    assert summary["status_superseded_rows"] == 163
    assert len(summary["status_without_pointer"]) == 3
    assert len(summary["pointer_without_status"]) == 190
    assert len(summary["multi_pointer_rows"]) == 2
    assert summary["chains_count"] == 43
    assert summary["chains_exact_uuid"] == 17
    assert summary["freeform_count"] == 13
    assert summary["unclassified"] == 0
    assert summary["ok"] is True
