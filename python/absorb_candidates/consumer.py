#!/usr/bin/env python3
"""absorb-candidates — downstream candidate identification (LLM stage).

Consumes docklang documents produced by absorb profiles, chunks them by
discourse-arc segments, extracts Specification Candidates with a local LLM
(Ollama), and attaches them to the existing harvest rows via nebula-srv
POST /api/harvest-candidates.

Deliberately SEPARATE from the absorb core package: absorb is an
ingest/projection layer (ratified constraint — no LLM stages inside it).
This consumer reads absorb's state and writes only through the declared
nebula API. Consumption tracking lives in absorb.watermarks under
profile_id='candidate-chunking', so the stockpiled corpus flows through
without re-ingestion and new documents are picked up automatically.

Usage:
    python3 -m absorb_candidates consume --limit 2 [--dry-run]
        [--model qwen2.5-coder:latest] [--chunk-chars 40000]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))            # this pkg's parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "absorb"))  # absorb core pkg

from absorb.core import ASSEMBLY_API, NEBULA_API, pg_fetchall, pg_execute, utcnow_iso  # noqa: E402
from absorb.errors import AbsorbError                                                  # noqa: E402

CONSUMER_PROFILE = "candidate-chunking"
CONSUMER_VERSION = 1

# ── Extraction schema + prompt (ported from legacy rover prompt.md) ──

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["Proposed", "Agreed", "Under Discussion", "Superseded"]},
                    "intent_description": {"type": "string"},
                    "requirements": {"type": "array", "items": {"type": "string"}},
                    "implementation_notes": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {"type": "array", "items": {"type": "string"}},
                    "code_snippets": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "language": {"type": "string"},
                            "purpose": {"type": "string"},
                            "raw_code": {"type": "string"}}},
                    },
                },
                "required": ["title", "intent_description"],
            },
        }
    },
    "required": ["candidates"],
}

SYSTEM_PROMPT = """You are a Software Archaeologist and Technical Analyst.
Extract actionable engineering intent ("Specification Candidates") from this
chunk of a developer chat transcript.

OUTPUT CONTRACT — use EXACTLY these keys, no others:
{"candidates": [{
    "title": str,                       # concise action-oriented title
    "status": "Proposed" | "Agreed" | "Under Discussion" | "Superseded",
    "intent_description": str,          # WHAT is wanted (never use "intent")
    "requirements": [str],
    "implementation_notes": [str],      # HOW it will be built; ARRAY of strings
    "open_questions": [str],
    "code_snippets": [{"language": str, "purpose": str, "raw_code": str}]
}]}

