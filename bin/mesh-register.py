#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
bin/mesh-register.py
====================

Probe the live Nexus service mesh and UPSERT every detected service into
the Postgres ``terrain`` topology schema. Both the TS ``terrain-mcp`` MCP
and the JVM ``TopologyServerApplication`` (port 8084, ``nexus/jvm/spring/
terrain``) read the same ``terrain.*`` tables, so writes here are visible
to both consumers via their existing tool/API surface — no source change is
required in either.

This script is the executable mirror of ``terrain-mcp``'s three write
tools:

* ``terrain_register_mcp_server``       → upsert into ``terrain.mcp_servers``
* ``terrain_register_runnable_service`` → upsert into ``terrain.runnable_services``
* ``terrain_register_dependency``       → upsert into ``terrain.service_dependencies``

Driver selection
----------------

``psycopg2`` is preferred when importable (single connection, transactional,
no subprocess overhead, atomic per-statement commit). If ``psycopg2`` is not
installed the script degrades to one ``psql -c`` subprocess per statement.

Usage
-----

::

    bin/mesh-register.py --probe-only      # print probe JSON, do not touch the DB
    bin/mesh-register.py --dry-run         # print upsert SQL to stdout
    bin/mesh-register.py --mesh            # print human-readable mesh summary
    bin/mesh-register.py                   # execute upserts via psycopg2 (or psql)
    bin/mesh-register.py --json            # alias for --probe-only
    bin/mesh-register.py --help

Environment
-----------

``PGPASSWORD`` is required at run time. ``PGHOST`` / ``PGPORT`` /
``PGDATABASE`` / ``PGUSER`` default to the same values the
``terrain-mcp`` ``db/client.ts`` uses (``localhost`` / ``5432`` /
``nexus`` / ``pguser``).

Exit codes
----------

* ``0`` — every requested operation completed without uncaught error.
* ``2`` — ``--dry-run`` produced SQL but invocation was halted by the flag.
* ``3`` — DB writer returned non-zero.
* ``4`` — required environment (PGPASSWORD, driver) was missing.

The probe itself never aborts the program if a single port is unreachable;
every candidate is queried independently and its result appears in the
JSON / table output. ``status`` is set from the probe so registered
services accurately read ONLINE (reachable) or OFFLINE (unreachable).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, asdict


# ── Configuration ──────────────────────────────────────────────────────

PG_ENV_DEFAULTS = {
    "PGHOST": "localhost",
    "PGPORT": "5432",
    "PGDATABASE": "nexus",
    "PGUSER": "pguser",
}

PROBE_TIMEOUT_SECONDS = 2.0

try:
    import psycopg2  # type: ignore[import-untyped]

    HAS_PSYCOPG2 = True
except ImportError:  # pragma: no cover - opt-in driver
    HAS_PSYCOPG2 = False


@dataclass(frozen=True)
class Candidate:
    """A service we know about and are willing to register."""

    name: str
    port: int | None
    kind: str  # "mcp_server" or "runnable_service"
    health_url: str
    transport_type: str | None = None  # only meaningful for mcp_server
    service_type: str | None = None  # only meaningful for runnable_service
    description: str = ""
    startup: str = ""
    workspace_path: str = ""
    health_cmd: str | None = None  # shell command for health check (Docker, CLI)


