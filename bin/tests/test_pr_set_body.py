#!/usr/bin/env python3
"""Hermetic guard for bin/pr-set-body.sh.

The bug this tool exists for is a *silent* one: `gh pr edit --body-file` exited
0, printed a deprecation warning, and left the body untouched (PR #830,
2026-11-01). A tool that claims a write it did not perform is worse than no
tool, so the wrapper's whole value is the read-back comparison. That means the
test that matters is the negative one — when the PATCH does not take effect, the
script must exit non-zero and say so.

No network: `GH_BIN` is pointed at a fake `gh` whose PATCH can be made a no-op
(`FAKE_HONOR_PATCH=0`), which reproduces exactly the observed failure.
"""
import json
import os
import pathlib
import stat
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "pr-set-body.sh"

FAKE_GH = r'''#!/usr/bin/env python3
"""Stand-in for `gh`. State lives in $FAKE_STATE; PATCH honours $FAKE_HONOR_PATCH."""
import json, os, sys

state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]

if not args or args[0] != "api":
    print("fake gh: unsupported: %r" % (args,), file=sys.stderr)
    sys.exit(64)

method = None
if "-X" in args:
    method = args[args.index("-X") + 1]

if method == "PATCH":
    payload = sys.stdin.read()
    if os.environ.get("FAKE_HONOR_PATCH", "1") == "1":
        state["body"] = json.loads(payload).get("body", "")
        json.dump(state, open(state_path, "w"))
    # A no-op PATCH exits 0 and says nothing: the real failure mode.
    sys.exit(0)

# GET path: `api repos/.../pulls/N --jq .body`
if "--jq" in args:
    field = args[args.index("--jq") + 1].lstrip(".")
    print(state.get(field) or "")
    sys.exit(0)

json.dump(state, sys.stdout)
sys.exit(0)
'''


def _setup(tmp_path, initial_body="ORIGINAL BODY", honor="1"):
    fake = tmp_path / "gh"
    fake.write_text(FAKE_GH)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"body": initial_body}))
    env = dict(os.environ)
    env["GH_BIN"] = str(fake)
    env["FAKE_STATE"] = str(state)
    env["FAKE_HONOR_PATCH"] = honor
    return state, env


def _run(args, env, stdin=None):
    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True, text=True, env=env,
        input=stdin if stdin is not None else "",
    )


def test_successful_write_is_verified(tmp_path):
    state, env = _setup(tmp_path, honor="1")
    body_file = tmp_path / "body.md"
    body_file.write_text("## New body\n\nverified content\n")

    r = _run(["--repo", "o/r", "--pr", "9", "--body-file", str(body_file)], env)

    assert r.returncode == 0, r.stderr
    assert "verified body" in r.stdout
    assert json.loads(state.read_text())["body"] == "## New body\n\nverified content\n"


def test_noop_patch_fails_loudly(tmp_path):
    """The exact observed bug: PATCH exits 0, body is untouched."""
    state, env = _setup(tmp_path, initial_body="ORIGINAL BODY", honor="0")
    body_file = tmp_path / "body.md"
    body_file.write_text("## New body that will NOT land\n")

    r = _run(["--repo", "o/r", "--pr", "9", "--body-file", str(body_file)], env)

    assert r.returncode == 1, (
        f"a PATCH that did not take effect must fail; got rc={r.returncode}"
    )
    assert "unchanged" in r.stderr.lower(), r.stderr
    assert "NOT written" in r.stderr
    # The state must be untouched, i.e. we did not fool ourselves.
    assert json.loads(state.read_text())["body"] == "ORIGINAL BODY"


def test_partial_write_reports_mismatch(tmp_path):
    """Written, but different from what was sent (truncation/rewrite)."""
    fake_dir = tmp_path
    state, env = _setup(tmp_path, initial_body="ORIGINAL BODY", honor="1")
    # A gh that accepts the PATCH but stores a truncated body.
    truncating = fake_dir / "gh-trunc"
    truncating.write_text(
        FAKE_GH.replace('state["body"] = json.loads(payload).get("body", "")',
                        'state["body"] = json.loads(payload).get("body", "")[:10]')
    )
    truncating.chmod(truncating.stat().st_mode | stat.S_IEXEC)
    env["GH_BIN"] = str(truncating)
    body_file = tmp_path / "body.md"
    body_file.write_text("A body long enough to be truncated by the fake")

    r = _run(["--repo", "o/r", "--pr", "9", "--body-file", str(body_file)], env)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "does not match" in r.stderr


def test_inline_body_flag(tmp_path):
    state, env = _setup(tmp_path)
    r = _run(["--repo", "o/r", "--pr", "9", "--body", "inline body text"], env)
    assert r.returncode == 0, r.stderr
    assert json.loads(state.read_text())["body"] == "inline body text"


def test_body_from_stdin(tmp_path):
    state, env = _setup(tmp_path)
    r = _run(["--repo", "o/r", "--pr", "7"], env, stdin="piped body\n")
    assert r.returncode == 0, r.stderr
    assert json.loads(state.read_text())["body"] == "piped body\n"


def test_missing_pr_is_usage_error(tmp_path):
    _, env = _setup(tmp_path)
    r = _run(["--repo", "o/r", "--body", "x"], env)
    assert r.returncode == 2
    assert "--pr is required" in r.stderr


def test_missing_repo_is_usage_error(tmp_path):
    _, env = _setup(tmp_path)
    env.pop("GH_REPO", None)
    r = _run(["--pr", "9", "--body", "x"], env)
    assert r.returncode == 2
    assert "--repo is required" in r.stderr


def test_missing_body_file_is_usage_error(tmp_path):
    _, env = _setup(tmp_path)
    r = _run(["--repo", "o/r", "--pr", "9", "--body-file", str(tmp_path / "nope.md")], env)
    assert r.returncode == 2
    assert "body file not found" in r.stderr


def test_unknown_argument_is_usage_error(tmp_path):
    _, env = _setup(tmp_path)
    r = _run(["--repo", "o/r", "--pr", "9", "--bogus"], env)
    assert r.returncode == 2
    assert "unknown argument" in r.stderr


def test_no_verify_skips_readback_but_says_so(tmp_path):
    _, env = _setup(tmp_path, honor="0")
    body_file = tmp_path / "body.md"
    body_file.write_text("does not matter\n")
    r = _run(["--repo", "o/r", "--pr", "9", "--body-file", str(body_file), "--no-verify"], env)
    assert r.returncode == 0
    assert "NOT confirmed" in r.stdout
