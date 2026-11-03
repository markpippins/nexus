#!/usr/bin/env python3
"""Hermetic tests for bin/vendor_attest.py.

No gh, no HTTP, no DB, no network: every external call goes through an
injected `runner` (subprocess.run-shaped) and `run_json`; the attestation
record body is read from the temp file while the runner still has it open,
so the posted payload is asserted, not inferred.

Pins the policy rails from the module docstring (operator ruling 2026-11-03):
  - diff allowlist: manifests/lockfiles only, never a governing path
  - no semver-major, and unclassifiable updates fail closed
  - CI must be green at the head, using merge_pr's own rules
  - every cited run is re-verified (conclusion + head SHA binding)
  - head is minted by pgie-evidence, never hand-typed
  - check-only default posts nothing; the gate passing already means skip
  - the tool never merges: there is no `gh pr merge` call anywhere
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent
sys.path.insert(0, str(BIN))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vendor = _load("vendor_attest", "vendor_attest.py")
janitor = _load("attestation_janitor", "attestation_janitor.py")

SHA = "abc1234" + "d" * 33          # headRefOid
HEAD_TAG = "head:" + SHA[:7]
RUN_ID = "36642067144"
PATCH_BODY = (
    "Bumps [qs](https://github.com/ljharb/qs) from 6.16.0 to 6.16.2.\n"
    "<details>\n<summary>Commits</summary>\n</details>\n"
    "---\n"
    "updated-dependencies:\n"
    "- dependency-name: qs\n"
    "  dependency-version: 6.16.2\n"
    "  dependency-type: direct:production\n"
    "  update-type: version-update:semver-patch\n"
    "  dependency-group: security-updates\n"
    "---\n"
)
LOCKFILE = "typescript/execution-srv/package-lock.json"


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def gate_report(passes=(), fails=()):
    lines = ["merge gate for PR #1:"]
    lines += [f"  [PASS] {p}" for p in passes]
    lines += [f"  [FAIL] {f}" for f in fails]
    lines.append("  => GATE FAILURE -- merge refused" if fails else "  => ALL GATES PASS")
    if not fails:
        lines.append("check-only mode: no action taken (use --merge to squash-merge)")
    return "\n".join(lines) + "\n"


GATE_MISSING = gate_report(
    passes=[
        "pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
        "ci green: all 4 checks completed successfully",
    ],
    fails=["tester attestation (ATT_MISSING): no attestation-shaped tester record"],
)
GATE_ATTESTED = gate_report(
    passes=[
        "pr open & ready: state=OPEN draft=False mergeable=MERGEABLE head=abc",
        "ci green: all 4 checks completed successfully",
        "tester attestation: record postdates head",
    ]
)


def rollup(n=2, conclusion="SUCCESS", url_run=RUN_ID):
    return [
        {
            "name": f"check-{i}",
            "status": "COMPLETED",
            "conclusion": conclusion,
            "detailsUrl": f"https://github.com/o/r/actions/runs/{url_run}/job/{100 + i}",
        }
        for i in range(n)
    ]


def pr_payload(
    files=(LOCKFILE,),
    body=PATCH_BODY,
    author="app/dependabot",
    draft=False,
    mergeable="MERGEABLE",
    checks=None,
):
    return {
        "number": 1,
        "title": "chore(deps): bump qs from 6.16.0 to 6.16.2 in /typescript/execution-srv",
        "body": body,
        "author": {"login": author},
        "headRefOid": SHA,
        "isDraft": draft,
        "mergeable": mergeable,
        "files": [{"path": p, "additions": 3, "deletions": 3} for p in files],
        "statusCheckRollup": rollup() if checks is None else checks,
    }


class World:
    """Everything vendor_attest can talk to, faked and recorded."""

    def __init__(self, pr, gate=GATE_MISSING, mint=HEAD_TAG, run_data=None):
        self.pr = pr
        self.gate = gate
        self.mint = mint
        self.run_data = run_data or {}
        self.calls = []
        self.posts = []
        self.post_body = ""
        self.repo = "markpippins/nexus"
        self.runs = [
            {"databaseId": int(RUN_ID), "status": "completed", "conclusion": "success"}
        ]

    # subprocess.run-shaped
    def __call__(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        joined = " ".join(argv)
        if "merge_pr.py" in joined:
            return FakeResult(0, self.gate)
        if "mint-head" in joined:
            return FakeResult(0, self.mint + "\n")
        if "post-agent-record.py" in joined:
            self.posts.append(argv)
            if "--file" in argv:
                self.post_body = Path(argv[argv.index("--file") + 1]).read_text()
            return FakeResult(0, "OK 201 record_id=deadbeef")
        if argv[:3] == ["gh", "pr", "view"]:
            return FakeResult(0, json.dumps(self.pr))
        if argv[:3] == ["gh", "pr", "list"]:
            return FakeResult(0, json.dumps([{"number": 1, "headRefOid": SHA}]))
        if argv[:2] == ["gh", "api"]:
            rid = argv[2].rsplit("/", 1)[-1]
            return FakeResult(
                0, json.dumps(self.run_data.get(rid, {"c": "success", "s": SHA}))
            )
        if argv[:3] == ["gh", "repo", "view"]:
            return FakeResult(0, json.dumps({"nameWithOwner": self.repo}))
        if argv[:3] == ["gh", "run", "list"]:
            return FakeResult(0, json.dumps(self.runs))
        raise AssertionError(f"unexpected command: {argv}")

    # merge_pr.verify_ci_runs-shaped
    def run_json(self, *args):
        rid = " ".join(str(a) for a in args).split("/actions/runs/")[1].split()[0]
        return self.run_data.get(rid, {"c": "success", "s": SHA})

    def merged(self):
        return [c for c in self.calls if "pr" in c and "merge" in c]


def run(pr=None, apply=True, gate=GATE_MISSING, **kw):
    world = World(pr or pr_payload(), gate=gate, **kw)
    outcome = vendor.attest_pr(
        1, apply=apply, runner=world, run_json=world.run_json
    )
    return outcome, world


# ── diff allowlist ──────────────────────────────────────────────────────

def test_lockfile_and_manifest_diff_is_vendor_only():
    ok, _ = vendor.classify_paths([LOCKFILE, "typescript/execution-srv/package.json"])
    assert ok


def test_requirements_variants_are_vendor_only():
    ok, _ = vendor.classify_paths(["python/fs/fs-crawler/requirements-dev.txt"])
    assert ok


def test_workflow_path_refuses_the_whole_pr():
    ok, detail = vendor.classify_paths([LOCKFILE, ".github/workflows/ci.yml"])
    assert not ok and "governing path" in detail


def test_bin_path_refuses_the_whole_pr():
    ok, detail = vendor.classify_paths([LOCKFILE, "bin/merge_pr.py"])
    assert not ok and "governing path" in detail


def test_sql_path_refuses_the_whole_pr():
    ok, _ = vendor.classify_paths(["sql/migrations/999-whatever.sql"])
    assert not ok


def test_source_file_refuses_the_whole_pr():
    ok, detail = vendor.classify_paths(["typescript/execution-srv/src/index.ts"])
    assert not ok and "not a dependency manifest" in detail


def test_manifest_basename_under_governing_dir_still_refuses():
    ok, _ = vendor.classify_paths([".github/package.json"])
    assert not ok


def test_empty_file_list_fails_closed():
    ok, _ = vendor.classify_paths([])
    assert not ok


# ── update classification ───────────────────────────────────────────────

def test_patch_update_is_allowed():
    ok, detail, _ = vendor.classify_update(PATCH_BODY)
    assert ok and "semver-patch" in detail


def test_minor_update_is_allowed():
    ok, _, _ = vendor.classify_update("update-type: version-update:semver-minor")
    assert ok


def test_grouped_patch_updates_are_allowed():
    body = PATCH_BODY + PATCH_BODY.replace("qs", "body-parser")
    ok, _, kinds = vendor.classify_update(body)
    assert ok and len(kinds) == 2


def test_major_update_is_refused():
    ok, detail, _ = vendor.classify_update(
        "update-type: version-update:semver-major"
    )
    assert not ok and "semver-major" in detail


def test_one_major_in_a_grouped_pr_refuses_the_whole_pr():
    body = PATCH_BODY + "  update-type: version-update:semver-major\n"
    ok, _, _ = vendor.classify_update(body)
    assert not ok


def test_unknown_update_type_is_refused():
    ok, detail, _ = vendor.classify_update("update-type: version-update:semver-whatever")
    assert not ok and "unrecognised" in detail


def test_unclassifiable_update_fails_closed():
    ok, detail, _ = vendor.classify_update("chore: bump some things")
    assert not ok and "unclassified" in detail


def test_title_pair_fallback_allows_same_major():
    ok, detail, _ = vendor.classify_update(
        "", "chore(deps): bump qs from 6.16.0 to 6.16.2 in /typescript/execution-srv"
    )
    assert ok and "same-major" in detail


def test_title_pair_fallback_refuses_major():
    ok, detail, _ = vendor.classify_update(
        "", "chore(deps): bump @adonisjs/core from 6.21.0 to 7.5.2 in /adonisjs/x"
    )
    assert not ok and "major version change" in detail


def test_security_update_body_pair_is_read_from_the_updates_line():
    # The security-update body format carries no update-type metadata.
    ok, detail, _ = vendor.classify_update("Updates `qs` from 6.16.0 to 6.16.2")
    assert ok and "same-major" in detail


def test_updates_line_major_is_refused():
    ok, detail, _ = vendor.classify_update("Updates `pillow` from 12.3.0 to 13.0.0")
    assert not ok and "major version change" in detail


def test_embedded_changelog_does_not_refuse_a_legitimate_pr():
    # Regression for #823: the dependency's own CHANGELOG is pasted into the
    # PR body, and a blanket "from X to Y" scan read an unrelated upstream
    # major as if it were this update.
    body = (
        "Updates `pymongo` from 4.6.3 to 4.18.3\n"
        "<li>Bump pillow from 12.3.0 to 13.0.0 by @dependabot in #2675</li>\n"
        "<li>Bump astral-sh/setup-uv from 7.1.6 to 7.2.0 in the actions group</li>\n"
    )
    ok, detail, _ = vendor.classify_update(body, "chore(deps): bump the pip group")
    assert ok and "same-major" in detail


def test_dependency_names_are_extracted():
    assert vendor.dependency_names(PATCH_BODY) == ["qs"]


def test_run_ids_fall_back_to_run_list_when_the_rollup_has_no_urls():
    # #788 shape: green rollup, but no Actions details URL to harvest.
    checks = [
        {"name": f"c{i}", "status": "COMPLETED", "conclusion": "SUCCESS"}
        for i in range(3)
    ]
    outcome, world = run(pr=pr_payload(checks=checks))
    assert outcome == "posted"
    assert f"CI run {RUN_ID}" in world.post_body


# ── evidence ────────────────────────────────────────────────────────────

def test_cited_run_ids_are_deduped_per_run():
    checks = rollup(4)  # four checks, one workflow run
    assert vendor.cited_run_ids(checks) == [RUN_ID]


def test_cited_run_ids_skip_failed_checks():
    checks = rollup(2, conclusion="FAILURE")
    assert vendor.cited_run_ids(checks) == []


def test_cited_run_ids_are_capped():
    checks = [
        {
            "name": f"c{i}",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "detailsUrl": f"https://github.com/o/r/actions/runs/{4000000000 + i}/job/1",
        }
        for i in range(40)
    ]
    assert len(vendor.cited_run_ids(checks)) == vendor.MAX_CITED_RUNS


def test_verify_rejects_a_run_bound_to_another_head():
    world = World(pr_payload(), run_data={RUN_ID: {"c": "success", "s": "f" * 40}})
    outcome = vendor.attest_pr(1, apply=True, runner=world, run_json=world.run_json)
    assert outcome == "refused" and not world.posts


def test_verify_rejects_a_non_success_run():
    world = World(pr_payload(), run_data={RUN_ID: {"c": "failure", "s": SHA}})
    outcome = vendor.attest_pr(1, apply=True, runner=world, run_json=world.run_json)
    assert outcome == "refused" and not world.posts


# ── the attestation itself ──────────────────────────────────────────────

def test_posts_an_evidence_bearing_attestation():
    outcome, world = run()
    assert outcome == "posted"
    assert len(world.posts) == 1
    argv = world.posts[0]
    assert argv[argv.index("--role") + 1] == "tester"
    assert argv[argv.index("--record-type") + 1] == "assessment"
    tags = argv[argv.index("--tags") + 1].split(",")
    assert "type:attestation" in tags
    assert "pr:1" in tags
    assert HEAD_TAG in tags
    assert "type:vendor-bump" in tags
    # The gate parses `CI run <8+ digits>`; the body must carry the real id.
    assert f"CI run {RUN_ID}" in world.post_body
    assert HEAD_TAG.split(":")[1] in world.post_body
    assert "vendor-bump/v1" in world.post_body


def test_the_tool_never_merges():
    _, world = run()
    assert world.merged() == []
    assert not any("--merge" in c for c in world.calls)


def test_check_only_posts_nothing():
    outcome, world = run(apply=False)
    assert outcome == "would-post" and not world.posts


def test_skips_when_the_gate_already_passes_gate_three():
    outcome, world = run(gate=GATE_ATTESTED)
    assert outcome == "attested" and not world.posts


def test_draft_is_not_attested():
    outcome, world = run(pr=pr_payload(draft=True))
    assert outcome == "refused" and not world.posts


def test_conflicting_branch_is_not_attested():
    outcome, world = run(pr=pr_payload(mergeable="CONFLICTING"))
    assert outcome == "refused" and not world.posts


def test_non_vendor_author_is_not_attested():
    outcome, world = run(pr=pr_payload(author="markpippins"))
    assert outcome == "refused" and not world.posts


def test_major_bump_pr_is_refused_end_to_end():
    body = "Bump @adonisjs/core from 6.21.0 to 7.5.2\nupdate-type: version-update:semver-major"
    outcome, world = run(pr=pr_payload(body=body))
    assert outcome == "refused" and not world.posts


def test_workflow_touching_pr_is_refused_end_to_end():
    pr = pr_payload(files=(LOCKFILE, ".github/workflows/ci.yml"))
    outcome, world = run(pr=pr)
    assert outcome == "refused" and not world.posts


def test_red_ci_is_not_attested():
    outcome, world = run(pr=pr_payload(checks=rollup(2, conclusion="FAILURE")))
    assert outcome == "refused" and not world.posts


def test_pending_ci_is_not_attested():
    checks = rollup(1)
    checks[0]["status"] = "IN_PROGRESS"
    checks[0]["conclusion"] = ""
    outcome, world = run(pr=pr_payload(checks=checks))
    assert outcome == "refused" and not world.posts


def test_empty_rollup_is_not_attested():
    outcome, world = run(pr=pr_payload(checks=[]))
    assert outcome == "refused" and not world.posts


def test_bad_head_tag_from_the_minter_refuses():
    outcome, world = run(mint="abc1234")
    assert outcome == "error" and not world.posts


def test_post_failure_is_reported_as_a_tool_error():
    world = World(pr_payload())

    def runner(argv, **kwargs):
        argv = [str(a) for a in argv]
        if "post-agent-record.py" in " ".join(argv):
            return FakeResult(1, "", "ERROR HTTP 500: boom")
        return world(argv, **kwargs)

    outcome = vendor.attest_pr(1, apply=True, runner=runner, run_json=world.run_json)
    assert outcome == "error"


# ── cycle ───────────────────────────────────────────────────────────────

def test_cycle_attests_and_respects_the_cap():
    world = World(pr_payload())
    rc = vendor.run_cycle(apply=True, cap=1, author="app/dependabot", runner=world)
    assert rc == 0 and len(world.posts) == 1


def test_cycle_resolves_the_repo_and_passes_it_to_the_minter():
    # mint-head cannot resolve OWNER/REPO from an R8.0 worktree on its own.
    world = World(pr_payload())
    vendor.run_cycle(apply=True, cap=1, author="app/dependabot", runner=world)
    mint = [c for c in world.calls if "mint-head" in " ".join(c)][0]
    assert "--repo" in mint and "markpippins/nexus" in mint


def test_cycle_discovery_failure_is_a_tool_error():
    def runner(argv, **kwargs):
        return FakeResult(1, "", "boom")

    rc = vendor.run_cycle(apply=True, cap=1, author="app/dependabot", runner=runner)
    assert rc == 1


# ── the janitor contract this tool depends on ───────────────────────────

def test_janitor_discovers_a_union_of_authors_without_duplicates():
    def runner(argv, **kwargs):
        argv = [str(a) for a in argv]
        author = argv[argv.index("--author") + 1]
        rows = [
            {"number": 10, "isDraft": False, "headRefOid": "x", "title": ""},
            {"number": 11 if author == "app/dependabot" else 12,
             "isDraft": False, "headRefOid": "y", "title": ""},
        ]
        return FakeResult(0, json.dumps(rows))

    prs = janitor.discover_open_prs(["markpippins", "app/dependabot"], runner)
    # 10 appears under both authors: deduped once, and the union is ordered.
    assert [p["number"] for p in prs] == [10, 11, 12]


def test_janitor_discovery_still_accepts_a_single_author_string():
    def runner(argv, **kwargs):
        return FakeResult(0, json.dumps([{"number": 7, "isDraft": False,
                                          "headRefOid": "x", "title": ""}]))

    assert [p["number"] for p in janitor.discover_open_prs("markpippins", runner)] == [7]


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors as failures
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
