#!/usr/bin/env python3
"""Hermetic tests for the MongoDB start script's data-version guard.

The script used to try mongo:4.4.18 -> 4.2.18 -> 4.0.28 and take the first
image that started. Once the 4.4 -> 8.0 migration upgrades the volume, the
FIRST entry is older than the data files, every attempt fails for the same
reason, and the next "fix" is removing the volume — data loss
(architect handoff c9540d99 §3.3).

What is pinned here:

  * the downgrade ladder is gone: no 4.2/4.0 fallback image survives, and the
    image is a single configurable value
  * a recorded volume version newer than the requested image is REFUSED (rc 2)
  * the refusal happens BEFORE the running container is stopped or removed —
    a refused start must change nothing
  * equal and newer images are allowed
  * the volume version is recorded only after a verified start, so a failed
    attempt can never raise the floor
  * MONGO_ALLOW_OLDER_IMAGE=1 is the only bypass, and it is loud

Run:
  python3 -m pytest bin/tests/test_mongo_start_version_guard.py -v
"""
from __future__ import annotations

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
SOURCE = REPO / "bin" / "start-mongodb-docker.sh"


class StartGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mongostart-")
        # A minimal repo layout: <tmp>/bin/<script> + <tmp>/.env
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.script = os.path.join(self.bin, "start-mongodb-docker.sh")
        shutil.copy(SOURCE, self.script)
        with open(os.path.join(self.tmp, ".env"), "w") as fh:
            fh.write("MONGO_ROOT_USER=testuser\nMONGO_ROOT_PASS=testpass\n")

        # Stub docker: records every invocation, controllable outcomes.
        self.stub = os.path.join(self.tmp, "stub")
        os.makedirs(self.stub)
        self.log = os.path.join(self.tmp, "docker.log")
        docker = os.path.join(self.stub, "docker")
        with open(docker, "w") as fh:
            fh.write(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{self.log}"
case "$1" in
  ps) exit 0 ;;                                   # no existing container
  run) exit ${{DOCKER_RUN_RC:-0}} ;;
  inspect) printf 'true\\n'; exit 0 ;;
  logs) printf 'stub logs\\n'; exit 0 ;;
  *) exit 0 ;;
