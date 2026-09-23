#!/usr/bin/env python3
"""
reconcile-python.py — TypeSpec ↔ Python coverage proof (reverse-engineering
step for the ./python fleet, parallel to reconcile-typescript.py).

Mechanically extracts the HTTP route surface (method + path) and MCP tool
catalog (tool name) from each Python service under ./python, extracts the
corresponding operations from the TypeSpec contracts under
./typespec/v1/<service>/python, and diffs the two. Exit code 0 only when
every declared route/tool has a matching contract operation.

Conventions (documented in typespec/v1/python-conventions.md):
  * REST services: one TypeSpec op per route. `@route("<path>")` + verb
    decorator (@get/@post/...). FastAPI `{id}` maps to TypeSpec `{id}`.
  * MCP servers: each tool is an operation with `@route("/tools/<tool-name>")`
    + `@post`, where <tool-name> is the decorated function name following
    `@mcp.tool()`.

Usage:
    python3 typespec/v1/scripts/reconcile-python.py            # full report
    python3 typespec/v1/scripts/reconcile-python.py --service timeclock

The manifest below is the authoritative list of Python services. A service in
the manifest with no `typespec/v1/<service>/python/` directory is reported as
"unmodeled" (not an error) until its contract exists.

Exit codes:
    0  every modeled service's routes/tools are fully covered (no missing,
       no extra, non-empty source scan) — per python-conventions.md
    1  any coverage gap: MISSING routes/tools, EXTRA contract-only
       routes/tools, or a NO-SOURCE scan (source scan found nothing while a
       contract exists — stale src_root or moved service; the proof is
       vacuous and must not read as OK)
    2  usage error (unknown --service)
"""

import argparse
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PY_DIR = os.path.join(REPO, "python")
TSP_DIR = os.path.join(REPO, "typespec", "v1")

# ---------------------------------------------------------------------------
# Authoritative service manifest.  type: "rest" | "mcp".
# `src_root` is relative to the repo; `framework` gates which extraction runs:
# fastapi (default), httpserver (http.server), or mcp.
# ---------------------------------------------------------------------------
MANIFEST = [
    {"name": "conduit-kernel", "type": "rest", "src_root": "python/conduit/app", "framework": "fastapi"},
    {"name": "fs-crawler", "type": "rest", "src_root": "python/fs/fs-crawler", "framework": "fastapi"},
    {"name": "fs-crawler-adapter", "type": "rest", "src_root": "python/fs/fs-crawler-adapter", "framework": "fastapi"},
    {"name": "losm-host", "type": "rest", "src_root": "python/vision/losm-host", "framework": "fastapi"},
    {"name": "vision-srv", "type": "rest", "src_root": "python/vision-srv", "framework": "fastapi"},
    {"name": "timeclock", "type": "rest", "src_root": "python/timeclock", "framework": "fastapi"},
    {"name": "substance", "type": "rest", "src_root": "python/substance", "framework": "fastapi"},
    {"name": "peb-kernel", "type": "rest", "src_root": "python/peb-kernel", "framework": "fastapi"},
    {"name": "address-tts", "type": "rest", "src_root": "python/address/tts", "framework": "httpserver"},
    {"name": "operator-svc", "type": "rest", "src_root": "python/operator_svc", "framework": "httpserver"},
    {"name": "tackle-mcp", "type": "mcp", "src_root": "python/tackle", "framework": "mcp"},
    {"name": "rover", "type": "mcp", "src_root": "python/rover", "framework": "mcp"},
]

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}

# FastAPI: `@app.get("/x")` / `@router.get("/x")` (path may be on the next line)
FASTAPI_ROUTE_RE = re.compile(r"@(\w+)\.(get|post|put|patch|delete|head|options)\s*\(")
# Router own prefix: `router = APIRouter(prefix="/artifacts", ...)`
APIRouter_RE = re.compile(r"(\w+)\s*=\s*APIRouter\s*\(")
PREFIX_RE = re.compile(r"prefix\s*=\s*['\"]([^'\"]*)['\"]")
# Mount: `app.include_router(delta_router, prefix="/delta")`
INCLUDE_ROUTER_RE = re.compile(r"\w+\.include_router\(\s*([\w.]+)")
# Imports: `from .api.routes_delta import router as delta_router` / `import x`
FROM_IMPORT_RE = re.compile(r"^[ \t]*from\s+([\w.]+)\s+import\s+(.+)$", re.MULTILINE)
IMPORT_RE = re.compile(r"^[ \t]*import\s+([\w.]+)(?:\s+as\s+(\w+))?", re.MULTILINE)