Rules:
1. Deduplicate intent: capture the final resolved state when a topic evolves.
2. Separate WHAT is wanted (intent_description) from HOW (implementation_notes).
3. Extract code word-for-word into code_snippets.raw_code; never truncate.
4. Flag unresolved questions/disagreements as open_questions.
5. Absolute precision: keep version numbers, stack choices, constraints exact.
If the chunk contains no actionable intent, return {"candidates": []}."""


# ── HTTP helpers ─────────────────────────────────────────────────────

# Ollama endpoint. Loopback by default: it used to be a hardcoded LAN address,
# so when DHCP moved the model host every chunk came back connection-refused —
# indistinguishable from a backend outage, and fixable only by editing code.
# NEXUS_OLLAMA_BASE_URL points absorb at a model server elsewhere instead.
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def ollama_base_url() -> str:
    """Resolve the Ollama base URL at call time, not import time.

    The env var is set per systemd unit and tests vary it without reimporting
    the module, so the lookup belongs at the call site."""
    return (os.environ.get("NEXUS_OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def _transport_error_class(url: str) -> str:
    """A refused or timed-out connection to the model server is an LLM outage,
    not a network one; the class decides whether the batch backs off or the
    backend is declared unavailable. Covers the default port and every
    configured base URL."""
    if "11434" in url or url.startswith(ollama_base_url()):
        return "E_TRANSIENT_LLM_UNAVAILABLE"
    return "E_TRANSIENT_NETWORK"


def _post_json(url: str, body: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode() if e.fp else ""
        cls = "E_PERMANENT" if e.code < 500 else "E_TRANSIENT"
        raise AbsorbError(f"{cls}_HTTP_{e.code}", f"{url} -> {e.code}: {text[:160]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AbsorbError(_transport_error_class(url), str(e)[:160])


# ── Tackle configbundle resolution (Rover lineage) ───────────────────

def _tackle_call(tool: str, args: dict) -> dict:
    req = urllib.request.Request(
        "http://localhost:3400/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": args}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read())
    return json.loads(d["result"]["content"][0]["text"])


def resolve_llm_target(role_config: str = "Rover") -> dict:
    """Resolve {base_url, model, api_key} from a tackle AI role config.

    Follows the Rover-lineage configbundle (cb-Rover-mod-deepseek-v4-pro /
    'rover-nemotron-3-super-free-nvidia'): role -> model -> provider
    endpoint. API key comes from opencode's auth store keyed by the
    provider's opencodeProvider name — never logged.
    """
    rc = _tackle_call("get_ai_role_config", {"role": role_config})
    model_id, provider_id = rc["model_id"], rc["provider_id"]
    model = _tackle_call("get_ai_model", {"id": model_id})
    ident = model.get("model_identifier") or model.get("ident") or model_id
    prov = _tackle_call("get_ai_provider", {"id": provider_id})
    p = prov.get("provider", prov)
    base_url = (p.get("endpoint_url") or "").rstrip("/")
    try:
        cfg = json.loads(p.get("config_json") or "{}")
    except json.JSONDecodeError:
        cfg = {}
    provider_name = cfg.get("opencodeProvider", "")
    api_key = ""
    try:
        auth = json.load(open(Path.home() / ".local/share/opencode/auth.json"))
        api_key = (auth.get(provider_name) or {}).get("key", "")
    except Exception:
        pass
    if not base_url or not api_key:
        raise AbsorbError(
            "E_CONFIG_LLM_TARGET_INCOMPLETE",
            f"role '{role_config}': base_url={bool(base_url)} key={bool(api_key)} "
            f"(provider '{provider_name}')",
        )
    return {"kind": "openai", "base_url": base_url, "model": ident,
            "api_key": api_key, "role_config": role_config}


def llm_extract(target: dict, chunk_text: str) -> list[dict]:
    """Dispatch extraction to the resolved target (OpenAI-compatible today)."""
    body = {
        "model": target["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"<transcript_chunk>\n{chunk_text}\n</transcript_chunk>"},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {target['api_key']}"}
    t0 = time.time()
    resp = None
    last_err: Exception | None = None
    for attempt in range(3):                       # NIM throws occasional 500s
        req = urllib.request.Request(f"{target['base_url']}/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                resp = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode() if e.fp else ""
            if e.code < 500:
                raise AbsorbError("E_PERMANENT_LLM_HTTP", f"{e.code}: {text[:160]}")
            last_err = AbsorbError("E_TRANSIENT_LLM_HTTP", f"{e.code}: {text[:120]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = AbsorbError("E_TRANSIENT_NETWORK", str(e)[:160])
        time.sleep(2 ** attempt * 1.5)
    if resp is None:
        raise last_err or AbsorbError("E_TRANSIENT_LLM_HTTP", "no response")
    content = resp["choices"][0]["message"]["content"]
    parsed = _loads_loose(content)
    out = [n for n in (normalize_candidate(c) for c in parsed.get("candidates", [])) if n]
    sys.stderr.write(f"    [llm] {target['model']} {time.time()-t0:.0f}s "
                     f"({resp.get('usage', {}).get('total_tokens', '?')} tok)\n")
    return out


def _loads_loose(content: str) -> dict:
    """Parse model output tolerantly. Observed failure modes: prose-wrapped
    JSON, code fences, stray leading/trailing braces (NIM nemotron-3-super).
    Strategy: direct parse -> fence strip -> raw_decode scan from every '{'
    until one yields a complete dict."""
    text = content.strip()
    # Each alternative is grouped explicitly (Sonar S5850): without it the
    # reader cannot tell whether the anchors bind to one branch or to the
    # whole alternation, and the intent here is "strip a leading fence OR a
    # trailing fence", anchored at the string edges.
    text = re.sub(r"(?:^```(?:json)?\s*)|(?:\s*```$)", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = dec.raw_decode(text, idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    # No complete object anywhere: if the output doesn't end with '}', it was
    # almost certainly cut by max_tokens — RETRYABLE (transient). Never mark
    # consumed on truncation; the next hourly run retries it.
    if not text.endswith("}"):
        raise AbsorbError("E_TRANSIENT_LLM_TRUNCATED",
                          f"len={len(content)} tail={text[-80:]!r}")
    tail = content.strip()[-160:]
    raise AbsorbError("E_PERMANENT_LLM_BAD_JSON",
                      f"head={content[:80]!r} tail={tail!r} len={len(content)}")


def normalize_candidate(c: dict) -> dict | None:
    """Coerce model-specific field naming/shapes to the canonical candidate
    schema (models drift: 'intent' vs 'intent_description', string vs array
    notes, 'code' vs 'raw_code'). Returns None if unusable."""
    if not isinstance(c, dict):
        return None
    title = c.get("title") or c.get("name")
    intent = (c.get("intent_description") or c.get("intent")
              or c.get("description") or "")
    if not title or not str(intent).strip():
        # derive a title from the first sentence of the intent
        if not str(intent).strip():
            return None
        title = str(intent).split(". ")[0][:80]
    def as_list(v):
        if v is None: return []
        return v if isinstance(v, list) else [str(v)]
    snippets = []
    for snip in (c.get("code_snippets") or []):
        if isinstance(snip, dict) and (snip.get("raw_code") or snip.get("code")):
            snippets.append({
                "language": snip.get("language", ""),
                "purpose": snip.get("purpose", ""),
                "raw_code": snip.get("raw_code") or snip.get("code", ""),
            })
    return {
        "title": str(title)[:300],
        "status": c.get("status", "Proposed"),
        "intent_description": str(intent),
        "requirements": as_list(c.get("requirements")),
        "implementation_notes": as_list(c.get("implementation_notes")),
        "open_questions": as_list(c.get("open_questions")),
        "code_snippets": snippets,
    }


def ollama_chat(model: str, chunk_text: str, timeout: int = 900) -> list[dict]:
    """Structured extraction via local Ollama. Returns validated candidate dicts.

    CPU-only inference reality (measured): warm short prompts ~4s, but a
    12-20k-char chunk generating 1-2k JSON tokens takes minutes. Timeout must
    be generous; num_predict bounds runaway generation."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"<transcript_chunk>\n{chunk_text}\n</transcript_chunk>"},
        ],
        "format": CANDIDATE_SCHEMA,          # Ollama structured output
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 2048},
    }
    resp = _post_json(f"{ollama_base_url()}/api/chat", body, timeout=timeout)
    content = resp.get("message", {}).get("content", "")
    parsed = _loads_loose(content)
    return [n for n in (normalize_candidate(c) for c in parsed.get("candidates", [])) if n]


