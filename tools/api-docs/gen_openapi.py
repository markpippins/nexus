#!/usr/bin/env python3
"""Generate OpenAPI 3.0 specs + markdown REST-API references for *-srv services.

Consumes the endpoint inventory produced by extract_routes.py and emits, per
service:
  - <service-dir>/openapi.yaml  — OpenAPI 3.0.3 spec (path/method/params level;
    request/response bodies are intentionally generic — field-level contracts
    live in the service code and README)
  - <service-dir>/API.md        — markdown REST-API reference (endpoint table)

Special cases:
  - vision-srv (FastAPI): fetches the live /openapi.json from the running
    service and saves it (converted to YAML) — far richer than the generic
    route-level spec. Falls back to the generic spec if the service is down.
  - semantics-srv: skipped — it already carries a registry-derived spec
    (scripts/generate-openapi.ts + openapi.yaml).
  - terrain-srv: skipped (retired). pty-srv: skipped (RETIRED M3, WebSocket-only, no REST).

Usage:
    python tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
    python tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json

Requires PyYAML (python3 -c "import yaml").
"""
import argparse
import json
import os
import re
import urllib.request

import yaml

import extract_routes  # noqa: E402 — JVM_SERVICES module-dir registry

# ── Service metadata: key = inventory key, port = default listen port ──────
SERVICES = {
    "typescript/assembly-srv": {
        "title": "assembly-srv — Assembly Forum REST API",
        "port": 3107,
        "desc": "Assembly forum service: forums, threads, comments, users, harvests, "
                "work requests, agent records, agendas, plans, specifications, "
                "assessments, observations, search, counts, and stats refresh.",
    },
    "typescript/cascade-srv": {
        "title": "cascade-srv — Event Query API",
        "port": 3106,
        "desc": "Query service over the cascade event model: events with filtering, "
                "pagination and time-range aggregation, assessments, analytics.",
    },
    "typescript/conduit-srv": {
        "title": "conduit-srv — WorkRequest Pipeline Orchestrator (REST surface)",
        "port": 3104,
        "desc": "REST surface of the conduit pipeline orchestrator: workflows, tickets, "
                "tokens, config (cron, failure-recovery), governance (replay, events), "
                "vision, and session log. The MCP tool surface is served separately "
                "(Streamable HTTP JSON-RPC on the same port).",
    },
    "typescript/draft-srv": {
        "title": "draft-srv — Draft Service Workspace / DB Workbench API",
        "port": 3170,
        "desc": "Draft service workspace hosting new backend components pending promotion "
                "to dedicated services. Current tenant: DB Workbench API (multi-engine "
                "database browsing, query/DDL execution, schema listing, and connection "
                "testing) backing data-explorer-ui.",
    },
    "typescript/execution-srv": {
        "title": "execution-srv — Execution Observability API",
        "port": 3110,
        "desc": "Read-only API over the execution schema: requests, leases, attempts, "
                "receipts, integrity scans, and cross-schema lineage.",
    },
    "typescript/harness-srv": {
        "title": "harness-srv — Generic Execution Harness",
        "port": 3420,
        "desc": "Merges Tackle role context (prompt + tool ACL + procedure cards) with "
                "Wind task context (inputs + acceptance criteria) and invokes an agent "
                "via the configured harness.",
    },
    "typescript/kernel-srv": {
        "title": "kernel-srv — Event-Sourced Kernel API",
        "port": 8100,
        "desc": "Kernel event model: transition lifecycle, receipts, causality chains, "
                "aggregate event streams, active policy, and health views.",
    },
    "typescript/knowledge-srv": {
        "title": "knowledge-srv — Knowledge Graph REST API",
        "port": 3109,
        "desc": "Knowledge graph surface: entities by section, relation edges, "
                "cross-references, migrations, and summary.",
    },
    "typescript/nebula-srv": {
        "title": "nebula-srv — Nebula Knowledge-Graph API",
        "port": 3101,
        "desc": "Canonical asset graph: systems, subsystems, features, documents, "
                "harvests, agent records, projections, knowledge graph, and cross-references.",
    },
    "typescript/peb-srv": {
        "title": "peb-srv — Push Event Bus API",
        "port": 3111,
        "desc": "Push Event Bus: decisions, transactions, fleet health, events, entities, "
                "state, traces, and the SSE event stream.",
    },
    "typescript/pty-srv": {
        "title": "pty-srv — WebSocket PTY Gateway (RETIRED M3)",
        "port": 3120,
        "desc": "RETIRED (M3): WebSocket terminal gateway (ws + node-pty), superseded by worker.pty-transport on :3130. No REST routes are mounted; "
                "the API is the WebSocket upgrade surface only.",
        "skip_openapi": True,
    },
    "typescript/role-memory-srv": {
        "title": "role-memory-srv — Role Memory Procedure Registry",
        "port": 3500,
        "desc": "Role Memory Procedure Registry: procedure cards and indexes, with a "
                "PG→Redis refresh endpoint.",
    },
    "typescript/semantics-srv": {
        "title": "semantics-srv — Semantics Topology Legend API",
        "port": 3160,
        "desc": "Type-level semantic topology: concepts, representations, relationships, "
                "consumers, identities, snapshots, observations, and drift findings.",
        "skip_openapi": True,  # has its own registry-derived spec
    },
    "typescript/tackle-prompt-sync-srv": {
        "title": "tackle-prompt-sync-srv — Prompt + Task Registry Sync",
        "port": 3501,
        "desc": "Reads prompt templates and active tasks from PostgreSQL and populates "
                "the Redis prompt:* / task:* caches for live agents.",
    },
    "typescript/tackle-srv": {
        "title": "tackle-srv — Tackle Role Memory + Agent Orchestration",
        "port": 3410,
        "desc": "Tackle role memory and orchestration: AI config, sessions, roles, "
                "scheduler, memory, prompts, tool access, failure recovery, tasks, and logs.",
    },
    "typescript/terrain-srv": {
        "title": "terrain-srv — Retired",
        "port": None,
        "desc": "Retired service (repointed to the Spring Boot terrain backend). "
                "Only dist/ artifacts remain; no live API.",
        "skip_openapi": True,
    },
    "typescript/voyager-srv": {
        "title": "voyager-srv — Filesystem / Entity Voyager API",
        "port": 3114,
        "desc": "Voyager over filesystems and entities: scan epochs, file/directory "
                "observations, topology signals and edge hints, identity candidates, "
                "entities, spans, requirements, and stats.",
    },
    "typescript/wind-srv": {
        "title": "wind-srv — Wind Workflow Schema API",
        "port": 3300,
        "desc": "REST API for the wind workflow schema: offices, titles, tasks, workflow "
                "graphs, runtime instances, tickets, and receipts.",
    },
    "typescript/aegis-srv": {
        "title": "aegis-srv — Aegis State-Machine Registry API",
        "port": 3116,
        "desc": "REST API for the aegis schema: TLA+ state-machine registries "
                "(constants, variables, states, transitions, invariants, properties, "
                "temporal properties, resolution-schema mappings), validation and "
                "model-check results, and audited execution logs.",
    },
    "python/vision-srv": {
        "title": "vision-srv — LOSM REST API",
        "port": 8003,
        "desc": "FastAPI backend for the LOSM (Layered Operational State Machine): work "
                "requests, branches, artifacts, and DAG compilation/validation.",
        "fastapi": True,
        "fastapi_note": "OpenAPI spec captured live from the service's /openapi.json (FastAPI-native, "
                         "schema-complete); the table below is the source-route inventory.",
    },
    # ── JVM port modules (Spring; extracted by extract_routes.JVM_SERVICES) ──
    # Specs live inside the module dirs; the ports mirror the TS services'
    # route surfaces (aegis at /api/*, shrapnel namespaced at /api/shrapnel/*
    # inside the nexus-core monolith, port 8092).
    "jvm/nexus-core-aegis": {
        "title": "nexus-core-aegis — JVM Aegis State-Machine Registry Port",
        "port": 8092,
        "desc": "JVM port of aegis-srv inside the nexus-core monolith (Spring, JdbcTemplate "
                "over the aegis schema): TLA+ state-machine registries, validation and "
                "model-check results, Wind compilations, and audited execution logs. "
                "Mirrors the typescript/aegis-srv route surface; aligned via TypeSpec.",
    },
    "jvm/nexus-core-shrapnel": {
        "title": "nexus-core-shrapnel — JVM Shrapnel EAV Object Store Port",
        "port": 8092,
        "desc": "JVM port of shrapnel inside the nexus-core monolith (Spring, JdbcTemplate "
                "over the shrapnel schema): encode/decode EAV objects, fields, field types, "
                "stereotypes. Routes mounted at /api/shrapnel/*; mirrors the "
                "typescript/shrapnel surface; contract-first via typespec/v1/shrapnel.",
    },
}