# http.server: `path == "/x"` / `path.startswith("/x/")` inside do_GET/do_POST
DO_METHOD_RE = re.compile(r"def\s+do_(\w+)\s*\(")
HTTPSRV_EQ_RE = re.compile(r"(?:parsed\.path|\bpath)\s*==\s*['\"]([^'\"]+)['\"]")
HTTPSRV_SW_RE = re.compile(r"(?:parsed\.path|\bpath)\.startswith\(\s*['\"]([^'\"]+)['\"]")

# MCP: `@mcp.tool()` + the decorated `def name(...)`
MCP_TOOL_RE = re.compile(r"@mcp\.tool\s*\(\)")
MCP_TOOL_NAMED_RE = re.compile(r"\bmcp\.tool\(\s*['\"]([^'\"]+)['\"]")

TSP_ROUTE_RE = re.compile(r"@route\s*\(\s*\"([^\"]+)\"\s*\)")
TSP_VERB_RE = re.compile(r"@(get|post|put|patch|delete|head|options)\b")

SKIP_DIRS = {"venv", ".venv", "__pycache__", ".pytest_cache", ".embedding_cache",
             ".idea", "node_modules", "dist", "build", ".git", "generated",
             ".ruff_cache", "tests", "test", "__pycache__"}


def norm_path(p: str) -> str:
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def src_root_for(entry: dict) -> str:
    return os.path.join(REPO, entry["src_root"])


def _iter_py_files(src_root: str):
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _module_path(relpath: str) -> str:
    """'app/api/routes.py' -> 'app.api.routes'."""
    p = relpath.replace(os.sep, ".")
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith(".__init__"):
        p = p[: -len(".__init__")]
    return p


# ---------------------------------------------------------------------------
# FastAPI extraction (cross-file prefix resolution)
# ---------------------------------------------------------------------------

def fastapi_routes(src_root: str) -> set[str]:
    if not os.path.isdir(src_root):
        return set()

    files = []  # (relpath, text)
    for full in _iter_py_files(src_root):
        rel = os.path.relpath(full, src_root)
        with open(full, encoding="utf-8", errors="replace") as f:
            files.append((rel, f.read()))

    # module path -> relpath index
    mod_index = {_module_path(rel): rel for rel, _ in files}

    # resolve a dotted module name to a file relpath (best-effort). Handles
    # relative imports (`.api.routes`) and absolute imports that include the
    # package root (`app.api.routes` when the src_root sits above/at `app`).
    def resolve_module(importing_rel: str, dotted: str) -> str | None:
        if dotted.startswith("."):
            # relative import: resolve against the importer's package
            dots = len(dotted) - len(dotted.lstrip("."))
            base = dotted[dots:]
            importer_dir = os.path.dirname(importing_rel)
            parts = importer_dir.split(os.sep) if importer_dir else []
            up = dots - 1
            keep = parts[: max(0, len(parts) - up)]
            full = ".".join(keep + ([base] if base else []))
            return mod_index.get(full)
        # absolute import: try exact, then progressively strip leading
        # components so `app.api.routes` matches a file indexed as
        # `api.routes` (and vice versa).
        parts = dotted.split(".")
        for i in range(len(parts)):
            cand = ".".join(parts[i:])
            if cand in mod_index:
                return mod_index[cand]
        return None

    # 1) router own prefixes: (rel, name) -> prefix
    own: dict[tuple[str, str], str] = {}
    # 2) import aliases: (rel, localname) -> (def_rel, imported_name)
    alias: dict[tuple[str, str], tuple[str, str]] = {}
    for rel, text in files:
        for m in APIRouter_RE.finditer(text):
            name = m.group(1)
            window = text[m.end(): m.end() + 200]
            pm = PREFIX_RE.search(window)
            own[(rel, name)] = pm.group(1) if pm else ""
        for m in FROM_IMPORT_RE.finditer(text):
            mod, names = m.group(1), m.group(2)
            for clause in names.split(","):
                clause = clause.strip()
                if not clause or clause.startswith("#"):
                    continue
                # "router as delta_router" | "router"
                parts = clause.split(" as ")
                imported = parts[0].strip()
                local = parts[1].strip() if len(parts) == 2 else imported
                if imported == "*":
                    continue
                target = resolve_module(rel, mod)
                if target:
                    alias[(rel, local)] = (target, imported)

    # 3) include mounts: (def_rel, def_name) -> mount_prefix
    mount: dict[tuple[str, str], str] = {}
    for rel, text in files:
        for m in INCLUDE_ROUTER_RE.finditer(text):
            arg = m.group(1)
            window = text[m.end(): m.end() + 200]
            pm = PREFIX_RE.search(window)
            mprefix = pm.group(1) if pm else ""
            # resolve arg (e.g. "delta_router" or "segment_sets.router") to (def_rel, def_name)
            target = None
            if "." in arg:
                # module.attr form: "segment_sets.router"
                modpart, _, attr = arg.partition(".")
                # resolve modulepart via import alias in this file
                if (rel, modpart) in alias:
                    def_rel, _ = alias[(rel, modpart)]
                    target = (def_rel, attr)
                else:
                    # modulepart may be a module imported directly
                    tgt = resolve_module(rel, modpart)
                    if tgt:
                        target = (tgt, attr)
            else:
                if (rel, arg) in alias:
                    target = alias[(rel, arg)]
                elif (rel, arg) in own:
                    target = (rel, arg)
            if target:
                mount.setdefault(target, mprefix)

    # 4) extract routes
    out: set[str] = set()
    for rel, text in files:
        for m in FASTAPI_ROUTE_RE.finditer(text):
            obj, verb = m.group(1), m.group(2)
            window = text[m.end(): m.end() + 400]
            # first string literal is the path
            pm = re.search(r"['\"]([^'\"]*)['\"]", window)
            path = pm.group(1) if pm else ""
            if obj == "app":
                full = path
            else:
                # resolve obj to (def_rel, def_name)
                if (rel, obj) in alias:
                    def_rel, def_name = alias[(rel, obj)]
                elif (rel, obj) in own:
                    def_rel, def_name = rel, obj
                else:
                    # unknown object — treat as local router with no prefix
                    def_rel, def_name = rel, obj
                p = (mount.get((def_rel, def_name), "") + own.get((def_rel, def_name), "") + path)
                full = p
            out.add(f"{verb.upper()} {norm_path(full)}")
    return out


