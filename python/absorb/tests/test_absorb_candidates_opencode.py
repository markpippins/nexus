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
