"""Guard: losm WR writes survive the V187 legacy-history demotion.

V187 (plan 8261650 Stage 6) renames ``vision.work_requests_history`` to
``..._v187_archive`` and does not recreate a write surface. The losm store
must therefore (a) stop mirroring when that relation is absent, (b) keep
writing canonical, and (c) read post-cutover rows back from canonical instead
of the legacy ``work_requests_losm`` view.

These are source-level guards because this tier runs without SQLAlchemy or a
live database. The behavioural suites remain ``losm-pg-suite.yml`` and the
canonical bridge battery.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "python/vision/losm-store/src/losm_store/canonical_bridge.py"
REPOSITORY = ROOT / "python/vision/losm-store/src/losm_store/repository.py"
INGESTOR = ROOT / "python/vision/losm-store/src/losm_store/ingestor.py"
HOST_API = ROOT / "python/vision/losm-host/losm/api/work_requests.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_bridge_detects_the_archived_legacy_relation():
    body = _text(BRIDGE)
    assert "def legacy_history_available" in body
    assert "to_regclass('vision.work_requests_history')" in body


def test_mirror_primitives_noop_when_history_is_absent():
    body = _text(BRIDGE)
    for fn in ("def mirror_insert_work_request",
               "def mirror_close_work_request",
               "def mirror_update_work_request"):
        start = body.index(fn)
        window = body[start:start + 900]
        assert "legacy_history_available(db)" in window, (
            f"{fn} must guard the renamed history base"
        )


def test_repository_falls_back_to_canonical_after_cutover():
    body = _text(REPOSITORY)
    assert "def _canonical_task_from_row" in body
    assert "resolution.work_request" in body
    assert "legacy_history_available(db)" in body
    # A transient PlanningTask (canonical fallback) is not in the session.
    assert "if wr in db:" in body


def test_writers_use_canonical_when_the_mirror_is_gone():
    for path in (REPOSITORY, INGESTOR, HOST_API):
        body = _text(path)
        assert "not legacy_history_available(db)" in body, (
            f"{path.name} must keep canonical writes after V187"
        )


def test_ingestor_reads_the_canonical_fallback():
    body = _text(INGESTOR)
    assert "get_work_request_by_wr_id" in body
    assert "db.query(PlanningTask).filter_by" not in body