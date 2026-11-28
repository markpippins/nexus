"""Tests for the deploy pin drift alarm and its liveness watchdog.

Three invariants are worth keeping honest here (same class of check as
test_pg_wal_guard_units.py):

  - the check posts once per drift episode and exits 2, so the journal is an
    oracle as well as the forum
  - the alarm cannot go silent unnoticed: every completed check writes a
    heartbeat, and --watchdog posts once per stall episode (and exits 2) when
    that heartbeat ages past --max-age
  - the pin history is versioned: bin/conduit-kernel.service.d/lilac-shadow.conf
    is the source of truth for the installed kernel drop-in, and the watchdog
    units are declared next to the alarm they watch

Hermetic: temp git repos, a loopback mock for the Assembly forum endpoint, no
Postgres, no systemd, no network. The one host-dependency is `git`, which the
alarm itself requires.

Run:
  python3 -m pytest bin/tests/test_deploy_pin_drift_alarm.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO_ROOT / "bin"
ALARM = BIN / "deploy-pin-drift-alarm.py"
SERVICE = BIN / "deploy-pin-drift-alarm.service"
TIMER = BIN / "deploy-pin-drift-alarm.timer"
WATCHDOG_SERVICE = BIN / "deploy-pin-drift-alarm-watchdog.service"
WATCHDOG_TIMER = BIN / "deploy-pin-drift-alarm-watchdog.timer"
DROPIN = BIN / "conduit-kernel.service.d" / "lilac-shadow.conf"

USER_UNIT_PREFIX = "Source of truth for ~/.config/systemd/user/"


def _parse_unit(path: pathlib.Path) -> dict[str, list[tuple[str, str]]]:
    """Minimal unit parser: section -> list of (key, value)."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        sections[current].append((key.strip(), value.strip()))
    return sections


def _get(sections: dict, section: str, key: str) -> list[str]:
    return [v for k, v in sections.get(section, []) if k == key]