SKIPPED_KEYS = {k for k, v in SERVICES.items() if v.get("skip_openapi")}

# Hand-authored spec section delimiter. Everything in a service's API.md from
# this marker to the end of file is preserved verbatim across regeneration,
# so field-level contracts / envelope docs written by hand survive
# extract_routes.py + gen_openapi.py runs. The generated endpoint table stays
# the canonical inventory; the hand-authored section is a UI/consumer spec.
API_SPEC_BEGIN = "<!-- API-SPEC-BEGIN -->"

JSON_BODY_REF = "#/components/schemas/JsonBody"
ERROR_REF = "#/components/schemas/Error"


def path_to_openapi(p):
    """Convert :param Express paths to {param} OpenAPI paths."""
    return re.sub(r":([A-Za-z_][\w]*)", r"{\1}", p)


def tag_for_path(p):
    segs = [s for s in p.split("/") if s]
    if not segs:
        return "root"
    if segs[0] == "api":
        segs = segs[1:]
    return segs[0] if segs else "root"


def operation_id(method, path):
    segs = [s for s in path.split("/") if s]
    if segs and segs[0] == "api":
        segs = segs[1:]
    parts = [method.lower()]
    for s in segs:
        s = re.sub(r"[^A-Za-z0-9]", "_", s).strip("_")
        if s:
            parts.append(s)
    return "_".join(parts)[:120]


