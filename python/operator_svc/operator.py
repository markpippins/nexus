#!/usr/bin/env python3
"""operator.operator — Core Operator logic.

Builds the system prompt, manages conversation context via a 10-item
FIFO continuity queue with session persistence, tool result caching,
and a hybrid topic+entity index for long-term memory.
"""

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set

import httpx

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_TACKLE_PARENT = os.path.dirname(_SCRIPT_DIR)  # nexus/python/
if _TACKLE_PARENT not in sys.path:
    sys.path.insert(0, _TACKLE_PARENT)

from tackle.inference import call_llm
from tackle.db import get_role_config
from tackle.tools_aggregator_client import SyncToolsAggregatorClient, ToolDefinition

from operator_svc.api_proxy import proxy_request
from operator_svc.chat_store import log_prompt_response, save_queue, load_queue

_log = logging.getLogger("operator")

MAX_TOOL_ROUNDS = 5
CONTINUITY_QUEUE_MAX = 10
ENTITY_INDEX_MAX = 100  # max items across all entities
SHORT_EXCHANGE_THRESHOLD = 200  # chars — skip LLM compaction below this

# ── Tools Aggregator Client ─────────────────────────────────────────
#
# The Operator no longer hard-codes its tool list. Instead it discovers
# them at chat-request time from the running `tools-aggregator.service`
# (port 3210, see nexus/typescript/tools-aggregator/). The aggregator
# composes tool registries from every available MCP — conduit-mcp,
# tackle-mcp, and (when brought online) knowledge-mcp, vision-mcp,
# service-broker-mcp — so the Operator gains access to all of them
# without each one needing its own xpath in this file.
#
# Tool-list discovery is TTL-bounded (DISCOVERED_TOOLS_TTL seconds).
# If the aggregator is unreachable at startup, we degrade to an empty
# tool list and surface the failure in logs — the LLM gets a clear
# "no tools currently available" system prompt section and the user
# sees a verbose reply rather than silent false-positive tool calls.

_TOOLS_AGGREGATOR_URL = os.environ.get("TOOLS_AGGREGATOR_URL", "http://localhost:3210")
_tools_client: Optional[SyncToolsAggregatorClient] = None
_tools_client_lock = threading.Lock()
_discovered_tools_cache: List[ToolDefinition] = []
_discovered_tools_timestamp: float = 0.0
DISCOVERED_TOOLS_TTL = 300  # 5 min


def _get_tools_client() -> SyncToolsAggregatorClient:
    """Lazy singleton for the synchronous tools-aggregator client."""
    global _tools_client
    if _tools_client is None:
        with _tools_client_lock:
            if _tools_client is None:
                c = SyncToolsAggregatorClient(_TOOLS_AGGREGATOR_URL)
                c.init()
                _tools_client = c
                _log.info(
                    "tools-aggregator client init'd at %s — %d tools available",
                    _TOOLS_AGGREGATOR_URL,
                    len(c.list_tools()),
                )
    return _tools_client


def _refresh_discovered_tools() -> List[ToolDefinition]:
    """TTL-bounded refresh from tools-aggregator.

    Returns the cached list if TTL hasn't expired. Otherwise re-inits the
    sync client (which triggers aggregator-side re-discovery + cache pop)
    and updates the local copy. On failure, falls back to the last good
    cache (possibly empty) and logs the failure.
    """
    global _discovered_tools_cache, _discovered_tools_timestamp, _tools_client
    if _discovered_tools_cache and (time.time() - _discovered_tools_timestamp) <= DISCOVERED_TOOLS_TTL:
        return _discovered_tools_cache

    try:
        client = _get_tools_client()
        client.init()  # forces aggregator-side re-discovery + local cache refresh
        tools = client.list_tools()
        _discovered_tools_cache = list(tools)
        _discovered_tools_timestamp = time.time()
        _log.info("Discovered %d tools from aggregator", len(_discovered_tools_cache))
    except Exception as e:
        _log.warning("tools-aggregator refresh failed at %s: %s — "
                     "using stale cache (%d tools)",
                     _TOOLS_AGGREGATOR_URL, e, len(_discovered_tools_cache))
        # Refresh the client next time around — a singleton stuck on a dead
        # socket doesn't help anyone.
        with _tools_client_lock:
            try:
                if _tools_client:
                    _tools_client.close()
            except Exception:
                pass
            _tools_client = None

    return _discovered_tools_cache


