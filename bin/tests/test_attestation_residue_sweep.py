"""Hermetic tests for bin/attestation_residue_sweep.py — no I/O, no network.

The sweep is the repair side of duplicate tester attestations: it retires the
stragglers of a (pr, head) group under the newest row, following Decision 24
Amendment 1. These tests pin the two things that could destroy records:

  1. SCOPE — a row is only touchable when it is a live, tester,
     attestation-shaped row with BOTH a PR tag and a well-formed head tag.
     Head-less rows (including the 679/681 shared pair, which is tagged with
     two PR numbers and no head) are unkeyable and must never be selected.
  2. SHAPE — retirement drops every status:*, adds exactly one status:retired,
     prefixes the title once, preserves all other tags and metadata, names the
     successor, and never touches the body.
"""

import importlib.util
import json
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "attestation_residue_sweep.py"
spec = importlib.util.spec_from_file_location("attestation_residue_sweep", BIN)
sweep = importlib.util.module_from_spec(spec)
sys.modules["attestation_residue_sweep"] = sweep
spec.loader.exec_module(sweep)

AT = "2026-11-23T13:00:00Z"
HEAD = "1fe3f28490b801c46abf041147c0ffee12345678"


def row(rid, pr=1029, head=HEAD, created=1000, role="tester",
        record_type="assessment", tags=None, title="Tester attestation PR 1029",
        content="body text", metadata=None):
    if tags is None:
        tags = ["type:approval", "status:done"]
        if pr is not None:
            tags.append(f"pr:{pr}")
        if head is not None:
            tags.append(f"head:{head}")
    return {
        "id": rid,
        "role": role,
        "recordType": record_type,
        "createdAt": created,
        "title": title,
        "content": content,
        "tags": list(tags),
        "metadata": dict(metadata or {}),
    }


