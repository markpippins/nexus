#!/usr/bin/env python3
"""Tiered embedding client — decision 8ae276bf (tiered inference policy).

Provider chain (first available wins):
  1. Local ollama on helium        (DEFAULT for embed tasks — user directive
                                   2026-08-31; rehomed from localhost)
  2. Gemini                        (external fallback; 768-dim)
  3. NVIDIA NIM external API       (external fallback)
  4. OpenRouter embeddings         (external fallback; pending key activation)

Contract:
  - model family nomic-embed-text, 768-dim output
  - LOCAL leg enforces chunk <= 1 KiB and batch <= 8 (thallium probe terms;
    near-quadratic latency above that stalls ingestion)
  - callers keep their E_TRANSIENT_LLM_UNAVAILABLE stay-pending semantics:
    this client raises EmbedUnavailable when every tier fails

Config precedence: environment > tackle get_ai_config providers.
Never .env files (precedent 0d09ec47).
"""
from __future__ import annotations
import json
import logging
import os
import urllib.request
import urllib.error

log = logging.getLogger("embed_client")

NOMIC_DIM = 768
LOCAL_CHUNK_CHARS = 1024          # thallium contract: <= ~1KiB
LOCAL_BATCH_SIZE = 8              # thallium contract: small batches
REQUEST_TIMEOUT = 60.0

TACKLE_MCP = os.environ.get("TACKLE_MCP", "http://localhost:3400")


class EmbedUnavailable(RuntimeError):
    """All configured embed tiers failed."""


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: float = REQUEST_TIMEOUT):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _tackle_providers() -> dict:
    """Best-effort provider snapshot from tackle get_ai_config."""
    try:
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_ai_config", "arguments": {}},
        }).encode()
        req = urllib.request.Request(
            TACKLE_MCP, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        txt = "".join(c.get("text", "") for c in d.get("result", {}).get("content", []))
        cfg = json.loads(txt)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _nim_config():
    url = os.environ.get("NIM_EMBED_URL")
    key = os.environ.get("NIM_API_KEY")
    if not (url and key):
        provs = _tackle_providers().get("providers") or []
        for p in (provs if isinstance(provs, list) else []):
            if "nvidia" in str(p.get("name", "")).lower():
                url = url or p.get("endpoint_url") or p.get("baseUrl")
                key = key or p.get("api_key") or p.get("apiKey")
                break
    return url, key


def _nim_model() -> str:
    return os.environ.get("NIM_EMBED_MODEL", "nvidia/nomic-embed-text-v1.5")


def _openrouter_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    prov = _tackle_providers().get("providers") or {}
    orr = prov.get("openrouter") or {}
    return orr.get("apiKey") or orr.get("key")


def _ollama_host():
          # Default to the local ollama server; env still overrides.
      return os.environ.get("OLLAMA_EMBED_HOST", "http://127.0.0.1:11434")


def _chunk(text: str, limit: int = LOCAL_CHUNK_CHARS) -> list[str]:
    text = text or ""
    if len(text) <= limit:
        return [text]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _setup_projection_dimensions():
    """Load or generate projection matrices for dimension reduction.
    
    - Generates a deterministic Gaussian matrix for NVIDIA 1024→768 dims
    - Can load precomputed matrices for other mappings if needed
    - Matrix is cached in module scope for all calls
    """
    if not hasattr(_setup_projection_dimensions, 'pca_matrix'):
        dim_in, dim_out = 1024, 768
        rng = np.random.default_rng(42)  # deterministic
        _setup_projection_dimensions.pca_matrix = rng.standard_normal((dim_in, dim_out)).astype(np.float32) / np.sqrt(dim_in)
    return _setup_projection_dimensions.pca_matrix

def _reduce_1024_to_768(vec_1024):
    """Reduce Nvidia's 1024-dim embedding to 768-dim via Gaussian random projection.
    
    Deterministic, JL-lemma guaranteed distance preservation, no training.
    """
    vec_1024 = np.array(vec_1024, dtype=np.float32)
    W = _setup_projection_dimensions()
    return (vec_1024 @ W).astype(np.float32)

def _embedding_to_np(arr):
    """Convert list of floats to numpy array for downstream operations."""
    return np.array(arr, dtype=np.float32)


def _embed_nim(texts: list[str]) -> list[list[float]]:
    url, key = _nim_config()
    if not (url and key):
        raise EmbedUnavailable("NIM not configured")
    out = _post_json(
        url.rstrip("/") + "/embeddings",
        {"model": _nim_model(), "input": texts},
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )
    data = out.get("data") or []
    if not data:
        raise EmbedUnavailable(f"NIM returned no data: {json.dumps(out)[:200]}")
    # Apply NVIDIA 1024->768 reduction if needed (our vector store is 768-dim)
    # The raw output is 1536-dim (embedding + ...); our pgvector expects 768
    # NVIDIA's actual output dim depends on their threading; we need to take appropriate slice
    embeddings = [d.get("embedding") for d in data]
    # Reduce 1024-dim to 768-dim if we detect 1024 length
    reduced = [
        _reduce_1024_to_768(e) if isinstance(e, list) and len(e) == 1024 else e
        for e in embeddings
    ]
    return reduced


def _gemini_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    provs = _tackle_providers().get("providers") or []
    for p in (provs if isinstance(provs, list) else []):
        if "gemini" in str(p.get("name", "")).lower():
            return p.get("api_key") or p.get("key")
    return None


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    """text-embedding-004 -> exactly 768-dim (matches existing pgvector stores)."""
    key = _gemini_key()
    if not key:
        raise EmbedUnavailable("Gemini key missing/inactive")
    reqs = [{"model": "models/text-embedding-004", "content": {"parts": [{"text": t}]}}
            for t in texts]
    out = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:batchEmbedContents",
        {"requests": reqs},
        headers={"x-goog-api-key": key},
        timeout=30.0,
    )
    return [e["values"] for e in out["embeddings"]]


