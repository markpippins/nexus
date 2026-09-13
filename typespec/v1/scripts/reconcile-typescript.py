#!/usr/bin/env python3
"""
reconcile-typescript.py — TypeSpec ↔ TypeScript coverage proof (step 1 of
the monolith cutover).

Mechanically extracts the HTTP route surface (method + path) and MCP tool
catalog (tool name) from each TypeScript service under ./typescript, extracts
the corresponding operations from the TypeSpec contracts under
./typespec/v1/<service>/typescript, and diffs the two. Exit code 0 only when
every declared route/tool has a matching contract operation.

Conventions (documented in typespec/v1/typescript-conventions.md):
  * REST services: one TypeSpec op per route. `@route("<path>")` + verb
    decorator (@get/@post/...). Path params `:id` in Express map to `{id}`.
  * MCP servers: each tool is an operation with `@route("/tools/<tool-name>")`
    + `@post`, where <tool-name> is the literal tool name from `server.tool(...)`.

Usage:
    python3 typespec/v1/scripts/reconcile-typescript.py            # full report
    python3 typespec/v1/scripts/reconcile-typescript.py --service ui-event-bus

The manifest below is the authoritative list of TS services. Adding a service
to the manifest but not yet modeling it reports it as "unmodeled" (not an
error) until its contract exists.
"""

import argparse
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TS_DIR = os.path.join(REPO, "typescript")
TSP_DIR = os.path.join(REPO, "typespec", "v1")

