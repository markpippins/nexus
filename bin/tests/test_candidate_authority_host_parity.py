"""Gate the candidate-stack authority host against its ansible source of truth.

`docker/candidate-stack/compose.vanadium.yaml` retargets the standby's PG and
Conduit to the authority host over LAN, and it does so with hand-copied
literals while ansible renders the same addresses from `authority_host` in
`ansible/inventory/group_vars/all.yml` (`{{ authority_host }}` into PG_HOST,
every `*_PG_DSN`, NEBULA_URL and CONDUIT_MCP_URL). Two hand-maintained twins
of one address is exactly the shape that drifted: PR #1024 had to retarget
both at once after the authority host moved from 192.168.1.120 to
192.168.1.71, and before that the dead address produced days of
`FATAL: ANALYZE failed` alerts (docs/runbook-thallium-maintenance-jobs.md).

Nothing stopped the next edit from touching only one side. A compose literal
left on a retired address fails at deploy time on the standby only, where the
candidate stack is not the primary target and the breakage is easy to miss.

This guard is hermetic: it reads two checked-in files, never the network. It
pins *agreement*, not reachability -- an address both files agree on can still
be unreachable over the LAN, which is a runbook question, not a repo one.
"""
from __future__ import annotations

import ipaddress
import pathlib
import re
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
ANSIBLE_VARS = REPO / "ansible" / "inventory" / "group_vars" / "all.yml"
COMPOSE = REPO / "docker" / "candidate-stack" / "compose.vanadium.yaml"

#: Ports the override's authority-derived URLs must keep. They are part of the
#: same literal, so a retarget that also transposes a port is drift too.
PG_PORT = 5432
CONDUIT_PORT = 3100

_IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")


def _authority_host() -> str:
    values = yaml.safe_load(ANSIBLE_VARS.read_text(encoding="utf-8")) or {}
    return str(values.get("authority_host", "")).strip()


def _compose() -> dict:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8")) or {}
    return doc


