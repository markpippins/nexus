"""Hermetic tests for bin/harness_job_backfill.py — no I/O, no network.

Item 5 attributes historical agent records to the harness run that wrote them.
The column is what `bin/check_harness_silent_success.py` uses to decide whether
a run left a record, so a WRONG id is worse than a null: it clears a silent run.
These tests pin the two properties that keep that from happening:

  1. ATTRIBUTION — only a declaration by the record itself counts (metadata,
     tag, or its own prose), and only when the named run is real, of the same
     role, and was running when the record was written.
  2. REFUSAL — a run merely being in flight (window_only) is never enough; the
     row stays null and the report names the reason per row.

The write path is exercised with fake transport so the verification contract
(read-back must show the id, the body must not change) is pinned without a
database.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "harness_job_backfill.py"
spec = importlib.util.spec_from_file_location("harness_job_backfill", BIN)
backfill = importlib.util.module_from_spec(spec)
sys.modules["harness_job_backfill"] = backfill
spec.loader.exec_module(backfill)

JOB = "11111111-1111-4111-8111-111111111111"
OTHER_JOB = "22222222-2222-4222-8222-222222222222"
UNKNOWN = "33333333-3333-4333-8333-333333333333"
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def job(job_id=JOB, role="engineer", start=T0, minutes=20):
    return backfill.Job(job_id=job_id, role=role, started=start,
                        ended=start + timedelta(minutes=minutes))


def rec(rid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", role="engineer",
        at=T0 + timedelta(minutes=5), title="R2 done", content="body",
        tags=None, metadata=None):
    return backfill.Record(id=rid, role=role, created_at=at, title=title,
                           content=content, tags=list(tags or []),
                           metadata=dict(metadata or {}))


def decide(record, jobs=None, cited=None):
    jobs = jobs if jobs is not None else [job()]
    by_id = {j.job_id: j for j in jobs}
    first = min((j.started for j in jobs), default=None)
    return backfill.classify(record, jobs, by_id, first, cited)


# ── attribution: the record declares its own run ─────────────────────────

def test_metadata_declaration_attributes():
    d = decide(rec(metadata={"harness_job_id": JOB}))
    assert d.attributed and d.reason == "declared_in_metadata" and d.job_id == JOB


def test_tag_declaration_attributes():
    d = decide(rec(tags=["type:status-update", f"job:{JOB}"]))
    assert d.attributed and d.reason == "declared_in_tags" and d.job_id == JOB


def test_text_declarations_attribute():
    for phrase in (f"Ran under harness job {JOB}.", f"HARNESS_JOB_ID={JOB}",
                   f"harness job `{JOB}`", f"harness_job_id: {JOB}"):
        d = decide(rec(content=phrase))
        assert d.attributed, phrase
        assert d.reason == "declared_in_text"
        assert d.job_id == JOB


def test_uppercase_declaration_is_normalized():
    d = decide(rec(content=f"harness job {JOB.upper()}"))
    assert d.attributed and d.job_id == JOB


def test_strongest_class_wins_when_declared_twice():
    d = decide(rec(content=f"harness job {JOB}", tags=[f"job:{JOB}"],
                   metadata={"harness_job_id": JOB}))
    assert d.attributed and d.reason == "declared_in_metadata"


# ── refusal: each reason, one per row ───────────────────────────────────

def test_role_mismatch_is_refused():
    # The real corpus' dominant declaration shape: an engineer record naming a
    # tester run it read. Attribution here would be a lie about authorship.
    d = decide(rec(role="engineer", content=f"the harness job {OTHER_JOB} posted the attestation"),
               jobs=[job(job_id=OTHER_JOB, role="tester")])
    assert not d.attributed and d.reason == "declared_role_mismatch"
    assert "run_role=tester" in d.detail


def test_outside_window_is_refused():
    d = decide(rec(at=T0 + timedelta(hours=6), content=f"harness job {JOB}"),
               jobs=[job()])
    assert not d.attributed and d.reason == "declared_outside_window"


def test_grace_window_covers_post_run_record():
    d = decide(rec(at=T0 + timedelta(minutes=20, seconds=120), content=f"harness job {JOB}"),
               jobs=[job()])
    assert d.attributed


def test_unknown_job_is_refused():
    d = decide(rec(content=f"harness job {UNKNOWN}"))
    assert not d.attributed and d.reason == "declares_unknown_job"


def test_multiple_declarations_are_refused():
    d = decide(rec(content=f"harness job {JOB} and harness job {OTHER_JOB}"),
               jobs=[job(), job(job_id=OTHER_JOB)])
    assert not d.attributed and d.reason == "declares_multiple_jobs"


def test_window_only_is_refused_never_guessed():
    d = decide(rec(), jobs=[job()], cited={})
    assert not d.attributed and d.reason == "window_only"
    assert JOB[:8] in d.detail


def test_window_only_records_a_corroborating_citation_as_advisory_only():
    r = rec()
    d = decide(r, jobs=[job()], cited={r.id: [JOB]})
    assert not d.attributed and d.reason == "window_only"
    assert "cited_in_job_log=" + JOB[:8] in d.detail


def test_ambiguous_window_is_refused():
    jobs = [job(), job(job_id=OTHER_JOB)]
    d = decide(rec(), jobs=jobs)
    assert not d.attributed and d.reason == "ambiguous_window"


def test_no_window_is_refused():
    d = decide(rec(at=T0 + timedelta(hours=9)), jobs=[job()])
    assert not d.attributed and d.reason == "no_harness_window"


def test_predates_harness_is_refused_before_anything_else():
    d = decide(rec(at=T0 - timedelta(days=30)), jobs=[job()])
    assert not d.attributed and d.reason == "predates_harness"


def test_open_run_window_is_unbounded_but_still_requires_declaration():
    open_job = backfill.Job(job_id=JOB, role="engineer", started=T0, ended=None)
    assert decide(rec(at=T0 + timedelta(hours=3)), jobs=[open_job]).reason == "window_only"
    assert decide(rec(at=T0 + timedelta(hours=3), content=f"harness job {JOB}"),
                  jobs=[open_job]).attributed


# ── report ──────────────────────────────────────────────────────────────

def test_every_null_row_gets_exactly_one_reason_and_counts_reconcile():
    records = [
        rec(rid="a" * 8 + "-aaaa-4aaa-8aaa-aaaaaaaaaaaa", metadata={"harness_job_id": JOB}),
        rec(rid="b" * 8 + "-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        rec(rid="c" * 8 + "-aaaa-4aaa-8aaa-aaaaaaaaaaaa", role="planner"),
    ]
    decisions = backfill.build_plan(records, [job()])
    summary = backfill.summarize(decisions, total_records=10)
    assert summary["reconciles"]
    assert summary["already_attributed"] == 7
    assert summary["null_rows"] == 3
    assert summary["attributable"] == 1 and summary["refused"] == 2
    assert summary["reasons"] == {"no_harness_window": 1, "window_only": 1}
    assert len(decisions) == 3
    for d in decisions:
        assert d.reason in backfill.REFUSED or d.reason in backfill.DECLARED


def test_report_tsv_has_a_line_per_row_with_its_reason():
    decisions = backfill.build_plan([rec(), rec(content=f"harness job {UNKNOWN}")], [job()])
    lines = backfill.report_tsv(decisions).strip().split("\n")
    assert lines[0].startswith("id\trole\tcreated_at\treason")
    assert len(lines) == 3
    assert "\twindow_only\t" in lines[1]
    assert "\tdeclares_unknown_job\t" in lines[2]


# ── write path: verified, idempotent, bounded ───────────────────────────

class Api:
    """Fake nebula: PATCH fills a null id, GET reads the row back."""

    def __init__(self, rows, fail_patch=None, lie_on_read=False, mutate_body=False):
        self.rows = rows
        self.fail_patch = fail_patch or set()
        self.lie_on_read = lie_on_read
        self.mutate_body = mutate_body
        self.patches = []

    def write(self, base, rid, job_id):
        self.patches.append((rid, job_id))
        if rid in self.fail_patch:
            return 500, {"error": "boom"}
        self.rows[rid]["harnessJobId"] = job_id
        if self.mutate_body:
            self.rows[rid]["content"] = "changed"
        return 200, self.rows[rid]

    def fetch(self, base, rid):
        row = dict(self.rows[rid])
        if self.lie_on_read:
            row["harnessJobId"] = None
        return row


def test_apply_verifies_each_write_by_read_back():
    r = rec(metadata={"harness_job_id": JOB})
    decisions = backfill.build_plan([r], [job()])
    api = Api({r.id: {"harnessJobId": None, "content": r.content}})
    result = backfill.apply_plan(decisions, "http://x", 10, fetch=api.fetch, write=api.write)
    assert result["verified"] == [r.id] and not result["failed"]
    assert api.patches == [(r.id, JOB)]


def test_apply_treats_a_read_back_mismatch_as_failure():
    r = rec(metadata={"harness_job_id": JOB})
    decisions = backfill.build_plan([r], [job()])
    api = Api({r.id: {"harnessJobId": None, "content": r.content}}, lie_on_read=True)
    result = backfill.apply_plan(decisions, "http://x", 10, fetch=api.fetch, write=api.write)
    assert result["verified"] == []
    assert "read-back" in result["failed"][0]["error"]


def test_apply_reports_http_failures_instead_of_claiming_success():
    r = rec(metadata={"harness_job_id": JOB})
    decisions = backfill.build_plan([r], [job()])
    api = Api({r.id: {"harnessJobId": None, "content": r.content}}, fail_patch={r.id})
    result = backfill.apply_plan(decisions, "http://x", 10, fetch=api.fetch, write=api.write)
    assert result["verified"] == [] and "HTTP 500" in result["failed"][0]["error"]


def test_apply_refuses_a_write_that_changes_the_body():
    r = rec(metadata={"harness_job_id": JOB})
    decisions = backfill.build_plan([r], [job()])
    api = Api({r.id: {"harnessJobId": None, "content": r.content}}, mutate_body=True)
    result = backfill.apply_plan(decisions, "http://x", 10, fetch=api.fetch, write=api.write)
    assert result["verified"] == [] and "body changed" in result["failed"][0]["error"]


def test_rerun_is_a_no_op_because_only_null_rows_are_planned():
    r = rec(metadata={"harness_job_id": JOB})
    jobs = [job()]
    first = backfill.build_plan([r], jobs)
    assert [d.attributed for d in first] == [True]
    # After the write the row is no longer null, so the next scan never sees it.
    assert backfill.build_plan([], jobs) == []


def test_apply_is_bounded():
    records = [rec(rid=f"{i:08d}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                   metadata={"harness_job_id": JOB}) for i in range(3)]
    decisions = backfill.build_plan(records, [job()])
    try:
        backfill.apply_plan(decisions, "http://x", max_apply=2,
                            fetch=lambda b, r: {}, write=lambda b, r, j: (200, {}))
    except RuntimeError as exc:
        assert "exceeds --max-apply" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected the bound to refuse an oversized plan")


def test_non_attributable_rows_are_never_written():
    r = rec()  # window_only
    decisions = backfill.build_plan([r], [job()])
    api = Api({r.id: {"harnessJobId": None, "content": r.content}})
    result = backfill.apply_plan(decisions, "http://x", 10, fetch=api.fetch, write=api.write)
    assert result["planned"] == 0 and api.patches == []
