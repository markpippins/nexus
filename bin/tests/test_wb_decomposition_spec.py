"""Hermetic tests for the W-B decomposition blueprint (plan 8261654).

Covers bin/plan-gating-specs/w-decomposition.json's `decomposition` block and
the conformance harness in bin/check_plan_gating.py:
  - structural bounds (six packages, one owner each, deps, evidence, stop)
  - unified status vocabulary for {pending, active, completed, archived}
  - evidence-slot completeness + aggregate-matrix linkage
  - W-B6 CD-1/CD-2/CD-3 regression fixtures (projection, expiry, routing)
  - the unsatisfiable-end-state guard (runtime agreement is delegated)

No database, no network, no runtime projection-sync logs.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # worktree root
SPEC_PATH = REPO / "bin" / "plan-gating-specs" / "w-decomposition.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load("check_plan_gating", REPO / "bin" / "check_plan_gating.py")


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


@pytest.fixture()
def dec(spec) -> dict:
    return copy.deepcopy(spec["decomposition"])


class TestSpecShape:
    def test_blueprint_block_present(self, spec):
        assert spec["version"].startswith("wb-decomposition")
        assert spec["decomposition"]["plan"] == "8261654"
        assert spec["decomposition"]["wave"] == "W-B"
        assert spec["decomposition"]["scope_class"] == "decomposition-blueprint"

    def test_six_bounded_packages(self, spec):
        packages = spec["decomposition"]["packages"]
        assert set(packages) == {f"W-B{i}" for i in range(1, 7)}
        for pid, pkg in packages.items():
            assert pkg["owner"] in {"dba", "engineer", "reviewer", "planner", "architect"}
            assert isinstance(pkg["dependencies"], list)
            assert pkg["evidence_outputs"]
            assert pkg["stop_condition"]
            assert pkg["child_work_request"]
            assert isinstance(pkg["independent"], bool)

    def test_blueprint_is_conformant(self, spec):
        assert G.validate_decomposition(spec) == []

    def test_vocabulary_covers_four_canonical_states(self, spec):
        vocab = spec["decomposition"]["status_vocabulary"]
        assert vocab["canonical_states"] == ["pending", "active", "completed", "archived"]
        assert set(vocab["conduit_receipt_states"]) == set(vocab["canonical_states"])
        assert set(vocab["nebula_projection_states"]) == set(vocab["canonical_states"])
        # REVIEW_REJECT must be rework (active), never folded into completed.
        assert "REVIEW_REJECT" in vocab["conduit_receipt_states"]["active"]
        assert "REVIEW_REJECT" not in vocab["conduit_receipt_states"]["completed"]
        # archived must be expressible by both surfaces.
        assert vocab["nebula_projection_states"]["archived"] == "archived"

    def test_every_slot_has_the_five_required_fields(self, spec):
        dec_ = spec["decomposition"]
        slots = dec_["evidence_slots"]
        assert slots
        for slot_id, slot in slots.items():
            for field in G.WB_SLOT_FIELDS:
                assert slot.get(field), f"{slot_id}: {field} missing"
            assert slot["package"] in dec_["packages"]

    def test_matrix_links_every_slot_exactly_once(self, spec):
        dec_ = spec["decomposition"]
        matrix = dec_["aggregate_matrix"]
        assert matrix["requires_runtime_projection_sync_logs"] is False
        linked = [s for link in matrix["links"] for s in link["slots"]]
        assert sorted(linked) == sorted(dec_["evidence_slots"])
        assert len(linked) == len(set(linked))
        assert {link["package"] for link in matrix["links"]} == set(dec_["packages"])

    def test_runtime_agreement_is_delegated(self, spec):
        stop = spec["decomposition"]["stop_condition"]
        assert stop["claims_runtime_agreement"] is False
        assert stop["open_builder_ticket_guard"]
        assert set(stop["linked_packages"]) == set(spec["decomposition"]["packages"])
        assert stop["delegation"]["st01_dependency"] is False
        assert "W-A" in stop["delegation"]["parallel_with"]
        assert stop["delegation"]["runtime_agreement"]


class TestTamperDetection:
    def test_missing_package_is_rejected(self, dec):
        del dec["packages"]["W-B6"]
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("packages must be exactly" in f for f in findings)

    def test_owner_is_required(self, dec):
        dec["packages"]["W-B1"]["owner"] = ""
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("W-B1: exactly one owner required" in f for f in findings)

    def test_slot_field_is_required(self, dec):
        del dec["evidence_slots"]["WB1-DISPOSITION"]["verification_owner"]
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("WB1-DISPOSITION: missing/empty verification_owner" in f for f in findings)

    def test_unlinked_slot_is_rejected(self, dec):
        dec["aggregate_matrix"]["links"] = [
            link for link in dec["aggregate_matrix"]["links"] if link["package"] != "W-B3"
        ]
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("matrix must link every evidence slot exactly once" in f for f in findings)

    def test_vocabulary_must_cover_four_states(self, dec):
        dec["status_vocabulary"]["canonical_states"] = ["pending", "active", "completed"]
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("canonical_states must be" in f for f in findings)

    def test_runtime_claim_is_rejected(self, dec):
        dec["stop_condition"]["claims_runtime_agreement"] = True
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("claims_runtime_agreement must be false" in f for f in findings)

    def test_missing_fixture_clause_is_rejected(self, dec):
        dec["wb6_contract"]["fixtures"] = [
            f for f in dec["wb6_contract"]["fixtures"] if f["clause"] != "CD-2"
        ]
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("missing clauses ['CD-2']" in f for f in findings)

    def test_broken_fixture_expectation_is_rejected(self, dec):
        f1 = dec["wb6_contract"]["fixtures"][0]
        f1["cases"][0]["expect"]["open_generations"] = {}
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("WB6-F1" in f for f in findings)

    def test_matrix_cannot_require_projection_sync_logs(self, dec):
        dec["aggregate_matrix"]["requires_runtime_projection_sync_logs"] = True
        findings = G.validate_decomposition({"decomposition": dec})
        assert any("requires_runtime_projection_sync_logs must be false" in f for f in findings)


class TestCD1ProjectionSemantics:
    def test_row_order_does_not_hide_newer_open_generation(self):
        rows = [
            {"ticket_id": "gen2", "role": "builder", "generation": 2, "status": "open"},
            {"ticket_id": "gen1", "role": "builder", "generation": 1, "status": "completed"},
        ]
        got = G.project_ticket_generations(rows)
        assert got["newest"]["builder"]["status"] == "open"
        assert got["open_generations"] == {"builder": 2}
        assert got["hidden_open_generations"] == []

    def test_newer_cancelled_does_not_resurrect_older_open(self):
        rows = [
            {"ticket_id": "gen1", "role": "builder", "generation": 1, "status": "open"},
            {"ticket_id": "gen2", "role": "builder", "generation": 2, "status": "cancelled"},
        ]
        got = G.project_ticket_generations(rows)
        assert got["newest"]["builder"]["status"] == "cancelled"
        assert got["open_generations"] == {}
        # The older open generation is hidden by a newer terminal row: the
        # projection reports the defect signal instead of silently dropping it.
        assert got["hidden_open_generations"] == ["gen1"]

    def test_roles_are_projected_independently(self):
        rows = [
            {"ticket_id": "b1", "role": "builder", "generation": 1, "status": "completed"},
            {"ticket_id": "r1", "role": "reviewer", "generation": 1, "status": "failed"},
        ]
        got = G.project_ticket_generations(rows)
        assert {role: e["status"] for role, e in got["newest"].items()} == {
            "builder": "completed",
            "reviewer": "failed",
        }


class TestCD2ExpirySemantics:
    def test_expiry_emits_one_non_completion_event_per_generation(self):
        tickets = [
            {"ticket_id": "e1", "plan_id": "P", "role": "builder", "generation": 1,
             "status": "open", "expires_at": "2026-09-24T12:00:00Z"},
            {"ticket_id": "e2", "plan_id": "P", "role": "builder", "generation": 2,
             "status": "claimed", "expires_at": "2026-09-26T12:00:00Z"},
            {"ticket_id": "e3", "plan_id": "P", "role": "reviewer", "generation": 1,
             "status": "completed", "expires_at": "2026-09-24T12:00:00Z"},
        ]
        got = G.expiry_audit_events(tickets, "2026-09-25T12:00:00Z")
        assert [e["ticket_id"] for e in got["events"]] == ["e1"]
        assert got["events"][0]["generation"] == 1
        assert got["events"][0]["to_status"] == "expired"
        assert got["events"][0]["reason"] == "expiry_detection"
        assert got["events"][0]["implies_completion"] is False
        assert got["unexpired"] == ["e2"]
        assert got["already_terminal_untouched"] == ["e3"]
        assert got["every_expired_generation_has_event"] is True

    def test_future_or_absent_expiry_is_not_audited(self):
        tickets = [
            {"ticket_id": "f1", "role": "builder", "generation": 1,
             "status": "open", "expires_at": "2026-10-01T00:00:00Z"},
            {"ticket_id": "f2", "role": "builder", "generation": 2, "status": "stale"},
        ]
        got = G.expiry_audit_events(tickets, "2026-09-25T12:00:00Z")
        assert got["events"] == []
        assert got["unexpired"] == ["f1", "f2"]


class TestCD3RoutingSemantics:
    def test_blueprint_scope_returns_to_planner(self):
        got = G.route_review_reject({"kind": "review_reject", "scope": "blueprint"})
        assert got["route"] == "planner_rescope_event"
        assert got["creates_builder_ticket"] is False
        assert got["stops_builder_loop"] is True
        assert got["preserves_evidence"] is True

    def test_implementation_scope_routes_to_builder_rework(self):
        got = G.route_review_reject({"kind": "review_reject", "scope": "implementation"})
        assert got["route"] == "builder_rework_ticket"
        assert got["creates_builder_ticket"] is True
        assert got["stops_builder_loop"] is False

    def test_absent_scope_defaults_to_implementation(self):
        got = G.route_review_reject({"kind": "review_reject"})
        assert got["scope"] == "implementation"
        assert got["route"] == "builder_rework_ticket"