def _format_tools_for_prompt(tools: List[ToolDefinition]) -> str:
    """Format the discovered tool list for the LLM system prompt."""
    if not tools:
        return "  (no tools currently available — tools-aggregator unreachable or zero tools discovered)"
    lines = []
    by_service: Dict[str, List[ToolDefinition]] = {}
    for t in tools:
        by_service.setdefault(t.service, []).append(t)
    for svc in sorted(by_service):
        svctools = by_service[svc]
        lines.append(f"## {svc} ({len(svctools)} tools)")
        for t in svctools:
            lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


# ── Procedure Cards (role-memory-srv) ──────────────────────────────
#
# The Operator's procedure-card index is fetched from role-memory-srv
# (port 3500, see nexus/typescript/role-memory-srv/). Cards codify
# *judgment* — which tool to dispatch for which question shape — in
# the same Redis-backed Procedure Registry that other roles consume
# via tackle-mcp's `memory_get_procedures` tool. The Operator does
# not have access to tackle-mcp's MCP tools directly (it is itself
# downstream of the aggregator that aggregates tackle-mcp), so we
# read the role-memory-srv REST endpoint directly: the same PG source
# of truth that tackle-mcp reads from.
#
# We surface only the trigger list + collapsed card summaries in the
# system prompt (not full `body_md` — that would bloat every request
# with ~6KB of static procedure prose). The LLM is told the
# card-lookup pathway exists; when its triggers fire it can call the
# `memory_get_procedure` tool through the aggregator to read the full
# card on demand.
#
# Same TTL-bounded-cache + degrade-on-failure pattern as
# `_refresh_discovered_tools()`: if role-memory-srv is unreachable,
# the prompt falls back to the tool catalog alone with a clear note
# that card-based guidance is unavailable.

_ROLE_MEMORY_SRV_URL = os.environ.get(
    "ROLE_MEMORY_SRV_URL", "http://localhost:3500"
)
_operator_card_cache: List[Dict[str, Any]] = []
_operator_card_cache_timestamp: float = 0.0
_operator_card_cache_lock = threading.Lock()
OPERATOR_CARD_TTL = 300  # 5 min — same as DISCOVERED_TOOLS_TTL


def _refresh_operator_cards() -> List[Dict[str, Any]]:
    """TTL-bounded refresh of the operator's procedure-card index.

    Returns the cached list if TTL hasn't expired. Otherwise fetches
    `GET /procedures/operator` from role-memory-srv and caches the
    result. On failure, falls back to the last good cache (possibly
    empty) and logs the failure — never throws.
    """
    global _operator_card_cache, _operator_card_cache_timestamp
    if _operator_card_cache and (
        time.time() - _operator_card_cache_timestamp
    ) <= OPERATOR_CARD_TTL:
        return _operator_card_cache

    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{_ROLE_MEMORY_SRV_URL}/procedures/operator")
            r.raise_for_status()
            cards = r.json()
        if not isinstance(cards, list):
            cards = []
        with _operator_card_cache_lock:
            _operator_card_cache = list(cards)
            _operator_card_cache_timestamp = time.time()
        _log.info(
            "operator procedure-card index fetched — %d cards",
            len(_operator_card_cache),
        )
    except Exception as e:
        _log.warning(
            "operator procedure-card refresh failed at %s: %s — "
            "using stale cache (%d cards)",
            _ROLE_MEMORY_SRV_URL,
            e,
            len(_operator_card_cache),
        )
    return _operator_card_cache


def _format_procedure_cards_for_prompt(cards: List[Dict[str, Any]]) -> str:
    """Format the operator's procedure-card index for the system prompt.

    Emits a short header explaining the pathway, then one collapsed
    line per card listing slug + summary + triggers. The LLM is told
    the full card is available via the `memory_get_procedure` tool.
    """
    if not cards:
        return (
            "  (no procedure cards currently available — "
            "role-memory-srv unreachable or zero cards for role='operator')"
        )
    lines = [
        f"{len(cards)} procedure cards available for the operator role. "
        f"These codify *which tool to dispatch for which question shape*. "
        f"When a card's triggers match the user's question, fetch the full "
        f"card via the `memory_get_procedure` tool (arguments: "
        f'{{ "slug": "<slug>" }}) and follow its procedure before '
        f"dispatching any tool."
    ]
    for c in cards:
        slug = c.get("slug", "")
        summary = c.get("summary", "").rstrip()
        triggers = c.get("triggers", []) or []
        trigger_str = (
            ", ".join(f'"{t}"' for t in triggers)
            if triggers
            else "(no triggers — always applicable)"
        )
        lines.append(f"- `{slug}` — {summary}  triggers: {trigger_str}")
    return "\n".join(lines)


