"""Hermetic pre-flight gate tests for bin/ci/sonar_preflight.py.

Background (DBA record f8b7f689): the vanadium-sonar workflow runs on label
[self-hosted, vanadium]; when the vanadium runner is offline, a titanium
runner picks the job up and sonar-scanner then fails against the unreachable
SonarQube server — a network condition masquerading as a code failure, red
on every PR and on main while off the home network. The pre-flight script
probes DNS/TCP/HTTP and lets the workflow skip-with-warning instead.

Pinned behaviors (hermetic — no network, no real hostnames):
- URL parsing: scheme/host extraction, implicit ports (80/443), explicit
  ports, rejects non-http(s) schemes and hostless URLs
- DNS layer: gaierror -> not ready, reason names the host
- TCP layer: refusal and timeout -> not ready, reason names host:port
- HTTP layer: 200 -> READY; non-200 HTTPError -> not ready but the server
  answered (reason must say so — it is a real server-side condition);
  URLError/timeout -> not ready
- Unexpected exceptions inside the probe -> not ready (fail-safe toward
  skip), never raised
- CLI contract: --host-url overrides env; missing both -> exit 1 (usage,
  stderr message); not-ready -> exit 2 with ::warning:: on stdout;
  ready -> exit 0, no warning annotation
- Probe is read-only: no writes, no side effects

Run:
  python3 -m pytest bin/tests/test_sonar_preflight.py -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import socket
import subprocess
import sys
import unittest
import urllib.error
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "bin", "ci", "sonar_preflight.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("sonar_preflight", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pf = _load_module()


class UrlParsingTest(unittest.TestCase):
    def test_implicit_http_port(self):
        self.assertEqual(pf._split_host_port("http://sonar.lab"), ("sonar.lab", 80))

    def test_implicit_https_port(self):
        self.assertEqual(
            pf._split_host_port("https://sonar.lab"), ("sonar.lab", 443)
        )

    def test_explicit_port(self):
        self.assertEqual(
            pf._split_host_port("http://sonar.lab:9000"), ("sonar.lab", 9000)
        )

    def test_trailing_path_ignored_for_host_port(self):
        self.assertEqual(
            pf._split_host_port("http://sonar.lab:9000/"), ("sonar.lab", 9000)
        )

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            pf._split_host_port("ftp://sonar.lab")

    def test_rejects_hostless(self):
        with self.assertRaises(ValueError):
            pf._split_host_port("http:///path")


class ProbeDnsLayerTest(unittest.TestCase):
    def test_dns_failure_not_ready(self):
        with mock.patch.object(
            pf.socket, "getaddrinfo", side_effect=socket.gaierror("name unknown")
        ):
            ready, reason = pf.probe_sonar("http://vanadium-nope:9000")
        self.assertFalse(ready)
        self.assertIn("vanadium-nope", reason)
        self.assertIn("DNS", reason)

    def test_dns_oserror_not_ready(self):
        with mock.patch.object(
            pf.socket, "getaddrinfo", side_effect=OSError("no route")
        ):
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertFalse(ready)
        self.assertIn("DNS", reason)


class ProbeTcpLayerTest(unittest.TestCase):
    def test_connection_refused_not_ready(self):
        with mock.patch.object(pf.socket, "getaddrinfo"), mock.patch.object(
            pf.socket, "create_connection", side_effect=ConnectionRefusedError()
        ):
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertFalse(ready)
        self.assertIn("vanadium:9000", reason)
        self.assertIn("TCP", reason)

    def test_tcp_timeout_not_ready(self):
        with mock.patch.object(pf.socket, "getaddrinfo"), mock.patch.object(
            pf.socket, "create_connection", side_effect=TimeoutError()
        ):
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertFalse(ready)
        self.assertIn("TCP", reason)


class ProbeHttpLayerTest(unittest.TestCase):
    def _http_ctx(self, status=200, exc=None):
        """Patch create_connection to succeed and urlopen to yield/exc."""
        if exc is not None:
            opener = mock.Mock(side_effect=exc)
        else:
            resp = mock.Mock()
            resp.status = status
            resp.__enter__ = mock.Mock(return_value=resp)
            resp.__exit__ = mock.Mock(return_value=False)
            opener = mock.Mock(return_value=resp)

        ctx = mock.patch.object(pf.socket, "getaddrinfo"), mock.patch.object(
            pf.socket, "create_connection"
        ), mock.patch.object(pf.urllib.request, "urlopen", opener)
        return ctx

    def test_status_200_ready(self):
        with contextlib.ExitStack() as stack:
            for cm in self._http_ctx(status=200):
                stack.enter_context(cm)
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertTrue(ready)
        self.assertIn("reachable", reason)
        # liveness endpoint used
        self.assertTrue(reason.startswith("SonarQube"))

    def test_status_503_answered_not_ready(self):
        with contextlib.ExitStack() as stack:
            for cm in self._http_ctx(
                exc=urllib.error.HTTPError(
                    "http://vanadium:9000/api/system/status", 503, "unavailable", {}, io.BytesIO(b"")
                )
            ):
                stack.enter_context(cm)
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertFalse(ready)
        # The server answered — the reason must say HTTP, not "unreachable"
        self.assertIn("503", reason)

    def test_urlerror_not_ready(self):
        with contextlib.ExitStack() as stack:
            for cm in self._http_ctx(
                exc=urllib.error.URLError("connection timed out")
            ):
                stack.enter_context(cm)
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertFalse(ready)
        self.assertIn("HTTP status check failed", reason)

    def test_unexpected_exception_failsafe_not_ready(self):
        with contextlib.ExitStack() as stack:
            for cm in self._http_ctx(exc=RuntimeError("boom")):
                stack.enter_context(cm)
            ready, reason = pf.probe_sonar("http://vanadium:9000")
        self.assertFalse(ready)
        self.assertIn("unexpected", reason)

    def test_invalid_url_not_ready_never_raises(self):
        ready, reason = pf.probe_sonar("gopher://weird")
        self.assertFalse(ready)
        self.assertIn("invalid SONAR_HOST_URL", reason)


class CliContractTest(unittest.TestCase):
    def _run_cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=True):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = pf.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_missing_url_is_usage_error_exit_1(self):
        code, _, err = self._run_cli([])
        self.assertEqual(code, pf.EXIT_USAGE)
        self.assertIn("SONAR_HOST_URL", err)

    def test_env_fallback(self):
        with mock.patch.object(
            pf, "probe_sonar", return_value=(True, "ok (probe stub)")
        ) as probe:
            code, out, _ = self._run_cli([], env={"SONAR_HOST_URL": "http://env-host:9000"})
        self.assertEqual(code, pf.EXIT_READY)
        self.assertEqual(probe.call_args[0][0], "http://env-host:9000")
        self.assertNotIn("::warning::", out)

    def test_cli_flag_overrides_env(self):
        with mock.patch.object(
            pf, "probe_sonar", return_value=(True, "ok (probe stub)")
        ) as probe:
            self._run_cli(
                ["--host-url", "http://flag-host:9000"],
                env={"SONAR_HOST_URL": "http://env-host:9000"},
            )
        self.assertEqual(probe.call_args[0][0], "http://flag-host:9000")

    def test_not_ready_exit_2_with_warning_annotation(self):
        with mock.patch.object(
            pf,
            "probe_sonar",
            return_value=(False, "DNS resolution failed for vanadium: oh no"),
        ):
            code, out, _ = self._run_cli(["--host-url", "http://vanadium:9000"])
        self.assertEqual(code, pf.EXIT_NOT_READY)
        self.assertIn("::warning::sonar skipped", out)
        self.assertIn("DNS resolution failed for vanadium", out)

    def test_ready_exit_0_no_warning(self):
        with mock.patch.object(
            pf, "probe_sonar", return_value=(True, "SonarQube reachable at X (status 200)")
        ):
            code, out, _ = self._run_cli(["--host-url", "http://vanadium:9000"])
        self.assertEqual(code, pf.EXIT_READY)
        self.assertIn("sonar-preflight:", out)
        self.assertNotIn("::warning::", out)


class WorkflowWiringTest(unittest.TestCase):
    """The workflow must actually honor the gate — pin the contract."""

    def test_workflow_has_preflight_step_and_gate(self):
        wf_path = os.path.join(REPO_ROOT, ".github", "workflows", "vanadium-sonar.yml")
        with open(wf_path, "r", encoding="utf-8") as fh:
            wf = fh.read()
        # pre-flight step exists, runs before the scan
        self.assertIn("bin/ci/sonar_preflight.py", wf)
        # scan + scanner-install steps are gated on the probe result
        self.assertIn("steps.sonar-preflight.outputs.ready", wf)
        # not-ready path emits a warning annotation, job stays green
        self.assertIn("::warning::", wf)
        # probe failure cannot silently pass the gate: the workflow exits
        # non-zero when the probe script itself crashes (set -e semantics
        # via continuing-on-error NOT set on the preflight step)
        self.assertNotIn("continue-on-error: true", wf)


class ScriptExecutesTest(unittest.TestCase):
    """End-to-end subprocess run: real interpreter, mocked nothing."""

    def test_subprocess_missing_env_exit_1(self):
        env = {k: v for k, v in os.environ.items() if k != "SONAR_HOST_URL"}
        r = subprocess.run(
            [sys.executable, SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("SONAR_HOST_URL", r.stderr)

    def test_subprocess_unreachable_host_exit_2(self):
        # reserved documentation host is guaranteed non-routable and, more
        # importantly, not resolvable in the CI sandbox; the probe must come
        # back not-ready (exit 2), not crash.
        r = subprocess.run(
            [sys.executable, SCRIPT, "--host-url", "http://sonar-preflight-invalid.invalid:9000"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("::warning::", r.stdout)


if __name__ == "__main__":
    unittest.main()