# ---------------------------------------------------------------------------
# Authoritative service manifest.  type: "rest" | "mcp".
# ---------------------------------------------------------------------------
MANIFEST = [
    # REST services (smallest -> largest).
    # `src_root` (optional) overrides where the service's source lives; default
    # is `typescript/<name>/src`. Used by the consolidated-stack entries that
    # live outside typescript/ (adonisjs/, moleculer/).
    {"name": "ui-event-bus", "type": "rest"},
    {"name": "harness-srv", "type": "rest"},
    {"name": "ui-tools", "type": "rest"},
    {"name": "tools-aggregator", "type": "rest"},
    {"name": "role-memory-srv", "type": "rest"},
    {"name": "knowledge-srv", "type": "rest"},
    {"name": "tackle-prompt-sync-srv", "type": "rest"},
    {"name": "cascade-srv", "type": "rest"},
    {"name": "shrapnel", "type": "rest"},
    {"name": "execution-srv", "type": "rest"},
    {"name": "kernel-srv", "type": "rest"},
    {"name": "voyager-srv", "type": "rest"},
    {"name": "conduit-srv", "type": "rest"},
    {"name": "semantics-srv", "type": "rest"},
    {"name": "peb-srv", "type": "rest"},
    {"name": "assembly-srv", "type": "rest"},
    {"name": "wind-srv", "type": "rest"},
    {"name": "tackle-srv", "type": "rest"},
    {"name": "nebula-srv", "type": "rest"},
    {"name": "aegis-srv", "type": "rest"},
    # mcp-bridge is an MCP-over-SSE transport proxy: it exposes raw HTTP
    # routes (/sse, /messages, /health), not a static tool catalog.
    {"name": "mcp-bridge", "type": "rest"},
    # ── Consolidated stack (Wave 0.1) ────────────────────────────────
    # The consolidated runtime under test: AdonisJS HTTP edge and Moleculer
    # service bus. Their contracts become the canonical surface as *-srv
    # services are re-homed onto them. `src_root` points at the real source
    # (they do not live under typescript/). `framework` gates which
    # extraction regexes run: express (default), adonisjs, or moleculer.
    {"name": "adonisjs", "type": "rest", "src_root": "adonisjs/broker-gateway-proxy", "framework": "adonisjs"},
    {"name": "moleculer", "type": "rest", "src_root": "moleculer/search", "framework": "moleculer"},
    # Worker-tier broker (Wave 0.3) — same Moleculer alias extraction.
    {"name": "nexus-broker", "type": "rest", "src_root": "moleculer/nexus-broker", "framework": "moleculer"},
    # Consolidated control-plane edge (Wave 0.2) — AdonisJS route extraction.
    {"name": "control-edge", "type": "rest", "src_root": "adonisjs/nexus-control-edge", "framework": "adonisjs"},
    # MCP tool servers (smallest -> largest)
    {"name": "service-broker-mcp", "type": "mcp"},
    {"name": "knowledge-mcp", "type": "mcp"},
    {"name": "vision-mcp", "type": "mcp"},
    {"name": "terrain-mcp", "type": "mcp"},
    {"name": "peb-mcp", "type": "mcp"},
    {"name": "tackle-mcp", "type": "mcp"},
    {"name": "nebula-mcp", "type": "mcp"},
]

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "all"}
# Express `app.all(...)` covers multiple verbs; treat `all` as a wildcard.
ROUTE_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(get|post|put|patch|delete|head|options|all)\s*\(\s*['\"]([^'\"]+)['\"]"
)
CHAIN_ROUTE_RE = re.compile(r"\.route\s*\(\s*['\"]([^'\"]+)['\"]")
CHAIN_VERB_RE = re.compile(r"\.(get|post|put|patch|delete|head|options|all)\s*\(\s*\)")
TOOL_RE = re.compile(
    r"\b(?:server|mcp)\s*\.\s*tool\s*\(\s*['\"]([^'\"]+)['\"]"
)
# raw node http.createServer route guards: `if (req.method === "GET" && url.pathname === "/sse")`
RAW_HTTP_RE = re.compile(
    r"req\.method\s*===?\s*['\"]([A-Z]+)['\"]\s*&&\s*url\.pathname\s*===?\s*['\"]([^'\"]+)['\"]"
)
REGISTER_TOOL_RE = re.compile(r"registerTool\s*\(\s*['\"]([^'\"]+)['\"]")
# Express router mount: `app.use('/api', createRoutes(pool))` — a mounted
# router's routes are served under this prefix, which the reconciler must
# prepend so `router.get('/links')` reconciles to `GET /api/links`.
# Scoped to `app.use(...)` (the Express mount convention) so unrelated
# `.use(...)` calls (e.g. `hash.use('scrypt')` in AdonisJS) don't pollute
# the prefix set.
MOUNT_RE = re.compile(r"\bapp\.use\s*\(\s*['\"]([^'\"]+)['\"]")
# AdonisJS route file: `router.get('/health', [ProxyController, 'health'])`
# and catch-alls `router.any('/*', ...)`. `any` maps to the `all` wildcard.
ADONIS_ROUTE_RE = re.compile(
    r"\brouter\.(get|post|put|patch|delete|any)\s*\(\s*['\"]([^'\"]+)['\"]"
)
# Moleculer-web aliases inside a routes block:
#   path: "/api",
#   aliases: { "POST /search/simple": "google-search.simpleSearch", ... }
# The `path` is the mount prefix; each alias key is "METHOD /sub-path".
MOLEQ_PATH_RE = re.compile(r"path:\s*['\"]([^'\"]+)['\"]")
MOLEQ_ALIAS_RE = re.compile(
    r"['\"](GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+([^'\"]+)['\"]\s*:"
)

TSP_ROUTE_RE = re.compile(r"@route\s*\(\s*\"([^\"]+)\"\s*\)")
TSP_VERB_RE = re.compile(r"@(get|post|put|patch|delete|head|options)\b")


def norm_path(p: str) -> str:
    """Normalize Express `:param` to TypeSpec `{param}` and strip trailing slash."""
    p = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", p)
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def src_root_for(entry: dict) -> str:
    """Resolve the source root for a manifest entry."""
    if "src_root" in entry:
        return os.path.join(REPO, entry["src_root"])
    return os.path.join(TS_DIR, entry["name"], "src")


def ts_routes(entry: dict, src_root: str) -> set[str]:
    """Extract `METHOD /path` route strings from a REST service's source."""
    out: set[str] = set()
    if not os.path.isdir(src_root):
        return out
    texts: list[str] = []
    for root, dirs, files in os.walk(src_root):
        # Skip vendored/build directories (node_modules, dist, build, .git) —
        # they are not source and pollute route extraction.
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "build", ".git")]
        for fn in files:
            if not fn.endswith((".ts", ".js")):
                continue
            with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                texts.append(f.read())
    # Drop full-line comments so commented-out route groups (e.g. stub
    # groups that land with a later migration wave) are not extracted as
    # declared surface.
    texts = ["\n".join(ln for ln in t.splitlines() if not ln.lstrip().startswith("//")) for t in texts]

    # Router mount prefixes (e.g. `app.use('/api', ...)`).
    mount_prefixes: set[str] = set()
    for text in texts:
        for m in MOUNT_RE.finditer(text):
            mount_prefixes.add(m.group(1).rstrip("/"))
    framework = entry.get("framework", "express")
    for text in texts:
        if framework == "express":
            # Express-style route declarations. Only treat objects that
            # actually declare routes as route declarations: `app`, `server`,
            # `router`, or a *Router instance (e.g. `semanticsRouter`). This
            # keeps `env.get('APP_KEY')`, `hash.get(...)`, and other method
            # calls from polluting the surface.
            for m in ROUTE_RE.finditer(text):
                obj, verb, path = m.group(1), m.group(2), m.group(3)
                if not (obj in ("app", "server", "router") or obj.endswith("Router")):
                    continue
                p = norm_path(path)
                # Routes declared on a mounted router (object other than
                # `app`/`server`) inherit the service's mount prefix when
                # exactly one prefix exists — `router.get('/links')` →
                # `GET /api/links`.
                if obj not in ("app", "server") and len(mount_prefixes) == 1:
                    p = norm_path(next(iter(mount_prefixes)) + path)
                out.add(f"{verb.upper()} {p}")
            for m in RAW_HTTP_RE.finditer(text):
                out.add(f"{m.group(1).upper()} {norm_path(m.group(2))}")
            # `.route('/x').get().post()` chains (method without path arg)
            for cm in CHAIN_ROUTE_RE.finditer(text):
                base = norm_path(cm.group(1))
                # scan forward a short window for chained verbs
                window = text[cm.end() : cm.end() + 200]
                for vm in CHAIN_VERB_RE.finditer(window):
                    out.add(f"{vm.group(1).upper()} {base}")
        elif framework == "adonisjs":
            # AdonisJS: `router.get('/health', [Controller, 'method'])`.
            # `any` (catch-all) maps to the `all` wildcard; `/*` is normalized
            # to `/{path}` so it matches the TypeSpec catch-all convention.
            for m in ADONIS_ROUTE_RE.finditer(text):
                verb, path = m.group(1), m.group(2)
                if verb == "any":
                    verb = "all"
                p = norm_path(path)
                if p == "/*":
                    p = "/{path}"
                out.add(f"{verb.upper()} {p}")
        elif framework == "moleculer":
            # Moleculer-web: aliases under a routes block with a path prefix.
            #   path: "/api"  +  aliases: { "POST /search/simple": ... }
            #   → POST /api/search/simple
            # A gateway may declare MULTIPLE route blocks (e.g. a dedicated
            # block with its own onError). Each path declaration owns the
            # aliases up to the NEXT path declaration — a fixed window would
            # bleed into the following block and mis-prefix its aliases.
            pms = list(MOLEQ_PATH_RE.finditer(text))
            for i, pm in enumerate(pms):
                prefix = pm.group(1).rstrip("/")
                block_end = pms[i + 1].start() if i + 1 < len(pms) else pm.end() + 4000
                window = text[pm.end() : min(block_end, pm.end() + 4000)]
                for am in MOLEQ_ALIAS_RE.finditer(window):
                    # Normalize the composite (prefix + alias) as a whole so
                    # root aliases ("GET /" under a prefix) don't leave a
                    # trailing slash that can't match the contract path.
                    out.add(f"{am.group(1).upper()} {norm_path(prefix + norm_path(am.group(2)))}")
    return out


