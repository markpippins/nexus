#!/usr/bin/env python3
"""Hermetic tests for bin/mongo-sh client resolution.

No database, no network, no container: every client is a fake executable on a
private PATH that records its argv, so the tests pin *which* client wins and
*what* it is handed — not what the server then does.

Contract pinned here (reopens to-do d6db2b90, upgrade context b3909d09):

- selection order is host mongosh -> host mongo -> container mongosh ->
  container mongo; the first available client wins and later ones are never
  invoked (the legacy shell disappears at 6.0, so mongosh must not be skipped
  when it exists)
- mongosh receives the connection URI **unchanged** as its first argument,
  preserving percent-encoded credentials; the legacy shell receives the
  decomposed -u/-p/--authenticationDatabase/db form instead
- MONGO_SHELL_BIN forces a client, and forcing `mongo` must win over an
  available `mongosh`
- no client anywhere exits 4 and names every location tried plus the install
  recipe — the failure mode that replaced a bare `docker exec` error
- process `MONGO_URI` overrides the env file; env-file `MONGO_URI` takes
  precedence over the legacy `SPRING_DATA_MONGODB_URI` key
- the Spring key remains a backward-compatible fallback
- configuration faults (missing env file, missing URI) exit 3
- no credential appears on stdout or stderr in any path

Run:
  python3 -m pytest bin/tests/test_mongo_sh_client_resolution.py -v
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MONGO_SH = os.path.join(_REPO, "bin", "mongo-sh")

_URI = "mongodb://mongoUser:p%40ssw0rd@localhost:27017/nexus?authSource=admin"
_PASSWORD_FRAGMENT = "p%40ssw0rd"


class MongoShResolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.bindir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bindir)
        self.env_file = os.path.join(self.tmp, ".env")
        self._write_env(f"SPRING_DATA_MONGODB_URI={_URI}\n")

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _write_env(self, body: str) -> None:
        with open(self.env_file, "w", encoding="utf-8") as fh:
            fh.write(body)

    def _run_with_uri(self, uri, monkey_env=None):
        self._fake("mongosh")
        r = self._run(monkey_env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._argv("mongosh")[0], uri)
        return r

    def _fake(self, name: str) -> str:
        """Create a fake client that appends its argv to a per-client file."""
        argv_file = os.path.join(self.tmp, f"{name}.argv")
        path = os.path.join(self.bindir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f'#!/bin/sh\nprintf "%s\n" "$@" > "{argv_file}"\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return argv_file

    def _argv(self, name: str):
        argv_file = os.path.join(self.tmp, f"{name}.argv")
        if not os.path.exists(argv_file):
            return None
        with open(argv_file, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def _run(self, monkey_env=None, path_dirs=None):
        env = {
            "PATH": ":".join(path_dirs or [self.bindir, "/usr/bin", "/bin"]),
            "MONGO_ENV_FILE": self.env_file,
            # A container name that cannot exist, so the container probes fail
            # deterministically whether or not docker is installed here.
            "MONGO_CONTAINER": "ci-no-such-container",
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        env.update(monkey_env or {})
        return subprocess.run(
            ["bash", _MONGO_SH, "--eval", "print(1)"],
            capture_output=True, text=True, env=env, timeout=120,
        )

    # -- selection order -------------------------------------------------
    def test_prefers_host_mongosh_over_host_mongo(self):
        self._fake("mongosh")
        self._fake("mongo")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._argv("mongosh")[0], _URI,
                         "mongosh must receive the URI unchanged as its first argument")
        self.assertIsNone(self._argv("mongo"),
                          "the legacy shell must not run when mongosh exists")

    def test_uri_passed_through_with_percent_encoding_intact(self):
        self._fake("mongosh")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = self._argv("mongosh")
        self.assertEqual(argv[0], _URI)
        self.assertIn(_PASSWORD_FRAGMENT, argv[0],
                      "percent-encoding must survive wrapping")

    def test_falls_back_to_host_legacy_shell_with_decomposed_credentials(self):
        self._fake("mongo")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = self._argv("mongo")
        self.assertEqual(argv[:5], [
            "-u", "mongoUser", "-p", "p@ssw0rd", "--authenticationDatabase",
        ])
        self.assertEqual(argv[5], "admin")
        self.assertEqual(argv[6], "nexus")
        self.assertEqual(argv[7:], ["--eval", "print(1)"])

    def test_override_forces_legacy_even_when_mongosh_exists(self):
        self._fake("mongosh")
        self._fake("mongo")
        r = self._run({"MONGO_SHELL_BIN": "mongo"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(self._argv("mongo"),
                             "MONGO_SHELL_BIN=mongo must force the legacy client")
        self.assertIsNone(self._argv("mongosh"),
                          "the override must not fall through to mongosh")

    # -- discoverable URI key --------------------------------------------
    def test_prefers_mongo_uri_from_env_file_over_spring_key(self):
        preferred = "mongodb://agentUser:agentPass@preferred-host:27017/nexus?authSource=admin"
        self._write_env(f"MONGO_URI={preferred}\nSPRING_DATA_MONGODB_URI={_URI}\n")
        self._run_with_uri(preferred)

    def test_process_mongo_uri_overrides_both_env_file_keys(self):
        preferred = "mongodb://processUser:processPass@process-host:27017/nexus?authSource=admin"
        self._write_env(f"MONGO_URI=mongodb://fileUser:filePass@file-host:27017/nexus?authSource=admin\nSPRING_DATA_MONGODB_URI={_URI}\n")
        self._run_with_uri(preferred, {"MONGO_URI": preferred})

    def test_spring_uri_remains_backward_compatible(self):
        self._write_env(f"SPRING_DATA_MONGODB_URI={_URI}\n")
        self._run_with_uri(_URI)

    # -- failure modes ---------------------------------------------------
    def test_no_client_anywhere_exits_4_with_actionable_message(self):
        r = self._run()
        self.assertEqual(r.returncode, 4, r.stderr)
        for location in ("host 'mongosh'", "host 'mongo'",
                         "in container 'ci-no-such-container'"):
            self.assertIn(location, r.stderr)
        self.assertIn("Install mongosh on the host", r.stderr)
        self.assertIn("MONGO_SHELL_BIN", r.stderr)

    def test_missing_env_file_is_a_configuration_error(self):
        env = {"MONGO_ENV_FILE": os.path.join(self.tmp, "absent.env")}
        r = self._run(env)
        self.assertEqual(r.returncode, 3)
        self.assertIn("not found", r.stderr)

    def test_missing_uri_is_a_configuration_error(self):
        self._write_env("SOMETHING_ELSE=1\n")
        r = self._run()
        self.assertEqual(r.returncode, 3)
        self.assertIn("MONGO_URI or SPRING_DATA_MONGODB_URI not set", r.stderr)

    # -- credential hygiene ----------------------------------------------
    def test_credentials_never_echoed(self):
        self._fake("mongosh")
        for label, extra in (("mongosh", {}), ("legacy", {"MONGO_SHELL_BIN": "mongo"})):
            if label == "legacy":
                self._fake("mongo")
            r = self._run(extra)
            for stream, value in (("stdout", r.stdout), ("stderr", r.stderr)):
                self.assertNotIn(_PASSWORD_FRAGMENT, value,
                                 f"credential leaked on {stream} ({label} path)")
                self.assertNotIn("p@ssw0rd", value,
                                 f"decoded credential leaked on {stream} ({label} path)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
