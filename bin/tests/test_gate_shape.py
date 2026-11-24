"""Hermetic tests for bin/gate_shape.py (ranking item 10, ruling 4963dee4).

The control test is `test_control_corpus_classifies_every_value_and_names_holds`:
one synthetic corpus carries every shape edge the ruling names — a PR, a
leading-zero PR, a name whose value begins with digits (`830-scope`), an
uppercase name (`G0`), a coherent named row, a named row still missing its
slug, a numeric row still missing its `pr:`, a dual-claim conflict, an orphan
`gate-name:` tag and a numeric `gate-name:` slug — and asserts each is
classified with the exact reason code, that repair targets are reported but do
not fail the audit, and that shape holds/orphans do fail it. Zero values are
left unclassified.

    python3 -m pytest bin/tests/test_gate_shape.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate_shape as gs  # noqa: E402


# ── the classifier, value by value ────────────────────────────────────────

@pytest.mark.parametrize("value,slug", [
    ("1000", None),
    ("1", None),
    ("0830", None),          # leading zeros: still PR shape (fork 2)
    ("830-scope", "830-scope"),   # numeric PREFIX, not numeric value (fork 1)
    ("G0", "g0"),
    ("stage-2", "stage-2"),
    ("admission", "admission"),
    ("g0", "g0"),
])
def test_pr_and_name_shapes(value, slug):
    shape = gs.classify_gate_value(value)
    if slug is None:
        assert shape.verdict == gs.PR
        assert shape.reason_code == gs.R_PR
        assert shape.slug is None
    else:
        assert shape.verdict == gs.NAME
        assert shape.reason_code == gs.R_NAME
        assert shape.slug == slug


@pytest.mark.parametrize("value,reason", [
    ("", gs.R_EMPTY),
    (None, gs.R_EMPTY),
    (" 830", gs.R_MALFORMED),
    ("830 ", gs.R_MALFORMED),
    ("8 30", gs.R_MALFORMED),
    ("stage 2", gs.R_MALFORMED),
])
def test_held_values(value, reason):
    shape = gs.classify_gate_value(value)
    assert shape.verdict == gs.HELD
    assert shape.reason_code == reason
    assert shape.slug is None


def test_dual_claim_is_held_not_resolved():
    tags = ["gate:830", "gate-name:830"]
    shape = gs.classify_row_gate("830", tags)
    assert shape.verdict == gs.HELD
    assert shape.reason_code == gs.R_CONFLICT
    # and the plain PR row is not affected
    assert gs.classify_row_gate("830", ["gate:830"]).verdict == gs.PR


def test_conflict_is_exact_not_case_folded():
    # gate-name:0830 names the same value; gate-name:830 is a *different* slug
    # and does not make the PR value a dual claim.
    assert gs.classify_row_gate("830", ["gate:830", "gate-name:830"]).is_held
    assert gs.classify_row_gate("830", ["gate:830", "gate-name:0830"]).is_pr


def test_classify_row_keeps_tag_order_and_classifies_each():
    tags = ["type:approval", "gate:835", "gate:836", "gate:stage-2", "gate-name:stage-2"]
    shapes = gs.classify_row(tags)
    assert [s.value for s in shapes] == ["835", "836", "stage-2"]
    assert [s.verdict for s in shapes] == [gs.PR, gs.PR, gs.NAME]


def test_resolve_gate_pr_never_coerces_a_name():
    assert gs.resolve_gate_pr("1000") == (1000, gs.R_PR)
    assert gs.resolve_gate_pr("0830") == (830, gs.R_PR)
    assert gs.resolve_gate_pr("830-scope") == (None, gs.R_NAME)
    assert gs.resolve_gate_pr("G0") == (None, gs.R_NAME)
    assert gs.resolve_gate_pr("830", ["gate:830", "gate-name:830"]) == (None, gs.R_CONFLICT)
    assert gs.resolve_gate_pr("") == (None, gs.R_EMPTY)


def test_gate_value_and_slug_extraction():
    tags = ["gate:G0", "gate-name:g0", "pr:1006", "gate:1006"]
    assert gs.gate_values(tags) == ["G0", "1006"]
    assert gs.gate_name_slugs(tags) == ["g0"]


# ── the control corpus ────────────────────────────────────────────────────

def arow(rid, tags, title="a record"):
    return gs.AuditRow(rid, title, list(tags))


def coherent_only_corpus():
    return [
        arow("pr-coherent", ["gate:1000", "pr:1000"], "attestation for #1000"),
        arow("pr-leading-zero", ["gate:0830", "pr:830"], "attestation for #830"),
        arow("name-coherent", ["gate:stage-2", "gate-name:stage-2"]),
        arow("name-uppercase", ["gate:G0", "gate-name:g0"]),
        arow("no-gate", ["pr:1001", "type:approval"]),
    ]


def test_control_corpus_classifies_every_value_and_names_holds():
    corpus = coherent_only_corpus() + [
        arow("name-missing-slug", ["gate:tester"]),
        arow("pr-missing-pr", ["gate:1002"]),
        arow("dual-claim", ["gate:1003", "gate-name:1003"]),
        arow("orphan-name", ["gate:passed", "gate-name:tester"]),
        arow("numeric-slug", ["gate:stage-2", "gate-name:stage-2", "gate-name:77"]),
        arow("malformed", ["gate: 830"]),
        arow("empty", ["gate:"]),
    ]
    audit = gs.audit_rows(corpus)

    # every value got a verdict: nothing is unclassified
    assert audit.unclassified == 0
    assert audit.gate_occurrences == sum(audit.verdicts.values())
    assert audit.gate_rows == 11

    # PR values: 1000, 0830, 1002, 1003 (dual claim is held, not PR)
    assert audit.verdicts[gs.PR] == 3
    # NAMEs: stage-2, G0, tester, passed, stage-2(again)
    assert audit.verdicts[gs.NAME] == 5
    # HELDs: dual claim, malformed, empty
    assert audit.verdicts[gs.HELD] == 3
    assert audit.reasons[gs.R_CONFLICT] == 1
    assert audit.reasons[gs.R_MALFORMED] == 1
    assert audit.reasons[gs.R_EMPTY] == 1

    # repair targets are reported but do not by themselves fail the audit
    assert [i["value"] for i in audit.named_without_slug] == ["tester", "passed"]
    assert [i["value"] for i in audit.numeric_without_pr] == ["1002"]
    # shape holds, orphans and numeric slugs do fail it, each named
    assert [h["reason"] for h in audit.held_rows] == [gs.R_CONFLICT, gs.R_MALFORMED, gs.R_EMPTY]
    assert [i["slug"] for i in audit.orphan_gate_names] == ["tester"]
    # the dual claim IS a numeric gate-name tag, so it is reported twice: once
    # as the held shape conflict, once as the numeric-slug fact itself
    assert [i["slug"] for i in audit.numeric_name_slugs] == ["1003", "77"]
    assert audit.ok is False
    summary = audit.summary()
    assert summary["unclassified_values"] == 0
    assert summary["shape_holds"] == 3
    assert summary["checks"] == {
        "zero_unclassified": True,
        "zero_shape_holds": False,
        "zero_orphan_gate_names": False,
        "zero_numeric_name_slugs": False,
    }


def test_coherent_corpus_is_clean_even_with_repair_targets_pending():
    audit = gs.audit_rows(coherent_only_corpus())
    assert audit.ok is True
    assert audit.unclassified == 0
    assert audit.shape_holds == 0
    assert audit.held_rows == []
    assert audit.verdicts == {gs.PR: 2, gs.NAME: 2, gs.HELD: 0}
    # a not-yet-applied item-9/8 repair is a target, not an audit failure
    pending = gs.audit_rows(coherent_only_corpus() + [arow("pending", ["gate:lease"])])
    assert pending.ok is True
    assert [i["value"] for i in pending.named_without_slug] == ["lease"]


def test_audit_never_treats_named_gate_as_pr():
    audit = gs.audit_rows([arow("named", ["gate:830-scope", "gate-name:830-scope"])])
    assert audit.verdicts[gs.NAME] == 1
    assert audit.numeric_without_pr == []
    assert audit.ok is True


def test_cli_audit_exit_codes(monkeypatch, capsys):
    rows = coherent_only_corpus()
    assert gs.main(["--audit"], load=lambda _dsn: rows) == 0
    bad = rows + [arow("dual", ["gate:1003", "gate-name:1003"])]
    assert gs.main(["--audit", "--json"], load=lambda _dsn: bad) == 1
    out = capsys.readouterr().out
    assert '"ok": false' in out
    assert gs.main(["--audit"], load=lambda _dsn: (_ for _ in ()).throw(RuntimeError("down"))) == 2