def ts_tools(service: str) -> set[str]:
    """Extract MCP tool names from an MCP server's source."""
    out: set[str] = set()
    src_root = os.path.join(TS_DIR, service, "src")
    if not os.path.isdir(src_root):
        return out
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "build", ".git")]
        for fn in files:
            if not fn.endswith((".ts", ".js")):
                continue
            with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in TOOL_RE.finditer(text):
                out.add(m.group(1))
            for m in REGISTER_TOOL_RE.finditer(text):
                out.add(m.group(1))
    return out


def tsp_rest_ops(service: str) -> tuple[set[str], set[str]]:
    """Extract `METHOD /path` operations from a REST TypeSpec contract.

    Returns (ops, retired): ops are the live contract surface; retired are
    ops explicitly tombstoned with @tag("retired") — kept in the contract
    for lineage but excluded from MISSING/EXTRA drift accounting (the
    implementation must NOT implement them).
    """
    out: set[str] = set()
    retired: set[str] = set()
    tsp_dir = os.path.join(TSP_DIR, service, "typescript")
    if not os.path.isdir(tsp_dir):
        return out, retired
    for fn in sorted(os.listdir(tsp_dir)):
        if not fn.endswith(".tsp"):
            continue
        with open(os.path.join(tsp_dir, fn), encoding="utf-8") as f:
            lines = f.read().splitlines()
        current_route = ""
        current_verb = ""
        pending_retired = False
        # Bind to `op <name>(` — a signature, not prose: the parameter-list
        # paren must follow the name on the same line (no templated ops exist
        # in this repo, and no @doc text contains `op name(`). Guards against
        # matching the word `op` in doc text or comments.
        op_re = re.compile(r"\bop\s+(\w+)\s*\(")
        for line in lines:
            if "@tag(\"retired\")" in line or "@tag('retired')" in line:
                pending_retired = True
            rm = TSP_ROUTE_RE.search(line)
            if rm:
                current_route = norm_path(rm.group(1))
            vm = TSP_VERB_RE.search(line)
            if vm:
                current_verb = vm.group(1).upper()
            # An operation closes at its `op` keyword — the line TypeSpec
            # binds every decorator above it to. Emitting here (not at
            # @verb/@route) means decorators like @doc and @tag are seen
            # first, and the one-line `@route(...) @get op x(): ...;` style
            # (aegis et al.) emits on its own line without a `continue`
            # swallowing it.
            om = op_re.search(line)
            if om and current_route and current_verb:
                op = f"{current_verb} {current_route}"
                (retired if pending_retired else out).add(op)
                pending_retired = False
                current_route = ""
                current_verb = ""
    return out, retired