class Store:
    """In-memory record store with the two endpoints the sweep uses."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r, tags=list(r["tags"]),
                                   metadata=dict(r.get("metadata") or {}))
                     for r in rows}
        self.patches = []

    def fetch_point(self, rid):
        if rid not in self.rows:
            raise RuntimeError(f"404 {rid}")
        return dict(self.rows[rid], tags=list(self.rows[rid]["tags"]),
                    metadata=dict(self.rows[rid].get("metadata") or {}))

    def patch(self, rid, payload):
        self.patches.append((rid, payload))
        current = self.rows[rid]
        for key in ("title", "tags", "metadata"):
            if key in payload:
                current[key] = payload[key]

    def snapshots(self):
        return {rid: (r["title"], r["content"], tuple(r["tags"]), json.dumps(r.get("metadata"), sort_keys=True))
                for rid, r in self.rows.items()}


# ── predicates ────────────────────────────────────────────────────────────

def test_head_and_pr_tag_parsing():
    assert sweep.head_tag(["head:ABC1234"]) == "abc1234"
    assert sweep.head_tag(["head:"]) is None
    assert sweep.head_tag([]) is None
    assert sweep.pr_number(["pr:1029"]) == 1029
    assert sweep.pr_number(["gate:1044"]) == 1044
    assert sweep.pr_number(["pi:1044"]) is None


def test_attestation_shape_canonical_and_legacy():
    assert sweep.is_attestation_shaped({"recordType": "assessment",
                                        "tags": ["type:approval", "status:done"]})
    assert sweep.is_attestation_shaped({"recordType": "engineering_log",
                                        "tags": ["type:attestation", "status:done"]})
    assert not sweep.is_attestation_shaped({"recordType": "assessment",
                                            "tags": ["type:approval"]})
    assert not sweep.is_attestation_shaped({"recordType": "engineering_log",
                                            "tags": ["status:done"]})


def test_withdrawn_and_refused_liveness():
    assert sweep.is_live_attestation(row("a", tags=["type:attestation", "status:done", "pr:1", f"head:{HEAD}"]))
    # Refused IS live and IS a stray: it is not the answer, but it is not terminal.
    assert sweep.is_live_attestation(row("b", tags=["type:attestation", "status:refused",
                                                    "pr:1", f"head:{HEAD}"]))
    assert not sweep.is_live_attestation(row("c", tags=["type:attestation", "status:retired",
                                                       "pr:1", f"head:{HEAD}"]))
    assert not sweep.is_live_attestation(row("d", role="engineer"))


def test_keyable_requires_pr_and_wellformed_head():
    assert sweep.keyable(row("a")) == (1029, HEAD.lower())
    assert sweep.keyable(row("b", head=None)) is None                 # head-less
    assert sweep.keyable(row("c", pr=None)) is None                   # no PR tag
    assert sweep.keyable(row("d", head="1fe3")) is None               # 4 hex: malformed
    assert sweep.keyable(row("e", head="zzzzzzzz")) is None           # not hex
    assert sweep.keyable(row("f", tags=["type:attestation", "status:done", "pr:1029"])) is None


# ── grouping ──────────────────────────────────────────────────────────────

def test_group_same_pr_and_head_keeps_newest_as_survivor():
    older = row("old00001", created=1000)
    newer = row("new00001", created=2000)
    groups, unkeyable = sweep.duplicate_groups([older, newer])
    assert unkeyable == []
    assert len(groups) == 1
    assert groups[0]["pr"] == 1029
    assert groups[0]["survivor"]["id"] == "new00001"
    assert [r["id"] for r in groups[0]["strays"]] == ["old00001"]


def test_no_group_for_other_heads_or_other_prs():
    groups, _ = sweep.duplicate_groups([
        row("a0001", head=HEAD, created=1000),
        row("b0001", head="ffffffffffffffffffffffffffffffffffffffff", created=2000),
        row("c0001", pr=1044, created=3000),
    ])
    assert groups == []


def test_head_prefix_variants_group_together():
    """The gate binds by prefix (7-40 hex); so does the sweep, so a 7-hex tag
    and a 40-hex tag for one commit are the same group."""
    short = row("short001", head=HEAD[:7], created=1000)
    full = row("full0001", head=HEAD, created=2000)
    groups, _ = sweep.duplicate_groups([short, full])
    assert len(groups) == 1
    assert groups[0]["survivor"]["id"] == "full0001"
    assert [r["id"] for r in groups[0]["strays"]] == ["short001"]


def test_survivor_is_deterministic_on_equal_timestamps():
    a = row("aaaa0001", created=1000)
    b = row("bbbb0001", created=1000)
    groups, _ = sweep.duplicate_groups([b, a])
    assert groups[0]["survivor"]["id"] == "aaaa0001"
    assert [r["id"] for r in groups[0]["strays"]] == ["bbbb0001"]


def test_headless_rows_are_unkeyable_and_never_grouped():
    """The 679/681 shape: two rows, several PR tags, NO head tag anywhere."""
    partial = row("c79223b8", tags=["type:inspection", "type:attestation",
                                    "pr:580", "pr:679", "pr:681", "status:partial-open"],
                  created=1000, record_type="inspection")
    complete = row("8e65c047", tags=["type:inspection", "type:attestation",
                                     "pr:679", "pr:681", "status:attested-complete"],
                   created=2000, record_type="inspection")
    groups, unkeyable = sweep.duplicate_groups([partial, complete])
    assert groups == []
    assert {r["id"] for r in unkeyable} == {"c79223b8", "8e65c047"}


def test_withdrawn_and_non_attestation_rows_are_not_live():
    rows = [
        row("a0001", created=1000),
        row("b0001", created=2000),
        row("c0001", created=3000, tags=["type:approval", "status:retired", "pr:1029", f"head:{HEAD}"]),
        row("d0001", created=4000, tags=["type:status-update", "status:open", "pr:1029", f"head:{HEAD}"],
            record_type="engineering_log"),
    ]
    live = [r for r in rows if sweep.is_live_attestation(r)]
    assert [r["id"] for r in live] == ["a0001", "b0001"]
    groups, _ = sweep.duplicate_groups(rows)
    assert groups[0]["survivor"]["id"] == "b0001"


# ── retirement patch shape ────────────────────────────────────────────────

def test_patch_drops_all_status_tags_and_keeps_everything_else():
    r = row("a0001", tags=["type:approval", "status:done", "status:blocked",
                           "pr:1029", f"head:{HEAD}", "verdict:approve"])
    patch = sweep.retirement_patch(r, 1029, HEAD, "succ0001", AT)
    assert [t for t in patch["tags"] if t.startswith("status:")] == ["status:retired"]
    assert "type:approval" in patch["tags"]
    assert "verdict:approve" in patch["tags"]
    assert "pr:1029" in patch["tags"] and f"head:{HEAD}" in patch["tags"]


def test_patch_prefixes_title_once_and_names_successor():
    r = row("a0001", title="Tester attestation PR 1029")
    patch = sweep.retirement_patch(r, 1029, HEAD, "succ0001", AT)
    assert patch["title"] == "[RETIRED 2026-11-23] Tester attestation PR 1029"
    retired = patch["metadata"]["record.retired"]
    assert retired["note"].startswith("duplicate attestation for PR #1029 at head 1fe3f284")
    assert "succ0001" in retired["note"]
    assert retired["convention"].startswith("Decision 24")
    assert set(retired) == {"at", "note", "by", "performed_by", "convention"}


def test_patch_preserves_other_metadata_and_never_contains_a_body():
    r = row("a0001", metadata={"harness_job_id": "abc", "snapshot_purpose": "keep"})
    patch = sweep.retirement_patch(r, 1029, HEAD, "succ0001", AT)
    assert patch["metadata"]["snapshot_purpose"] == "keep"
    assert "content" not in patch  # retirement is a status transition only


def test_patch_is_noop_on_withdrawn_row():
    r = row("a0001", tags=["type:attestation", "status:retired", "pr:1029", f"head:{HEAD}"])
    assert sweep.retirement_patch(r, 1029, HEAD, "succ0001", AT) is None


# ── dry run / apply / idempotency ─────────────────────────────────────────

def test_dry_run_never_writes():
    store = Store([row("old00001", created=1000), row("new00001", created=2000)])
    before = store.snapshots()
    code, summary = sweep.run(
        list(store.rows.values()), apply=False, at_iso=AT,
        fetch_point=store.fetch_point, patch=store.patch)
    assert code == 0
    assert summary["actions"] and not summary["applied"]
    assert store.patches == []
    assert store.snapshots() == before


def test_apply_retires_only_strays_and_preserves_headless_rows():
    headless = row("8e65c047", tags=["type:inspection", "type:attestation",
                                     "pr:679", "pr:681", "status:attested-complete"],
                   record_type="inspection", created=500)
    store = Store([row("old00001", created=1000), row("new00001", created=2000), headless])
    headless_before = store.snapshots()["8e65c047"]

    code, summary = sweep.run(
        list(store.rows.values()), apply=True, at_iso=AT,
        fetch_point=store.fetch_point, patch=store.patch, post=None)
    assert code == 0
    assert [r["id"] for r in summary["results"] if r["ok"]] == ["old00001"]
    assert summary["protected_headless"] == 1

    retired = store.rows["old00001"]
    assert "status:retired" in retired["tags"]
    assert "status:done" not in retired["tags"]
    assert retired["title"].startswith("[RETIRED ")
    assert retired["content"] == "body text"                     # body untouched
    assert retired["metadata"]["record.retired"]["note"].endswith("(attestation-residue-sweep)")

    survivor = store.rows["new00001"]
    assert "status:done" in survivor["tags"] and "status:retired" not in survivor["tags"]
    assert store.snapshots()["8e65c047"] == headless_before        # guard held


def test_rerun_is_a_noop_after_apply():
    store = Store([row("old00001", created=1000), row("new00001", created=2000)])
    sweep.run(list(store.rows.values()), apply=True, at_iso=AT,
              fetch_point=store.fetch_point, patch=store.patch, post=None)
    patches_after_first = len(store.patches)

    code, summary = sweep.run(
        list(store.rows.values()), apply=True, at_iso=AT,
        fetch_point=store.fetch_point, patch=store.patch, post=None)
    assert code == 0
    assert summary["groups"] == [] and summary["actions"] == []
    assert len(store.patches) == patches_after_first              # zero new writes
    # The dry run now reports the same set as retired, not as duplicate.
    dry, dry_summary = sweep.run(
        list(store.rows.values()), apply=False, at_iso=AT,
        fetch_point=store.fetch_point, patch=store.patch)
    assert dry == 0 and dry_summary["groups"] == []


def test_failed_verification_is_reported_not_claimed():
    store = Store([row("old00001", created=1000), row("new00001", created=2000)])

    def bad_patch(rid, payload):
        store.patch(rid, payload)
        store.rows[rid]["content"] = "tampered"                   # body changed

    code, summary = sweep.run(list(store.rows.values()), apply=True, at_iso=AT,
                              fetch_point=store.fetch_point, patch=bad_patch, post=None)
    assert code == 1
    assert summary["results"][0]["ok"] is False
    assert "body changed" in summary["results"][0]["error"]


def test_max_retire_bounds_the_run():
    rows = [row(f"r{i:07d}", created=1000 + i) for i in range(6)]
    store = Store(rows)
    code, summary = sweep.run(list(store.rows.values()), apply=True, at_iso=AT,
                              fetch_point=store.fetch_point, patch=store.patch,
                              post=None, max_retire=3)
    assert code == 0
    assert len(store.patches) == 3


def test_multiple_groups_and_multiple_prs():
    rows = [
        row("a1", pr=1029, created=1000), row("a2", pr=1029, created=2000),
        row("b1", pr=1044, created=1000), row("b2", pr=1044, created=2000),
    ]
    store = Store(rows)
    code, summary = sweep.run(list(store.rows.values()), apply=True, at_iso=AT,
                              fetch_point=store.fetch_point, patch=store.patch, post=None)
    assert code == 0
    assert {(g["pr"], g["strays"][0]) for g in summary["groups"]} == {(1029, "a1"), (1044, "b1")}
    assert store.rows["a2"]["tags"].count("status:done") == 1
    assert store.rows["b2"]["tags"].count("status:done") == 1


def test_changelog_post_only_on_successful_retirement():
    posts = []
    store = Store([row("old00001", created=1000), row("new00001", created=2000)])
    sweep.run(list(store.rows.values()), apply=True, at_iso=AT,
              fetch_point=store.fetch_point, patch=store.patch,
              post=lambda t, b: (posts.append((t, b)) or True))
    assert len(posts) == 1
    assert "old00001" in posts[0][1] and "new00001" in posts[0][1]

    empty = Store([row("solo0001", created=1000)])
    sweep.run(list(empty.rows.values()), apply=True, at_iso=AT,
              fetch_point=empty.fetch_point, patch=empty.patch,
              post=lambda t, b: (posts.append((t, b)) or True))
    assert len(posts) == 1                                        # no new post


def test_report_names_groups_and_protected_rows():
    rows = [row("old00001", created=1000), row("new00001", created=2000),
            row("8e65c047", head=None, tags=["type:attestation", "status:done", "pr:679"])]
    groups, unkeyable = sweep.duplicate_groups(rows)
    text = sweep.format_report(3, [r for r in rows if sweep.is_live_attestation(r)],
                               groups, unkeyable, sweep.plan(groups, AT), None, False)
    assert "DRY RUN (no writes)" in text
    assert "GROUP PR #1029" in text and "survivor new00001" in text
    assert "protected: 1 head-less" in text
