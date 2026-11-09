"""wr-conf-019: fabricated-branch Git verification adapter.

AC1 proves a fabricated branch/commit is a known false claim and cannot be
reported as verified. AC2 proves the same read-only adapter accepts a real
in-scope descendant commit. AC3–AC6 cover wrong refs, commit mismatches,
unrelated history, and declared-path scope drift. AC7 proves verifier
infrastructure failure remains ``unavailable`` rather than being mislabeled as
a fabricated claim. AC8–AC10 cover content-addressed replay behavior,
boundary escapes, and claimant self-verification.

The adapter never mutates the fixture repository. Test setup is the only code
that creates temporary Git commits/refs, and every fixture is deleted by
TemporaryDirectory cleanup.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

_GUARD_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "git", doing="these tests build throwaway git repositories"
)

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_NEXUS_PYTHON = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", ".."))
if _NEXUS_PYTHON not in sys.path:
    sys.path.insert(0, _NEXUS_PYTHON)

from nexus_core.wrp.git_verifier import (  # noqa: E402
    GitVerificationAdapter,
    GitVerificationRequest,
    SubprocessGitRunner,
)


FIXED_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _fixture_repo() -> tuple[TemporaryDirectory, Path, str]:
    temp = TemporaryDirectory(prefix="wr-conf-019-")
    repo = Path(temp.name)
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "wr-conf-019@example.invalid")
    _git(repo, "config", "user.name", "wr-conf-019")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "--quiet", "-m", "baseline")
    _git(repo, "branch", "-M", "main")
    baseline = _git(repo, "rev-parse", "HEAD")
    return temp, repo, baseline


def _request(repo: Path, base: str, ref: str, commit: str, paths: tuple[str, ...]):
    return GitVerificationRequest(
        grant_id="grant-wr-conf-019",
        lease_id="lease-wr-conf-019",
        attempt_id="attempt-wr-conf-019",
        repository_root=repo,
        base_ref=base,
        claimed_ref=ref,
        claimed_commit=commit,
        declared_paths=paths,
        claimant_id="analyst/wr-conf-019",
        policy_hash="sha256:policy-wr-conf-019",
    )


def _assert_read_only(commands: list[tuple[str, ...]]) -> None:
    mutation_commands = {"add", "branch", "checkout", "commit", "fetch", "push", "reset", "switch", "worktree"}
    assert commands, "adapter must inspect Git"
    for command in commands:
        assert command[0] in {"rev-parse", "merge-base", "diff"}, command
        assert command[0] not in mutation_commands, command


class RecordingRunner:
    def __init__(self):
        self.commands: list[tuple[str, ...]] = []
        self._delegate = SubprocessGitRunner()

    def run(self, args, cwd, timeout_s):
        self.commands.append(tuple(args))
        return self._delegate.run(args, cwd, timeout_s)


class TimeoutRunner:
    def __init__(self):
        self.commands: list[tuple[str, ...]] = []

    def run(self, args, cwd, timeout_s):
        self.commands.append(tuple(args))
        raise subprocess.TimeoutExpired(["git", *args], timeout_s)


class TestAc1FabricatedBranch(unittest.TestCase):
    """A model-invented branch/ref must be rejected as known false."""

    def test_missing_branch_and_fake_commit_is_rejected(self):
        temp, repo, baseline = _fixture_repo()
        try:
            runner = RecordingRunner()
            adapter = GitVerificationAdapter(runner, clock=lambda: FIXED_TIME)
            evidence = adapter.verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/persistent-poller-poc",
                    "a" * 40,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "CLAIMED_REF_NOT_FOUND")
            self.assertFalse(evidence.ref_exists)
            self.assertFalse(evidence.commit_reachable)
            self.assertIsNone(evidence.resolved_commit)
            self.assertEqual(evidence.base_commit, baseline)
            self.assertEqual(evidence.evidence_kind, "git_ref_commit")
            self.assertTrue(evidence.evidence_id.startswith("git-evidence-v1:"))
            self.assertEqual(len(evidence.evidence_hash), 64)
            _assert_read_only(runner.commands)

            # A replay of identical facts is content-idempotent. The adapter
            # does not emit a receipt or mutate claim state.
            replay = adapter.verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/persistent-poller-poc",
                    "a" * 40,
                    ("README.md",),
                )
            )
            self.assertEqual(replay.evidence_id, evidence.evidence_id)
            self.assertEqual(replay.evidence_hash, evidence.evidence_hash)
        finally:
            temp.cleanup()


class TestAc2PositiveControl(unittest.TestCase):
    """A real in-scope branch and descendant commit verifies successfully."""

    def test_real_branch_commit_and_declared_change_is_verified(self):
        temp, repo, baseline = _fixture_repo()
        try:
            _git(repo, "branch", "lease/attempt-wr-conf-019")
            _git(repo, "switch", "lease/attempt-wr-conf-019")
            (repo / "README.md").write_text("baseline\nverified change\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "commit", "--quiet", "-m", "declared change")
            claimed_commit = _git(repo, "rev-parse", "HEAD")

            runner = RecordingRunner()
            adapter = GitVerificationAdapter(runner, clock=lambda: FIXED_TIME)
            evidence = adapter.verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/lease/attempt-wr-conf-019",
                    claimed_commit,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "verified")
            self.assertIsNone(evidence.reason)
            self.assertTrue(evidence.ref_exists)
            self.assertTrue(evidence.commit_reachable)
            self.assertTrue(evidence.scope_matches)
            self.assertEqual(evidence.resolved_commit, claimed_commit)
            self.assertEqual(evidence.base_commit, baseline)
            self.assertEqual(evidence.changed_paths, ("README.md",))
            self.assertEqual(evidence.undeclared_paths, ())
            self.assertTrue(evidence.verifier_independence)
            envelope = evidence.to_peb_admission_envelope(
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            )
            self.assertEqual(
                envelope["execution_context"]["grant_id"],
                "grant-wr-conf-019",
            )
            self.assertEqual(
                envelope["execution_evidence"]["outcome"],
                "verified",
            )
            self.assertEqual(
                envelope["execution_claim"]["resolution_claim_id"],
                "11111111-1111-1111-1111-111111111111",
            )
            self.assertEqual(evidence.repository_root, str(repo.resolve()))
            self.assertEqual(evidence.observed_at, FIXED_TIME)
            _assert_read_only(runner.commands)
        finally:
            temp.cleanup()


class TestAc3WrongRef(unittest.TestCase):
    """A ref outside the grant's claimed branch identity is rejected."""

    def test_wrong_branch_ref_is_rejected_even_with_a_real_commit(self):
        temp, repo, baseline = _fixture_repo()
        try:
            _git(repo, "branch", "lease/attempt-wr-conf-019")
            evidence = GitVerificationAdapter(clock=lambda: FIXED_TIME).verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/lease/attempt-wr-conf-019-wrong",
                    baseline,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "CLAIMED_REF_NOT_FOUND")
            self.assertFalse(evidence.ref_exists)
            self.assertIsNone(evidence.resolved_commit)
        finally:
            temp.cleanup()