# ── Per-Session State ─────────────────────────────────────────────

_session_queues: Dict[str, deque] = {}
_session_entity_indexes: Dict[str, Dict[str, List[Dict]]] = {}
_session_topic_maps: Dict[str, Dict[str, List[Dict]]] = {}
_session_locks: Dict[str, threading.RLock] = {}
_global_lock = threading.Lock()


def _get_session_lock(session_id: str) -> threading.RLock:
    with _global_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.RLock()
        return _session_locks[session_id]


def _get_session_queue(session_id: str) -> deque:
    with _get_session_lock(session_id):
        if session_id not in _session_queues:
            db_items = load_queue(session_id)
            q = deque(maxlen=CONTINUITY_QUEUE_MAX)
            for item in db_items:
                q.append(item)
            _session_queues[session_id] = q
            _log.info("Loaded %d items for session %s", len(q), session_id)
        return _session_queues[session_id]


def _get_entity_index(session_id: str) -> Dict[str, List[Dict]]:
    with _get_session_lock(session_id):
        if session_id not in _session_entity_indexes:
            _session_entity_indexes[session_id] = {}
        return _session_entity_indexes[session_id]


def _get_topic_map(session_id: str) -> Dict[str, List[Dict]]:
    with _get_session_lock(session_id):
        if session_id not in _session_topic_maps:
            _session_topic_maps[session_id] = {}
        return _session_topic_maps[session_id]


# ── Tool Result Cache ─────────────────────────────────────────────

_tool_cache: Dict[str, Dict[str, Any]] = {}
_tool_cache_lock = threading.Lock()

TOOL_CACHE_TTL = {
    "terrain": 300,
    "conduit": 120,
    "nebula": 60,
}


def _cache_key(service: str, method: str, path: str, body: Any = None) -> str:
    raw = f"{service}:{method}:{path}:{json.dumps(body, sort_keys=True) if body else ''}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(service: str, method: str, path: str, body: Any = None) -> Optional[str]:
    if method != "GET":
        return None
    key = _cache_key(service, method, path, body)
    with _tool_cache_lock:
        entry = _tool_cache.get(key)
        if entry:
            ttl = TOOL_CACHE_TTL.get(service, 60)
            if time.time() - entry["timestamp"] < ttl:
                _log.info("Cache hit: %s %s", service, path)
                return entry["result"]
            else:
                del _tool_cache[key]
    return None


def _set_cached(service: str, method: str, path: str, body: Any, result: str) -> None:
    if method != "GET":
        return
    key = _cache_key(service, method, path, body)
    with _tool_cache_lock:
        _tool_cache[key] = {"result": result, "timestamp": time.time()}


# ── Regex Entity Extraction ───────────────────────────────────────

# Known service names from terrain
KNOWN_SERVICES = {
    "nats", "postgresql", "address-tts", "broker-gateway", "cascade",
    "conduit-ui", "conduit-mcp", "duality-ui",    "file-system-server", "secure-file-system-server",
    "image-server", "mongodb", "nebula-srv", "nebula-mcp", "nebula-ui",
    "nexus-console", "ollama", "operator-svc", "peb-kernel", "plurality-ui",
    "redis", "role-memory-srv", "service-registry", "tackle-ui", "terrain",
    "ui-event-bus",
    "wrp-bridge-daemon",
}

# Patterns for structured IDs
UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
PLAN_ID_PATTERN = re.compile(r"\bplan[\s-]*(\d+)\b", re.IGNORECASE)
WR_ID_PATTERN = re.compile(r"\b(?:wr|work[\s-]*request)[\s-]*(\d+)\b", re.IGNORECASE)
REQ_ID_PATTERN = re.compile(r"\b(?:req|requirement)[\s-]*(\d+)\b", re.IGNORECASE)
PORT_PATTERN = re.compile(r"\bport[\s:]*(\d{4,5})\b", re.IGNORECASE)


def _extract_regex_entities(text: str) -> Set[str]:
    """Extract entities from text using regex patterns."""
    entities = set()

    # Known service names
    text_lower = text.lower()
    for svc in KNOWN_SERVICES:
        if svc in text_lower:
            entities.add(svc)

    # Structured IDs
    for match in UUID_PATTERN.finditer(text):
        entities.add(match.group().lower())
    for match in PLAN_ID_PATTERN.finditer(text):
        entities.add(f"plan-{match.group(1)}")
    for match in WR_ID_PATTERN.finditer(text):
        entities.add(f"wr-{match.group(1)}")
    for match in REQ_ID_PATTERN.finditer(text):
        entities.add(f"req-{match.group(1)}")
    for match in PORT_PATTERN.finditer(text):
        entities.add(f"port-{match.group(1)}")

    return entities