# ---------------------------------------------------------------------------
# http.server extraction
# ---------------------------------------------------------------------------

def httpserver_routes(src_root: str) -> set[str]:
    out: set[str] = set()
    if not os.path.isdir(src_root):
        return out
    for full in _iter_py_files(src_root):
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read()
        # ordered list of (pos, verb) for do_GET/do_POST/...
        methods = [(m.start(), m.group(1).upper()) for m in DO_METHOD_RE.finditer(text)]
        methods.sort()
        for eq in HTTPSRV_EQ_RE.finditer(text):
            verb = "GET"
            for pos, v in methods:
                if pos < eq.start():
                    verb = v
                else:
                    break
            out.add(f"{verb} {norm_path(eq.group(1))}")
        for sw in HTTPSRV_SW_RE.finditer(text):
            verb = "GET"
            for pos, v in methods:
                if pos < sw.start():
                    verb = v
                else:
                    break
            prefix = sw.group(1).rstrip("/")
            out.add(f"{verb} {norm_path(prefix + '/{param}')}")
    return out


# ---------------------------------------------------------------------------
# MCP extraction
# ---------------------------------------------------------------------------

def mcp_tools(src_root: str) -> set[str]:
    out: set[str] = set()
    if not os.path.isdir(src_root):
        return out
    for full in _iter_py_files(src_root):
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for m in MCP_TOOL_RE.finditer(text):
            window = text[m.end(): m.end() + 200]
            dm = re.search(r"(?:async\s+def|def)\s+(\w+)\s*\(", window)
            if dm:
                out.add(dm.group(1))
        for m in MCP_TOOL_NAMED_RE.finditer(text):
            out.add(m.group(1))
    return out


def py_routes(entry: dict) -> set[str]:
    src_root = src_root_for(entry)
    framework = entry.get("framework", "fastapi")
    if framework == "fastapi":
        return fastapi_routes(src_root)
    if framework == "httpserver":
        return httpserver_routes(src_root)
    if framework == "mcp":
        return mcp_tools(src_root)
    return set()


# ---------------------------------------------------------------------------
# TypeSpec contract extraction
# ---------------------------------------------------------------------------

def tsp_rest_ops(service: str) -> set[str]:
    out: set[str] = set()
    tsp_dir = os.path.join(TSP_DIR, service, "python")
    if not os.path.isdir(tsp_dir):
        return out
    for fn in sorted(os.listdir(tsp_dir)):
        if not fn.endswith(".tsp"):
            continue
        with open(os.path.join(tsp_dir, fn), encoding="utf-8") as f:
            lines = f.read().splitlines()
        current_route = ""
        for line in lines:
            rm = TSP_ROUTE_RE.search(line)
            if rm:
                current_route = norm_path(rm.group(1))
                vm = TSP_VERB_RE.search(line)
                if vm:
                    out.add(f"{vm.group(1).upper()} {current_route}")
                continue
            vm = TSP_VERB_RE.search(line)
            if vm and current_route:
                out.add(f"{vm.group(1).upper()} {current_route}")
    return out