def _ago(*, minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_repo(root: pathlib.Path) -> pathlib.Path:
    repo = root / "deploy"
    (repo / "python").mkdir(parents=True)
    (repo / "python" / "mod.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # Configure the identity in the repo itself: a CI runner has no global
    # git identity, and every commit the fixture makes needs one.
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init pin"], check=True)
    return repo


class _Assembly:
    """Loopback stand-in for the two Assembly routes the alarm touches."""

    def __init__(self) -> None:
        self.threads: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep test output clean
                pass

            def _json(self, code: int, obj) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/api/users":
                    self._json(200, [{"id": "user-1", "name": "engineer-ii"}])
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path.startswith("/api/forums/") and self.path.endswith("/threads"):
                    outer.threads.append({"path": self.path, **body})
                    self._json(201, {"id": f"thread-{len(outer.threads)}"})
                else:
                    self._json(404, {"error": "not found"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def reset(self) -> None:
        self.threads.clear()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class _AlarmCase(unittest.TestCase):
    assembly: _Assembly

    @classmethod
    def setUpClass(cls) -> None:
        cls.assembly = _Assembly()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.assembly.close()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.repo = _make_repo(self.root)
        self.cache = self.root / "cache"
        self.state = self.cache / "state.json"
        self.heartbeat = self.cache / "heartbeat.json"
        self.watchdog_state = self.cache / "watchdog.json"
        self.assembly.reset()

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "DEPLOY_PIN_REPO": str(self.repo),
                "DEPLOY_PIN_STATE": str(self.state),
                "DEPLOY_PIN_HEARTBEAT": str(self.heartbeat),
                "DEPLOY_PIN_WATCHDOG_STATE": str(self.watchdog_state),
                "DEPLOY_PIN_ALARM_ASSEMBLY_URL": self.assembly.url,
                "DEPLOY_PIN_ALARM_FORUM": "issues-and-open-questions",
                "DEPLOY_PIN_ALARM_ROLE": "engineer-ii",
                "NEXUS_AGENT_MODEL": "test/model",
            }
        )
        env.update(extra)
        return env

    def run_alarm(self, *args: str, post: bool = True) -> subprocess.CompletedProcess:
        """Run the alarm with its delivery channel, as the timers do.

        ``post=False`` omits ``--post``: that is a check or a drill, which must be
        unable to announce anything (the 2026-11-28 implicit-channel guard).
        """
        argv = [*args, "--post"] if post else list(args)
        proc = subprocess.run(
            [sys.executable, str(ALARM), *argv],
            capture_output=True,
            text=True,
            env=self.env(),
            timeout=120,
        )
        return proc

    def baseline(self) -> None:
        proc = self.run_alarm("--accept")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.run_alarm().returncode, 0)

    def write_heartbeat(self, *, minutes_ago: int, verdict: str = "OK") -> None:
        payload = json.loads(self.heartbeat.read_text())
        payload["checked_at"] = _ago(minutes=minutes_ago)
        payload["verdict"] = verdict
        self.heartbeat.write_text(json.dumps(payload, indent=2, sort_keys=True))


class DriftEpisodeTest(_AlarmCase):
    def test_clean_tree_matches_the_baseline_and_posts_nothing(self):
        self.baseline()
        proc = self.run_alarm()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK head=", proc.stdout)
        self.assertEqual(self.assembly.threads, [])

    def test_in_place_edit_posts_one_episode_and_fails_the_unit(self):
        self.baseline()
        (self.repo / "python" / "mod.py").write_text("x = 2\n")
        proc = self.run_alarm()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("M python/mod.py", proc.stdout)
        self.assertEqual(len(self.assembly.threads), 1)
        post = self.assembly.threads[0]
        self.assertIn("DEPLOY PIN DRIFT", post["title"])
        self.assertIn("`python/mod.py` | modified", post["body"])
        self.assertEqual(post["path"], "/api/forums/issues-and-open-questions/threads")
        self.assertEqual(post["role"], "engineer-ii")

    def test_a_persisting_drift_does_not_re_post(self):
        self.baseline()
        (self.repo / "python" / "mod.py").write_text("x = 2\n")
        self.assertEqual(self.run_alarm().returncode, 2)
        again = self.run_alarm()
        self.assertEqual(again.returncode, 2)
        self.assertIn("already posted", again.stdout)
        self.assertEqual(len(self.assembly.threads), 1)

    def test_head_only_move_is_drift(self):
        self.baseline()
        # Empty commit: the digests stay identical, only HEAD moves.
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "--allow-empty", "-m", "advance"],
            check=True,
        )
        proc = self.run_alarm()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("changed=0", proc.stdout)
        self.assertEqual(len(self.assembly.threads), 1)


class NoChannelTest(_AlarmCase):
    """A run that names no channel reports; it must not post and must not consume.

    Before this, a hand-check or a drill of the alarm posted a real-looking
    DEPLOY PIN DRIFT / ALARM STALLED thread to issues-and-open-questions — the
    same implicit-channel footgun as bin/vanadium_runner_watchdog.py.
    """

    def test_drift_without_a_channel_posts_nothing_and_still_fails(self):
        self.baseline()
        (self.repo / "python" / "mod.py").write_text("x = 2\n")
        proc = self.run_alarm(post=False)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("NOT posted", proc.stdout)
        self.assertIn("--post", proc.stdout)
        self.assertEqual(self.assembly.threads, [])
        # The episode was not consumed: the next run that names the channel posts it.
        again = self.run_alarm()
        self.assertEqual(again.returncode, 2, again.stderr)
        self.assertIn("POSTED", again.stdout)
        self.assertEqual(len(self.assembly.threads), 1)

    def test_stall_without_a_channel_posts_nothing_and_writes_no_episode(self):
        self.baseline()
        self.write_heartbeat(minutes_ago=50)
        proc = self.run_alarm("--watchdog", post=False)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("NOT posted", proc.stdout)
        self.assertEqual(self.assembly.threads, [])
        self.assertFalse(self.watchdog_state.exists(), "no episode may be recorded unposted")
        again = self.run_alarm("--watchdog")
        self.assertEqual(again.returncode, 2, again.stderr)
        self.assertEqual(len(self.assembly.threads), 1)

    def test_both_timer_units_name_their_channel(self):
        for path in (SERVICE, WATCHDOG_SERVICE):
            start = _get(_parse_unit(path), "Service", "ExecStart")
            self.assertEqual(len(start), 1, path.name)
            self.assertIn("--post", start[0], f"{path.name} would be silent")