# ── Combined Compaction + Entity Extraction ───────────────────────
# Single LLM call produces both summary and structured metadata.

COMPACTION_NER_PROMPT = """Analyze this conversation exchange. Produce ONLY valid JSON with this structure:
{{"summary": "2-3 sentence summary", "topics": ["topic1"], "entities": ["entity1", "entity2"]}}

summary: Factual summary of what was asked, what data was retrieved, and what was answered.
topics: 1-3 high-level topics (infrastructure, pipeline, requirements, architecture, agent, general).
entities: Specific names mentioned — services, plans, work requests, requirements, ports, UUIDs.
- Be specific: "NATS" not "messaging", "plan-12" not "plan"
- Include port numbers as "port-XXXX"
- Do NOT include generic words unless they are proper names

User: {user_message}

Operator: {model_response}"""


def _compact_and_extract(user_message: str, model_response: str) -> Dict[str, Any]:
    """Compact the exchange and extract entities/topics.

    For short exchanges (< SHORT_EXCHANGE_THRESHOLD chars), uses regex-only
    extraction to avoid the LLM call latency. For longer exchanges, uses a
    single combined LLM call for both summary and entity extraction.
    """
    combined = user_message + " " + model_response

    # Fast path: short exchanges — regex-only, no LLM call
    if len(combined) < SHORT_EXCHANGE_THRESHOLD:
        regex_entities = _extract_regex_entities(combined)
        return {
            "summary": user_message[:200],
            "topics": [_detect_topic(user_message)],
            "entities": list(regex_entities),
        }

    # Full path: longer exchanges — single combined LLM call
    regex_entities = _extract_regex_entities(combined)
    prompt = COMPACTION_NER_PROMPT.format(
        user_message=user_message[:2000],
        model_response=model_response[:2000],
    )
    try:
        result = call_llm(
            prompt=prompt,
            role="operator",
            system_prompt="You are a summarization and entity extraction assistant. Output only valid JSON.",
            fallback=True,
        )
        result = (result or "").strip()
        if result.startswith("```"):
            result = re.sub(r"^```(?:json)?\s*", "", result)
            result = re.sub(r"\s*```$", "", result)
        parsed = json.loads(result)
        summary = (parsed.get("summary") or "").strip()
        topics = parsed.get("topics") or parsed.get("topic") or []
        entities = parsed.get("entities") or parsed.get("entity") or []
        if isinstance(topics, str):
            topics = [topics]
        if isinstance(entities, str):
            entities = [entities]
    except (json.JSONDecodeError, Exception) as e:
        _log.warning("Combined compaction+NER failed: %s", e)
        summary = user_message[:200]
        topics = []
        entities = []

    # Merge regex entities
    all_entities = list(entities) + list(regex_entities)
    seen = set()
    deduped = []
    for e in all_entities:
        e_lower = e.lower().strip()
        if e_lower and e_lower not in seen:
            seen.add(e_lower)
            deduped.append(e_lower)

    if not topics:
        topics = [_detect_topic(user_message)]

    return {
        "summary": summary or user_message[:200],
        "topics": topics,
        "entities": deduped,
    }


# ── Topic Detection (keyword fallback) ────────────────────────────

TOPIC_KEYWORDS = {
    "infrastructure": ["service", "server", "health", "status", "running", "terrain", "deploy", "port"],
    "pipeline": ["plan", "pipeline", "conduit", "work request", "ticket", "session", "circuit"],
    "requirements": ["requirement", "need", "must", "should", "backlog", "rms", "priority"],
    "architecture": ["design", "architecture", "decision", "trade", "pattern", "component"],
    "agent": ["agent", "record", "harvest", "report", "analysis", "nebula"],
}


def _detect_topic(user_message: str) -> str:
    """Detect topic via keyword matching (fallback for when LLM extraction fails)."""
    msg_lower = user_message.lower()
    scores = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > 0:
            scores[topic] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


# ── Index Operations ──────────────────────────────────────────────