# Every service we have either built in this session or know is live
# upstream of nexus/. Edit this list as the mesh evolves.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        name="conduit-mcp",
        port=3100,
        kind="mcp_server",
        transport_type="streamable-http",
        health_url="http://localhost:3100/health",
        description=(
            "WorkRequest orchestrator (conduit-mcp). Plan lifecycle: proposed "
            "→ planning → pending → active → completed. Exposes /state."
        ),
        startup="systemd: systemctl --user start conduit-mcp.service",
        workspace_path="nexus/typescript/conduit-mcp",
    ),
    Candidate(
        name="nebula-srv",
        port=3101,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:3101/health",
        description=(
            "Canonical REST API over the relational schemas. Endpoints: "
            "/api/agent-records, /api/requirements, /api/systems, "
            "/api/harvests (also projected via nebula-mcp SSE @ 3102)."
        ),
        startup="systemd: systemctl --user start nebula-srv.service",
        workspace_path="nexus/typescript/nebula-srv",
    ),
    Candidate(
        name="nebula-mcp-sse",
        port=3102,
        kind="mcp_server",
        transport_type="sse",
        # Probe uses the non-streaming /health endpoint rather than /sse:
        # the SSE handler holds the connection open, which causes curl /
        # urllib.request.urlopen to time out at PROBE_TIMEOUT_SECONDS and
        # mark the server OFFLINE even when the socket is healthy. The
        # /health endpoint returns a small JSON body and closes.
        health_url="http://localhost:3102/health",
        description=(
            "SSE wrapper around nebula-srv so stdio-only MCP clients (e.g. "
            "Claude Desktop) can speak to the canonical DB API."
        ),
        startup="systemd: systemctl --user start nebula-mcp-sse.service",
        workspace_path="nexus/typescript/nebula-mcp",
    ),
    Candidate(
        name="nebula-mcp",
        port=None,
        kind="mcp_server",
        transport_type="stdio",
        # Stdio-only candidate; no HTTP health endpoint. Use the systemd
        # unit as the liveness proxy (mirrors terrain-mcp): nebula-mcp.service
        # is a launcher stub with RemainAfterExit=yes, so is-active returns 0
        # once the unit has been started even with no client currently
        # spawned. Prevents the recurring OFFLINE status that flipped
        # terrainUp=false every mesh-register cycle (5-min timer).
        health_url="",
        health_cmd="systemctl --user is-active nebula-mcp.service",
        description=(
            "Stdio MCP server for Nebula RMS. Client-launched (not a daemon). "
            "Referenced by dependency edges as the primary Nebula MCP "
            "consumer of nebula-srv."
        ),
        startup="cd typescript/nebula-mcp && npm run dev",
        workspace_path="nexus/typescript/nebula-mcp",
    ),
    Candidate(
        name="vision-srv-py",
        port=8003,
        kind="runnable_service",
        service_type="FastAPI",
        health_url="http://localhost:8003/health",
        description=(
            "Vision Python service (8003) — full CRUD + DAG + edges + "
            "validation. vision-mcp proxies to this service."
        ),
        startup="systemd: systemctl --user start vision-srv-py.service",
        workspace_path="nexus/python/vision-srv",
    ),
    Candidate(
        name="tackle-srv",
        port=3410,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:3410/health",
        description=(
            "Tackle REST API — AI configuration registry and role memory "
            "procedure server. Backs tackle-mcp with canonical PostgreSQL + "
            "Redis access. Systemd-managed."
        ),
        startup="systemd: systemctl --user start tackle-srv.service",
        workspace_path="nexus/typescript/tackle-srv",
    ),
    Candidate(
        name="tackle-mcp",
        port=3400,
        kind="mcp_server",
        transport_type="streamable-http",
        health_url="http://localhost:3400/health",
        description="Tackle MCP server (already up before this session).",
        startup="systemd: systemctl --user start tackle-mcp.service",
        workspace_path="nexus/typescript/tackle-mcp",
    ),
    Candidate(
        name="peb-kernel",
        port=8098,
        kind="runnable_service",
        service_type="Python",
        health_url="http://localhost:8098/actuator/health",
        description=(
            "Python FastAPI kernel for the Persistent Engineering Brain (PEB). "
            "Replaces the JVM Spring Boot kernel in the active runtime path; "
            "the JVM implementation remains as the retained reference."
        ),
        startup="systemd: systemctl --user start peb-kernel.service",
        workspace_path="nexus/python/peb-kernel",
    ),
    Candidate(
        name="broker-gateway",
        port=8081,
        kind="runnable_service",
        service_type="Spring Boot",
        health_url="http://localhost:8081/actuator/health",
        description=(
            "Spring broker gateway. Pre-existing JVM service, started Jun21."
        ),
        startup="systemd: systemctl --user start broker-gateway.service",
        workspace_path="nexus/jvm/spring/service-broker/broker-gateway",
    ),
    Candidate(
        name="terrain",
        port=8084,
        kind="runnable_service",
        service_type="Spring Boot",
        health_url="http://localhost:8084/actuator/health",
        description=(
            "JVM TopologyServerApplication — Spring Boot 3.x consumer of "
            "the terrain.* schema. Registered as 'terrain' for backward "
            "compatibility with existing dependency edges. Systemd-managed."
        ),
        startup="systemd: systemctl --user start terrain.service",
        workspace_path="nexus/jvm/spring/terrain",
    ),
    Candidate(
        name="image-server",
        port=9081,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:9081/health",
        description=(
            "Static image server. Serves images from multiple search "
            "locations (device/, logo/, ui/shared/, ui/3d-fluency/, "
            "ui/neon/, ui/plastina-3d)."
        ),
        startup="systemd: systemctl --user start image-server.service",
        workspace_path="nexus/typescript/image-server",
    ),
    Candidate(
        name="file-system-server",
        port=4042,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:4042/health",
        description=(
            "Node.js file system proxy server for monaco-judge. Provides CRUD "
            "operations over a remote filesystem root directory (ls, cd, "
            "mkdir, rmdir, newfile, deletefile, rename, copy, move). "
            "Consumer: angular/monaco-judge (port 4042)."
        ),
        startup="systemd: systemctl --user start file-system-server.service",
        workspace_path="nexus/typescript/file-system-server",
    ),
    Candidate(
        name="secure-file-system-server",
        port=4040,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:4040/health",
        description=(
            "Secure Node.js file system proxy server for service-broker. "
            "Provides CRUD operations over a remote filesystem root "
            "directory. Consumer: jvm/spring/service-broker/file-service "
            "via broker-gateway restfs.api.url (port 4040)."
        ),
        startup="systemd: systemctl --user start secure-file-system-server.service",
        workspace_path="nexus/typescript/secure-file-system-server",
    ),
    Candidate(
        name="terrain-mcp",
        port=None,
        kind="mcp_server",
        transport_type="stdio",
        # Stdio-only candidate; no HTTP health endpoint. Use the systemd
        # unit as the liveness proxy: terrain-mcp.service is a stdio
        # launcher stub with RemainAfterExit=yes, so is-active returns 0
        # once the unit has been started even with no client currently
        # spawned. This measures "MCP launch stub loaded", not "actively
        # serving requests". A return of "inactive" correctly reflects a
        # not-yet-registered unit (a transient bootstrap condition).
        # Replaces the prior behaviour of always writing OFFLINE because
        # probe_one() short-circuited on empty health_url alone.
        health_url="",
        health_cmd="systemctl --user is-active terrain-mcp.service",
        description=(
            "TS stdio MCP server. Read+write surface over terrain.* tables. "
            "Not currently running on a TCP port — stdio-only."
        ),
        startup="cd typescript/terrain-mcp && npm run dev",
        workspace_path="nexus/typescript/terrain-mcp",
    ),
    Candidate(
        name="address-tts",
        port=8600,
        kind="runnable_service",
        service_type="Python Service",
        health_url="http://localhost:8600/health",
        description=(
            "TTS speech projection layer. NATS subscriber on "
            "nexus.kernel.v1.transition.> — speaks work request events. "
            "REST API: POST /synthesize, POST /speak, GET /health. "
            "Engine: Piper TTS (en_US-lessac-medium)."
        ),
        startup="systemd: systemctl --user start address-tts.service",
        workspace_path="nexus/python/address/tts",
    ),
    Candidate(
        name="address-tts-mcp",
        port=3105,
        kind="mcp_server",
        transport_type="streamable-http",
        health_url="http://localhost:3105/health",
        description=(
            "MCP server for Address TTS. Agent-facing interface: "
            "tts_synthesize, tts_speak, tts_health. Proxies to "
            "address-tts REST API on port 8600."
        ),
        startup="systemd: systemctl --user start address-tts-mcp.service",
        workspace_path="nexus/typescript/address-tts-mcp",
    ),
    Candidate(
        name="vision-srv-py",
        port=8003,
        kind="runnable_service",
        service_type="Python Service",
        health_url="http://localhost:8003/health",
        description=(
            "Python FastAPI/uvicorn LOSM backend. Provides REST API for "
            "vision services on port 8003. Systemd-managed."
        ),
        startup="systemd: systemctl --user start vision-srv-py.service",
        workspace_path="nexus/python/vision-srv",
    ),
    Candidate(
        name="role-memory-srv",
        port=3500,
        kind="runnable_service",
        service_type="Microservice",
        health_url="http://localhost:3500/health",
        description=(
            "PG-to-Redis sync server for the Role Memory Procedure Registry. "
            "Reads tackle.memory + tackle.role_memory from PostgreSQL and "
            "populates Redis keys. Systemd-managed."
        ),
        startup="systemd: systemctl --user start role-memory-srv.service",
        workspace_path="nexus/typescript/role-memory-srv",
    ),
    Candidate(
        name="wrp-bridge-daemon",
        port=None,
        kind="runnable_service",
        service_type="Python Service",
        health_url="",
        health_cmd="systemctl --user is-active wrp-bridge-daemon.service",
        description=(
            "Conduit -> Kernel bridge daemon. Syncs receipts from "
            "vision.receipts to the WRP Kernel Runtime. Polls every 30s. "
            "Systemd-managed; no HTTP port."
        ),
        startup="systemd: systemctl --user start wrp-bridge-daemon.service",
        workspace_path="nexus/python/conduit",
    ),
    Candidate(
        name="redis",
        port=6379,
        kind="runnable_service",
        service_type="Database",
        health_url="",
        health_cmd="docker exec atomic-redis-dev redis-cli ping | grep -q PONG",
        description=(
            "Redis in-memory cache via Docker (atomic-redis-dev). "
            "Used by role-memory-srv, tackle-srv, and tackle-mcp. Systemd-managed "
            "(oneshot). Auto-prunes old Docker artifacts before start."
        ),
        startup="systemd: systemctl --user start redis.service",
        workspace_path="nexus/bin/start-redis-docker.sh",
    ),
    Candidate(
        name="mongodb",
        port=27017,
        kind="runnable_service",
        service_type="Database",
        health_url="",
        health_cmd="docker exec atomic-mongodb mongo --eval 'db.runCommand({ping:1})' --quiet 2>/dev/null | grep -q ok",
        description=(
            "MongoDB document database via Docker (atomic-mongodb). "
            "Systemd-managed (oneshot). Auto-prunes old Docker artifacts "
            "before start."
        ),
        startup="systemd: systemctl --user start mongodb.service",
        workspace_path="nexus/bin/start-mongodb-docker.sh",
    ),
    Candidate(
        name="service-registry",
        port=8085,
        kind="runnable_service",
        service_type="Spring Boot",
        health_url="http://localhost:8085/actuator/health",
        description=(
            "Nexus service discovery and registration. Spring Boot app "
            "with PostgreSQL + Redis caching. Exposes REST API for "
            "service lookup and health aggregation. Systemd-managed."
        ),
        startup="systemd: systemctl --user start service-registry.service",
        workspace_path="nexus/jvm/spring/service-registry",
    ),
    Candidate(
        name="assembly-mcp",
        port=3113,
        kind="mcp_server",
        transport_type="streamable-http",
        health_url="http://localhost:3113/health",
        description=(
            "MCP server for the assembly (social/deliberation) schema - "
            "agent short-route to forums, threads, posts, and bridge "
            "tables to nebula artifacts. Express + JSON-RPC over POST / on "
            "ASSEMBLY_MCP_PORT (now 3113; 3112 is taken by service-broker-mcp). "
            "Systemd-managed. Delegates to assembly-srv REST API."
        ),
        startup="systemd: systemctl --user start assembly-mcp.service",
        workspace_path="nexus/typescript/assembly-mcp",
    ),
    Candidate(
        name="timeclock-mcp",
        port=3600,
        kind="mcp_server",
        transport_type="streamable-http",
        health_url="http://localhost:3600/healthz",
        description=(
            "MCP server for agent timeclock. Tracks session clock-in/out "
            "by role and model. Provides heartbeat, active session query, "
            "session log, and timeout cleanup. Systemd-managed."
        ),
        startup="systemd: systemctl --user start timeclock.service",
        workspace_path="nexus/python/timeclock",
    ),
    Candidate(
        name="wind-srv",
        port=3300,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:3300/health",
        description=(
            "Wind IDE workflow API server. Express.js CRUD for offices, "
            "titles, tasks, outcomes, workflows, nodes, edges, instances, "
            "tickets, receipts, v-roles. Used by wind-ui and conduit-ui "
            "legacy. Systemd-managed."
        ),
        startup="systemd: systemctl --user start wind-srv.service",
        workspace_path="nexus/typescript/wind-srv",
    ),
    Candidate(
        name="conduit-ui-legacy",
        port=4015,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:4015/",
        description=(
            "Conduit Legacy UI — production build of the Angular conduit-ui "
            "dashboard served via Express. Proxies API calls to conduit-mcp "
            "(port 3100). Systemd-managed."
        ),
        startup="systemd: systemctl --user start conduit-ui-legacy.service",
        workspace_path="nexus/angular/conduit-ui-legacy",
    ),
    Candidate(
        name="conduit-srv",
        port=3104,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:3104/health",
        description=(
            "Conduit REST API — standalone Express service extracted from "
            "conduit-mcp per the 'No SQL in MCP Servers' architectural "
            "directive. Serves 16 pure-DB REST routes (workflows, tickets, "
            "tokens, config, governance, vision, session-log) with direct "
            "PostgreSQL access via the conduit schema. Systemd-managed."
        ),
        startup="systemd: systemctl --user start conduit-srv.service",
        workspace_path="nexus/typescript/conduit-srv",
    ),
    Candidate(
        name="apidocs-srv",
        port=3180,
        kind="runnable_service",
        service_type="Python Service",
        health_url="http://localhost:3180/health",
        description=(
            "API docs index — Swagger UI + ReDoc over all *-srv OpenAPI "
            "specs. Plain HTTP on 127.0.0.1:3180 (localhost tooling); "
            "HTTPS listener on 0.0.0.0:8443 (self-signed cert, "
            "auto-generated) for LAN clients. Systemd-managed."
        ),
        startup="systemd: systemctl --user start apidocs-srv.service",
        workspace_path="nexus/tools/api-docs",
    ),
    Candidate(
        name="semantics-srv",
        port=3160,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:3160/health",
        description=(
            "REST API over the semantics.* Postgres schema (type-level "
            "semantic topology legend). Sends service-registry heartbeats "
            "via heartbeat-client (id 60, 30s). Systemd-managed."
        ),
        startup="systemd: systemctl --user start semantics-srv.service",
        workspace_path="nexus/typescript/semantics-srv",
    ),
    Candidate(
        name="semantics-mcp",
        port=3161,
        kind="mcp_server",
        transport_type="streamable-http",
        health_url="http://localhost:3161/health",
        description=(
            "Semantics MCP server (streamable HTTP on 3161) — exposes the "
            "semantics.* legend to MCP clients via semantics-srv."
        ),
        startup="systemd: systemctl --user start semantics-mcp.service",
        workspace_path="nexus/typescript/semantics-mcp",
    ),
    Candidate(
        name="moleculer-solscript",
        port=4060,
        kind="runnable_service",
        service_type="Express",
        health_url="http://localhost:4060/health",
        description=(
            "Moleculer SOLScript facade — resolution-domain evaluation API "
            "(REST via moleculer-web). Registers on the NATS mesh in "
            "namespace 'solscript'. Systemd-managed (moleculer-solscript.service)."
        ),
        startup="systemd: systemctl --user start moleculer-solscript.service",
        workspace_path="nexus/moleculer/solscript",
    ),
)