class TestAc4MismatchedCommit(unittest.TestCase):
    """A real ref cannot be paired with a different claimed commit."""

    def test_claimed_commit_must_match_resolved_branch_commit(self):
        temp, repo, baseline = _fixture_repo()
        try:
            _git(repo, "branch", "lease/attempt-wr-conf-019")
            _git(repo, "switch", "lease/attempt-wr-conf-019")
            (repo / "README.md").write_text("actual branch commit\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "commit", "--quiet", "-m", "actual branch commit")
            actual_commit = _git(repo, "rev-parse", "HEAD")

            evidence = GitVerificationAdapter(clock=lambda: FIXED_TIME).verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/lease/attempt-wr-conf-019",
                    baseline,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "CLAIMED_COMMIT_MISMATCH")
            self.assertTrue(evidence.ref_exists)
            self.assertEqual(evidence.resolved_commit, actual_commit)
            self.assertNotEqual(evidence.resolved_commit, baseline)
        finally:
            temp.cleanup()


class TestAc5UnrelatedHistory(unittest.TestCase):
    """A commit on an unrelated root cannot satisfy base ancestry."""

    def test_unrelated_root_commit_is_rejected(self):
        temp, repo, _baseline = _fixture_repo()
        try:
            _git(repo, "switch", "--orphan", "lease/unrelated-wr-conf-019")
            # An orphan switch leaves the baseline file in the worktree but
            # not in the new index; remove it directly from this temporary
            # fixture before creating the unrelated root commit.
            (repo / "README.md").unlink(missing_ok=True)
            (repo / "unrelated.txt").write_text("unrelated history\n", encoding="utf-8")
            _git(repo, "add", "unrelated.txt")
            _git(repo, "commit", "--quiet", "-m", "unrelated root")
            unrelated_commit = _git(repo, "rev-parse", "HEAD")

            evidence = GitVerificationAdapter(clock=lambda: FIXED_TIME).verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/lease/unrelated-wr-conf-019",
                    unrelated_commit,
                    ("unrelated.txt",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "COMMIT_NOT_REACHABLE_FROM_BASE")
            self.assertTrue(evidence.ref_exists)
            self.assertFalse(evidence.commit_reachable)
            self.assertEqual(evidence.resolved_commit, unrelated_commit)
        finally:
            temp.cleanup()