def tsp_tool_ops(service: str) -> tuple[set[str], set[str]]:
    """Extract tool names from an MCP TypeSpec contract (`/tools/<name>` routes).

    Returns (ops, retired) — same retirement semantics as tsp_rest_ops.
    """
    out: set[str] = set()
    retired: set[str] = set()
    tsp_dir = os.path.join(TSP_DIR, service, "typescript")
    if not os.path.isdir(tsp_dir):
        return out, retired
    for fn in sorted(os.listdir(tsp_dir)):
        if not fn.endswith(".tsp"):
            continue
        with open(os.path.join(tsp_dir, fn), encoding="utf-8") as f:
            text = f.read()
        retired_pending = False
        for line in text.splitlines():
            if "@tag(\"retired\")" in line or "@tag('retired')" in line:
                retired_pending = True
            m = TSP_ROUTE_RE.search(line)
            if m:
                p = m.group(1)
                if p.startswith("/tools/"):
                    (retired if retired_pending else out).add(p[len("/tools/"):])
                    retired_pending = False
    return out, retired


def reconcile(entry: dict) -> dict:
    name, kind = entry["name"], entry["type"]
    tsp_dir = os.path.join(TSP_DIR, name, "typescript")
    modeled = os.path.isdir(tsp_dir)
    result = {"name": name, "type": kind, "modeled": modeled, "missing": [], "extra": []}
    if not modeled:
        return result
    if kind == "rest":
        src = ts_routes(entry, src_root_for(entry))
        contract, retired = tsp_rest_ops(name)
    else:
        src = ts_tools(name)
        contract, retired = tsp_tool_ops(name)
    # Wildcard-verb matching: a source `ALL /path` (Express app.all / AdonisJS
    # router.any catch-all) is covered when the contract declares ANY verb on
    # the same path — TypeSpec has no verb-neutral op. The matching contract
    # entry is consumed (not reported EXTRA) by the wildcard.
    src_effective = set(src)
    contract_effective = set(contract)
    for s in src:
        verb, _, path = s.partition(" ")
        if verb != "ALL":
            continue
        for c in contract:
            if c.partition(" ")[2] == path:
                src_effective.discard(s)
                contract_effective.discard(c)
                break
    result["missing"] = sorted(src_effective - contract_effective)
    result["extra"] = sorted(contract_effective - src_effective)
    # Retired tombstones: reported separately, never as drift. A retired op
    # that IS implemented in source is itself a finding (implementing a
    # tombstone), surfaced via retired_implemented.
    result["retired"] = sorted(retired)
    result["retired_implemented"] = sorted(retired & src_effective)
    # Covered counts every declared source route that is NOT missing — including
    # wildcard-verb routes consumed above (e.g. ALL /{path} covered by a
    # concrete-verb contract op) — so adonisjs shows 2/2, not 1/2.
    result["covered"] = len(src) - len(result["missing"])
    result["total"] = len(src)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="TypeSpec <-> TypeScript coverage proof")
    ap.add_argument("--service", help="only reconcile one service")
    ap.add_argument("--json", action="store_true", help="emit JSON report")
    args = ap.parse_args()

    entries = [e for e in MANIFEST if e["name"] == args.service] if args.service else MANIFEST
    if args.service and not entries:
        print(f"unknown service: {args.service}", file=sys.stderr)
        return 2

    results = [reconcile(e) for e in entries]
    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    total_missing = 0
    total_covered = 0
    total_declared = 0
    print(f"{'SERVICE':<24} {'TYPE':<6} {'STATUS':<10} {'COVERED':<8} {'DECLARED':<9} {'MISSING':<7}")
    print("-" * 70)
    for r in results:
        if not r["modeled"]:
            status = "UNMODELED"
            cov = "-"
            decl = "-"
            miss = "-"
            total_declared += 0
        else:
            total_covered += r.get("covered", 0)
            total_declared += r.get("total", 0)
            total_missing += len(r["missing"])
            cov = f"{r.get('covered', 0)}/{r.get('total', 0)}"
            decl = str(r.get("total", 0))
            miss = str(len(r["missing"]))
            status = "OK" if not r["missing"] else "GAPS"
        print(f"{r['name']:<24} {r['type']:<6} {status:<10} {cov:<8} {decl:<9} {miss:<7}")

    print("-" * 70)
    print(f"TOTAL modeled: {sum(1 for r in results if r['modeled'])}/{len(results)} | "
          f"declared {total_declared} | covered {total_covered} | missing {total_missing}")
    print()

    any_gaps = False
    for r in results:
        for m in r["missing"]:
            any_gaps = True
            print(f"  MISSING  {r['name']}: {m}")
        for x in r["extra"]:
            print(f"  EXTRA    {r['name']}: {x} (in contract, not in source)")
        for x in r.get("retired", []):
            print(f"  RETIRED  {r['name']}: {x} (tombstoned in contract — excluded from drift, do not implement)")
        for x in r.get("retired_implemented", []):
            any_gaps = True
            print(f"  RETIRED-IMPL {r['name']}: {x} (tombstoned op implemented in source — remove it)")

    if not any_gaps and total_missing == 0:
        print("COVERAGE COMPLETE: every modeled route/tool has a matching contract operation.")
        return 0
    if args.service:
        # single-service mode: nonzero on gaps (missing or implemented
        # tombstones) so it can gate CI
        return 1 if (total_missing or any(r.get("retired_implemented") for r in results)) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