def _push_to_entity_index(session_id: str, item: Dict[str, Any]) -> None:
    """Add item to entity index (entity → [items])."""
    entity_index = _get_entity_index(session_id)
    for entity in item.get("entities", []):
        if entity not in entity_index:
            entity_index[entity] = []
        entity_index[entity].append(item)
    # Trim if too large
    total = sum(len(v) for v in entity_index.values())
    if total > ENTITY_INDEX_MAX:
        # Remove oldest entity with fewest items
        smallest = min(entity_index, key=lambda k: len(entity_index[k]))
        if entity_index[smallest]:
            entity_index[smallest].pop(0)
            if not entity_index[smallest]:
                del entity_index[smallest]


def _push_to_topic_map(session_id: str, item: Dict[str, Any]) -> None:
    """Add item to topic map (topic → [items])."""
    topic_map = _get_topic_map(session_id)
    for topic in item.get("topics", ["general"]):
        if topic not in topic_map:
            topic_map[topic] = []
        topic_map[topic].append(item)
    # Trim
    total = sum(len(v) for v in topic_map.values())
    if total > ENTITY_INDEX_MAX:
        smallest = min(topic_map, key=lambda k: len(topic_map[k]))
        if topic_map[smallest]:
            topic_map[smallest].pop(0)


def _find_relevant_context(session_id: str, user_message: str) -> List[Dict[str, Any]]:
    """Find relevant items using entity matching + topic matching."""
    # Extract entities from the user's message
    user_entities = _extract_regex_entities(user_message)
    entity_index = _get_entity_index(session_id)
    topic_map = _get_topic_map(session_id)

    matched_items: Dict[str, Dict[str, Any]] = {}  # summary → item (dedup)

    # Entity matching (highest relevance)
    for entity in user_entities:
        for item in entity_index.get(entity, []):
            key = item.get("summary", "")
            if key not in matched_items:
                matched_items[key] = item

    # Topic matching (secondary)
    topic = _detect_topic(user_message)
    for item in topic_map.get(topic, []):
        key = item.get("summary", "")
        if key not in matched_items:
            matched_items[key] = item

    # Return up to 5 most recent items
    items = list(matched_items.values())
    items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return items[:5]


# ── Tool Definitions ──────────────────────────────────────────────
#
# Historically this was a hard-coded `TOOLS` array of three named tools
# (query_nebula / query_conduit / query_terrain) — each with a fixed
# list of REST paths to be hit through `_execute_tool_call` → `api_proxy`.
#
# As of 2026-07-22 the Operator no longer hard-codes the tool catalog.
# It pulls them at runtime from `tools-aggregator.service` (port 3210)
# via the `_refresh_discovered_tools()` helper above. The aggregator
# composes registries from every live MCP. To preserve the legacy
# `service:/method:/path:/body:` block grammar (and the matching
# `_execute_tool_call` REST-proxy dispatch through `api_proxy.py`),
# we ALSO accept service-shaped blocks in `_parse_tool_call` so that
# existing UI clients invoking `POST /api/proxy/<service>` directly
# keep working unchanged. New MCP tool calls use the `tool:/arguments:`
# block shape below.

# Legacy catalog retained as a documentation / fallback reference ONLY —
# it is NOT emitted to the LLM. That honor goes to whatever the
# aggregator reports from `_refresh_discovered_tools()`.
TOOLS_LEGACY_REST_PROXY = [
    {
        "name": "query_nebula",
        "description": "Query the Nebula agent records service. Use for: agent records, requirements, harvests, reports, analyses, open questions.",
        "endpoints": [
            "GET  /api/agent-records          — list records (query: role, tag, type, limit)",
            "GET  /api/requirements            — list requirements (query: status, priority, limit)",
            "GET  /api/harvests                — list harvests (query: limit)",
            "GET  /api/open-questions          — list open questions (query: status, limit)",
            "GET  /api/agent-records/:id       — get single record by ID",
        ],
    },
    {
        "name": "query_conduit",
        "description": "Query the Conduit pipeline service. Use for: plans, tickets, sessions, work requests, pipeline status.",
        "endpoints": [
            "GET  /state                       — full pipeline state (plans, tickets, sessions)",
            "GET  /wr                          — list work requests (query: status, limit)",
            "GET  /wr/:id                      — get work request by ID",
            "GET  /wr/:id/events               — get event log for a work request",
            "GET  /wr/:id/projection-drift     — check projection drift for a work request",
            "GET  /health                      — conduit health check",
        ],
    },
    {
        "name": "query_terrain",
        "description": "Query the Terrain infrastructure topology service. Use for: runnable services, service types, servers, service dependencies, platform health.",
        "endpoints": [
            "GET  /api/v1/runnable-services          — list all registered services",
            "GET  /api/v1/runnable-services/:id       — get service by ID",
            "GET  /api/v1/service-types                — list service types",
            "GET  /api/v1/servers                     — list servers",
            "GET  /api/v1/service-dependencies        — list service dependencies",
            "GET  /api/v1/platform/health             — platform health check",
        ],
    },
]

