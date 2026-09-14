"""Sinks (spec C2): explicit failure policies, no silent defaults.

Every profile sink entry MUST carry a `policy` block. The literal string
`policy: default` expands to the documented type default at registration
(expansion logged); `custom.*` types have no default at all.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .core import ASSEMBLY_API, NEBULA_API, SINK_TYPE_DEFAULT_POLICY
from .errors import AbsorbError


# ── Policy handling ──────────────────────────────────────────────────

def expand_policy(sink_cfg: dict, profile_id: str) -> tuple[dict, bool]:
    """Returns (policy, expanded_from_default). Raises E_CONFIG_* on violations."""
    stype = sink_cfg.get("type")
    if not stype:
        raise AbsorbError("E_CONFIG_MISSING_SINK_POLICY", f"sink entry missing 'type': {sink_cfg}")
    raw = sink_cfg.get("policy")
    if raw is None:
        raise AbsorbError(
            "E_CONFIG_MISSING_SINK_POLICY",
            f"sink '{stype}' in profile '{profile_id}' has no policy block "
            "(use `policy: default` to accept the documented type default)",
        )
    if raw == "default":
        base = SINK_TYPE_DEFAULT_POLICY.get(stype)
        if base is None:
            raise AbsorbError(
                "E_CONFIG_MISSING_SINK_POLICY",
                f"sink '{stype}' has no documented default; declare an explicit policy",
            )
        return json.loads(json.dumps(base)), True
    if not isinstance(raw, dict) or "on_failure" not in raw:
        raise AbsorbError(
            "E_CONFIG_MISSING_SINK_POLICY",
            f"sink '{stype}' policy must be a mapping with on_failure",
        )
    pol = {
        "on_failure": raw["on_failure"],
        "retry": {"max_attempts": 3, "backoff": "exponential", **(raw.get("retry") or {})},
        "timeout_seconds": int(raw.get("timeout_seconds", 120)),
    }
    if pol["on_failure"] not in ("fail_run", "skip_sink"):
        raise AbsorbError("E_CONFIG_BAD_SINK_POLICY", f"on_failure={pol['on_failure']!r}")
    return pol, False


# ── Delivery with retry (transient-only) ─────────────────────────────

def _post_json(url: str, body: dict, timeout: int) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode() if e.fp else ""
        # 4xx = permanent; 5xx = transient
        cls = "E_PERMANENT" if 400 <= e.code < 500 else "E_TRANSIENT"
        raise AbsorbError(f"{cls}_HTTP_{e.code}", f"{url} -> {e.code}: {text[:160]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AbsorbError("E_TRANSIENT_NETWORK", str(e))


def _get_json(url: str, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AbsorbError(f"E_PERMANENT_HTTP_{e.code}", f"{url} -> {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AbsorbError("E_TRANSIENT_NETWORK", str(e))


def deliver_with_retry(policy: dict, fn):
    """Run fn() honoring retry.max_attempts for transient errors only."""
    retries = policy.get("retry") or {}
    attempts = int(retries.get("max_attempts", 3))
    backoff = retries.get("backoff", "exponential")
    delay = 0.5
    last_err: AbsorbError | None = None
    for i in range(max(attempts, 1)):
        try:
            return fn(), attempts - i - 1
        except AbsorbError as err:
            last_err = err
            if not err.retryable or i == attempts - 1:
                break
            time.sleep(delay if backoff == "fixed" else delay * (2 ** i))
    assert last_err is not None
    raise last_err


def deliver(sink_type: str, cfg: dict, policy: dict, ctx: dict) -> dict:
    """Deliver one payload to one sink.

    Returns {status: 'delivered'|'skipped', ref{}, skip_reason?, warnings[]}
    Raises AbsorbError only when policy.on_failure == 'fail_run'
    (or for E_CONFIG_*, which always halts the run).
    """
    timeout = int(policy.get("timeout_seconds", 120))

    def attempt():
        if sink_type == "pg.harvests":
            return _sink_pg_harvests(ctx, timeout)
        if sink_type == "mongo.mirror":
            return _sink_mongo_mirror(ctx, timeout)
        if sink_type == "assembly.forum":
            return _sink_assembly_forum(cfg, ctx, timeout)
        raise AbsorbError("E_CONFIG_UNKNOWN_SINK_TYPE", f"no sink implementation for {stype_or_raise(sink_type)}")

    def stype_or_raise(t):
        return t

    try:
        ref, remaining = deliver_with_retry(policy, attempt)
        return {"status": "delivered", "ref": ref, "attempts_left": remaining}
    except AbsorbError as err:
        if err.error_class == "configuration":
            raise  # configuration errors halt the run regardless of policy
        if policy["on_failure"] == "fail_run":
            raise
        return {"status": "skipped", "ref": {}, "skip_reason": err.error_code,
                "skip_detail": err.message}


# ── Implementations ──────────────────────────────────────────────────

def _sink_pg_harvests(ctx: dict, timeout: int) -> dict:
    """Canonical store — writes through nebula-srv REST (projection layer).
    Idempotent per document identity: the runner passes existing_harvest_id
    when an artifact already exists, so retries/watermark loss can never
    double-post (spec C2 + C3)."""
    if ctx.get("existing_harvest_id"):
        return {"harvest_id": ctx["existing_harvest_id"], "reused": True}
    doc = ctx["docklang"]
    status, body = _post_json(
        f"{NEBULA_API}/harvests",
        {
            "sourcePath": ctx["source_rel_path"],
            "sourceFilename": ctx["title"],
            "model": f"absorb/{ctx['profile_id']}",
            "sourceText": ctx["source_text"],
            "docklang": doc,
            "tags": ["absorb", ctx["profile_id"]],
            "metadata": {
                "absorb_profile_id": ctx["profile_id"],
                "absorb_profile_version": ctx["profile_version"],
                "conversation_id": ctx["conversation_id"],
                "source_date": ctx.get("source_date"),
                "content_hash": ctx["content_hash"],
                "segments": doc.get("segment_count"),
            },
            "sourceHash": ctx["content_hash"],
            "fileSize": len(ctx["source_text"]),
        },
        timeout,
    )
    hid = body.get("id") or body.get("harvest", {}).get("id")
    if not hid:
        raise AbsorbError("E_PERMANENT_SINK_BAD_RESPONSE", f"no harvest id in response: {json.dumps(body)[:160]}")
    return {"harvest_id": hid}


_MONGO_CACHE = {}

def _mongo_collection(timeout_ms: int):
    """Lazy pymongo import — Mongo is OPTIONAL everywhere (spec §9)."""
    if "coll" not in _MONGO_CACHE:
        try:
            from pymongo import MongoClient
        except ImportError as e:
            raise AbsorbError("E_TRANSIENT_MONGO_UNAVAILABLE", f"pymongo not installed: {e}")
        try:
            client = MongoClient(
                "mongodb://mongoUser:somePassword@localhost:27017/",
                serverSelectionTimeoutMS=timeout_ms,
            )
            coll = client["nexus"]["docklang"]
            coll.database.client.admin.command("ping")
            _MONGO_CACHE["coll"] = coll
        except Exception as e:
            raise AbsorbError("E_TRANSIENT_MONGO_UNAVAILABLE", str(e)[:160])
    return _MONGO_CACHE["coll"]


def _sink_mongo_mirror(ctx: dict, timeout: int) -> dict:
    coll = _mongo_collection(timeout * 1000)
    result = coll.update_one(
        {"file_metadata.source_file": ctx["source_rel_path"],
         "file_metadata.absorb_profile_id": ctx["profile_id"]},
        {"$set": {
            "file_metadata": {
                "source_file": ctx["source_rel_path"],
                "absorb_profile_id": ctx["profile_id"],
                "absorb_profile_version": ctx["profile_version"],
                "conversation_id": ctx["conversation_id"],
                "content_hash": ctx["content_hash"],
                "title": ctx["title"],
            },
            "turns": [{"index": t["index"], "role": t["role"], "content": t["content_md"]}
                      for t in ctx["turns"]],
            "docklang": ctx["docklang"],
        }, "$setOnInsert": {"ingested_at": __import__("datetime").datetime.utcnow()}},
        upsert=True,
    )
    return {"mongo_matched": result.matched_count, "mongo_upserted": result.upserted_id is not None}


def _resolve_user(alias: str, timeout: int) -> str:
    users = _get_json(f"{ASSEMBLY_API}/users", timeout)
    for u in users if isinstance(users, list) else users.get("items", []):
        if (u.get("alias") or u.get("name")) == alias:
            return u["id"]
    raise AbsorbError("E_PERMANENT_USER_NOT_FOUND", f"assembly user {alias!r}")


def _sink_assembly_forum(cfg: dict, ctx: dict, timeout: int) -> dict:
    """Thread per conversation in the target forum; reply per discourse segment."""
    slug = cfg.get("slug", "transcripts")
    granularity = cfg.get("granularity", "segment")
    alias = cfg.get("author_user_alias", "engineer-ii")

    forum = _get_json(f"{ASSEMBLY_API}/forums/by-slug/{slug}", timeout)
    forum_id = forum.get("id")
    if not forum_id:
        raise AbsorbError("E_PERMANENT_FORUM_NOT_FOUND", slug)
    user_id = _resolve_user(alias, timeout)

    title = ctx["title"]
    existing = _get_json(f"{ASSEMBLY_API}/forums/{slug}/threads", timeout)
    items = existing if isinstance(existing, list) else existing.get("items", [])
    thread = next((t for t in items if t.get("title") == title), None)

    if thread is None:
        _, created = _post_json(
            f"{ASSEMBLY_API}/forums/{slug}/threads",
            {"title": title, "body": f"Ingested by **absorb** · profile `{ctx['profile_id']}` v{ctx['profile_version']} · "
                                     f"{ctx['docklang'].get('turn_count')} turns / {ctx['docklang'].get('segment_count')} segments\n\n"
                                     f"Source: `{ctx['source_rel_path']}`",
             "postedById": user_id, "role": alias, "model": "absorb"},
            timeout,
        )
        thread_id = created.get("id") or created.get("thread", {}).get("id")
    else:
        thread_id = thread["id"]

    comments_posted = 0
    comments_skipped_existing = 0
    if granularity == "segment":
        # Idempotency: fetch existing comment bodies on this thread and skip
        # any turn already posted (protects re-runs after watermark loss).
        existing = _get_json(f"{ASSEMBLY_API}/forums/threads/{thread_id}", timeout)
        seen_bodies = {(c.get("body") or "").strip() for c in existing.get("comments", [])}
        units = ctx["docklang"].get("discourse_units") or []
        # One comment per TURN (block), not per arc: each block carries its own
        # provenance.role (user|assistant); the unit-level role is the arc owner
        # (user-led arcs are all 'user') and must not drive turn labels.
        for u in units:
            for b in u.get("blocks") or []:
                role = (b.get("provenance") or {}).get("role") or "unknown"
                role_label = "User" if role == "user" else "Assistant" if role == "assistant" else "Turn"
                btype = b.get("type") or "paragraph"
                text = ""
                if btype == "list" and b.get("items"):
                    text = "\n".join(str(i) for i in b["items"])
                elif btype == "separator":
                    text = "---"
                elif isinstance(b.get("content"), str):
                    text = b["content"]
                if not text.strip():
                    continue
                body_text = f"**[{role_label}]**\n\n{text}"
                if len(body_text) > 100_000:
                    body_text = body_text[:100_000] + "\n\n…(truncated)"
                if body_text.strip() in seen_bodies:
                    comments_skipped_existing += 1
                    continue
                _post_json(
                    f"{ASSEMBLY_API}/forums/threads/{thread_id}/comments",
                    {"body": body_text, "postedById": user_id, "role": alias, "model": "absorb"},
                    timeout,
                )
                comments_posted += 1

    return {"forum_slug": slug, "thread_id": thread_id,
            "comments_posted": comments_posted,
            "comments_skipped_existing": comments_skipped_existing}