# Model the opencode backend requests. This must be an id that `opencode models`
# actually lists: a retired id does not fail validation, it comes back from the
# provider as "Unexpected server error" with rc=1, which reads like an outage.
OPENCODE_MODEL = "opencode/big-pickle"


class BackendUnavailable(AbsorbError):
    """The model route itself is unusable — not one document.

    Raised when the provider rejects the request (quota, credentials) or stops
    answering. Grinding the rest of the batch cannot succeed, and every chunk
    costs a full timeout, so the run aborts instead of stalling silently.
    """

    def __init__(self, message: str):
        super().__init__("E_TRANSIENT_BACKEND_UNAVAILABLE", message)


# Definitive provider rejections. `opencode run` exits 0 while printing these,
# so the exit code cannot tell "out of credit" apart from "model answered" —
# the balance outage looked like a slow model for hours because of it.
BACKEND_REJECTION_MARKERS = (
    "insufficient balance",
    "insufficient_quota",
    "credit balance is too low",
    "top up at",
    "quota exceeded",
    "invalid api key",
    "invalid_api_key",
    "payment required",
    "unauthorized",
)


def backend_rejection(text: str) -> str | None:
    """The matched marker, if this output is a provider rejection."""
    low = (text or "").lower()
    return next((m for m in BACKEND_REJECTION_MARKERS if m in low), None)


