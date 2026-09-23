#!/usr/bin/env python3
"""Shared plumbing for the push-based promotion flow (plan 0005).

Doctrine: D-2026-08-23-D (befca0bb). All external targets are isolated here
so the Adonis/Moleculer cutover only touches this module.
"""
import hashlib
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

NEBULA = os.environ.get("NEBULA_SRV", "http://localhost:3101")
FORUM = os.environ.get("ASSEMBLY_SRV", "http://localhost:3107")
STATE_DIR = os.environ.get(
    "PROMOTION_STATE_DIR", "/home/codex/dev/nexus/state/promotion-flow"
)
ENGINEER_USER_CANDIDATES = ("engineer", "engineer-ii", "engineer-iii")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://192.168.1.202:11434")


def _req(url, method="GET", body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, None
    except Exception as e:
        return 0, {"error": str(e)}


def get(url):
    return _req(url)


def post(url, body=None, timeout=60):
    return _req(url, "POST", body or {}, timeout)


def patch(url, body):
    return _req(url, "PATCH", body)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def engineer_user_id():
    st, users = get(f"{FORUM}/api/users")
    if st != 200:
        return None
    for u in users if isinstance(users, list) else []:
        if u.get("name") in ENGINEER_USER_CANDIDATES:
            return u["id"]
    return None


def forum_post(slug, title, body, status_rating=None):
    payload = {
        "title": title[:500],
        "body": body,
        "postedById": engineer_user_id(),
        "role": "engineer-ii",
        "model": "promotion-flow/0005",
    }
    if status_rating is not None:
        payload["statusRating"] = status_rating
    st, resp = post(f"{FORUM}/api/forums/{slug}/threads", payload)
    if st != 201:
        # forum-per-table fallback per doctrine
        log(f"forum_post({slug}) -> {st}, falling back to change-log")
        return post(f"{FORUM}/api/forums/change-log/threads", payload)
    return st, resp


def forum_comment(thread_id, body):
    return post(
        f"{FORUM}/api/forums/threads/{thread_id}/comments",
        {
            "body": body,
            "postedById": engineer_user_id(),
            "role": "engineer-ii",
            "model": "promotion-flow/0005",
        },
    )


def agent_record(title, content, tags, record_type="report"):
    return post(
        f"{NEBULA}/api/agent-records",
        {
            "recordType": record_type,
            "role": "engineer",
            "title": title,
            "content": content,
            "tags": tags,
            "level": 1,
            "visibilityScope": "all",
        },
    )


def inbox_ping(role, title, tags):
    """Tag-routed ping: an agent record addressed to <role>."""
    tags = list(dict.fromkeys(["to:" + role] + tags))
    return agent_record(title, title, tags, record_type="response")


def save_manifest(batch_id, manifest):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"batch-{batch_id}.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


def load_manifests():
    if not os.path.isdir(STATE_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(STATE_DIR)):
        if fn.startswith("batch-") and fn.endswith(".json"):
            with open(os.path.join(STATE_DIR, fn)) as f:
                out.append(json.load(f))
    return out


def candidate_set_identity(candidates):
    """Deterministic canonical identity of a candidate set.

    ST.01: a candidate-set's identity is the sha256 digest of the sorted
    candidate UUIDs. Order-independent and stable, so the same 20-card set
    always hashes to the same identity regardless of emission order or the
    timestamp/batch metadata that distinguish re-emissions.
    """
    ids = sorted(str(c["id"]) for c in candidates if c.get("id"))
    return hashlib.sha256(",".join(ids).encode("utf-8")).hexdigest()


def is_open_batch(manifest):
    """A promotion batch is OPEN when it is not fully executed.

    ``executed`` is set only by stage 3 once EVERY candidate is promoted or
    struck (fully drained). Until then the batch remains open and awaiting
    operator verdicts. Superseded duplicates (ST.02) count as open too — they
    are historical lineage, not fresh admission pressure.
    """
    return not manifest.get("executed")


def open_batch_covering_set(identity, manifests=None):
    """Return the first OPEN batch whose candidate-set identity matches.

    ST.01 admission guard: stage 1 must not re-emit a candidate set that an
    open batch already covers. A materially-changed set (different identity)
    or a lifecycle transition (the covering batch became executed) MAY emit a
    new batch — this check answers to both.
    """
    manifests = load_manifests() if manifests is None else manifests
    for m in manifests:
        if not is_open_batch(m):
            continue
        if candidate_set_identity(m.get("candidates", [])) == identity:
            return m
    return None


def mark_superseded(manifest, superseded_by, reason):
    """Append-only supersession marker (ST.02).

    Never deletes or rewrites historical candidate rows — only adds
    supersession lineage pointing a duplicate manifest at its active twin.
    The original manifest content is preserved verbatim.
    """
    manifest["superseded_by"] = superseded_by
    manifest["superseded_at"] = now_iso()
    manifest["supersession_reason"] = reason
    return manifest


def ollama_available(timeout=4):
    """Stage-0 gate: semantic discovery needs embeddings (ollama).
    Offline outside announced embed windows per ruling aaffca31."""
    st, _ = _req(f"{OLLAMA}/api/tags", timeout=timeout)
    return st == 200