def build_operation(method, path, summary):
    status = {"GET": "200", "POST": "201", "PUT": "200", "PATCH": "200", "DELETE": "200"}.get(method, "200")
    op = {
        "tags": [tag_for_path(path)],
        "operationId": operation_id(method, path),
        "responses": {
            status: {
                "description": "Success",
                "content": {"application/json": {"schema": {"$ref": JSON_BODY_REF}}},
            },
            "400": {"description": "Bad request", "content": {"application/json": {"schema": {"$ref": ERROR_REF}}}},
            "404": {"description": "Not found", "content": {"application/json": {"schema": {"$ref": ERROR_REF}}}},
            "500": {"description": "Server error", "content": {"application/json": {"schema": {"$ref": ERROR_REF}}}},
        },
    }
    if summary:
        op["summary"] = summary[:140]
    params = []
    for m in re.finditer(r"\{([A-Za-z_][\w]*)\}", path):
        params.append({
            "name": m.group(1),
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        })
    if params:
        op["parameters"] = params
    return op


def build_spec(meta, endpoints):
    paths = {}
    tags = set()
    for e in endpoints:
        opath = path_to_openapi(e["path"])
        paths.setdefault(opath, {})[e["method"].lower()] = build_operation(e["method"], opath, e.get("summary") or "")
        tags.add(tag_for_path(opath))
    import hashlib
    inv_digest = hashlib.sha1(
        json.dumps(sorted(endpoints, key=lambda e: (e["path"], e["method"])), sort_keys=True).encode()
    ).hexdigest()[:8]
    return {
        "openapi": "3.0.3",
        "info": {
            "title": meta["title"],
            "version": f"1.0.{inv_digest}",
            "description": meta["desc"] + (
                "\n\nEndpoint inventory generated from source route registrations by "
                "`nexus/tools/api-docs/`. Request/response bodies are generic; "
                "field-level contracts live in the service code and its README."
            ),
        },
        "servers": [{"url": f"http://localhost:{meta['port']}"}] if meta.get("port") else [],
        "tags": [{"name": t} for t in sorted(tags)],
        "paths": paths,
        "components": {
            "schemas": {
                "JsonBody": {"type": "object", "additionalProperties": True, "description": "Generic JSON body (fields are service-specific)."},
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string", "description": "Machine-readable error code (e.g. not_found, validation_failed)."},
                        "message": {"type": "string", "description": "Human-readable error detail."},
                    },
                    "additionalProperties": True,
                },
            }
        },
        "x-generated-by": "nexus/tools/api-docs/gen_openapi.py",
    }


def fetch_fastapi_spec(port):
    url = f"http://localhost:{port}/openapi.json"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


