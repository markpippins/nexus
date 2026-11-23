"""Hermetic tests for bin/metadata_namespace_tripwire.py.

The tripwire exists because the API guard only covers POST/PATCH: a migration,
a repair script, `psql`, or a future service writing SQL directly can put a
legacy tool-owned metadata key back into `nebula.agent_records` and nothing in
the request path would notice.

The **control** is explicit here: a corpus holding each legacy spelling must be
detected (this is what proves the tripwire is not a decoration). A clean corpus
must pass, and the verdict/exit-code/state contract is pinned.

    python3 -m pytest bin/tests/test_metadata_namespace_tripwire.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import metadata_namespace_normalize as norm  # noqa: E402
import metadata_namespace_tripwire as tw  # noqa: E402


def rows(*metadata):
    return [(f"{i:08d}-0000-0000-0000-000000000000", "tester", md)
            for i, md in enumerate(metadata)]


# ── the control: detection ───────────────────────────────────────────────

def test_control_detects_every_legacy_spelling():
    """CONTROL: one synthetic row per legacy key — every one must be found."""
    corpus = rows(*[{legacy: "x"} for legacy in tw.LEGACY_ENFORCED])
    found = tw.find_legacy(corpus)
    assert len(found) == len(tw.LEGACY_ENFORCED)
    assert {f["legacy_key"] for f in found} == set(tw.LEGACY_ENFORCED)
    for f in found:
        assert f["canonical_key"] == tw.LEGACY_ENFORCED[f["legacy_key"]]


def test_control_names_the_record_role_and_canonical_target():
    found = tw.find_legacy([("rec-1", "dba", {"retired": {"at": "x"}, "pr": 1})])
    assert found == [{
        "record_id": "rec-1",
        "role": "dba",
        "legacy_key": "retired",
        "canonical_key": "record.retired",
    }]


def test_control_one_record_with_several_legacy_keys_yields_one_finding_each():
    found = tw.find_legacy(rows({"retired": 1, "snapshot_purpose": "p", "corrections": []}))
    assert {f["legacy_key"] for f in found} == {"retired", "snapshot_purpose", "corrections"}


# ── clean corpus / non-findings ──────────────────────────────────────────

def test_clean_corpus_has_no_findings():
    assert tw.find_legacy(rows(
        {"record.retired": {"at": "x"}, "correction.claims": []},
        {"pr": 1049, "branch": "x", "verdict": "approved"},
        {},
        {"snapshot.purpose": "pre-fk-migration-evidence"},
    )) == []


def test_conditional_generic_keys_are_not_flagged():
    """`source_id` alone is not proof of a legacy writer: the API guard lets it
    through, so the tripwire must not call it a violation."""
    assert tw.find_legacy(rows({"source_id": "whatever", "source_table": "t"})) == []


def test_registry_matches_the_api_guard_exactly():
    """The tripwire's enforced set must be the set the write path refuses.

    Parse the TypeScript guard literally: if someone adds a legacy key there
    without adding it here (or vice versa), the two drift apart and one side
    silently stops protecting the corpus.
    """
    src = (HERE.parent.parent / "typescript" / "nebula-srv" / "src" /
           "metadata-namespace.ts").read_text()
    block = src.split("LEGACY_OWNED_KEYS", 1)[1].split("});", 1)[0]
    ts_keys = set(re.findall(r"^\s*(\w+):\s*'", block, re.M))
    assert ts_keys, "could not parse the guard's legacy key set"
    assert ts_keys == set(tw.LEGACY_ENFORCED)


# ── evidence digest / episode dedupe ─────────────────────────────────────

def test_digest_is_order_independent_and_value_independent():
    a = [{"record_id": "a", "legacy_key": "retired", "canonical_key": "record.retired", "role": ""}]
    b = [{"record_id": "a", "legacy_key": "retired", "canonical_key": "record.retired", "role": ""}]
    assert tw.evidence_digest(a) == tw.evidence_digest(b)
    c = [{"record_id": "b", "legacy_key": "retired", "canonical_key": "record.retired", "role": ""}]
    assert tw.evidence_digest(a) != tw.evidence_digest(c)


# ── CLI contract ─────────────────────────────────────────────────────────

def _clean_fetch(_dsn, _limit=None):
    return rows({"record.retired": {"at": "x"}}, {"pr": 1})


def _dirty_fetch(_dsn, _limit=None):
    return rows({"retired": {"at": "x"}}, {"snapshot_purpose": "p"})


def test_main_clean_exits_zero_and_says_clean(tmp_path, capsys):
    code = tw.main(["--state-dir", str(tmp_path)], fetch=_clean_fetch)
    assert code == 0
    out = capsys.readouterr()
    assert "CLEAN" in out.out
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["verdict"] == "CLEAN" and state["findings"] == 0


def test_main_fails_loudly_on_a_legacy_key(tmp_path, capsys):
    code = tw.main(["--state-dir", str(tmp_path), "--dry-run"], fetch=_dirty_fetch)
    assert code == 1
    err = capsys.readouterr().err
    assert "LEGACY METADATA KEYS PRESENT" in err
    assert "retired -> record.retired" in err
    assert "snapshot_purpose -> snapshot.purpose" in err


def test_json_verdict_shape(tmp_path, capsys):
    code = tw.main(["--json", "--state-dir", str(tmp_path), "--dry-run"], fetch=_dirty_fetch)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "LEGACY_KEYS_PRESENT"
    assert payload["findings"] == 2 and payload["records"] == 2
    assert {f["legacy_key"] for f in payload["detail"]} == {"retired", "snapshot_purpose"}


def test_main_reports_a_tool_error_as_exit_two(tmp_path, capsys):
    def boom(_dsn, _limit=None):
        raise RuntimeError("connection refused")

    assert tw.main(["--state-dir", str(tmp_path)], fetch=boom) == 2
    assert "cannot read the corpus" in capsys.readouterr().err


def test_post_fires_once_per_episode_not_every_run(tmp_path, capsys):
    posted = []
    fetch = _dirty_fetch
    tw.main(["--post", "--state-dir", str(tmp_path)], fetch=fetch, post=posted.append)
    tw.main(["--post", "--state-dir", str(tmp_path)], fetch=fetch, post=posted.append)
    assert len(posted) == 1, "a persisting violation must not spam the change-log"

    def new_fetch(_dsn, _limit=None):
        return rows({"retired": 1}, {"corrections": []})

    tw.main(["--post", "--state-dir", str(tmp_path)], fetch=new_fetch, post=posted.append)
    assert len(posted) == 2, "a new evidence set is a new episode"


def test_dry_run_writes_no_state(tmp_path, capsys):
    tw.main(["--dry-run", "--post", "--state-dir", str(tmp_path)], fetch=_dirty_fetch,
            post=lambda _f: pytest.fail("dry run must not post"))
    assert not (tmp_path / "state.json").exists()


def test_clean_run_after_an_episode_restores_clean_state(tmp_path):
    tw.main(["--state-dir", str(tmp_path)], fetch=_dirty_fetch)
    tw.main(["--state-dir", str(tmp_path)], fetch=_clean_fetch)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["verdict"] == "CLEAN" and state["findings"] == 0


def test_limit_is_forwarded_to_the_fetch(tmp_path):
    seen = {}

    def fetch(dsn, limit=None):
        seen["limit"] = limit
        return []

    tw.main(["--limit", "5", "--state-dir", str(tmp_path), "--dry-run"], fetch=fetch)
    assert seen["limit"] == 5


def test_enforced_set_is_the_direct_registry(tmp_path):
    assert tw.LEGACY_ENFORCED == {legacy: canonical for canonical, legacy in norm.DIRECT.items()}