class TestAc6ScopeDrift(unittest.TestCase):
    """A verified descendant is rejected when it changes undeclared paths."""

    def test_undeclared_changed_path_is_rejected(self):
        temp, repo, baseline = _fixture_repo()
        try:
            _git(repo, "branch", "lease/attempt-wr-conf-019")
            _git(repo, "switch", "lease/attempt-wr-conf-019")
            (repo / "README.md").write_text("declared change\n", encoding="utf-8")
            (repo / "secret.txt").write_text("undeclared change\n", encoding="utf-8")
            _git(repo, "add", "README.md", "secret.txt")
            _git(repo, "commit", "--quiet", "-m", "scope drift")
            claimed_commit = _git(repo, "rev-parse", "HEAD")

            evidence = GitVerificationAdapter(clock=lambda: FIXED_TIME).verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/lease/attempt-wr-conf-019",
                    claimed_commit,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "UNDECLARED_ARTIFACT_CHANGE")
            self.assertTrue(evidence.ref_exists)
            self.assertTrue(evidence.commit_reachable)
            self.assertFalse(evidence.scope_matches)
            self.assertEqual(evidence.changed_paths, ("README.md", "secret.txt"))
            self.assertEqual(evidence.undeclared_paths, ("secret.txt",))
        finally:
            temp.cleanup()