# Backward-compat alias — any external importer of `TOOLS` from this module
# gets the legacy reference list (for documentation purposes; it is NOT used
# by Operator's own LLM-tool-call flow anymore).
TOOLS = TOOLS_LEGACY_REST_PROXY


# ── Operator System Prompt Builder ─────────────────────────────────

# Static base — never modified. The dynamic tool-list section is appended
# at chat-request time by `_build_system_prompt()`. Keeping the base static
# means a chat request with zero discovered tools still produces a valid,
# usable operator prompt (the LLM just won't have any tools to call).
_OPERATOR_SYSTEM_PROMPT_BASE = """You are Operator, the host personality for the Nexus system.

You are the friendly, knowledgeable interface between the user and the Nexus
infrastructure. You can answer questions about pipeline state, requirements,
implementation plans, service status, and architecture.

## Tools

You have access to Nexus backend services via tool calls. To use a tool,
output EXACTLY this format (one call at a time):

[
tool: <tool name from the list below>
arguments: <JSON object of arguments for this tool>
]

Tool names come from the catalog at the bottom of this prompt. Pass arguments
as a single JSON object on the "arguments:" line — for tools taking no
arguments, pass an empty object: `arguments: {}`.

After you make a tool call, you will receive the ACTUAL data from the service.
CRITICAL: You MUST use the actual data in your response. Do NOT generate, fabricate,
or invent data. If the tool returns JSON, summarize what it contains. If it returns
an error, report the error. Never make up agent records, plans, requirements, or
any other data — only report what the tool actually returned.

## Behavior

- Be concise, helpful, and direct.
- When you need data to answer a question, make a tool call first.
- When you receive tool results, report what they actually contain.
- When you don't know something, say so. Don't make up data.
- Stay in character as the Nexus operator.

## Available tools (discovered from the tools-aggregator at request time)

"""

_OPERATOR_SYSTEM_PROMPT_TAIL = """

## Notes

- A "tool call" only needs a tool name and arguments object — the underlying
  service (conduit-mcp, tackle-mcp, knowledge-mcp, etc.) is routed by the
  aggregator, not by you.
- If you cannot find a fitting tool for a user's question, say so plainly
  — do not invent a tool name and do not emit a tool call with placeholder
  data."""


def _build_system_prompt() -> str:
    """Assemble the Operator system prompt with the live tool catalog.

    Layout:
        _BASE  + tool-catalog + card-index + _TAIL

    The card index is fetched from role-memory-srv (Redis-backed
    cache populated by syncAll). On fetch failure the section
    collapses to a clear "(unavailable)" note and the prompt is still
    valid — the tool catalog alone remains a working dispatch
    surface. The card section sits *between* the tool catalog and the
    tail so the notes in the tail still apply to whatever preceded.
    """
    tools = _refresh_discovered_tools()
    body = _format_tools_for_prompt(tools)
    cards = _refresh_operator_cards()
    card_body = _format_procedure_cards_for_prompt(cards)
    return (
        _OPERATOR_SYSTEM_PROMPT_BASE
        + body
        + "\n\n## Procedure cards (role-memory-srv, role='operator')\n\n"
        + card_body
        + "\n"
        + _OPERATOR_SYSTEM_PROMPT_TAIL
    )


# Backward-compat alias — `SYSTEM_PROMPT` is now a function call away,
# but anyone importing the constant gets a useful (if degraded) base prompt
# that lists zero tools. The runtime chat flow always uses `_build_system_prompt()`.
SYSTEM_PROMPT = _build_system_prompt()


def _push_to_queue(session_id: str, user_message: str, model_response: str) -> None:
    """Compact, extract entities, and push to the continuity queue."""
    # Single LLM call: compaction + entity extraction
    extracted = _compact_and_extract(user_message, model_response)
    summary = extracted["summary"]
    if not summary:
        return

    item = {
        "summary": summary,
        "user_message": user_message[:500],
        "topics": extracted["topics"],
        "entities": extracted["entities"],
        "timestamp": time.time(),
    }

    q = _get_session_queue(session_id)
    with _get_session_lock(session_id):
        # If queue is full, push oldest to indexes
        if len(q) >= CONTINUITY_QUEUE_MAX:
            oldest = q[0]
            _push_to_entity_index(session_id, oldest)
            _push_to_topic_map(session_id, oldest)
        q.append(item)

    # Persist to DB in background
    threading.Thread(
        target=save_queue,
        args=(session_id, list(q)),
        daemon=True,
    ).start()

    _log.info(
        "Session %s queue: %d items, topics: %s, entities: %s",
        session_id, len(q),
        extracted["topics"],
        extracted["entities"][:5],
    )