def tsp_tool_ops(service: str) -> set[str]:
    out: set[str] = set()
    tsp_dir = os.path.join(TSP_DIR, service, "python")
    if not os.path.isdir(tsp_dir):
        return out
    for fn in sorted(os.listdir(tsp_dir)):
        if not fn.endswith(".tsp"):
            continue
        with open(os.path.join(tsp_dir, fn), encoding="utf-8") as f:
            text = f.read()
        for m in TSP_ROUTE_RE.finditer(text):
            p = m.group(1)
            if p.startswith("/tools/"):
                out.add(p[len("/tools/"):])
    return out


def reconcile(entry: dict) -> dict:
    name, kind = entry["name"], entry["type"]
    tsp_dir = os.path.join(TSP_DIR, name, "python")
    modeled = os.path.isdir(tsp_dir)
    result = {"name": name, "type": kind, "modeled": modeled, "missing": [], "extra": [],
              "no_source": False}
    if not modeled:
        return result
    if kind == "rest":
        src = py_routes(entry)
        contract = tsp_rest_ops(name)
    else:
        src = mcp_tools(src_root_for(entry))
        contract = tsp_tool_ops(name)
    # Path-param names are a modeling concern, not a coverage concern:
    # normalize `{...}` → `{}` on both sides so synthetic source names
    # (http.server startswith → `{param}`) reconcile with the documented
    # contract names (`{name}`, `{id}`, `{service}`).
    canon = lambda s: re.sub(r"\{[^}]+\}", "{}", s)
    src = {canon(s) for s in src}
    contract = {canon(c) for c in contract}
    result["missing"] = sorted(src - contract)
    result["extra"] = sorted(contract - src)
    result["covered"] = len(src) - len(result["missing"])
    result["total"] = len(src)
    # A modeled service whose source scan finds NOTHING while its contract
    # declares operations is not "OK 0/0" — the scan is vacuous (stale
    # src_root, moved service, or scanner gap) and must be surfaced as its
    # own status, never silently OK.
    result["no_source"] = len(src) == 0 and len(contract) > 0
    return result


def status_of(r: dict) -> str:
    """Honest summary status — must agree with the detail section.

    OK        no missing, no extra, source scan non-empty
    GAPS      missing and/or extra (extras are coverage gaps too: the
              contract declares surface the source does not have)
    NO-SOURCE modeled, contract non-empty, but the source scan found 0
              routes/tools — the comparison proved nothing
    UNMODELED no contract dir (documented as not-an-error)
    """
    if not r.get("modeled"):
        return "UNMODELED"
    if r.get("no_source"):
        return "NO-SOURCE"
    if r["missing"] or r["extra"]:
        return "GAPS"
    return "OK"


def exit_code_for(results: list[dict]) -> int:
    """0 only when every modeled service is honestly OK; 1 on any gap."""
    for r in results:
        if status_of(r) in ("GAPS", "NO-SOURCE"):
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TypeSpec <-> Python coverage proof")
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
        return exit_code_for(results)

    total_missing = 0
    total_extra = 0
    total_covered = 0
    total_declared = 0
    print(f"{'SERVICE':<22} {'TYPE':<6} {'STATUS':<10} {'COVERED':<8} {'DECLARED':<9} {'MISSING':<7} {'EXTRA':<6}")
    print("-" * 78)
    for r in results:
        if not r["modeled"]:
            cov = decl = miss = extra = "-"
        else:
            total_covered += r.get("covered", 0)
            total_declared += r.get("total", 0)
            total_missing += len(r["missing"])
            total_extra += len(r["extra"])
            cov = f"{r.get('covered', 0)}/{r.get('total', 0)}"
            decl = str(r.get("total", 0))
            miss = str(len(r["missing"]))
            extra = str(len(r["extra"]))
        status = status_of(r)
        print(f"{r['name']:<22} {r['type']:<6} {status:<10} {cov:<8} {decl:<9} {miss:<7} {extra:<6}")

    print("-" * 78)
    print(f"TOTAL modeled: {sum(1 for r in results if r['modeled'])}/{len(results)} | "
          f"declared {total_declared} | covered {total_covered} | missing {total_missing} | extra {total_extra}")
    print()

    for r in results:
        if r.get("no_source"):
            print(f"  NO-SOURCE {r['name']}: source scan found 0 routes/tools while the "
                  f"contract declares {len(r['extra'])} — check src_root (stale manifest "
                  f"entry: {next(e['src_root'] for e in MANIFEST if e['name'] == r['name'])})")
        for m in r["missing"]:
            print(f"  MISSING  {r['name']}: {m}")
        for x in r["extra"]:
            print(f"  EXTRA    {r['name']}: {x} (in contract, not in source)")

    code = exit_code_for(results)
    if code == 0:
        print("COVERAGE COMPLETE: every modeled route/tool has a matching contract operation.")
    else:
        print("COVERAGE GAPS DETECTED: see MISSING / EXTRA / NO-SOURCE above.")
    return code


if __name__ == "__main__":
    sys.exit(main())
