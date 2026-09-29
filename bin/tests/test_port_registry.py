"""Hermetic tests for the single-source moleculer port registry.

The registry (moleculer/ports.yaml) was commissioned by architect ruling
7c97ea63 (decision 91540185) §2 after the port set proved to be stored three
times as hand-maintained prose — every port PR collided O(N²) against every
other, and consumers (e.g. bin/assert_moleculer_ports.sh's hand list) drifted
silently. These tests pin the registry contract:

  schema      — required fields, unique ports, canary band, incumbents exist
  cross-check — registry canary set == check_drift.MOLECULER_MIRRORS keys
                (the incident pin: a dropped twin must break here LOUDLY)
  generator   — region rendering, marker discipline, --check byte-identity

No network, no services: the generator tests run against a synthetic root.
"""

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "tools" / "api-docs"
REGISTRY = REPO / "moleculer" / "ports.yaml"
GEN = DOCS / "gen_port_registry.py"

sys.path.insert(0, str(DOCS))
_spec = importlib.util.spec_from_file_location("check_drift", DOCS / "check_drift.py")
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)

_gspec = importlib.util.spec_from_file_location("gen_port_registry", GEN)
gen = importlib.util.module_from_spec(_gspec)
_gspec.loader.exec_module(gen)

CANARY_BAND = range(4100, 4600)
CANARY_FIELDS = ("port", "name", "namespace", "incumbent", "incumbent_port",
                 "description", "readme_status", "map_registry_status",
                 "ratified", "ratification")
INFRA_FIELDS = ("port", "name", "namespace", "readme_counterpart",
                "readme_gate", "readme_status", "map_row")


def load():
    return yaml.safe_load(REGISTRY.read_text())


class RegistrySchema(unittest.TestCase):
    """The registry is the source of truth; its shape must be load-bearing."""

    def setUp(self):
        self.reg = load()

    def test_sections_present_and_nonempty(self):
        self.assertTrue(self.reg.get("infra"), "infra section missing/empty")
        self.assertTrue(self.reg.get("canary"), "canary section missing/empty")

    def test_ports_unique(self):
        ports = [e["port"] for e in self.reg["infra"] + self.reg["canary"]]
        self.assertEqual(len(ports), len(set(ports)), f"duplicate ports: {ports}")

    def test_canary_entries_have_all_fields(self):
        for e in self.reg["canary"]:
            missing = [f for f in CANARY_FIELDS if f not in e]
            self.assertEqual(missing, [], f"{e.get('port')}: missing {missing}")
            self.assertIn(e["port"], CANARY_BAND,
                          f"{e['port']}: canary ports live in the 4100-4599 band")
            self.assertTrue(str(e["incumbent"]).endswith("-srv"),
                            f"{e['port']}: incumbent must be a *-srv service")

    def test_infra_entries_have_all_fields(self):
        for e in self.reg["infra"]:
            missing = [f for f in INFRA_FIELDS if f not in e]
            self.assertEqual(missing, [], f"{e.get('port')}: missing {missing}")

    def test_incumbent_dirs_and_contracts_exist(self):
        for e in self.reg["canary"]:
            d = REPO / e["incumbent"]
            self.assertTrue(d.is_dir(), f"{e['port']}: incumbent dir missing: {e['incumbent']}")
            self.assertTrue((d / "openapi.yaml").is_file(),
                            f"{e['port']}: incumbent contract missing: {e['incumbent']}/openapi.yaml")

    def test_ratification_provenance_carried(self):
        # Every canary row must carry a ratification statement; ports without
        # a dated Ruling-4 clause must say so explicitly (the 4111/4116 gap
        # must stay LOUD until the architect rules).
        for e in self.reg["canary"]:
            self.assertTrue(str(e["ratification"]).strip(),
                            f"{e['port']}: empty ratification field")