# ── Prompt Building ───────────────────────────────────────────────

def build_prompt(user_message: str, session_id: str) -> str:
    """Build the prompt string for the LLM call.

    Uses the continuity queue for recent context and the entity+topic
    index for relevant long-term context.
    """
    parts = []

    # Add compacted history from continuity queue
    q = _get_session_queue(session_id)
    if q:
        parts.append("Previous conversation context:")
        for i, item in enumerate(q):
            parts.append(f"[{i+1}] {item['summary']}")
        parts.append("")

    # Add relevant context from entity+topic index
    relevant = _find_relevant_context(session_id, user_message)
    if relevant:
        parts.append("Earlier related discussion:")
        for item in relevant:
            parts.append(f"  - {item['summary']}")
        parts.append("")

    # Add the new user message
    parts.append(f"User: {user_message}")

    return "\n".join(parts)


# ── Tool Call Parsing & Execution ─────────────────────────────────
#
# Two block-grammar shapes are accepted from the LLM:
#
#   NEW (preferred — routes through tools-aggregator → underlying MCP):
#
#     [tool: <tool name>
#     arguments: <JSON object>]
#
#   LEGACY (backward compat — routes through api_proxy.py → REST service):
#
#     [service: <query_nebula|query_conduit|query_terrain>
#     method: <GET|POST>
#     path: <API path>
#     body: <JSON object>]
#
# The kind is selected by which top-level key the LLM emits in the block.
# tool: → kind="tool", dispatch via tools-aggregator's /tools/call.
# service: → kind="rest", dispatch via the existing api_proxy path.

def _parse_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Parse a fenced block from LLM output. Accepts both new (tool:) and
    legacy (service:) shapes."""
    match = re.search(r"\[(.*?)\]", text, re.DOTALL)
    if not match:
        match = re.search(r"\[(.*)", text, re.DOTALL)
    if not match:
        return None

    block = match.group(1)
    raw: Dict[str, str] = {}
    for line in block.strip().splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key in ("tool", "arguments", "service", "method", "path", "body"):
                raw[key] = value

    # Dispatch on the emitted key.
    if "tool" in raw:
        # New shape (MCP tool call via tools-aggregator)
        args: Dict[str, Any] = {}
        args_str = raw.get("arguments", "{}")
        if args_str:
            try:
                args = json.loads(args_str)
                if not isinstance(args, dict):
                    args = {"_value": args}
            except json.JSONDecodeError:
                # Empty/non-JSON arguments are not a hard error; pass an empty
                # object so the tool itself can decide what to do.
                _log.warning("tool call arguments not valid JSON (%r); defaulting to {}", args_str[:80])
                args = {}
        return {
            "kind": "tool",
            "tool": raw["tool"],
            "arguments": args,
        }

    if "service" in raw and "path" in raw:
        # Legacy shape (REST proxy)
        return {
            "kind": "rest",
            "service": raw["service"],
            "method": raw.get("method", "GET"),
            "path": raw["path"],
            "body": raw.get("body"),
        }

    return None


def _execute_tool_call(call: Dict[str, Any]) -> str:
    """Execute a parsed tool call. Dispatches by `kind`:
       "tool" → tools-aggregator /tools/call (the new MCP path)
       "rest" → api_proxy.proxy_request (the legacy REST proxy path)
    """
    kind = call.get("kind", "rest")  # default rest for shape backward compat

    if kind == "tool":
        return _execute_mcp_tool_call(call)
    return _execute_rest_tool_call(call)


def _execute_mcp_tool_call(call: Dict[str, Any]) -> str:
    """Dispatch through the tools-aggregator's POST /tools/call."""
    tool_name = call["tool"]
    args = call.get("arguments", {}) or {}

    # Cache check — same `_tool_cache` shared with rest path, just keyed by
    # tool_name + json-stable args. Reuse the same cache helpers by encoding
    # the tool-call row as a fake rest triple (service/ method / path).
    cache_service = f"tool:{tool_name}"
    cache_method = "call"
    cache_path = ""
    cached = _get_cached(cache_service, cache_method, cache_path, args)
    if cached is not None:
        return cached

    try:
        client = _get_tools_client()
        result = client.call_tool(tool_name, args)
    except Exception as e:
        _log.error("tool %s call failed: %s", tool_name, e)
        return f"Error calling tool {tool_name}: {e}"

    # The aggregator returns the unwrapped MCP result already (the index.ts
    # callRemoteToolJsonRpc unwraps content[0].text into parsed JSON when
    # possible). Treat the result uniformly.
    text = json.dumps(result, indent=2, default=str) if not isinstance(result, str) else result
    if len(text) > 8000:
        text = text[:8000] + "\n... (truncated)"

    _set_cached(cache_service, cache_method, cache_path, args, text)
    return text