# Dependencies (source → target). Criticality: critical | high | medium | low.
#
# Names MUST match rows currently registered in terrain.mcp_servers /
# terrain.runnable_services. The emit_upsert_dependency DO/$do$ block
# silently RETURNs when EITHER endpoint fails to resolve, so a typo here
# produces zero feedback. Re-audit before changing any name.
#
# Aligned 2026-06-23: a probe-driven audit of the terrain.* tables found
# these original spellings corresponded to actual rows under different
# names (resolved via SELECT INTO probes; see the developer's audit
# transcript for the 8-vs-2 diagnostic). Specifically:
#
#   - the SSE-wrapping MCP is registered as "nebula-mcp" (not "…-sse")
#   - the JVM TopologyServerApplication is registered as "terrain"
#     (matching the workspace directory name, not my Python label)
#   - the vision REST is registered as "vision-srv" (the port tag was
#     dropped because the DB row itself doesn't carry port info)
#   - "peb-kernel" was never registered: until it lands in
#     terrain.runnable_services, no edge to it can resolve.
DEPENDENCIES: tuple[tuple[str, str, str, str], ...] = (
    ("mcp_server", "terrain-mcp", "runnable_service", "nebula-srv"),
    ("mcp_server", "nebula-mcp", "runnable_service", "nebula-srv"),
    ("mcp_server", "conduit-mcp", "runnable_service", "nebula-srv"),
    ("mcp_server", "tackle-mcp", "runnable_service", "tackle-srv"),
    ("mcp_server", "tackle-mcp", "runnable_service", "nebula-srv"),
    ("mcp_server", "tackle-mcp", "runnable_service", "redis"),
    ("mcp_server", "terrain-mcp", "runnable_service", "terrain"),
    ("runnable_service", "broker-gateway", "runnable_service", "nebula-srv"),
    ("runnable_service", "vision-srv-py", "runnable_service", "nebula-srv"),
    ("runnable_service", "role-memory-srv", "runnable_service", "redis"),
    ("runnable_service", "wrp-bridge-daemon", "runnable_service", "nebula-srv"),
    ("mcp_server", "timeclock-mcp", "runnable_service", "nebula-srv"),
    ("runnable_service", "wind-srv", "runnable_service", "nebula-srv"),
    ("runnable_service", "conduit-ui-legacy", "mcp_server", "conduit-mcp"),
    ("runnable_service", "conduit-srv", "runnable_service", "nebula-srv"),
)


