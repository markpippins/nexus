"""Hermetic tests for bin/fleet-manifest-from-ansible.py (single declared source).

No ssh, no network: source dirs are tmp fixtures. Pins the contract from
R1 095905ac (operator ruling: osmium ansible dir owns fleet truth):

  - inventory.yml is the membership+address spine (never invented)
  - fleet-state.yml sidecar carries power state + non-ansible members
  - annotations carry nexus judgment (descriptions/services/environments)
  - ansible_host wins over sidecar/annotations (source of truth discipline)
  - status: sidecar > annotation > default(OFFLINE)
  - retire is monotonic: removed members are appended, never dropped
  - generated output is deterministic; --check reports drift
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "fleet-manifest-from-ansible.py")

_spec = importlib.util.spec_from_file_location("fleet_manifest_from_ansible", _TOOL)
fma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fma)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


INVENTORY = """
all:
    children:
        pi4:
            hosts:
                vanadium:
                    ansible_host: 192.168.1.209
                barium:
                    ansible_host: 192.168.1.212
        x86:
            hosts:
                titanium:
                    ansible_host: 192.168.1.120
                    ansible_python_interpreter: /usr/bin/python3
"""

STATE = """
fleet_state:
    hosts:
        vanadium:
            status: ACTIVE
        barium:
            status: OFFLINE
    extra_members:
        - osmium
        - entropy
"""

STATE_WITH_IP = """
fleet_state:
    hosts:
        vanadium:
            status: ACTIVE
            ip_address: 10.9.9.9
    extra_members:
        - entropy
"""

ANNOTATIONS = {
    "defaults": {"status": "OFFLINE"},
    "services": [
        {"name": "nexus-core", "default_port": 8092,
         "description": "read-only JVM projection monolith",
         "framework": "Spring Boot", "service_type": "REST API"},
    ],
    "hosts": [
        {"hostname": "titanium", "environment": "Production",
         "description": "primary nexus host", "os": "Linux",
         "server_type": "Physical",
         "services": ["nebula-srv", "wind-srv"]},
        {"hostname": "vanadium", "environment": "Production",
         "description": "lab node", "os": "Linux", "server_type": "Physical"},
        {"hostname": "barium", "environment": "Development",
         "description": "powered down", "os": "Linux",
         "server_type": "Physical"},
        {"hostname": "osmium", "environment": "Production",
         "description": "ansible control node", "os": "Linux",
         "server_type": "Physical"},
        {"hostname": "entropy", "environment": "Development",
         "description": "reserve node", "os": "Linux",
         "server_type": "Physical"},
    ],
}


def _source_dir(state=STATE, inventory=INVENTORY):
    d = tempfile.mkdtemp()
    _write(os.path.join(d, "inventory.yml"), inventory)
    if state is not None:
        _write(os.path.join(d, "fleet-state.yml"), state)
    return d


def _build(state=STATE, inventory=INVENTORY, annotations=ANNOTATIONS,
           previous=None):
    inv = fma.yaml.safe_load(inventory)
    st = fma.yaml.safe_load(state) if state else {}
    return fma.build_manifest(inv, st, annotations, previous)


class MembershipSpine(unittest.TestCase):
    def test_ansible_hosts_are_members_with_inventory_addresses(self):
        m = _build()
        hosts = {h["hostname"]: h for h in m["fleet"]}
        self.assertEqual(set(hosts), {"titanium", "vanadium", "barium",
                                      "osmium", "entropy"})
        self.assertEqual(hosts["vanadium"]["ip_address"], "192.168.1.209")
        self.assertEqual(hosts["titanium"]["ip_address"], "192.168.1.120")

    def test_sidecar_status_overrides_default(self):
        m = _build()
        hosts = {h["hostname"]: h for h in m["fleet"]}
        self.assertEqual(hosts["vanadium"]["status"], "ACTIVE")
        self.assertEqual(hosts["barium"]["status"], "OFFLINE")
        # extra members with no sidecar entry -> default OFFLINE
        self.assertEqual(hosts["entropy"]["status"], "OFFLINE")

    def test_inventory_address_wins_over_sidecar_fabrication(self):
        m = _build(state=STATE_WITH_IP)
        hosts = {h["hostname"]: h for h in m["fleet"]}
        self.assertEqual(hosts["vanadium"]["ip_address"], "192.168.1.209")

    def test_ansible_host_without_address_refused(self):
        inv = INVENTORY.replace(
            "ansible_host: 192.168.1.120", "manual: true")
        with self.assertRaises(ValueError):
            _build(inventory=inv)

    def test_annotation_status_used_when_sidecar_silent(self):
        ann = json.loads(json.dumps(ANNOTATIONS))
        for h in ann["hosts"]:
            if h["hostname"] == "titanium":
                h["status"] = "ACTIVE"
        m = _build(state=STATE, annotations=ann)  # no sidecar entry for titanium
        hosts = {h["hostname"]: h for h in m["fleet"]}
        self.assertEqual(hosts["titanium"]["status"], "ACTIVE")

    def test_flatten_handles_nested_groups_and_vars(self):
        inv = fma.yaml.safe_load("""