esac
""")
        os.chmod(docker, 0o755)

        self.verfile = os.path.join(self.tmp, "state", "mongodb-volume-version")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------
    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [line.strip() for line in fh if line.strip()]

    def set_pin(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text + "\n")

    def set_volume_version(self, version):
        os.makedirs(os.path.dirname(self.verfile), exist_ok=True)
        with open(self.verfile, "w") as fh:
            fh.write(version + "\n")

    def volume_version(self):
        if not os.path.exists(self.verfile):
            return None
        with open(self.verfile) as fh:
            return fh.read().strip()

    def run_script(self, extra_env=None):
        env = {
            "PATH": self.stub + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmp,
            "MONGO_VOLUME_VERSION_FILE": self.verfile,
            "START_SETTLE_SECONDS": "0",
        }
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", self.script], capture_output=True, text=True,
            env=env, timeout=60,
        )

    # -- the ladder is gone ------------------------------------------------
    def test_no_downgrade_fallback_images_remain(self):
        body = SOURCE.read_text()
        for old in ("mongo:4.2.18", "mongo:4.0.28"):
            self.assertNotIn(old, body,
                             f"{old} must be gone: an older image cannot open a "
                             f"newer volume, and trying it is the §3.3 hazard")
        self.assertNotIn("MONGO_VERSIONS=(", body,
                         "the multi-version ladder must not come back")

    def test_image_is_single_and_configurable(self):
        body = SOURCE.read_text()
        self.assertIn('MONGO_VERSION="${MONGO_VERSION:-mongo:4.4.18}"', body)

    # -- refusal -----------------------------------------------------------
    def test_refuses_image_older_than_data_volume(self):
        self.set_volume_version("5.0.14")
        r = self.run_script({"MONGO_VERSION": "mongo:4.4.18"})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("REFUSING to start an image older than the data volume", r.stderr)
        self.assertIn("5.0.14", r.stderr)
        self.assertIn("Do NOT delete or re-initialise the volume", r.stderr)

    def test_refusal_happens_before_the_container_is_touched(self):
        self.set_volume_version("6.0.1")
        self.run_script({"MONGO_VERSION": "mongo:4.4.18"})
        dangerous = [c for c in self.calls()
                     if c.startswith("stop") or c.startswith("rm") or c.startswith("run")]
        self.assertEqual(dangerous, [],
                         "a refused start must not stop/remove/start anything")

    def test_refused_start_does_not_lower_the_recorded_version(self):
        self.set_volume_version("5.0.14")
        self.run_script({"MONGO_VERSION": "mongo:4.4.18"})
        self.assertEqual(self.volume_version(), "5.0.14",
                         "the floor must never be rewritten by a refused start")

    # -- allowed paths -----------------------------------------------------
    def test_equal_version_is_allowed_and_recorded(self):
        self.set_volume_version("4.4.18")
        r = self.run_script({"MONGO_VERSION": "mongo:4.4.18"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.volume_version(), "4.4.18")
        self.assertTrue(any(c.startswith("run") for c in self.calls()))

    def test_newer_image_is_allowed(self):
        self.set_volume_version("4.4.18")
        r = self.run_script({"MONGO_VERSION": "mongo:5.0.14"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("REFUSING", r.stderr)

    def test_first_run_without_a_version_file_is_allowed_and_seeds_it(self):
        self.assertIsNone(self.volume_version())
        r = self.run_script({"MONGO_VERSION": "mongo:4.4.18"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.volume_version(), "4.4.18")

    # -- failure must not raise the floor ----------------------------------
    def test_failed_start_does_not_record_a_new_version(self):
        self.set_volume_version("4.4.18")
        r = self.run_script({"MONGO_VERSION": "mongo:5.0.14", "DOCKER_RUN_RC": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.volume_version(), "4.4.18",
                         "a failed start must not claim the volume is upgraded")
        self.assertIn("Not falling back to an older image", r.stderr)

    # -- volume targeting (post-8.0 cutover) ------------------------------
    def test_default_volume_is_the_live_v8_volume(self):
        # Production moved to mongodb_data_v8 at the cutover. A default of the
        # old volume would recreate the hazard on the next bare run.
        self.set_volume_version("8.0.38")
        r = self.run_script({"MONGO_VERSION": "mongo:8.0.38"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        runs = [c for c in self.calls() if c.startswith("run")]
        self.assertTrue(runs, "expected a docker run")
        self.assertIn("mongodb_data_v8:/data/db", runs[0],
                      "the script must open the live volume by default")

    def test_version_file_is_keyed_by_volume(self):
        # A single global stamp let an 8.0 image through against the 4.4
        # rollback volume, because the stamp said 8.0.38 for every volume.
        self.set_volume_version("")
        r = self.run_script({"MONGO_VERSION": "mongo:8.0.38",
                             "MONGO_VOLUME": "mongodb_data",
                             "MONGO_VOLUME_VERSION_FILE": ""})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        per_volume = os.path.join(self.tmp, ".local", "state", "nexus",
                                  "mongodb-volume-version-mongodb_data")
        self.assertTrue(os.path.exists(per_volume),
                        f"expected a per-volume stamp at {per_volume}")
        with open(per_volume) as fh:
            self.assertEqual(fh.read().strip(), "8.0.38")

    def test_guard_refuses_an_8x_image_on_the_44_rollback_volume(self):
        # The version guard cannot catch this case: 8.0.38 >= 4.4.18 is an
        # "upgrade" by its rules, yet opening the 4.4 rollback volume with 8.0
        # would upgrade it in place and destroy the rollback. A pin refuses
        # every image, whatever the direction.
        self.set_volume_version("4.4.18")
        pin = os.path.join(self.tmp, "state", "mongodb-volume-mongodb_data.pin")
        self.set_pin(pin, "4.4.18 rollback copy — do not upgrade")
        r = self.run_script({"MONGO_VERSION": "mongo:8.0.38",
                             "MONGO_VOLUME": "mongodb_data",
                             "MONGO_VOLUME_PIN_FILE": pin})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("pinned rollback volume", r.stderr)
        self.assertEqual(self.volume_version(), "4.4.18",
                         "the pinned copy's stamp must not change")
        dangerous = [c for c in self.calls()
                     if c.startswith("stop") or c.startswith("rm") or c.startswith("run")]
        self.assertEqual(dangerous, [],
                         "the rollback volume must not be touched at all")

    def test_pin_can_be_overridden_only_explicitly(self):
        self.set_volume_version("4.4.18")
        pin = os.path.join(self.tmp, "state", "mongodb-volume-mongodb_data.pin")
        self.set_pin(pin, "rollback")
        r = self.run_script({"MONGO_VERSION": "mongo:8.0.38",
                             "MONGO_VOLUME": "mongodb_data",
                             "MONGO_VOLUME_PIN_FILE": pin,
                             "MONGO_ALLOW_PINNED_VOLUME": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertIn("PINNED", r.stderr)

    def test_downgrade_refusal_suggests_a_real_image_tag(self):
        # The refusal is read while someone is recovering; it must not hand
        # them a malformed tag ("8.0.38.0.38").
        self.set_volume_version("8.0.38")
        r = self.run_script({"MONGO_VERSION": "mongo:4.4.18"})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MONGO_VERSION=mongo:8.0.38 bin/start-mongodb-docker.sh", r.stderr)
        self.assertNotIn("8.0.38.0.38", r.stderr)

    def test_dry_run_reports_the_plan_and_touches_nothing(self):
        self.set_volume_version("8.0.38")
        r = self.run_script({"MONGO_VERSION": "mongo:8.0.38", "MONGO_DRY_RUN": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DRY RUN", r.stdout)
        self.assertIn("mongodb_data_v8", r.stdout)
        self.assertIn("mongo:8.0.38", r.stdout)
        self.assertEqual(self.calls(), [],
                         "a dry run must not invoke docker at all")

    def test_dry_run_still_enforces_the_guard(self):
        # dry-run must not become a way to skip the downgrade guard
        self.set_volume_version("8.0.38")
        r = self.run_script({"MONGO_VERSION": "mongo:4.4.18", "MONGO_DRY_RUN": "1"})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    # -- explicit, loud bypass ---------------------------------------------
    def test_override_is_explicit_and_loud(self):
        self.set_volume_version("5.0.14")
        r = self.run_script({"MONGO_VERSION": "mongo:4.4.18",
                             "MONGO_ALLOW_OLDER_IMAGE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertIn("OLDER than the data volume", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
