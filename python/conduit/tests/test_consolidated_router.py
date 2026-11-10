"""Consolidated kernel router (WorkRequest 8261651 prerequisite).

Locks the bare-``/api`` surface declared by
``typespec/v1/conduit-kernel/python/operations.tsp``:

1. Every consolidated path exists with the contract's method.
2. The legacy prefixed paths are still served (rollback surface preserved).
3. Static consolidated paths are registered before ``/api/{identifier}``, so
   ``/api/running`` / ``/api/stale`` / ``/api/breaker`` / ``/api/failure-recovery``
   are never parsed as plan ids.
4. The ``GET /api/{plan_id}`` vs ``GET /api/{session_id}`` collision resolves by
   identifier shape (UUID → session, otherwise → plan receipts), and the legacy
   prefixed names are refused instead of being answered as plan reads.
5. No duplicate (method, path) pair exists inside the consolidated router.

Style note: source/route-contract assertions — no DB required.

Usage:
  cd python/conduit
  PYTHONPATH=.:.. python3 -m pytest tests/test_consolidated_router.py -q
"""
import os
import sys

import pytest

_CONDUIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYTHON_ROOT = os.path.dirname(_CONDUIT_ROOT)
for _p in (_CONDUIT_ROOT, _PYTHON_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.api import routes_consolidated as rc  # noqa: E402


# ── Contract surface ─────────────────────────────────────────────────

CONSOLIDATED = {
    ("GET", "/api"): "consolidated_list_sessions",
    ("POST", "/api"): "consolidated_insert_receipt",
    ("GET", "/api/{plan_id}"): "consolidated_get_plan_receipts",
    ("GET", "/api/{plan_id}/raw"): "consolidated_get_receipts_raw",
    ("GET", "/api/{plan_id}/latest-type"): "consolidated_get_latest_receipt_type",
    ("DELETE", "/api/{plan_id}"): "consolidated_delete_receipts",
    ("GET", "/api/running"): "consolidated_running_sessions",
    ("GET", "/api/stale"): "consolidated_stale_sessions",
    ("GET", "/api/{session_id}"): "consolidated_get_session",
    ("PATCH", "/api/{session_id}/cost"): "consolidated_update_session_cost",
    ("POST", "/api/{session_id}/heartbeat"): "consolidated_update_session_heartbeat",
    ("POST", "/api/{session_id}/kill"): "consolidated_kill_session",
    ("GET", "/api/breaker"): "consolidated_get_breaker",
    ("POST", "/api/trip"): "consolidated_trip_breaker",
    ("POST", "/api/reset"): "consolidated_reset_breaker",
    ("POST", "/api/pause"): "consolidated_pause",
    ("POST", "/api/resume"): "consolidated_resume",
    ("GET", "/api/failure-recovery"): "consolidated_get_failure_recovery",
    ("POST", "/api/failure-recovery"): "consolidated_save_failure_recovery",
}

LEGACY = [
    ("GET", "/api/receipts/{plan_id}"),
    ("POST", "/api/receipts/"),
    ("GET", "/api/sessions/"),
    ("GET", "/api/sessions/{session_id}"),
    ("GET", "/api/breaker/"),
    ("POST", "/api/breaker/trip"),
]


def _route_keys():
    keys = set()
    for route in rc.router.routes:
        for method in getattr(route, "methods", set()) or set():
            keys.add((method, route.path))
    return keys


# ── 1. consolidated paths exist ──────────────────────────────────────

class TestConsolidatedSurface:
    def test_every_contract_path_is_registered(self):
        keys = _route_keys()
        missing = [k for k in CONSOLIDATED if k not in keys]
        assert not missing, f"missing consolidated routes: {missing}"

    def test_operation_names_are_unique(self):
        names = [r.name for r in rc.router.routes]
        assert len(names) == len(set(names)), f"duplicate route names: {names}"

    def test_no_duplicate_method_path_pairs(self):
        seen, dupes = set(), []
        for route in rc.router.routes:
            for method in getattr(route, "methods", set()) or set():
                key = (method, route.path)
                if key in seen:
                    dupes.append(key)
                seen.add(key)
        assert not dupes, f"duplicate (method, path): {dupes}"


# ── 2. static paths precede the parameterized route ──────────────────

class TestStaticPrecedence:
    def test_static_paths_registered_before_dynamic_routes(self):
        paths = [r.path for r in rc.router.routes]
        first_dynamic = next(i for i, p in enumerate(paths) if "{" in p)
        for static in (
            "/api",
            "/api/running",
            "/api/stale",
            "/api/breaker",
            "/api/failure-recovery",
        ):
            assert paths.index(static) < first_dynamic, (
                f"{static} must be registered before the parameterized routes"
            )

    def test_every_dynamic_route_is_last(self):
        paths = [r.path for r in rc.router.routes]
        first_dynamic = next(
            i for i, p in enumerate(paths) if "{" in p
        )
        assert all("{" in p for p in paths[first_dynamic:]), (
            "no static path may be registered after a parameterized one"
        )


# ── 3. collision resolution ──────────────────────────────────────────

class TestIdentifierCollision:
    @pytest.mark.parametrize(
        "identifier,expect_session",
        [
            ("51189b5b-46c8-4f8c-8e67-66fd726384d0", True),
            ("7b4efcd7-ca66-41e3-b79a-7b512a147124", True),
            ("8261651", False),
            ("0003", False),
            ("rec-8261651-IMPLEMENTATION-ab12cd", False),
        ],
    )
    def test_identifier_shape_selects_lookup(self, identifier, expect_session):
        assert rc.is_session_id(identifier) is expect_session

    def test_uuid_routes_to_session_handler(self, monkeypatch):
        calls = {}

        def fake_session(session_id):
            calls["session"] = session_id
            return {"id": session_id}

        def fake_receipts(plan_id):
            calls["plan"] = plan_id
            return {"plan_id": plan_id, "receipts": []}

        monkeypatch.setattr(rc, "get_session", fake_session)
        monkeypatch.setattr(rc, "get_plan_receipts", fake_receipts)

        sid = "51189b5b-46c8-4f8c-8e67-66fd726384d0"
        assert rc.get_api_resource(sid) == {"id": sid}
        assert calls == {"session": sid}

    def test_both_contract_paths_share_the_dispatcher(self, monkeypatch):
        """Both declared GET paths must produce the same shape-based answer."""
        calls = []

        def fake_session(session_id):
            calls.append(("session", session_id))
            return {"id": session_id}

        def fake_receipts(plan_id):
            calls.append(("plan", plan_id))
            return {"plan_id": plan_id, "receipts": []}

        monkeypatch.setattr(rc, "get_session", fake_session)
        monkeypatch.setattr(rc, "get_plan_receipts", fake_receipts)

        sid = "51189b5b-46c8-4f8c-8e67-66fd726384d0"
        assert rc.consolidated_get_plan_receipts(sid) == {"id": sid}
        assert rc.consolidated_get_session("8261651")["plan_id"] == "8261651"
        assert calls == [("session", sid), ("plan", "8261651")]

    def test_plan_id_routes_to_receipts_handler(self, monkeypatch):
        calls = {}

        def fake_session(session_id):  # pragma: no cover - must not run
            calls["session"] = session_id
            return {"id": session_id}

        def fake_receipts(plan_id):
            calls["plan"] = plan_id
            return {"plan_id": plan_id, "receipts": []}

        monkeypatch.setattr(rc, "get_session", fake_session)
        monkeypatch.setattr(rc, "get_plan_receipts", fake_receipts)

        assert rc.get_api_resource("8261651")["plan_id"] == "8261651"
        assert calls == {"plan": "8261651"}

    @pytest.mark.parametrize("segment", sorted(rc.RESERVED_LEGACY_SEGMENTS))
    def test_legacy_prefixed_names_are_refused_not_read_as_plans(
        self, segment, monkeypatch
    ):
        def boom(_identifier):  # pragma: no cover - must not run
            raise AssertionError("legacy segment must not reach a handler")

        monkeypatch.setattr(rc, "get_session", boom)
        monkeypatch.setattr(rc, "get_plan_receipts", boom)

        with pytest.raises(rc.HTTPException) as exc:
            rc.get_api_resource(segment)
        assert exc.value.status_code == 404


# ── 4. app wiring and legacy preservation ────────────────────────────

class TestAppWiring:
    @pytest.fixture(scope="class")
    def spec(self):
        from app.main import app

        return app.openapi()

    def test_app_exposes_consolidated_paths(self, spec):
        paths = spec["paths"]
        for method, path in CONSOLIDATED:
            assert path in paths, f"{path} missing from /openapi.json"
            assert method.lower() in paths[path], f"{method} {path} missing"

    def test_app_still_exposes_legacy_paths(self, spec):
        paths = spec["paths"]
        for method, path in LEGACY:
            assert path in paths, f"legacy {path} removed"
            assert method.lower() in paths[path], f"legacy {method} {path} removed"

    def test_main_mounts_the_consolidated_router(self):
        with open(os.path.join(_CONDUIT_ROOT, "app", "main.py")) as f:
            src = f.read()
        assert "from app.api.routes_consolidated import router as consolidated_router" in src
        assert "app.include_router(consolidated_router)" in src