def _walk_ipv4(node, path="$"):
    """Yield (path, dotted-quad) for every IPv4 literal in a parsed value.

    Comments are deliberately not scanned: the file's own header names
    vanadium's address (192.168.1.209), which is not the authority host and is
    legitimate. Only values compose would actually interpolate matter.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_ipv4(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_ipv4(value, f"{path}[{index}]")
    elif isinstance(node, str):
        for match in _IPV4.finditer(node):
            yield path, match.group(1)


def _environment(doc: dict, service: str) -> dict:
    block = ((doc.get("services") or {}).get(service) or {})
    env = block.get("environment")
    if env is None:
        return {}
    if isinstance(env, list):  # ["KEY=value"] form
        out = {}
        for item in env:
            key, _, value = str(item).partition("=")
            out[key] = value
        return out
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def mismatches(doc: dict, authority: str) -> list[str]:
    """Every authority-host disagreement in one compose document.

    Split out from the tests so the self-test below can prove the checker
    still catches drift instead of only agreeing with a currently-correct
    file.
    """
    problems: list[str] = []
    for path, literal in _walk_ipv4(doc):
        if literal != authority:
            problems.append(f"{path}: {literal} != authority_host {authority}")

    for service, env in ((name, _environment(doc, name))
                         for name in (doc.get("services") or {})):
        pg_host = env.get("PG_HOST")
        if pg_host is not None and pg_host.strip() != authority:
            problems.append(
                f"services.{service}.environment.PG_HOST: {pg_host!r} "
                f"!= authority_host {authority}"
            )
        conduit = env.get("CONDUIT_MCP_URL")
        if conduit is not None:
            expected = f"http://{authority}:{CONDUIT_PORT}"
            if conduit.strip() != expected:
                problems.append(
                    f"services.{service}.environment.CONDUIT_MCP_URL: "
                    f"{conduit!r} != {expected!r}"
                )
    return problems


class AuthorityHostSource(unittest.TestCase):
    def test_authority_host_is_a_bare_ipv4(self):
        """It is interpolated into URLs and DSNs, so no scheme, CIDR or name."""
        authority = _authority_host()
        self.assertTrue(authority, f"{ANSIBLE_VARS} declares no authority_host")
        try:
            parsed = ipaddress.IPv4Address(authority)
        except ipaddress.AddressValueError as exc:  # pragma: no cover - message
            self.fail(f"authority_host {authority!r} is not a bare IPv4: {exc}")
        self.assertEqual(str(parsed), authority)


class ComposeAgreesWithAnsible(unittest.TestCase):
    def test_compose_file_exists_and_parses(self):
        self.assertTrue(COMPOSE.is_file(), f"missing {COMPOSE}")
        self.assertIn("services", _compose())

    def test_no_ipv4_literal_disagrees_with_authority_host(self):
        self.assertEqual(mismatches(_compose(), _authority_host()), [])

    def test_broker_pg_host_is_the_authority_host(self):
        env = _environment(_compose(), "broker")
        self.assertIn(
            "PG_HOST", env,
            "broker no longer retargets PG_HOST in the vanadium override",
        )
        self.assertEqual(env["PG_HOST"], _authority_host())

    def test_control_edge_pg_host_is_the_authority_host(self):
        env = _environment(_compose(), "control-edge")
        self.assertIn(
            "PG_HOST", env,
            "control-edge no longer retargets PG_HOST in the vanadium override",
        )
        self.assertEqual(env["PG_HOST"], _authority_host())

    def test_broker_conduit_url_points_at_the_authority_host(self):
        env = _environment(_compose(), "broker")
        self.assertIn(
            "CONDUIT_MCP_URL", env,
            "broker no longer retargets CONDUIT_MCP_URL in the vanadium override",
        )
        self.assertEqual(
            env["CONDUIT_MCP_URL"],
            f"http://{_authority_host()}:{CONDUIT_PORT}",
        )

    def test_no_service_escapes_the_pg_host_retarget(self):
        """Every PG_HOST/BROKER-style authority setting is the same address."""
        doc = _compose()
        checked = 0
        for service in doc.get("services") or {}:
            env = _environment(doc, service)
            if "PG_HOST" not in env:
                continue
            checked += 1
            self.assertEqual(
                env["PG_HOST"], _authority_host(),
                f"services.{service}.environment.PG_HOST drifted",
            )
        self.assertGreaterEqual(
            checked, 2,
            "expected broker and control-edge to both pin PG_HOST; found "
            f"{checked}",
        )


class CheckerStillCatchesDrift(unittest.TestCase):
    """A guard that trivially passes is worse than none."""

    def _doc(self):
        return {
            "services": {
                "broker": {
                    "environment": {
                        "PG_HOST": "192.168.1.71",
                        "CONDUIT_MCP_URL": "http://192.168.1.71:3100",
                    }
                },
                "control-edge": {"environment": {"PG_HOST": "192.168.1.71"}},
            }
        }

    def test_consistent_document_is_clean(self):
        self.assertEqual(mismatches(self._doc(), "192.168.1.71"), [])

    def test_stale_pg_host_is_flagged(self):
        doc = self._doc()
        doc["services"]["control-edge"]["environment"]["PG_HOST"] = "192.168.1.120"
        problems = mismatches(doc, "192.168.1.71")
        self.assertTrue(any("control-edge" in p and "192.168.1.120" in p
                            for p in problems), problems)

    def test_stale_conduit_url_is_flagged(self):
        doc = self._doc()
        doc["services"]["broker"]["environment"]["CONDUIT_MCP_URL"] = (
            "http://192.168.1.120:3100"
        )
        problems = mismatches(doc, "192.168.1.71")
        self.assertTrue(any("CONDUIT_MCP_URL" in p for p in problems), problems)

    def test_transposed_port_is_flagged(self):
        doc = self._doc()
        doc["services"]["broker"]["environment"]["CONDUIT_MCP_URL"] = (
            "http://192.168.1.71:3101"
        )
        self.assertTrue(mismatches(doc, "192.168.1.71"))

    def test_literal_hidden_in_another_value_is_flagged(self):
        doc = self._doc()
        doc["services"]["broker"]["environment"]["NEBULA_URL"] = (
            "http://192.168.1.120:3101"
        )
        self.assertTrue(mismatches(doc, "192.168.1.71"))

    def test_list_form_environment_is_read(self):
        doc = {"services": {"broker": {"environment": ["PG_HOST=192.168.1.120"]}}}
        self.assertTrue(mismatches(doc, "192.168.1.71"))


if __name__ == "__main__":
    unittest.main()
