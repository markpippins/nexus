"""Guards for the Ollama endpoint used by absorb_candidates extraction.

The endpoint was a hardcoded LAN address. When DHCP moved the model host, every
chunk came back connection-refused — which reads in the logs exactly like a
backend outage — and the only remedy was a code change. These pin the override,
the URL it builds, and the error class a dead endpoint reports.
"""

import pathlib
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

consumer = pytest.importorskip(
    "absorb_candidates.consumer",
    reason="absorb_candidates needs its runtime deps (psycopg2) to import",
)


def test_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("NEXUS_OLLAMA_BASE_URL", raising=False)
    assert consumer.ollama_base_url() == "http://127.0.0.1:11434"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("NEXUS_OLLAMA_BASE_URL", "http://helium.attlocal.net:11434")
    assert consumer.ollama_base_url() == "http://helium.attlocal.net:11434"


def test_trailing_slash_does_not_double_up(monkeypatch):
    monkeypatch.setenv("NEXUS_OLLAMA_BASE_URL", "http://127.0.0.1:11434/")
    assert consumer.ollama_base_url() == "http://127.0.0.1:11434"


def test_empty_override_falls_back_to_loopback(monkeypatch):
    # An unset-but-exported variable in a unit file must not point at "".
    monkeypatch.setenv("NEXUS_OLLAMA_BASE_URL", "")
    assert consumer.ollama_base_url() == "http://127.0.0.1:11434"


def test_chat_posts_to_the_configured_endpoint(monkeypatch):
    seen = {}

    def fake_post(url, body, timeout=180):
        seen["url"] = url
        return {"message": {"content": '{"candidates": []}'}}

    monkeypatch.setattr(consumer, "_post_json", fake_post)
    monkeypatch.setenv("NEXUS_OLLAMA_BASE_URL", "http://10.1.2.3:9999")
    assert consumer.ollama_chat("some-model", "chunk text") == []
    assert seen["url"] == "http://10.1.2.3:9999/api/chat"


def test_dead_model_server_reads_as_llm_unavailable_not_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setenv("NEXUS_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    with pytest.raises(consumer.AbsorbError) as excinfo:
        consumer.ollama_chat("some-model", "chunk text")
    # E_TRANSIENT_LLM_UNAVAILABLE (not E_TRANSIENT_NETWORK): the batch should
    # report a dead model server, and it stays retryable either way.
    assert excinfo.value.error_code == "E_TRANSIENT_LLM_UNAVAILABLE"
    assert excinfo.value.retryable is True


def test_other_backends_keep_their_network_classification(monkeypatch):
    monkeypatch.delenv("NEXUS_OLLAMA_BASE_URL", raising=False)
    assert consumer._transport_error_class("http://localhost:3400/") == "E_TRANSIENT_NETWORK"
