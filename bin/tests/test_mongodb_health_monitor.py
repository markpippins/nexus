#!/usr/bin/env python3
"""Hermetic tests for bin/mongodb-health-monitor.sh client resolution.

Context: the MongoDB server was upgraded 4.4.18 -> 8.0.38. `mongo:8.0` ships
`mongosh` only, and the monitor's authoritative readiness probe called the
legacy `mongo` binary inside the container:

    docker exec atomic-mongodb mongo --quiet ... listCollections

On 8.0 that fails with `exec: "mongo": executable file not found`, so every
cycle fell through to the port-only path and reported "degraded" instead of
verifying authentication and a real read. This is the same class of defect as
the backup gate (#964) and bin/mongo-sh (#960).

Docker is stubbed: `ps` reports the container, `exec ... mongosh` returns the
readiness `1`, and `exec ... mongo` fails the way the 8.0 image fails.

Run:
  python3 -m pytest bin/tests/test_mongodb_health_monitor.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_GUARD_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "bash", doing="these tests execute the bin/ shell script"
)

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_SRC = REPO / "bin" / "mongodb-health-monitor.sh"


class MonitorClientResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mongomon-")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.script = os.path.join(self.bin, "mongodb-health-monitor.sh")
        shutil.copy(SCRIPT_SRC, self.script)
        with open(os.path.join(self.tmp, ".env"), "w") as fh:
            fh.write("MONGO_ROOT_USER=testuser\nMONGO_ROOT_PASS=testpass\n")

        self.stub = os.path.join(self.tmp, "stub")
        os.makedirs(self.stub)
        self.log = os.path.join(self.tmp, "docker.log")
        docker = os.path.join(self.stub, "docker")
        # mongosh_ok=1  -> mongosh answers 1 (an 8.0 image)
        # mongosh_ok=0  -> mongosh missing (a 4.4/5.0 image)
        with open(docker, "w") as fh:
            fh.write(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{self.log}"
if [ "$1" = "ps" ]; then
  printf 'atomic-mongodb\\n'
  exit 0
fi
if [ "$1" = "exec" ]; then
  case "$*" in
    *" mongosh "*) [ "${{MONGOSH_OK:-1}}" = 1 ] && {{ printf '1\\n'; exit 0; }}
                   echo 'exec: "mongosh": executable file not found in $PATH' >&2; exit 127 ;;
    *" mongo "*)   [ "${{LEGACY_OK:-0}}" = 1 ] && {{ printf '1\\n'; exit 0; }}
                   echo 'exec: "mongo": executable file not found in $PATH' >&2; exit 127 ;;
  esac
  exit 0
fi
exit 0
""")
        os.chmod(docker, 0o755)
        self.runtime = os.path.join(self.tmp, "run")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_monitor(self, extra_env=None):
        env = {
            # Only the stub + system dirs: the host's real mongosh must not
            # rescue a broken in-container probe, or the test proves nothing.
            "PATH": self.stub + ":/usr/bin:/bin",
            "HOME": self.tmp,
            "XDG_RUNTIME_DIR": self.runtime,
            "PEB_DATABASE_URL": "postgresql://unused",
            # Point the liveness-only fallback at a closed port so the DOWN
            # case does not depend on whatever is listening on the host.
            "MONGO_PORT": "65000",
        }
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", self.script], capture_output=True, text=True,
            env=env, timeout=60,
        )

    def state(self):
        path = os.path.join(self.runtime, "nexus-monitor",
                            "mongodb-health-state.json")
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [l.strip() for l in fh if l.strip()]

    # -- the fix -----------------------------------------------------------
    def test_reports_healthy_against_mongosh_only_image(self):
        r = self.run_monitor({"MONGOSH_OK": "1", "LEGACY_OK": "0"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("MongoDB is healthy", out)
        self.assertNotIn("degraded", out.lower(),
                         "an 8.0 image must not be reported as degraded")
        self.assertTrue(self.state()["mongodb_was_up"])

    def test_probe_asks_the_container_for_mongosh_first(self):
        self.run_monitor({"MONGOSH_OK": "1"})
        execs = [c for c in self.calls() if c.startswith("exec")]
        self.assertTrue(execs)
        self.assertIn("mongosh", execs[0],
                      "the first in-container probe must be mongosh on 6.0+")

    def test_still_works_on_a_legacy_shell_image(self):
        # 4.4/5.0 have no mongosh: the fallback must keep those working.
        r = self.run_monitor({"MONGOSH_OK": "0", "LEGACY_OK": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MongoDB is healthy", r.stdout + r.stderr)
        self.assertTrue(self.state()["mongodb_was_up"])

    def test_reports_down_when_neither_client_answers(self):
        r = self.run_monitor({"MONGOSH_OK": "0", "LEGACY_OK": "0"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MongoDB is DOWN", r.stdout + r.stderr)
        self.assertFalse(self.state()["mongodb_was_up"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