class HeartbeatTest(_AlarmCase):
    def test_completed_check_writes_a_heartbeat(self):
        self.baseline()
        hb = json.loads(self.heartbeat.read_text())
        self.assertEqual(hb["verdict"], "OK")
        self.assertEqual(hb["head"], _git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(hb["repo"], str(self.repo))

    def test_baseline_writes_a_heartbeat(self):
        self.run_alarm("--accept")
        self.assertEqual(json.loads(self.heartbeat.read_text())["verdict"], "BASELINE")

    def test_drift_still_writes_a_heartbeat(self):
        self.baseline()
        (self.repo / "python" / "mod.py").write_text("x = 3\n")
        self.run_alarm()
        self.assertEqual(json.loads(self.heartbeat.read_text())["verdict"], "DRIFT")

    def test_dry_run_writes_no_heartbeat(self):
        self.baseline()
        self.heartbeat.unlink()
        self.assertEqual(self.run_alarm("--dry-run").returncode, 0)
        self.assertFalse(self.heartbeat.exists(), "dry-run must not fake liveness")


class WatchdogTest(_AlarmCase):
    def test_quiet_when_a_check_just_completed(self):
        self.baseline()
        proc = self.run_alarm("--watchdog")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK watchdog", proc.stdout)
        self.assertEqual(self.assembly.threads, [])

    def test_quiet_on_a_fresh_install(self):
        """Nothing has ever run: a missing heartbeat alone is not a stall."""
        proc = self.run_alarm("--watchdog")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NO-HEARTBEAT", proc.stdout)
        self.assertEqual(self.assembly.threads, [])

    def test_shouts_once_per_stall_episode(self):
        self.baseline()
        self.write_heartbeat(minutes_ago=50)
        proc = self.run_alarm("--watchdog")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("STALLED", proc.stdout)
        self.assertEqual(len(self.assembly.threads), 1)
        post = self.assembly.threads[0]
        self.assertIn("DEPLOY PIN ALARM STALLED", post["title"])
        self.assertIn("stopped running", post["body"])
        self.assertIn(str(self.heartbeat), post["body"])
        self.assertIn("unknown", post["body"])

        again = self.run_alarm("--watchdog")
        self.assertEqual(again.returncode, 2)
        self.assertIn("already posted", again.stdout)
        self.assertEqual(len(self.assembly.threads), 1, "a continuing stall must not spam")

    def test_a_new_gap_is_a_new_episode(self):
        self.baseline()
        self.write_heartbeat(minutes_ago=50)
        self.assertEqual(self.run_alarm("--watchdog").returncode, 2)
        self.write_heartbeat(minutes_ago=200)
        proc = self.run_alarm("--watchdog")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(len(self.assembly.threads), 2)

    def test_missing_heartbeat_after_a_baseline_is_a_stall(self):
        self.run_alarm("--accept")
        self.heartbeat.unlink()
        state = json.loads(self.state.read_text())
        state["baseline"]["captured_at"] = _ago(minutes=180)
        self.state.write_text(json.dumps(state, indent=2, sort_keys=True))
        proc = self.run_alarm("--watchdog")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("via baseline", proc.stdout)
        self.assertEqual(len(self.assembly.threads), 1)

    def test_dry_run_does_not_post(self):
        self.baseline()
        self.write_heartbeat(minutes_ago=50)
        proc = self.run_alarm("--watchdog", "--dry-run")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("DRY-RUN", proc.stdout)
        self.assertEqual(self.assembly.threads, [])
        self.assertFalse(self.watchdog_state.exists())


class VersionedDropinTest(unittest.TestCase):
    def test_header_names_the_installed_path_and_the_deploy_step(self):
        text = DROPIN.read_text()
        self.assertIn(
            USER_UNIT_PREFIX + "conduit-kernel.service.d/lilac-shadow.conf", text
        )
        self.assertIn("cp bin/conduit-kernel.service.d/lilac-shadow.conf", text)
        self.assertIn("systemctl --user daemon-reload", text)

    def test_pin_history_is_inside_the_versioned_copy(self):
        text = DROPIN.read_text()
        for commit in ("4823c435", "9eca6e4d", "318d9ea8", "39898886"):
            self.assertIn(commit, text, f"{commit} is part of the pin history")
        self.assertIn("#8261650", text)
        self.assertIn("deploy/lilac-pin-318d9ea8-receipt-issued", text)

    def test_service_section_is_the_live_kernel_wiring(self):
        sections = _parse_unit(DROPIN)
        self.assertEqual(set(sections), {"Service"})
        self.assertEqual(
            sections["Service"],
            [
                ("WorkingDirectory", "/home/codex/deploy/lilac-shadow/python/conduit"),
                (
                    "Environment",
                    "PYTHONPATH=/home/codex/deploy/lilac-shadow/python/conduit",
                ),
                ("Environment", "CONDUIT_LILAC_SHADOW=1"),
            ],
        )

    def test_names_the_check_and_the_watchdog(self):
        text = DROPIN.read_text()
        self.assertIn("deploy-pin-drift-alarm.timer", text)
        self.assertIn("deploy-pin-drift-alarm-watchdog.timer", text)


class WatchdogUnitTest(unittest.TestCase):
    def test_service_runs_watchdog_mode_through_the_repo_tree(self):
        sections = _parse_unit(WATCHDOG_SERVICE)
        self.assertEqual(_get(sections, "Service", "Type"), ["oneshot"])
        start = _get(sections, "Service", "ExecStart")
        self.assertEqual(len(start), 1)
        self.assertIn("/home/codex/dev/nexus/bin/deploy-pin-drift-alarm.py", start[0])
        self.assertIn("--watchdog", start[0])
        self.assertTrue(_get(sections, "Service", "TimeoutStartSec"))

    def test_timer_is_offset_from_the_check_it_watches(self):
        sections = _parse_unit(WATCHDOG_TIMER)
        self.assertEqual(_get(sections, "Timer", "OnCalendar"), ["*:7/15"])
        self.assertEqual(
            _get(sections, "Timer", "Unit"), ["deploy-pin-drift-alarm-watchdog.service"]
        )
        self.assertEqual(_get(sections, "Timer", "Persistent"), ["true"])
        self.assertEqual(_get(sections, "Install", "WantedBy"), ["timers.target"])
        self.assertNotEqual(
            _get(_parse_unit(TIMER), "Timer", "OnCalendar"),
            _get(sections, "Timer", "OnCalendar"),
            "a watchdog on the same boundary as the check can race it",
        )

    def test_units_carry_the_deploy_header(self):
        for path in (WATCHDOG_SERVICE, WATCHDOG_TIMER):
            self.assertIn(USER_UNIT_PREFIX + path.name, path.read_text())
        self.assertIn("cp bin/deploy-pin-drift-alarm-watchdog.service", WATCHDOG_SERVICE.read_text())

    def test_alarm_unit_installs_the_watchdog_too(self):
        text = SERVICE.read_text()
        self.assertIn("deploy-pin-drift-alarm-watchdog.timer", text)
        self.assertIn("--watchdog", text)
        self.assertIn(USER_UNIT_PREFIX + "deploy-pin-drift-alarm.service", text)


if __name__ == "__main__":
    unittest.main()