def _execute_rest_tool_call(call: Dict[str, str]) -> str:
    """Legacy REST path (api_proxy → nebula-srv / conduit-mcp / terrain)."""
    service = call["service"]
    method = call["method"]
    path = call["path"]
    body = None

    SERVICE_ALIAS = {
        "query_nebula": "nebula",
        "query_conduit": "conduit",
        "query_terrain": "terrain",
    }
    service = SERVICE_ALIAS.get(service, service)

    if "body" in call and method != "GET":
        try:
            body = json.loads(call["body"])
        except json.JSONDecodeError:
            return f"Error: invalid JSON body: {call['body']}"

    # Check cache first
    cached = _get_cached(service, method, path, body)
    if cached is not None:
        return cached

    # Execute the request
    result = proxy_request(service=service, path=path, method=method, body=body)

    if result["error"]:
        return f"Error ({result['status']}): {result['error']}"

    data = result["data"]
    text = json.dumps(data, indent=2, default=str)
    if len(text) > 8000:
        text = text[:8000] + "\n... (truncated)"

    # Cache the result
    _set_cached(service, method, path, body, text)

    return text


# ── Main Response Pipeline ────────────────────────────────────────

def respond(
    user_message: str,
    session_id: str,
    role: str = "operator",
    log_level: str = "ERROR",
) -> Dict[str, Any]:
    """Process a user message and return the operator's response."""
    start = time.time()

    # Build prompt from continuity queue + entity/topic index
    prompt = build_prompt(user_message, session_id)

    # Build system prompt with the live discovered tool catalog
    system_prompt = _build_system_prompt()

    # Tool-calling loop
    response_text = ""
    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            response_text = call_llm(
                prompt=prompt,
                role=role,
                system_prompt=system_prompt,
                fallback=True,
            )
            if response_text is None:
                _log.error("LLM call failed: All models exhausted for role=%s (check tackle-mcp role config for this role)", role)
                response_text = "[Operator] I'm sorry — all models for this role are currently unavailable. Please try again in a moment."
                break
        except Exception as e:
            _log.error("LLM call failed: %s", e)
            response_text = f"[Operator] I encountered an error: {e}"
            break

        tool_call = _parse_tool_call(response_text)
        if tool_call is None:
            break

        _log.info("Tool call: kind=%s %s", tool_call.get("kind", "rest"),
                  json.dumps({k: v for k, v in tool_call.items() if k != "kind"}, default=str))
        tool_result = _execute_tool_call(tool_call)

        prompt += (
            f"\n{response_text}"
            f"\n\nTool result:\n{tool_result}"
            f"\n\nNow answer the user's question using the tool result above."
        )
    else:
        pass

    latency_ms = int((time.time() - start) * 1000)

    try:
        cfg = get_role_config(role)
        model_id = cfg.get("model_identifier", "unknown")
    except Exception:
        model_id = "unknown"

    clean_response = re.sub(r"<tool_call>.*?</tool_call>", "", response_text, flags=re.DOTALL).strip()
    clean_response = re.sub(r"<tool_call>.*", "", clean_response, flags=re.DOTALL).strip()

    # Push to continuity queue (non-blocking, includes DB persistence)
    threading.Thread(
        target=_push_to_queue,
        args=(session_id, user_message, clean_response),
        daemon=True,
    ).start()

    # Log to database for audit trail
    log_prompt_response(
        session_id=session_id,
        user_message=user_message,
        model_response=clean_response or "",
        role=role,
        model_identifier=model_id,
        latency_ms=latency_ms,
    )

    return {
        "response": clean_response or "",
        "model_identifier": model_id,
        "latency_ms": latency_ms,
    }