def _run_bounded(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run argv under a timeout that also reaps its children.

    `opencode run` spawns helper processes (terrain-mcp, prompt bridge) that
    inherit the stdout pipe. `subprocess.run(timeout=...)` kills only the
    direct child, leaving those helpers alive and holding the pipe; a new
    session plus a group kill means a timed-out call leaves nothing behind.
    """
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def probe_backend(backend: str, timeout: int) -> None:
    """Prove the route answers before spending the batch on it.

    A dead route otherwise costs the full per-chunk timeout on every chunk of
    every document before a single failure is visible.
    """
    if backend != "opencode":
        return  # the other backends refuse quickly on first use
    try:
        res = _run_bounded(
            ["opencode", "run", "-m", OPENCODE_MODEL, "--pure",
             "Reply with exactly: ok"], timeout)
    except subprocess.TimeoutExpired as exc:
        raise BackendUnavailable(
            f"opencode gave no answer in {timeout}s — route stalled") from exc
    combined = f"{res.stdout or ''}\n{res.stderr or ''}"
    marker = backend_rejection(combined)
    if marker:
        raise BackendUnavailable(
            f"opencode rejected a probe request ({marker}): {combined.strip()[:160]}")


def opencode_extract(model_full: str, chunk_text: str,
                     timeout: int = 300) -> list[dict]:
    """Harness-mediated extraction: shells out to `opencode run` under the
    named model (default OPENCODE_MODEL). This is the low-rate-
    limit route for ox-alpha-family models — they have no raw OpenAI API
    route (prov-opencode resolves to conduit :3100), so the harness IS the
    transport. stdout carries the response; logs go to stderr."""
    msg = (SYSTEM_PROMPT +
           "\n\nRespond with ONLY the raw JSON object, nothing else."
           f"\n\n<transcript_chunk>\n{chunk_text}\n</transcript_chunk>")
    try:
        # stdin is pinned to /dev/null inside _run_bounded: opencode blocks at
        # init when stdin is an inherited pipe that nobody writes to.
        res = _run_bounded(
            ["opencode", "run", "-m", model_full, "--pure", msg], timeout)
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired is not an AbsorbError: the caller reads
        # `error_class` off whatever it catches, so letting this escape
        # crashed the whole batch with AttributeError instead of recording a
        # failed document.
        raise AbsorbError(
            "E_TRANSIENT_LLM_TIMEOUT",
            f"opencode exceeded {timeout}s on a {len(chunk_text)}-char chunk") from exc
    combined = f"{res.stdout or ''}\n{res.stderr or ''}"
    marker = backend_rejection(combined)
    if marker:
        # rc is 0 for these, so this check must come before the rc check.
        raise BackendUnavailable(
            f"opencode rejected the request ({marker}) — quota or credentials, "
            f"not a slow chunk: {combined.strip()[:160]}")
    if res.returncode != 0:
        raise AbsorbError("E_TRANSIENT_LLM_UNAVAILABLE",
                          f"opencode rc={res.returncode}: {res.stderr[-160:]}")
    parsed = _loads_loose(res.stdout)
    return [n for n in (normalize_candidate(c) for c in parsed.get("candidates", [])) if n]


# ── Document selection + chunking ────────────────────────────────────

def pending_documents(limit: int | None) -> list[dict]:
    """Most-recent-first selection (hourly cadence default)."""
    sql = """
      SELECT d.id::text, d.title, d.metadata->>'content_hash' AS content_hash,
             a.ref->>'harvest_id' AS harvest_id,
             count(s.seg_index)::int AS seg_count
      FROM absorb.documents d
      JOIN absorb.artifacts a ON a.document_id = d.id AND a.artifact_type='pg.harvests'
      LEFT JOIN absorb.segments s ON s.document_id = d.id
      WHERE NOT EXISTS (
        SELECT 1 FROM absorb.watermarks w
        WHERE w.profile_id=%s AND w.profile_version=%s
          AND w.source_fingerprint = d.id::text)
      GROUP BY d.id, d.title, content_hash, harvest_id
      ORDER BY d.created_at DESC
      LIMIT %s"""
    return pg_fetchall(sql, (CONSUMER_PROFILE, CONSUMER_VERSION, limit or 10**9))


def largest_pending_documents(limit: int | None) -> list[dict]:
    """Largest-first selection (manual deep-extraction runs): ranked by total
    transcript content length."""
    sql = """
      SELECT d.id::text, d.title, d.metadata->>'content_hash' AS content_hash,
             a.ref->>'harvest_id' AS harvest_id,
             COALESCE(sum(s.end_turn - s.start_turn + 1), 0)::int AS seg_count,
             COALESCE(sum(t.len), 0)::bigint AS content_len
      FROM absorb.documents d
      JOIN absorb.artifacts a ON a.document_id = d.id AND a.artifact_type='pg.harvests'
      LEFT JOIN absorb.segments s ON s.document_id = d.id
      LEFT JOIN LATERAL (
          SELECT sum(length(u.content_md)) AS len FROM absorb.turns u
          WHERE u.document_id = d.id
            AND u.turn_index BETWEEN s.start_turn AND s.end_turn
      ) t ON true
      WHERE NOT EXISTS (
        SELECT 1 FROM absorb.watermarks w
        WHERE w.profile_id=%s AND w.profile_version=%s
          AND w.source_fingerprint = d.id::text)
      GROUP BY d.id, d.title, content_hash, harvest_id
      ORDER BY content_len DESC
      LIMIT %s"""
    return pg_fetchall(sql, (CONSUMER_PROFILE, CONSUMER_VERSION, limit or 10))


def document_chunks(document_id: str, max_chars: int) -> list[str]:
    """Group whole discourse-arc segments into ~max_chars chunks (no mid-arc cuts)."""
    units = pg_fetchall(
        """SELECT seg_index, start_turn, end_turn, heading FROM absorb.segments
           WHERE document_id=%s ORDER BY seg_index""", (document_id,))
    turns = pg_fetchall(
        """SELECT turn_index, role, content_md FROM absorb.turns
           WHERE document_id=%s ORDER BY turn_index""", (document_id,))
    by_index = {t["turn_index"]: t for t in turns}
    chunks, buf, size = [], [], 0
    for u in units:
        span = [by_index[i]["content_md"] for i in range(u["start_turn"], u["end_turn"] + 1)
                if i in by_index]
        text = f"[{u['heading'] or 'segment'}]\n" + "\n\n".join(span)
        if size and size + len(text) > max_chars:
            chunks.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(text)
        size += len(text)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


# ── Nebula write-back ────────────────────────────────────────────────

def post_candidate(harvest_id: str, cand: dict, profile_tag: str) -> str:
    snippets = [
        {"language": s.get("language", ""), "purpose": s.get("purpose", ""),
         "rawCode": s.get("raw_code", "")}
        for s in (cand.get("code_snippets") or [])
    ]
    status, body = None, None
    # The DB status column is a PIPELINE lifecycle state (pending/linked/
    # useful/rejected/promoted/superseded) — NOT the model's alignment
    # status. New candidates enter as the DB default; alignment travels
    # as a tag.
    align = str(cand.get("status") or "").strip()
    align_tag = ("alignment:" + align.lower().replace(" ", "-")) if align else ""
    data = json.dumps({
        "harvestId": harvest_id,
        "title": str(cand["title"])[:300],
        "intentDescription": cand.get("intent_description", ""),
        "implementationNotes": [str(x) for x in (cand.get("implementation_notes") or [])],
        "requirements": [str(x) for x in (cand.get("requirements") or [])],
        "openQuestions": [str(x) for x in (cand.get("open_questions") or [])],
        "codeSnippets": snippets,
        "tags": list(dict.fromkeys(
            [t for t in ["absorb", profile_tag, "candidate-chunking", align_tag] if t])),
    }).encode()
    req = urllib.request.Request(f"{NEBULA_API}/harvest-candidates", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
            return body.get("id") or ""
    except urllib.error.HTTPError as e:
        text = e.read().decode() if e.fp else ""
        raise AbsorbError(
            "E_PERMANENT_CANDIDATE_REJECTED" if e.code < 500 else "E_TRANSIENT_NEBULA",
            f"{e.code}: {text[:200]} | payload_status={cand.get('status')!r}")


def mark_consumed(document_id: str) -> None:
    pg_execute(
        "INSERT INTO absorb.watermarks (profile_id, profile_version, source_fingerprint) "
        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
        (CONSUMER_PROFILE, CONSUMER_VERSION, document_id))



# 0 (default) = never retire a document because of timeouts. A provider-wide
# outage looks exactly like one oversized chunk, and silently consuming the
# queue during an outage would discard corpus content that was never
# extracted. Operators opt in to a ceiling for one pathological document.
MAX_EXTRACTION_TIMEOUTS = int(os.environ.get("ABSORB_MAX_EXTRACTION_TIMEOUTS", "0"))


def note_extraction_timeout(document_id: str) -> int:
    """Count extraction timeouts for one document.

    A chunk that reliably exceeds the model budget never makes progress, so
    without a ceiling the same document is retried forever and every run burns
    the entire timeout again.
    """
    rows = pg_fetchall(
        "UPDATE absorb.documents "
        "SET metadata = jsonb_set(coalesce(metadata, '{}'::jsonb), "
        "                         '{candidate_timeouts}', "
        "                         to_jsonb(coalesce((metadata->>'candidate_timeouts')::int, 0) + 1)) "
        "WHERE id = %s "
        "RETURNING (metadata->>'candidate_timeouts')::int AS attempts",
        (document_id,))
    return int(rows[0]["attempts"]) if rows else 0


# ── Main loop ────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="absorb-candidates")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("consume", help="extract candidates from unconsumed documents")
    c.add_argument("--limit", type=int, default=5,
                   help="batch size; hourly cadence uses 5 most-recent")
    c.add_argument("--probe-seconds", type=int, default=90,
                   help="prove the model route answers before doing any work; "
                        "0 disables the probe")
    c.add_argument("--timeout", type=int, default=300,
                   help="per-chunk extraction budget in seconds (default 300)")
    c.add_argument("--backend", choices=["opencode", "tackle", "ollama"],
                   default="opencode",
                   help=f"opencode = harness-mediated {OPENCODE_MODEL} (low rate limits, "
                        "default per operator); tackle = Rover/Nemotron configbundle; "
                        "ollama = local fallback")
    c.add_argument("--model", default="qwen2.5-coder:latest",
                   help="only used with --backend ollama")
    c.add_argument("--role-config", default="Rover",
                   help="tackle AI role config driving the tackle backend")
    c.add_argument("--chunk-chars", type=int, default=12000)
    c.add_argument("--largest", type=int, metavar="N",
                   help="process the N largest pending documents (by transcript size) "
                        "instead of most-recent")
    c.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if getattr(args, 'largest', None):
        docs = largest_pending_documents(args.largest)
        print(f"largest {len(docs)} pending document(s):")
        for d in docs:
            print(f"  ~ {int(d.get('content_len', 0))//1024}KB {d['title'][:60]}")
    else:
        docs = pending_documents(args.limit)
        print(f"{len(docs)} document(s) pending candidate extraction")
    if not args.dry_run and args.probe_seconds > 0:
        try:
            probe_backend(args.backend, args.probe_seconds)
        except BackendUnavailable as err:
            print(f"\n\u26d4 BACKEND UNAVAILABLE — batch aborted before doing any work\n"
                  f"   {err.message[:200]}\n"
                  f"   Fix the route (quota, credentials, host). No document was consumed.")
            return 2

    ok = failed = candidates_written = 0
    consecutive_timeouts = 0
    for d in docs:
        label = f"{(d['title'] or '?')[:52]}"
        try:
            chunks = document_chunks(d["id"], args.chunk_chars)
            if args.dry_run:
                print(f"  ~ {label} — would run {len(chunks)} chunk(s)")
                continue
            found = []
            t0 = time.time()
            target = None
            if args.backend == "tackle":
                target = resolve_llm_target(args.role_config)
            oc_model = OPENCODE_MODEL
            for i, ch in enumerate(chunks):
                if args.backend == "opencode":
                    found.extend(opencode_extract(oc_model, ch, timeout=args.timeout))
                elif target:
                    found.extend(llm_extract(target, ch))
                else:
                    found.extend(ollama_chat(args.model, ch))
            wrote = 0
            for cand in found:
                cid = post_candidate(d["harvest_id"], cand, "absorb")
                wrote += 1 if cid else 0
            mark_consumed(d["id"])
            candidates_written += wrote
            ok += 1
            consecutive_timeouts = 0
            print(f"  ✓ {label} — {len(chunks)} chunk(s), {wrote} candidate(s) "
                  f"[{time.time()-t0:.0f}s]")
        except BackendUnavailable as err:
            print(f"  ✗ {label} — [{err.error_code}] {err.message[:160]}")
            print("\n\u26d4 BACKEND UNAVAILABLE — aborting the batch; the queue is intact")
            return 2
        except AbsorbError as err:
            failed += 1
            print(f"  ✗ {label} — [{err.error_code}] ({err.error_class}) {err.message[:120]}")
            if err.error_code == "E_TRANSIENT_LLM_TIMEOUT":
                consecutive_timeouts += 1
                if consecutive_timeouts >= 2:
                    print(f"\n\u26d4 BACKEND STALLED — {consecutive_timeouts} timeouts in a row "
                          f"with no successful chunk; aborting the batch")
                    return 2
            if err.error_code == "E_TRANSIENT_LLM_TIMEOUT" and MAX_EXTRACTION_TIMEOUTS > 0:
                attempts = note_extraction_timeout(d["id"])
                if attempts >= MAX_EXTRACTION_TIMEOUTS:
                    mark_consumed(d["id"])
                    print(f"    skipped after {attempts} timeouts — chunk exceeds "
                          f"the model budget; queued no further")
        except Exception as err:  # never let one doc kill the batch
            failed += 1
            print(f"  ✗ {label} — [E_UNEXPECTED] {type(err).__name__}: {str(err)[:120]}")
            # Unclassified failures stay queued. Consuming the document here
            # would drop its content without ever extracting a candidate, and
            # reading `error_class` off an arbitrary exception raised
            # AttributeError, which took the whole batch down with it.

    print(f"\ndone: {ok} | failed: {failed} | candidates written: {candidates_written}")
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