class TestAc8Replay(unittest.TestCase):
    """Identical facts replay to one content identity; context changes do not."""

    def test_identical_replay_is_idempotent_but_attempt_context_changes_identity(self):
        temp, repo, baseline = _fixture_repo()
        try:
            _git(repo, "branch", "lease/attempt-wr-conf-019")
            _git(repo, "switch", "lease/attempt-wr-conf-019")
            (repo / "README.md").write_text("replay-safe change\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "commit", "--quiet", "-m", "replay-safe change")
            claimed_commit = _git(repo, "rev-parse", "HEAD")
            request = _request(
                repo,
                "refs/heads/main",
                "refs/heads/lease/attempt-wr-conf-019",
                claimed_commit,
                ("README.md",),
            )
            adapter = GitVerificationAdapter(clock=lambda: FIXED_TIME)

            first = adapter.verify(request)
            replay = adapter.verify(request)
            changed_context = adapter.verify(
                replace(request, attempt_id="attempt-wr-conf-019-retry")
            )

            self.assertEqual(first.outcome, "verified")
            self.assertEqual(replay.evidence_id, first.evidence_id)
            self.assertEqual(replay.evidence_hash, first.evidence_hash)
            self.assertEqual(replay.observed_at, first.observed_at)
            self.assertNotEqual(changed_context.evidence_id, first.evidence_id)
            self.assertNotEqual(changed_context.evidence_hash, first.evidence_hash)
            self.assertEqual(changed_context.attempt_id, "attempt-wr-conf-019-retry")
            self.assertEqual(changed_context.outcome, "verified")
        finally:
            temp.cleanup()


class TestAc9BoundaryEscape(unittest.TestCase):
    """Repository and declared-path boundaries cannot be escaped."""

    def test_declared_path_escape_is_rejected_before_git_execution(self):
        temp, repo, baseline = _fixture_repo()
        try:
            runner = RecordingRunner()
            evidence = GitVerificationAdapter(runner, clock=lambda: FIXED_TIME).verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/main",
                    baseline,
                    ("../outside.txt",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "INVALID_VERIFICATION_SCOPE")
            self.assertEqual(runner.commands, [])
        finally:
            temp.cleanup()

    def test_nested_repository_root_is_rejected_as_scope_escape(self):
        temp, repo, baseline = _fixture_repo()
        try:
            nested = repo / "nested"
            nested.mkdir()
            evidence = GitVerificationAdapter(clock=lambda: FIXED_TIME).verify(
                _request(
                    nested,
                    "refs/heads/main",
                    "refs/heads/main",
                    baseline,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "INVALID_VERIFICATION_SCOPE")
            self.assertEqual(evidence.repository_root, str(nested.resolve()))
        finally:
            temp.cleanup()


class TestAc10SelfVerification(unittest.TestCase):
    """The claimant cannot also be the independent verifier."""

    def test_claimant_as_verifier_is_rejected(self):
        temp, repo, baseline = _fixture_repo()
        try:
            runner = RecordingRunner()
            claimant = "agent/self-verifier-wr-conf-019"
            evidence = GitVerificationAdapter(
                runner,
                verifier_id=claimant,
                clock=lambda: FIXED_TIME,
            ).verify(
                replace(
                    _request(
                        repo,
                        "refs/heads/main",
                        "refs/heads/main",
                        baseline,
                        ("README.md",),
                    ),
                    claimant_id=claimant,
                )
            )

            self.assertEqual(evidence.outcome, "rejected")
            self.assertEqual(evidence.reason, "SELF_VERIFICATION_NOT_ALLOWED")
            self.assertFalse(evidence.verifier_independence)
            self.assertEqual(runner.commands, [])
        finally:
            temp.cleanup()


class TestAc7VerifierUnavailable(unittest.TestCase):
    """A verifier timeout blocks acceptance without asserting falsity."""

    def test_timeout_is_unavailable_not_rejected(self):
        temp, repo, _baseline = _fixture_repo()
        try:
            runner = TimeoutRunner()
            adapter = GitVerificationAdapter(runner, clock=lambda: FIXED_TIME)
            evidence = adapter.verify(
                _request(
                    repo,
                    "refs/heads/main",
                    "refs/heads/lease/attempt-wr-conf-019",
                    "b" * 40,
                    ("README.md",),
                )
            )

            self.assertEqual(evidence.outcome, "unavailable")
            self.assertEqual(evidence.reason, "VERIFIER_TIMEOUT")
            self.assertNotEqual(evidence.outcome, "rejected")
            self.assertFalse(evidence.ref_exists)
            self.assertIsNone(evidence.resolved_commit)
            self.assertEqual(len(runner.commands), 1)
            _assert_read_only(runner.commands)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