# ── Probe logic ────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    candidate: Candidate
    reachable: bool
    http_status: int | None = None
    body_excerpt: str = ""
    error: str = ""


def probe_one(c: Candidate) -> ProbeResult:
    # ── Docker / CLI health check ────────────────────────────────────
    if c.health_cmd:
        try:
            proc = subprocess.run(
                ["bash", "-c", c.health_cmd],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
            )
            if proc.returncode == 0:
                return ProbeResult(
                    candidate=c,
                    reachable=True,
                    http_status=200,
                    body_excerpt=proc.stdout.strip()[:120],
                )
            return ProbeResult(
                candidate=c,
                reachable=False,
                error=f"health_cmd exited {proc.returncode}: {proc.stderr.strip()[:120]}",
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return ProbeResult(
                candidate=c,
                reachable=False,
                error=f"health_cmd failed: {e}",
            )

    # Refuse streaming-shaped endpoints up-front. urlopen() against
    # /sse, /events, /ws, or /stream holds the connection open and
    # exhausts PROBE_TIMEOUT_SECONDS, silently reporting OFFLINE even
    # when the socket is healthy. Surface this as an actionable error
    # so candidate authors wire up /health or health_cmd instead.
    if c.health_url and any(
        c.health_url.endswith(sfx)
        for sfx in ("/sse", "/events", "/ws", "/stream")
    ):
        return ProbeResult(
            candidate=c,
            reachable=False,
            error=(
                f"streaming endpoint; configure /health or health_cmd "
                f"instead of {c.health_url}"
            ),
        )

    if not c.health_url:
        return ProbeResult(
            candidate=c,
            reachable=False,
            error="no health URL (stdio-only candidate; presence is best-effort)",
        )
    req = urllib.request.Request(c.health_url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as r:
            # Inspect Content-Type BEFORE reading the body. urllib only
            # reads response headers here; the body stream is still
            # untouched, so closing via the `with` exit tears down the
            # stream without consuming chunked frames. This catches
            # streaming endpoints whose URL shape does not advertise
            # SSE/WS, e.g. notify, /stream/agent-events, gRPC-web.
            #
            # Use the canonical media type (lowercased, parameter-
            # stripped) and exact match against the marker set, rather
            # than a substring scan, so vendor extensions whose media
            # type contains "event-stream" as a substring do not falsely
            # reject. RFC 9110 §8.3 lets servers vary case; lower-case
            # before compare.
            ctype = str(r.headers.get("Content-Type", "")).lower().split(";", 1)[0].strip()
            if ctype in (
                "text/event-stream",
                "multipart/x-mixed-replace",
                "application/grpc-web",
            ):
                return ProbeResult(
                    candidate=c,
                    reachable=False,
                    error=(
                        f"streaming endpoint detected via Content-Type "
                        f"({ctype!r}); configure /health or health_cmd "
                        f"instead of {c.health_url}"
                    ),
                )
            body = r.read(512).decode(errors="replace")
            return ProbeResult(
                candidate=c,
                reachable=True,
                http_status=r.status,
                body_excerpt=body[:120],
            )
    except urllib.error.HTTPError as e:
        # A real HTTP error response still proves the socket is open.
        body = ""
        try:
            body = e.read(512).decode(errors="replace")
        except Exception:  # defensive — connection is already erroring
            pass
        return ProbeResult(
            candidate=c,
            reachable=True,
            http_status=e.code,
            body_excerpt=body[:120],
            error=f"HTTP {e.code}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return ProbeResult(
            candidate=c,
            reachable=False,
            error=str(e),
        )


def probe_all() -> list[ProbeResult]:
    return [probe_one(c) for c in CANDIDATES]


# ── SQL emission ───────────────────────────────────────────────────────


def sql_quote(value: object) -> str:
    """Render a Python value as a SQL literal (single-quoted, NULL-safe)."""
    if value is None or value == "":
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    s = str(value).replace("'", "''")
    return f"'{s}'"


def sql_int(value: int | None) -> str:
    """Render a Python int as a SQL integer literal (no quotes)."""
    if value is None:
        return "NULL"
    if not isinstance(value, int):
        raise TypeError(
            f"sql_int expected int, got {type(value).__name__}: {value!r}"
        )
    return str(value)


def fetch_service_type_ids() -> dict[str, int]:
    """
    Resolve service-type labels to ids by querying ``terrain.service_types``.

    Fails loudly on connection / SELECT error so we never silently emit
    wrong ids (the registry is a long-lived artifact; bad ids here would
    propagate to every topology consumer).
    """
    sql = (
        "SELECT name, id FROM terrain.service_types WHERE name IN "
        "('MCP','Microservice','Express','Spring Boot','Python Service')"
    )
    rows = run_capture(sql)
    ids: dict[str, int] = {}
    for row in rows:
        if len(row) >= 2:
            try:
                ids[row[0]] = int(row[1])
            except ValueError:
                pass
    if not ids:
        raise RuntimeError(
            "fetch_service_type_ids: SELECT returned zero rows; refusing to "
            "fall back to magic constants. Investigate terrain.service_types."
        )
    return ids


def emit_upsert_runnabble_service(
    c: Candidate,
    type_ids: dict[str, int],
    status: str,
) -> str:
    if c.kind != "runnable_service":
        raise ValueError(f"{c.name}: not a runnable_service")
    type_id = type_ids.get(c.service_type or "Express", type_ids["Express"])
    cols = {
        "name": sql_quote(c.name),
        "service_type_id": sql_int(type_id),
        "port": sql_int(c.port),
        "workspace_path": sql_quote(c.workspace_path),
        "health_check_url": sql_quote(c.health_url),
        "status": sql_quote(status),
        "version": "NULL",
        "description": sql_quote(c.description),
        "startup": sql_quote(c.startup),
        "health": "NULL",
    }
    name_expr = cols["name"]
    set_clause = ", ".join(f"{k} = {v}" for k, v in cols.items() if k != "name")
    insert_cols = ", ".join(cols.keys())
    insert_vals = ", ".join(cols.values())
    return (
        "DO $do$ BEGIN\n"
        f"  UPDATE terrain.runnable_services SET {set_clause} WHERE name = {name_expr};\n"
        "  IF NOT FOUND THEN\n"
        f"    INSERT INTO terrain.runnable_services ({insert_cols})\n"
        f"    VALUES ({insert_vals});\n"
        "  END IF;\n"
        "END $do$;\n"
    )


def emit_upsert_mcp_server(c: Candidate, status: str) -> str:
    if c.kind != "mcp_server":
        raise ValueError(f"{c.name}: not an mcp_server")
    # service_type_id=1 is fixed for MCP servers — mirrors terrain-mcp's tool.
    cols = {
        "name": sql_quote(c.name),
        "service_type_id": sql_int(1),
        "port": sql_int(c.port),
        "workspace_path": sql_quote(c.workspace_path),
        "health_check_url": sql_quote(c.health_url),
        "status": sql_quote(status),
        "transport_type": sql_quote(c.transport_type),
        "version": "NULL",
        "description": sql_quote(c.description),
        "startup": sql_quote(c.startup),
        "health": "NULL",
    }
    name_expr = cols["name"]
    set_clause = ", ".join(f"{k} = {v}" for k, v in cols.items() if k != "name")
    insert_cols = ", ".join(cols.keys())
    insert_vals = ", ".join(cols.values())
    return (
        "DO $do$ BEGIN\n"
        f"  UPDATE terrain.mcp_servers SET {set_clause} WHERE name = {name_expr};\n"
        "  IF NOT FOUND THEN\n"
        f"    INSERT INTO terrain.mcp_servers ({insert_cols})\n"
        f"    VALUES ({insert_vals});\n"
        "  END IF;\n"
        "END $do$;\n"
    )


def emit_upsert_dependency(
    source_kind: str,
    source_name: str,
    target_kind: str,
    target_name: str,
) -> str:
    # We use PL/pgSQL DECLARE + variable lookup rather than WITH CTE + FROM
    # so that BOTH the UPDATE and the fallback INSERT share the same
    # src_id / tgt_id / edge_desc bindings. (WITH-CTE is local to a
    # single statement, so a follow-up INSERT would not see src/tgt
    # from the prior UPDATE's CTE.)
    #
    # Note: the variable name is `edge_desc`, NOT `desc`. `desc` is
    # reserved in PL/pgSQL (it's the ORDER BY ... DESC direction),
    # which Postgres will throw `syntax error at or near "desc"` on.
    src_table = "mcp_servers" if source_kind == "mcp_server" else "runnable_services"
    tgt_table = "mcp_servers" if target_kind == "mcp_server" else "runnable_services"
    edge_desc_expr = sql_quote(f"{source_name} -> {target_name}")
    return (
        "DO $do$ DECLARE\n"
        "  src_id INTEGER;\n"
        "  tgt_id INTEGER;\n"
        f"  edge_desc TEXT := {edge_desc_expr};\n"
        "BEGIN\n"
        f"  SELECT id INTO src_id FROM terrain.{src_table} "
        f"   WHERE name = {sql_quote(source_name)};\n"
        f"  SELECT id INTO tgt_id FROM terrain.{tgt_table} "
        f"   WHERE name = {sql_quote(target_name)};\n"
        "  IF src_id IS NULL OR tgt_id IS NULL THEN\n"
        "    RETURN;  -- one side not yet registered; skip silently\n"
        "  END IF;\n"
        "  UPDATE terrain.service_dependencies d SET\n"
        "    criticality = COALESCE(d.criticality, 'medium'),\n"
        "    description = COALESCE(d.description, edge_desc)\n"
        f"  WHERE d.source_type = {sql_quote(source_kind)}\n"
        "    AND d.source_id = src_id\n"
        f"    AND d.target_type = {sql_quote(target_kind)}\n"
        "    AND d.target_id = tgt_id;\n"
        "  IF NOT FOUND THEN\n"
        "    INSERT INTO terrain.service_dependencies\n"
        "      (source_type, source_id, target_type, target_id,\n"
        "       criticality, description)\n"
        f"    VALUES ({sql_quote(source_kind)}, src_id,\n"
        f"            {sql_quote(target_kind)}, tgt_id,\n"
        "            'medium', edge_desc);\n"
        "  END IF;\n"
        "END $do$;\n"
    )


def emit_all_upserts(
    type_ids: dict[str, int],
    status_per_name: dict[str, str],
) -> list[str]:
    statements: list[str] = []
    for c in CANDIDATES:
        status = status_per_name.get(c.name, "OFFLINE")
        if c.kind == "mcp_server":
            statements.append(emit_upsert_mcp_server(c, status))
        elif c.kind == "runnable_service":
            statements.append(emit_upsert_runnabble_service(c, type_ids, status))
    for sk, sn, tk, tn in DEPENDENCIES:
        statements.append(emit_upsert_dependency(sk, sn, tk, tn))
    return statements


# ── Driver selection + DB execution ────────────────────────────────────


def find_psql() -> str:
    """Locate the psql binary. Used only when psycopg2 is unavailable."""
    on_path = shutil.which("psql")
    if on_path:
        return on_path
    candidates = sorted(glob.glob("/usr/lib/postgresql/*/bin/psql")) + [
        "/usr/local/postgres*/bin/psql",
        "/opt/homebrew/opt/postgresql*/bin/psql",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError(
        "psql binary not found and psycopg2 is not importable. "
        "Install postgresql-client or psycopg2 to proceed.",
    )


def select_driver() -> tuple[str, str | None]:
    """Return (driver, path) where path is None for psycopg2."""
    if HAS_PSYCOPG2:
        return ("psycopg2", None)
    return ("psql", find_psql())


def pg_env() -> dict[str, str]:
    env = os.environ.copy()
    for key, default in PG_ENV_DEFAULTS.items():
        env.setdefault(key, default)
    if "PGPASSWORD" not in env:
        print(
            "error: PGPASSWORD is not set. Set it to the postgres password "
            "(terrain-mcp's default is 'pgpass').",
            file=sys.stderr,
        )
        sys.exit(4)
    return env


def _run_capture_psycopg2(sql: str, env: dict[str, str]) -> list[list[str]]:
    # autocommit=True is harmless on SELECTs and keeps this driver's
    # transactional model identical to _execute_many_psycopg2 below —
    # see that function for the full rationale. Set post-connect
    # because psycopg2 2.x rejects ``autocommit`` as a libpq DSN option
    # (audit 2026-06-23).
    conn = psycopg2.connect(  # type: ignore[name-defined]
        host=env["PGHOST"],
        port=int(env["PGPORT"]),
        dbname=env["PGDATABASE"],
        user=env["PGUSER"],
        password=env["PGPASSWORD"],
        connect_timeout=5,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [
                ["" if c is None else str(c) for c in row]
                for row in cur.fetchall()
            ]
    finally:
        conn.close()


def _run_capture_psql(sql: str, psql_path: str, env: dict[str, str]) -> list[list[str]]:
    cmd = [
        psql_path,
        "-h", env["PGHOST"],
        "-p", env["PGPORT"],
        "-U", env["PGUSER"],
        "-d", env["PGDATABASE"],
        "-tAc", sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    return [line.split("\t") for line in proc.stdout.splitlines() if line.strip()]


def run_capture(sql: str) -> list[list[str]]:
    """Run a SELECT and return rows as lists of column strings."""
    env = pg_env()
    driver, path = select_driver()
    if driver == "psycopg2":
        return _run_capture_psycopg2(sql, env)
    return _run_capture_psql(sql, path or find_psql(), env)


def _execute_many_psycopg2(statements: Iterable[str], env: dict[str, str]) -> int:
    # autocommit=True makes each ``cur.execute()`` its own atomic
    # transaction. A failed statement rolls back at the server; subsequent
    # executes proceed unaffected. Crucially, this avoids a silent loss of
    # data: with autocommit=False (psycopg2's default), the connect-time
    # transaction stays open across every iteration of the loop, and
    # ``conn.close()`` at function-end rolls back the entire UPSERT batch
    # per PEP 249's transactional-closure semantics — i.e. every successful
    # registration this script emits disappears into the void unless we
    # either commit explicitly or set autocommit=True here.
    #
    # NOTE: psycopg2 2.x rejects ``autocommit`` as a connect() keyword
    # (it is not a libpq DSN option); it must be set as an attribute on
    # the connection *after* connect. Getting this wrong aborts the
    # script at fetch_service_type_ids() before any UPSERT runs and
    # silently drops every registration — see the audit on 2026-06-23.
    conn = psycopg2.connect(  # type: ignore[name-defined]
        host=env["PGHOST"],
        port=int(env["PGPORT"]),
        dbname=env["PGDATABASE"],
        user=env["PGUSER"],
        password=env["PGPASSWORD"],
        connect_timeout=5,
    )
    conn.autocommit = True
    failures = 0
    try:
        with conn.cursor() as cur:
            for sql in statements:
                try:
                    cur.execute(sql)
                    sys.stdout.write(f"ok: {sql.splitlines()[0]}\n")
                except Exception as e:
                    # Rollback is a no-op under autocommit=True (each statement
                    # is already atomic at the server) — preserved as a
                    # defensive call that costs nothing.
                    conn.rollback()
                    failures += 1
                    sys.stderr.write(
                        f"failed: {sql.splitlines()[0]}\n"
                        f"  err: {str(e)[:400]}\n"
                    )
    finally:
        conn.close()
    return failures


def _execute_many_psql(
    statements: Iterable[str], psql_path: str, env: dict[str, str]
) -> int:
    failures = 0
    for sql in statements:
        proc = subprocess.run(
            [
                psql_path,
                "-h", env["PGHOST"],
                "-p", env["PGPORT"],
                "-U", env["PGUSER"],
                "-d", env["PGDATABASE"],
                "-v", "ON_ERROR_STOP=1",
                "-c", sql,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if proc.returncode != 0:
            failures += 1
            sys.stderr.write(
                f"failed (exit={proc.returncode}): "
                f"{sql.splitlines()[0]}\n"
                f"stderr: {proc.stderr.strip()[:400]}\n"
            )
        else:
            sys.stdout.write(f"ok: {sql.splitlines()[0]}\n")
    return failures


def execute_many(statements: Iterable[str]) -> int:
    env = pg_env()
    driver, path = select_driver()
    if driver == "psycopg2":
        return _execute_many_psycopg2(statements, env)
    return _execute_many_psql(statements, path or find_psql(), env)


# ── Pretty printing ────────────────────────────────────────────────────


def render_probe(probes: list[ProbeResult]) -> str:
    """Render probe results as a fixed-width table."""
    headers = ("name", "port", "kind", "reachable", "status", "excerpt")
    rows: list[tuple[str, ...]] = []
    for p in probes:
        rows.append(
            (
                p.candidate.name,
                str(p.candidate.port) if p.candidate.port is not None else "-",
                p.candidate.kind,
                "yes" if p.reachable else "no",
                str(p.http_status) if p.http_status else "-",
                (p.body_excerpt or p.error or "")[:40].replace("\n", " "),
            )
        )
    widths = [
        max(len(h), max(len(r[i]) for r in rows) if rows else 0)
        for i, h in enumerate(headers)
    ]
    line = lambda fields: "  ".join(f.ljust(w) for f, w in zip(fields, widths))
    buf = [line(headers), "-" * (sum(widths) + 2 * (len(widths) - 1))]
    for r in rows:
        buf.append(line(r))
    return "\n".join(buf)


def render_mesh_summary() -> str:
    """Read the live terrain.* tables and emit a single human-readable
    snapshot of the registered mesh.

    Three fixed-width tables:

    * MCP servers (name, port, status, transport)
    * Runnable services (name, port, status, service type)
    * Service dependencies (source_type/source_name → target_type/target_name + criticality)

    This is the read-side mirror of the ``terrain_infrastructure_summary``
    tool exposed by terrain-mcp over stdio. Cross-check the two via
    ``bin/verify-mesh-mcp.py``.
    """
    headers_mcp = ["name", "port", "status", "transport"]
    headers_svc = ["name", "port", "status", "svc_type"]
    headers_dep = [
        "source_type", "source_name",
        "target_type", "target_name",
        "criticality",
    ]
    mcp_rows = run_capture(
        "SELECT name, "
        "  COALESCE(port::text, '-') AS port, "
        "  COALESCE(status, '-') AS status, "
        "  COALESCE(transport_type, '-') AS transport "
        "FROM terrain.mcp_servers ORDER BY name"
    )
    svc_rows = run_capture(
        "SELECT rs.name, "
        "  COALESCE(rs.port::text, '-') AS port, "
        "  COALESCE(rs.status, '-') AS status, "
        "  COALESCE(st.name, '-') AS svc_type "
        "FROM terrain.runnable_services rs "
        "LEFT JOIN terrain.service_types st ON st.id = rs.service_type_id "
        "ORDER BY rs.name"
    )
    dep_rows = run_capture(
        "SELECT sd.source_type, "
        "  COALESCE(ms.name, rs_src.name) AS source_name, "
        "  sd.target_type, "
        "  COALESCE(rs.name, ms_tgt.name) AS target_name, "
        "  COALESCE(sd.criticality, '-') AS criticality "
        "FROM terrain.service_dependencies sd "
        "LEFT JOIN terrain.mcp_servers ms "
        "       ON sd.source_type = 'mcp_server' AND sd.source_id = ms.id "
        "LEFT JOIN terrain.runnable_services rs_src "
        "       ON sd.source_type = 'runnable_service' AND sd.source_id = rs_src.id "
        "LEFT JOIN terrain.runnable_services rs "
        "       ON sd.target_type = 'runnable_service' AND sd.target_id = rs.id "
        "LEFT JOIN terrain.mcp_servers ms_tgt "
        "       ON sd.target_type = 'mcp_server' AND sd.target_id = ms_tgt.id "
        "ORDER BY sd.source_type, source_name"
    )

    def _pad(row: list[str], n: int) -> list[str]:
        return (row + [""] * n)[:n]

    def _tbl(headers: list[str], rows: list[list[str]]) -> str:
        n = len(headers)
        norm = [_pad(r, n) for r in rows]
        widths = [len(h) for h in headers]
        for r in norm:
            for i, cell in enumerate(r):
                if len(cell) > widths[i]:
                    widths[i] = len(cell)
        sep = "  "
        out: list[str] = [
            sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)),
            sep.join("-" * widths[i] for i in range(n)),
        ]
        for r in norm:
            out.append(sep.join(c.ljust(widths[i]) for i, c in enumerate(r)))
        return "\n".join(out)

    buf: list[str] = [
        "=== Mesh (terrain.* live snapshot) ===",
        "",
        f"## MCP Servers ({len(mcp_rows)})",
        _tbl(headers_mcp, mcp_rows),
        "",
        f"## Runnable Services ({len(svc_rows)})",
        _tbl(headers_svc, svc_rows),
        "",
        f"## Service Dependencies ({len(dep_rows)})",
        _tbl(headers_dep, dep_rows),
    ]
    return "\n".join(buf)


# ── CLI ───────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mesh-register",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print upsert SQL to stdout instead of executing it.",
    )
    mode.add_argument(
        "--probe-only",
        "--json",
        action="store_true",
        help="Emit the probe result as JSON to stdout and exit.",
    )
    mode.add_argument(
        "--mesh",
        action="store_true",
        help="Print a single human-readable summary of the registered mesh "
             "(mcp_servers + runnable_services + service_dependencies). The "
             "read-side mirror of terrain_infrastructure_summary exposed by "
             "terrain-mcp; cross-check with bin/verify-mesh-mcp.py.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    probes = probe_all()

    if args.probe_only:
        sys.stdout.write(
            json.dumps(
                [
                    {
                        **asdict(p.candidate),
                        "reachable": p.reachable,
                        "http_status": p.http_status,
                        "body_excerpt": p.body_excerpt,
                        "error": p.error,
                    }
                    for p in probes
                ],
                indent=2,
                sort_keys=True,
            ),
        )
        sys.stdout.write("\n")
        return 0

    if args.mesh:
        sys.stdout.write(render_mesh_summary())
        sys.stdout.write("\n")
        return 0

    sys.stderr.write("-- live mesh probe --\n")
    sys.stderr.write(render_probe(probes))
    sys.stderr.write("\n")

    status_per_name = {
        p.candidate.name: ("ONLINE" if p.reachable else "OFFLINE") for p in probes
    }

    if args.dry_run:
        # Static id fallback for dry-run only (no DB touched).
        type_ids = {
            "MCP": 1,
            "Microservice": 2,
            "Express": 3,
            "Spring Boot": 4,
            "Python Service": 12,
        }
        for stmt in emit_all_upserts(type_ids, status_per_name):
            sys.stdout.write(stmt)
            sys.stdout.write("\n")
        sys.stderr.write(
            "dry-run: SQL emitted above; pass without --dry-run to execute.\n"
        )
        return 2

    # Sanity-probe the driver before any heavy work.
    driver, _ = select_driver()
    sys.stderr.write(f"-- driver: {driver} --\n")

    type_ids = fetch_service_type_ids()
    sys.stderr.write(
        f"-- service type ids: {json.dumps(type_ids, sort_keys=True)} --\n"
    )
    failures = execute_many(emit_all_upserts(type_ids, status_per_name))
    if failures:
        return 3
    sys.stderr.write(
        f"-- registered {len(CANDIDATES)} service(s) and "
        f"{len(DEPENDENCIES)} dependency edge(s) --\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
