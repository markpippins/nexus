"""Hermetic tests for bin/terrain-status-sync.py (one declared truth).

No DB, no terrain: both loaders are injected; the plan function is pure.
Pins the R1 1b15e5b7 contract:

  - join on lowercased hostname; syncs exactly status + active_flag
  - only diverging rows are queued for PUT; in-sync rows untouched
  - terrain-only hosts -> unknown, left alone (absence is data)
  - registry-only hosts -> missing-in-terrain, reported, never created
  - apply() mutates a full-row copy (round-trip PUT shape) and classifies
    success from the response body
"""

import importlib.util
import json
import os
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "terrain-status-sync.py")

_spec = importlib.util.spec_from_file_location("terrain_status_sync", _TOOL)
tss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tss)


REGISTRY = {
    "titanium": {"status": "ACTIVE", "active_flag": True},
    "barium": {"status": "OFFLINE", "active_flag": True},
    "osmium": {"status": "ACTIVE", "active_flag": True},
    "strontium": {"status": "RETIRED", "active_flag": False},
    "entropy": {"status": "OFFLINE", "active_flag": True},
}

def _terrain(**over):
    base = {
        "titanium": {"id": 1, "hostname": "titanium",
                     "status": "ONLINE", "activeFlag": True},
        "barium": {"id": 4, "hostname": "barium",
                   "status": "ONLINE", "activeFlag": True},
        "osmium": {"id": 6, "hostname": "osmium",
                   "status": "ONLINE", "activeFlag": True},
        "mystery": {"id": 9, "hostname": "mystery",
                    "status": "ONLINE", "activeFlag": True},
    }
    base.update(over)
    return base


class Plan(unittest.TestCase):
    def test_vocabulary_translation_active_to_online(self):
        """ACTIVE (registry) == ONLINE (terrain): same idea, different
        dialect — verbatim copying would blind the drift checker."""
        a = tss.plan(REGISTRY, _terrain())
        self.assertIn("titanium", a["in-sync"])   # ACTIVE→ONLINE, agrees

    def test_diverging_rows_queued_with_from_and_to(self):
        a = tss.plan(REGISTRY, _terrain())
        ups = {u["hostname"]: u for u in a["update"]}
        self.assertEqual(set(ups), {"barium"})     # terrain claims ONLINE
        self.assertEqual(ups["barium"]["from"],
                         {"status": "ONLINE", "active_flag": True})
        self.assertEqual(ups["barium"]["to"],
                         {"status": "OFFLINE", "active_flag": True})

    def test_in_sync_rows_untouched(self):
        a = tss.plan(REGISTRY, _terrain())
        self.assertIn("osmium", a["in-sync"])      # ACTIVE=ACTIVE
        self.assertNotIn("osmium", [u["hostname"] for u in a["update"]])

    def test_terrain_only_host_is_unknown_left_alone(self):
        a = tss.plan(REGISTRY, _terrain())
        unk = {u["hostname"] for u in a["unknown"]}
        self.assertEqual(unk, {"mystery"})
        self.assertNotIn("mystery", [u["hostname"] for u in a["update"]])

    def test_registry_only_host_reported_never_created(self):
        a = tss.plan(REGISTRY, _terrain())
        missing = {m["hostname"] for m in a["missing-in-terrain"]}
        self.assertEqual(missing, {"strontium", "entropy"})
        # and no update/insert action exists for them
        self.assertNotIn("strontium", [u["hostname"] for u in a["update"]])

    def test_unknown_registry_status_refused_loudly(self):
        reg = dict(REGISTRY, osmium={"status": "MAINTENANCE",
                                     "active_flag": True})
        with self.assertRaises(ValueError):
            tss.plan(reg, _terrain())

    def test_case_insensitive_join(self):
        a = tss.plan(REGISTRY, _terrain(
            titanium={"id": 1, "hostname": "Titanium",
                      "status": "ONLINE", "activeFlag": True}))
        self.assertIn("titanium", a["in-sync"])

    def test_active_flag_divergence_alone_triggers_update(self):
        a = tss.plan(REGISTRY, _terrain(
            osmium={"id": 6, "hostname": "osmium", "status": "ONLINE",
                    "activeFlag": False}))
        ups = {u["hostname"]: u for u in a["update"]}
        self.assertIn("osmium", ups)
        self.assertEqual(ups["osmium"]["from"]["status"],
                         ups["osmium"]["to"]["status"])   # status agrees
        self.assertFalse(ups["osmium"]["from"]["active_flag"])
        self.assertTrue(ups["osmium"]["to"]["active_flag"])


class Apply(unittest.TestCase):
    def _run_apply(self, actions, put_response=None, dry_run=False):
        sent = {}

        def fake_urlopen(req, timeout=15):
            sent["url"] = req.full_url
            sent["body"] = json.loads(req.data.decode())
            sent["method"] = req.get_method()

            class R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps(put_response).encode()

            return R()

        with mock.patch.object(tss, "load_terrain_row",
                               return_value={"id": 4, "hostname": "barium",
                                             "status": "ONLINE",
                                             "activeFlag": True,
                                             "notes": "keep me"}), \
             mock.patch.object(tss.urllib.request, "urlopen", fake_urlopen):
            changed = tss.apply_updates(actions, dry_run=dry_run)
        return changed, sent

    def test_dry_run_writes_nothing(self):
        actions = tss.plan(REGISTRY, _terrain())
        changed, sent = self._run_apply(actions, dry_run=True)
        self.assertEqual(changed, 0)
        self.assertEqual(sent, {})                 # no PUT issued

    def test_put_carries_full_row_with_synced_fields(self):
        actions = tss.plan(REGISTRY, _terrain())
        changed, sent = self._run_apply(
            actions, {"id": 4, "status": "OFFLINE", "activeFlag": True})
        self.assertEqual(changed, 1)
        self.assertEqual(sent["method"], "PUT")
        self.assertTrue(sent["url"].endswith("/api/v1/servers/4"))
        self.assertEqual(sent["body"]["status"], "OFFLINE")
        self.assertTrue(sent["body"]["activeFlag"])
        self.assertEqual(sent["body"]["notes"], "keep me")  # round-trip intact

    def test_response_mismatch_counts_as_failure(self):
        actions = tss.plan(REGISTRY, _terrain())
        changed, _ = self._run_apply(
            actions, {"id": 4, "status": "ONLINE", "activeFlag": True})
        self.assertEqual(changed, 0)               # terrain refused the write


if __name__ == "__main__":
    unittest.main()