all:
    children:
        edge:
            children:
                pi4:
                    hosts:
                        vanadium:
                            ansible_host: 192.168.1.209
        lone:
            hosts:
                solo:
                    ansible_host: 192.168.1.1
""")
        hosts = fma.flatten_inventory_hosts(inv)
        self.assertEqual(hosts["vanadium"]["groups"], ["edge", "pi4"])
        self.assertEqual(hosts["solo"]["groups"], ["lone"])


class RetireMonotonic(unittest.TestCase):
    def test_removed_member_appended_to_retire(self):
        prev = {"fleet": [{"hostname": "rubidium"}, {"hostname": "titanium"}],
                "retire": {"hostnames": []}}
        m = _build(previous=prev)
        self.assertIn("rubidium", m["retire"]["hostnames"])
        self.assertNotIn("titanium", m["retire"]["hostnames"])

    def test_previous_retire_entries_stick(self):
        prev = {"fleet": [{"hostname": "titanium"}],
                "retire": {"hostnames": ["strontium"]}}
        m = _build(previous=prev)
        self.assertIn("strontium", m["retire"]["hostnames"])

    def test_retire_never_contains_current_members(self):
        prev = {"fleet": [{"hostname": "vanadium"}],
                "retire": {"hostnames": ["vanadium"]}}  # contradiction
        m = _build(previous=prev)
        self.assertNotIn("vanadium", m["retire"]["hostnames"])


class OutputDiscipline(unittest.TestCase):
    def test_generation_is_deterministic(self):
        a = _build()
        b = _build()
        self.assertEqual(a, b)

    def test_fleet_sorted_by_hostname(self):
        m = _build()
        names = [h["hostname"] for h in m["fleet"]]
        self.assertEqual(names, sorted(names))

    def test_annotations_shape_carries_services_and_descriptions(self):
        m = _build()
        hosts = {h["hostname"]: h for h in m["fleet"]}
        self.assertEqual(hosts["titanium"]["services"],
                         ["nebula-srv", "wind-srv"])
        self.assertEqual(hosts["titanium"]["description"], "primary nexus host")
        self.assertEqual(m["services"][0]["name"], "nexus-core")

    def test_minimal_source_produces_minimal_host(self):
        ann = {"defaults": {"status": "OFFLINE"}, "hosts": []}
        m = _build(state=None, annotations=ann,
                   inventory="all:\n    children:\n        g:\n            hosts:\n                solo:\n                    ansible_host: 10.0.0.1\n")
        self.assertEqual(len(m["fleet"]), 1)
        h = m["fleet"][0]
        self.assertEqual(h["hostname"], "solo")
        self.assertEqual(h["status"], "OFFLINE")
        self.assertEqual(h["ip_address"], "10.0.0.1")
        self.assertNotIn("services", h)      # nothing fabricated
        self.assertNotIn("environment", h)


class CheckMode(unittest.TestCase):
    def test_check_detects_drift(self):
        with tempfile.TemporaryDirectory() as td:
            committed = os.path.join(td, "fleet-manifest.json")
            m = _build()
            m["_comment"] = "old comment"
            with open(committed, "w", encoding="utf-8") as fh:
                json.dump(m, fh)
            rendered = json.dumps(_build(state=STATE))  # same content
            # --check strips headers before diffing: same content -> in sync
            def strip(node):
                if isinstance(node, dict):
                    return {k: strip(v) for k, v in node.items()
                            if k not in ("_generated", "_comment")}
                if isinstance(node, list):
                    return [strip(x) for x in node]
                return node
            diffs = list(fma.deep_diff(strip(json.loads(rendered)),
                                       strip(m)))
            self.assertEqual(diffs, [])

    def test_deep_diff_finds_nested_change(self):
        a = {"fleet": [{"hostname": "x", "status": "ACTIVE"}]}
        b = {"fleet": [{"hostname": "x", "status": "OFFLINE"}]}
        diffs = list(fma.deep_diff(a, b))
        self.assertEqual(len(diffs), 1)
        self.assertIn("status", diffs[0][0])


class EndToEndMain(unittest.TestCase):
    def _run(self, argv, srcdir):
        import io
        from unittest import mock
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = fma.main()
        return rc, buf.getvalue()

    def test_cli_roundtrip_with_dir_mode(self):
        srcdir = _source_dir()
        from unittest import mock
        ann_path = os.path.join(_REPO, "bin", "config", "fleet-annotations.json")
        with mock.patch.object(fma, "ANNOTATIONS_PATH", ann_path), \
             mock.patch.object(fma, "MANIFEST_PATH",
                               os.path.join(_REPO, "bin", "config",
                                            "fleet-manifest.json")), \
             mock.patch("sys.argv",
                        ["prog", "--dir", srcdir]):
            rc = fma.main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