class QuotingDumper(yaml.SafeDumper):
    """Quote strings that YAML 1.1 would coerce to booleans/numbers/null."""

    def represent_str(self, data):
        low = data.lower()
        if low in ("yes", "no", "true", "false", "on", "off", "null", "~") or data.strip() == "":
            return self.represent_scalar("tag:yaml.org,2002:str", data, style='"')
        return super().represent_str(data)


QuotingDumper.add_representer(str, QuotingDumper.represent_str)


def dump_yaml(obj, path):
    with open(path, "w") as f:
        yaml.dump(obj, f, Dumper=QuotingDumper, default_flow_style=False, sort_keys=False, allow_unicode=True, width=120)


def build_api_md(meta, endpoints, kind):
    note = meta.get("fastapi_note")
    lines = [
        f"# {meta['title']}",
        "",
        f"> Port: **{meta['port']}**  ",
        "> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)",
        "",
        meta["desc"],
        "",
        f"**{len(endpoints)} endpoints** — inventory generated from source route "
        "registrations (`nexus/tools/api-docs/`).",
    ]
    if note:
        lines += ["", f"> {note}"]
    lines += [
        "",
        "| Method | Path | Description |",
        "|--------|------|-------------|",
    ]
    for e in sorted(endpoints, key=lambda x: (x["path"], x["method"])):
        desc = (e.get("summary") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {e['method']} | `{e['path']}` | {desc} |")
    lines += [
        "",
        "## Regeneration",
        "",
        "```bash",
        "cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json",
        f"python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json{'' if kind == 'fastapi' else '   # (vision-srv also refreshes from the live FastAPI spec)'}",
        "```",
        "",
        API_SPEC_BEGIN,
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate OpenAPI + API.md per *-srv from the endpoint inventory.")
    ap.add_argument("--inventory", default="/tmp/api_inventory.json")
    ap.add_argument("--root", default="/home/codex/dev/nexus")
    ap.add_argument("--skip-fastapi", action="store_true", help="do not fetch vision-srv's live FastAPI spec")
    ap.add_argument("--only", default="", help="comma-separated service names to regenerate (default: all)")
    args = ap.parse_args(argv)
    only_list = [s.strip() for s in args.only.split(",") if s.strip()]

    with open(args.inventory) as f:
        inventory = json.load(f)

    summary = {}
    for key, meta in SERVICES.items():
        if key in SKIPPED_KEYS:
            summary[key] = "skipped"
            continue
        if only_list and not any(key.endswith(o) for o in only_list):
            summary[key] = "skipped (--only)"
            continue
        # JVM service keys ("jvm/nexus-core-aegis") map to their repo-relative
        # module dir from extract_routes.JVM_SERVICES; TS/python keys map to
        # <base>/<name> directly.
        jvm_rel = extract_routes.JVM_SERVICES.get(key)
        if jvm_rel:
            svc_dir = os.path.join(args.root, jvm_rel)
        else:
            svc_dir = os.path.join(args.root, key.replace("/", os.sep))
        if not os.path.isdir(svc_dir):
            summary[key] = "missing dir"
            continue
        endpoints = inventory.get(key, [])
        kind = "generic"
        if meta.get("fastapi") and not args.skip_fastapi:
            try:
                spec = fetch_fastapi_spec(meta["port"])
                dump_yaml(spec, os.path.join(svc_dir, "openapi.yaml"))
                kind = "fastapi(live)"  # API.md still uses the source-route inventory table
            except Exception as e:
                print(f"  ! {key}: FastAPI fetch failed ({e}); falling back to generic")
        if kind == "generic":
            spec = build_spec(meta, endpoints)
            dump_yaml(spec, os.path.join(svc_dir, "openapi.yaml"))
        api_path = os.path.join(svc_dir, "API.md")
        generated = build_api_md(meta, endpoints, kind)
        # Preserve any hand-authored spec section from the previous file so it
        # survives regeneration (see API_SPEC_BEGIN).
        hand = ""
        if os.path.exists(api_path):
            with open(api_path, encoding="utf-8", errors="replace") as f:
                prev = f.read()
            idx = prev.find(API_SPEC_BEGIN)
            if idx != -1:
                hand = prev[idx + len(API_SPEC_BEGIN):]
        with open(api_path, "w") as f:
            f.write(generated)
            if hand:
                f.write(hand)
                if not hand.endswith("\n"):
                    f.write("\n")
        summary[key] = f"{len(endpoints)} endpoints ({kind})"
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
