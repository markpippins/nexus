"""Hermetic tests for bin/status_vocab.py (ranking item 11, ruling 963501f2).

The control corpus is `bin/tests/fixtures/status_vocab_control.json`: the live
census of all 141 distinct `status:` values with their occurrence counts. Every
value must classify, the five ratified aliases must map, the eight ambiguous
values must stay HELD, and the near-miss families must stay distinct. The audit
must account for every occurrence: canonical + alias + held == total, with no
value unclassified and no tag dropped.

    python3 -m pytest bin/tests/test_status_vocab.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import status_vocab as sv  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "status_vocab_control.json"
CENSUS = json.loads(FIXTURE.read_text())
VALUES = [(v["value"], v["count"]) for v in CENSUS["values"]]


def rows_from_census():
    """One AuditRow per occurrence, so audit counts equal the census counts."""
    rows = []
    for value, count in VALUES:
        for i in range(count):
            rows.append(sv.AuditRow(f"{value}-{i}", f"record with status:{value}",
                                    [f"status:{value}"]))
    return rows


# ── the ratified alias table ──────────────────────────────────────────────

@pytest.mark.parametrize("alias,canonical,kind", [
    ("complete", "done", "equivalence"),
    ("completed", "done", "equivalence"),
    ("in_progress", "in-progress", "format"),
    ("inprogress", "in-progress", "format"),
    ("review-request", "review-requested", "format"),
])
def test_alias_table_maps_exactly(alias, canonical, kind):
    c = sv.classify_status_value(alias)
    assert c.verdict == sv.ALIAS
    assert c.reason_code == sv.R_ALIAS
    assert c.canonical == "status:" + canonical
    assert c.family == canonical
    assert c.alias_kind == kind


def test_alias_table_is_the_only_collapse():
    assert set(sv.ALIASES) == {"complete", "completed", "in_progress", "inprogress",
                               "review-request"}
    assert sv.ALIASES["complete"] == ("done", "equivalence")


def test_abbreviation_pairs_are_not_collapsed():
    for pair in [("ack", "acknowledged"), ("info", "information"),
                 ("decided", "decision-made"), ("needs-answer", "needs-response"),
                 ("proposal", "proposed"), ("action-needed", "action-required")]:
        a, b = (sv.classify_status_value(v) for v in pair)
        assert a.verdict == sv.CANONICAL and b.verdict == sv.CANONICAL
        assert a.canonical != b.canonical, pair


# ── near-misses must stay distinct ────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("closed", "closed-partial"),
    ("done", "scope-complete"),
    ("resolved", "partially-resolved"),
    ("approved", "approved-within-scope"),
    ("ratified", "ratified-with-conditions"),
    ("blocked", "blocked-infra"),
    ("blocked-needs-work", "blocked-needs-rebase"),
    ("held", "hold"),
    ("correction", "corrected"),
    ("reviewed", "review-requested"),
    ("awaiting-review", "awaiting-ratification"),
    ("verified", "verified-with-owed-items"),
    ("superseded", "retired"),
])
def test_near_misses_do_not_collapse(a, b):
    ca, cb = sv.classify_status_value(a), sv.classify_status_value(b)
    assert ca.canonical == "status:" + a
    assert cb.canonical == "status:" + b
    assert ca.canonical != cb.canonical


@pytest.mark.parametrize("value", [
    "conditional", "disposition", "standing", "ok", "partial", "review",
    "advanced", "mixed",
])
def test_ambiguous_values_are_held_with_a_reason(value):
    c = sv.classify_status_value(value)
    assert c.verdict == sv.HELD
    assert c.reason_code == sv.R_AMBIGUOUS
    assert c.canonical is None
    assert isinstance(sv.AMBIGUOUS[value], str) and sv.AMBIGUOUS[value]


@pytest.mark.parametrize("value,reason", [
    ("", sv.R_MALFORMED),
    (" ", sv.R_MALFORMED),
    ("done ", sv.R_MALFORMED),
    ("never-seen-before", sv.R_UNKNOWN),
    ("Done", sv.R_UNKNOWN),        # case is not folded: not a ratified alias
])
def test_malformed_and_unknown_are_held(value, reason):
    c = sv.classify_status_value(value)
    assert c.verdict == sv.HELD
    assert c.reason_code == reason


# ── the control corpus: every census value classifies ─────────────────────

def test_every_census_value_has_a_verdict_and_the_counts_match_the_ruling():
    assert len(VALUES) == 141
    classes = {v: sv.classify_status_value(v) for v, _ in VALUES}
    verdicts = {sv.CANONICAL: 0, sv.ALIAS: 0, sv.HELD: 0}
    occurrences = {sv.CANONICAL: 0, sv.ALIAS: 0, sv.HELD: 0}
    for value, count in VALUES:
        verdicts[classes[value].verdict] += 1
        occurrences[classes[value].verdict] += count
    # ruling 963501f2 §11.8: 128 canonical + 5 alias + 8 held = 141
    assert verdicts == {sv.CANONICAL: 128, sv.ALIAS: 5, sv.HELD: 8}
    assert occurrences[sv.ALIAS] == 342
    assert occurrences[sv.HELD] == 32
    assert occurrences[sv.CANONICAL] == CENSUS["total_occurrences"] - 342 - 32


def test_audit_accounts_for_every_occurrence():
    audit = sv.audit_rows(rows_from_census())
    summary = audit.summary()
    assert summary["occurrences"] == CENSUS["total_occurrences"] == 3479
    assert summary["status_rows"] == 3479
    assert summary["unclassified"] == 0
    assert summary["checks"] == {
        "zero_unclassified": True, "zero_unknown": True, "zero_malformed": True,
    }
    assert sum(summary["verdicts"].values()) == summary["occurrences"]
    assert summary["verdict_values"] == {sv.CANONICAL: 128, sv.ALIAS: 5, sv.HELD: 8}
    # family totals fixed by the ruling
    assert summary["families"]["done"] == 832
    assert summary["families"]["in-progress"] == 103
    assert summary["families"]["review-requested"] == 4
    # held is a named class, reported per occurrence, never rewritten
    assert len(summary["held"]) == 32
    assert {h["value"] for h in summary["held"]} == set(sv.AMBIGUOUS)
    assert summary["ok"] is True


def test_strict_makes_held_fatal_but_non_strict_does_not():
    rows = rows_from_census()
    assert sv.audit_rows(rows).summary()["ok"] is True
    strict = sv.audit_rows(rows).summary(strict=True)
    assert strict["ok"] is False
    assert strict["checks"]["zero_held"] is False


def test_report_accounts_one_line_per_occurrence():
    audit = sv.audit_rows(rows_from_census())
    lines = sv.report_tsv(audit).strip().splitlines()
    assert len(lines) == 1 + 3479
    assert lines[0].startswith("record_id\traw_status_tag\tverdict")


# ── canonical_status contract and writer refusal ─────────────────────────

def test_canonical_status_contract():
    assert sv.canonical_status([]) is None
    assert sv.canonical_status(["type:approval"]) is None
    assert sv.canonical_status(["status:done"]) == "status:done"
    assert sv.canonical_status(["status:complete"]) == "status:done"
    assert sv.canonical_status(["status:completed"]) == "status:done"
    assert sv.canonical_status(["status:conditional"]) is None
    assert sv.canonical_status(["status:done", "status:closed"]) is None
    # an alias and its canonical target are ONE target, not multiplicity
    assert sv.canonical_status(["status:done", "status:complete"]) == "status:done"


def test_require_canonical_for_write_refuses_instead_of_guessing():
    assert sv.require_canonical_for_write("done") == "status:done"
    for value, reason in [("complete", sv.R_ALIAS), ("in_progress", sv.R_ALIAS),
                          ("conditional", sv.R_AMBIGUOUS), ("never-seen", sv.R_UNKNOWN),
                          ("", sv.R_MALFORMED)]:
        with pytest.raises(sv.StatusWriteRefused) as exc:
            sv.require_canonical_for_write(value)
        assert exc.value.reason_code == reason


def test_classify_tag_ignores_other_namespaces():
    assert sv.classify_tag("type:approval") is None
    assert sv.classify_tag("gate:1000") is None
    assert sv.classify_tag("status:done").canonical == "status:done"
    assert sv.is_canonical_status("status:done") is True
    assert sv.is_canonical_status("status:complete") is False


def test_cli_exit_codes(monkeypatch, capsys):
    rows = rows_from_census()
    assert sv.main(["--audit"], load=lambda _dsn: rows) == 0
    assert sv.main(["--audit", "--strict"], load=lambda _dsn: rows) == 1
    bad = rows + [sv.AuditRow("x", "new", ["status:brand-new-status"])]
    assert sv.main(["--audit", "--json"], load=lambda _dsn: bad) == 1
    assert '"ok": false' in capsys.readouterr().out
    assert sv.main(["--audit"], load=lambda _dsn: (_ for _ in ()).throw(RuntimeError("down"))) == 2
