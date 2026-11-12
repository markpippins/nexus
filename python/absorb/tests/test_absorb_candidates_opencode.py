"""Guards for the opencode extraction path in absorb_candidates.

Two defects motivated these:

1. The backend hardcoded ``opencode/x-preview-f-free``, which is not in
   ``opencode models``. A retired id does not fail validation — the provider
   answers with "Unexpected server error" and rc=1, which looks like an outage.
2. It let ``opencode run`` inherit stdin. When stdin is a pipe nobody writes
   to, opencode blocks at init and prints nothing, so the call hangs until the
   subprocess timeout expires.
"""

import pathlib
import re
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

consumer = pytest.importorskip(
    "absorb_candidates.consumer",
    reason="absorb_candidates needs its runtime deps (psycopg2) to import",
)


def test_opencode_model_is_a_concrete_provider_model_id():
    assert re.fullmatch(
        r"[a-z0-9][a-z0-9-]*/[A-Za-z0-9._-]+", consumer.OPENCODE_MODEL
    ), consumer.OPENCODE_MODEL


def test_opencode_model_is_not_the_retired_id():
    assert consumer.OPENCODE_MODEL != "opencode/x-preview-f-free"


def test_opencode_extract_pins_stdin_so_it_cannot_hang(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return types.SimpleNamespace(
            returncode=0, stdout='{"candidates": []}', stderr=""
        )

    monkeypatch.setattr(consumer.subprocess, "run", fake_run)
    assert consumer.opencode_extract("opencode/big-pickle", "chunk text") == []

    assert seen["kwargs"]["stdin"] is consumer.subprocess.DEVNULL
    assert seen["argv"][:3] == ["opencode", "run", "-m"]
    assert seen["argv"][3] == "opencode/big-pickle"
    assert "--pure" in seen["argv"]


def test_opencode_extract_raises_on_nonzero_exit(monkeypatch):
    def fake_run(argv, **kwargs):
        return types.SimpleNamespace(
            returncode=1, stdout="", stderr="Unexpected server error"
        )

    monkeypatch.setattr(consumer.subprocess, "run", fake_run)
    with pytest.raises(consumer.AbsorbError) as exc:
        consumer.opencode_extract("opencode/big-pickle", "chunk text")
    assert exc.value.error_code == "E_TRANSIENT_LLM_UNAVAILABLE"


# ── timeout handling ──────────────────────────────────────────────────
# Third defect: `opencode run` exceeding its budget raised TimeoutExpired,
# which escaped `opencode_extract`. The batch's catch-all then read
# `error_class` off it, raised AttributeError, and killed the whole run --
# after already marking the document consumed, so its content was dropped.

def test_opencode_timeout_becomes_a_classified_error(monkeypatch):
    def fake_run(argv, **kwargs):
        raise consumer.subprocess.TimeoutExpired(cmd="opencode", timeout=300)

    monkeypatch.setattr(consumer.subprocess, "run", fake_run)
    with pytest.raises(consumer.AbsorbError) as exc:
        consumer.opencode_extract("opencode/big-pickle", "chunk text")
    assert exc.value.error_code == "E_TRANSIENT_LLM_TIMEOUT"


def _one_doc(monkeypatch):
    monkeypatch.setattr(
        consumer, "pending_documents",
        lambda limit: [{"id": "doc-1", "title": "huge doc", "harvest_id": "h-1"}])


def test_unexpected_error_does_not_consume_the_document(monkeypatch):
    consumed = []
    _one_doc(monkeypatch)

    def boom(document_id, max_chars):
        raise RuntimeError("boom")

    monkeypatch.setattr(consumer, "document_chunks", boom)
    monkeypatch.setattr(consumer, "mark_consumed", consumed.append)

    rc = consumer.main(["consume", "--limit", "1"])

    assert consumed == [], "a crash must not watermark the document away"
    assert rc == 1


def test_timeout_under_the_ceiling_stays_queued(monkeypatch):
    consumed = []
    _one_doc(monkeypatch)
    monkeypatch.setattr(consumer, "document_chunks", lambda i, m: ["chunk"])

    def slow(*args, **kwargs):
        raise consumer.AbsorbError("E_TRANSIENT_LLM_TIMEOUT", "too slow")

    monkeypatch.setattr(consumer, "opencode_extract", slow)
    monkeypatch.setattr(consumer, "note_extraction_timeout", lambda doc: 1)
    monkeypatch.setattr(consumer, "mark_consumed", consumed.append)

    consumer.main(["consume", "--limit", "1", "--backend", "opencode"])

    assert consumed == [], "retries below the ceiling must keep the document"


def test_timeout_at_the_ceiling_advances_the_queue(monkeypatch):
    consumed = []
    _one_doc(monkeypatch)
    monkeypatch.setattr(consumer, "document_chunks", lambda i, m: ["chunk"])

    def slow(*args, **kwargs):
        raise consumer.AbsorbError("E_TRANSIENT_LLM_TIMEOUT", "too slow")

    monkeypatch.setattr(consumer, "opencode_extract", slow)
    monkeypatch.setattr(consumer, "MAX_EXTRACTION_TIMEOUTS", 3)
    monkeypatch.setattr(consumer, "note_extraction_timeout", lambda doc: 3)
    monkeypatch.setattr(consumer, "mark_consumed", consumed.append)

    consumer.main(["consume", "--limit", "1", "--backend", "opencode"])

    assert consumed == ["doc-1"], "a permanently oversized chunk must not pin the queue"


def test_documents_are_never_retired_by_default(monkeypatch):
    """A provider outage looks like a slow chunk; retiring on timeout would
    silently discard the backlog. Retirement must be operator opt-in."""
    assert consumer.MAX_EXTRACTION_TIMEOUTS == 0