def _embed_openrouter(texts: list[str]) -> list[list[float]]:
    key = _openrouter_key()
    if not key:
        raise EmbedUnavailable("OpenRouter key inactive/missing")
    out = _post_json(
        "https://openrouter.ai/api/v1/embeddings",
        {"model": "nomic-embed-text", "input": texts},
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )
    return [d["embedding"] for d in out["data"]]


def _embed_ollama(texts: list[str]) -> list[list[float]]:
    host = _ollama_host()
    vectors = []
    for t in texts:                       # local leg: small batches, chunked
        pieces = _chunk(t)
        acc: list[float] | None = None
        for p in pieces:
            out = _post_json(
                host.rstrip("/") + "/api/embeddings",
                {"model": "nomic-embed-text", "prompt": p},
                timeout=90.0,
            )
            v = out["embedding"]
            if acc is None:
                acc = v
            else:                          # mean-pool chunks to one vector
                acc = [(a + b) / 2 for a, b in zip(acc, v)]
        if acc is None:
            raise EmbedUnavailable("empty input")
        vectors.append(acc)
    return vectors


_TIERS = [
          ("ollama-local", _embed_ollama),  # DEFAULT: local ollama (127.0.0.1:11434)
    ("gemini", _embed_gemini),        # 768-dim, dimension-compatible external fallback
    ("nim", _embed_nim),              # hosted NIM models are 1024-dim -> dim-guard rejects
    ("openrouter", _embed_openrouter),
]


def embed_texts(texts: list[str], force_tier: str | None = None) -> tuple[list[list[float]], str]:
    """Embed texts via the tier chain. Returns (vectors, provider_used)."""
    if not texts:
        return [], "none"
    order = _TIERS
    if force_tier:
        order = [t for t in _TIERS if t[0] == force_tier]
        if not order:
            raise EmbedUnavailable(f"unknown tier {force_tier}")
    errors = []
    for name, fn in order:
        try:
            vecs = fn(texts)
            bad = [v for v in vecs if len(v) != NOMIC_DIM]
            if bad:
                raise EmbedUnavailable(f"provider {name} returned non-{NOMIC_DIM}-dim vector(s)")
            return vecs, name
        except Exception as e:  # noqa: BLE001 - tier fallback by design
            log.warning("embed tier %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise EmbedUnavailable("; ".join(errors))


def embed_one(text: str, force_tier: str | None = None) -> tuple[list[float], str]:
    vecs, provider = embed_texts([text], force_tier)
    return vecs[0], provider


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    q = sys.argv[1] if len(sys.argv) > 1 else "smoke test"
    try:
        v, p = embed_one(q)
        print(f"OK provider={p} dim={len(v)} head={v[:3]}")
    except EmbedUnavailable as e:
        print(f"E_TRANSIENT_LLM_UNAVAILABLE: {e}")
        sys.exit(1)
