#!/usr/bin/env python3
"""Pre-flight reachability gate for the vanadium-sonar workflow.

Why this exists (2026-09-15, DBA): the job runs on label [self-hosted,
vanadium]; when the vanadium runner is offline (laptop away from the home
network), a titanium runner picks the job up, runs the coverage steps, and
then sonar-scanner fails against the unreachable SonarQube server on
vanadium — a network condition masquerading as a code failure, red on every
PR and on main (observed on PRs #251/#252/#253/#254 and main's own push
runs).

Design decision (per DBA record f8b7f689): the workflow gains a pre-flight
first step. When the SonarQube server is unreachable, the step emits a
GitHub ::warning:: annotation, prints a one-line skip reason, and every
subsequent scan step is skipped via an `if:` on the captured output — the
job concludes green. When reachable, behavior is byte-identical to the
pre-preflight workflow: the scan runs and the quality gate still fails the
job for real code problems. Only unreachability is downgraded to green —
never a red quality gate, never an auth failure.

Probe layers (all three must pass for "ready"):
  1. DNS:      socket.getaddrinfo on the host
  2. TCP:      connect to host:port with a short timeout
  3. HTTP:     GET {host_url}/api/system/status (SonarQube liveness
               endpoint; 200 = UP). Auth is not required for status.

CLI:
  sonar_preflight.py               # reads SONAR_HOST_URL (required)
  sonar_preflight.py --host-url URL
Exit codes: 0 = ready (run the scan), 2 = not ready (skip + warn).
Anything unexpected inside the probe is treated as NOT READY (fail-safe
toward skipping: a flaky pre-flight must not invent scan failures), except
missing host-url, which is always a hard error (exit 1) because it means
the workflow/script contract is broken, not the network.

Run:
  python3 -m pytest bin/tests/test_sonar_preflight.py -v
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

#: TCP connect timeout (seconds). Tight by design: this probe runs on a
#: self-hosted box that is either on the lab LAN (sub-millisecond) or
#: nowhere near it (immediate refusal / fast timeout).
TCP_TIMEOUT = 3.0

#: HTTP status request timeout (seconds).
HTTP_TIMEOUT = 5.0

STATUS_PATH = "/api/system/status"

EXIT_READY = 0
EXIT_NOT_READY = 2
EXIT_USAGE = 1


def _split_host_port(host_url: str) -> tuple[str, int]:
    """Extract (host, port) from a SONAR_HOST_URL-style base URL.

    Raises ValueError on URLs without a scheme or host.
    """
    parts = urlsplit(host_url.strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme in {host_url!r} (need http/https)")
    if not parts.hostname:
        raise ValueError(f"no hostname in {host_url!r}")
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return parts.hostname, port


def probe_sonar(host_url: str) -> tuple[bool, str]:
    """Return (ready, reason) for the SonarQube server at host_url.

    reason is always a human-readable one-liner suitable for the workflow
    annotation. Never raises: unexpected conditions are reported as
    not-ready with the exception text embedded (fail-safe toward skip).
    """
    try:
        host, port = _split_host_port(host_url)
    except (ValueError, AttributeError) as exc:
        return False, f"invalid SONAR_HOST_URL: {exc}"

    # Layer 1: DNS
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed for {host}: {exc}"
    except OSError as exc:
        return False, f"DNS lookup error for {host}: {exc}"

    # Layer 2: TCP
    try:
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT):
            pass
    except OSError as exc:
        return False, f"TCP connect to {host}:{port} failed: {exc}"

    # Layer 3: HTTP liveness (SonarQube status endpoint; no auth needed)
    status_url = host_url.rstrip("/") + STATUS_PATH
    try:
        req = urllib.request.Request(status_url, method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status == 200:
                return True, f"SonarQube reachable at {host_url} (status 200)"
            return (
                False,
                f"SonarQube status endpoint returned HTTP {resp.status} at {status_url}",
            )
    except urllib.error.HTTPError as exc:
        # A non-200 from the server means the server IS answering — that is
        # a real SonarQube-side condition, reported (and left to fail the
        # scan honestly) rather than swallowed as unreachable.
        return (
            False,
            f"SonarQube status endpoint answered HTTP {exc.code} at {status_url}",
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, f"HTTP status check failed for {status_url}: {exc}"
    except Exception as exc:  # noqa: BLE001 - probe must never crash the job
        return False, f"unexpected pre-flight error: {exc!r}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-flight reachability probe for the vanadium-sonar workflow."
    )
    parser.add_argument(
        "--host-url",
        default=None,
        help="SonarQube base URL (falls back to SONAR_HOST_URL env)",
    )
    args = parser.parse_args(argv)

    host_url = args.host_url or os.environ.get("SONAR_HOST_URL")
    if not host_url:
        print(
            "SONAR_HOST_URL is not set — cannot probe (workflow contract broken)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    ready, reason = probe_sonar(host_url)
    print(f"sonar-preflight: {reason}")
    if not ready:
        # GitHub workflow-command warning annotation: shows on the PR, does
        # not fail the run.
        print(f"::warning::sonar skipped — {reason}")
        return EXIT_NOT_READY
    return EXIT_READY


if __name__ == "__main__":
    sys.exit(main())