class RegistryCrossCheck(unittest.TestCase):
    """The registry and the drift machinery must agree — always."""

    def setUp(self):
        self.reg = load()

    def test_canary_set_equals_drift_mirrors(self):
        # THE incident pin: operator merge fa15c433 dropped moleculer/peb and
        # the drift gate stayed green because its registry entry vanished too.
        # Here a divergence between the two registries is a test failure.
        canary = {(f"moleculer/{e['name']}", e["incumbent"]) for e in self.reg["canary"]}
        mirrors = set(cd.MOLECULER_MIRRORS.items())
        self.assertEqual(canary, mirrors,
                         "ports.yaml canary set != check_drift.MOLECULER_MIRRORS — "
                         "a twin was added/removed on one side only")

    def test_moleculer_service_dirs_exist(self):
        for e in self.reg["canary"]:
            d = REPO / "moleculer" / e["name"]
            self.assertTrue(d.is_dir(),
                            f"moleculer/{e['name']} missing — find_services() would "
                            f"silently skip it (exit 2 from --check-registry-only is "
                            f"the other tripwire)")

    def test_namespaces_unique(self):
        ns = [e["namespace"] for e in self.reg["infra"] + self.reg["canary"]]
        self.assertEqual(len(ns), len(set(ns)), f"duplicate namespaces: {ns}")


class GeneratedDocsInSync(unittest.TestCase):
    """The generated regions in the real docs must match the registry."""

    def test_generator_check_green_on_repo(self):
        r = subprocess.run([sys.executable, str(GEN), "--check"],
                           capture_output=True, text=True, cwd=REPO, timeout=120)
        self.assertEqual(r.returncode, 0,
                         f"generated docs drifted from the registry:\n{r.stdout}\n{r.stderr}")

    def test_gate_port_list_equals_registry(self):
        import re
        text = (REPO / "bin" / "assert_moleculer_ports.sh").read_text()
        m = re.search(r'MAPPED_PORTS="([0-9 ]+)"', text)
        fallback = m.group(1).split() if m else []
        declared = [str(e["port"]) for e in load()["infra"] + load()["canary"]]
        self.assertEqual(fallback, declared,
                         "assert_moleculer_ports.sh inline fallback != registry")


class GeneratorUnit(unittest.TestCase):
    """Marker discipline + rendering, on a synthetic root."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="portreg-"))
        (self.tmp / "moleculer").mkdir()
        (self.tmp / "jvm").mkdir()
        (self.tmp / "moleculer" / "ports.yaml").write_text(
            "infra:\n"
            "  - port: 4050\n"
            "    name: \"search\"\n"
            "    namespace: \"search\"\n"
            "    readme_counterpart: \"broker search service\"\n"
            "    readme_gate: \"typespec (route-count only)\"\n"
            "    readme_status: \"M1 slices 1 done\"\n"
            "    map_row: \"| 4050 | api gw | search | broker-gateway :8081 | ONLINE |\"\n"
            "canary:\n"
            "  - port: 4114\n"
            "    name: \"voyager\"\n"
            "    namespace: \"voyager\"\n"
            "    incumbent: \"typescript/voyager-srv\"\n"
            "    incumbent_port: 3114\n"
            "    description: \"read-only voyager; contract pinned\"\n"
            "    readme_status: \"port complete, not cut over\"\n"
            "    map_registry_status: \"CANARY — merged (PR #453)\"\n"
            "    ratified: \"2026-09-22\"\n"
            "    ratification: \"originally ratified exception\"\n"
            "  - port: 4116\n"
            "    name: \"aegis\"\n"
            "    namespace: \"aegis\"\n"
            "    incumbent: \"typescript/aegis-srv\"\n"
            "    incumbent_port: 3116\n"
            "    description: \"TLA+ registry; rate limiter + TLC clamp\"\n"
            "    readme_status: \"port complete, not cut over\"\n"
            "    map_registry_status: \"CANARY — submitted\"\n"
            "    ratified: null\n"
            "    ratification: \"ruling-4 clause never issued\"\n")
        (self.tmp / "moleculer" / "README.md").write_text(
            "# t\n\n| App | Port | Counterpart | Parity gate | Status |\n|---|---|---|---|---|\n"
            "<!-- GENERATED: moleculer-port-registry BEGIN readme-twin-table -->\n"
            "STALE\n"
            "<!-- GENERATED: moleculer-port-registry END readme-twin-table -->\n\n"
            "after\n")
        (self.tmp / "moleculer" / "PORT-MAP.md").write_text(
            "| Port | Service | Namespace | Legacy counterpart | Registry status |\n"
            "|------|---------|-----------|--------------------|-----------------|\n"
            "<!-- GENERATED: moleculer-port-registry BEGIN portmap-table -->\n"
            "STALE\n"
            "<!-- GENERATED: moleculer-port-registry END portmap-table -->\n\n"
            "- Canary/deployment ports (9999) are the exception to one-live-authority.\n"
            "  x <!-- GENERATED: moleculer-port-registry BEGIN ratification-ledger -->\n"
            "STALE\n"
            "<!-- GENERATED: moleculer-port-registry END ratification-ledger -->\n")
        (self.tmp / "jvm" / "ARCHITECTURE.md").write_text(
            "1. something canary twins\n   1111/2222) are recorded in"
            " `moleculer/PORT-MAP.md`.\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _reg(self):
        return gen.load_registry(str(self.tmp))

    def test_generate_then_check_green(self):
        reg = self._reg()
        gen.regenerate(str(self.tmp), reg, check=False)
        results = gen.regenerate(str(self.tmp), reg, check=True)
        self.assertTrue(all(ok for _, _, ok in results),
                        f"second generation not byte-identical: {results}")

    def test_readme_region_rendered_from_registry(self):
        gen.regenerate(str(self.tmp), self._reg(), check=False)
        body = (self.tmp / "moleculer" / "README.md").read_text()
        self.assertIn("| `voyager/` | 4114 | `typescript/voyager-srv` (:3114) |", body)
        region = body.split("BEGIN readme-twin-table")[1].split("END")[0]
        self.assertNotIn("STALE", region, "placeholder content survived generation")

    def test_pending_port_rendered_loud(self):
        gen.regenerate(str(self.tmp), self._reg(), check=False)
        pm = (self.tmp / "moleculer" / "PORT-MAP.md").read_text()
        self.assertIn("- 4116 (aegis): PENDING — ruling-4 clause never issued", pm)
        self.assertIn("- 4114 (voyager): 2026-09-22 — originally ratified exception", pm)

    def test_canary_enumeration_follows_registry(self):
        gen.regenerate(str(self.tmp), self._reg(), check=False)
        pm = (self.tmp / "moleculer" / "PORT-MAP.md").read_text()
        self.assertIn("Canary/deployment ports (4114/4116) are the exception", pm)
        arch = (self.tmp / "jvm" / "ARCHITECTURE.md").read_text()
        self.assertIn("canary twins\n   4114/4116)", arch)

    def test_missing_markers_abort_loudly(self):
        (self.tmp / "moleculer" / "README.md").write_text("# no markers\n")
        with self.assertRaises(SystemExit):
            gen.regenerate(str(self.tmp), self._reg(), check=True)

    def test_duplicated_markers_abort_loudly(self):
        (self.tmp / "moleculer" / "README.md").write_text(
            "<!-- GENERATED: moleculer-port-registry BEGIN readme-twin-table -->\n"
            "<!-- GENERATED: moleculer-port-registry END readme-twin-table -->\n"
            "<!-- GENERATED: moleculer-port-registry BEGIN readme-twin-table -->\n"
            "<!-- GENERATED: moleculer-port-registry END readme-twin-table -->\n")
        with self.assertRaises(SystemExit):
            gen.regenerate(str(self.tmp), self._reg(), check=True)


if __name__ == "__main__":
    unittest.main()
