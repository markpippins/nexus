#!/usr/bin/env python3
"""Extract resolved REST endpoint inventories from every *-srv (final).

Handles:
  - Express (TS/JS): app/router `.get/.post/.put/.patch/.delete` with quoted
    paths; `app.use('/prefix', target)` mounts resolved through module imports:
      * default-import routers  (import X from './routes/x.js'  -> export default router)
      * named router exports    (import { X } from ...           -> export const X = Router())
      * factory mounts          (app.use('/api', createRoutes(p)) -> export function createRoutes + const router = Router())
  - FastAPI (Python): `@app.get("/path")` / `@router.get("/path")` decorators.
  - `//` comment lines above a route become the endpoint description.

Output: /tmp/api_inventory.json — per service, list of
{method, path, summary}. Skips node_modules/dist/tests/scripts.

Usage:
    python tools/api-docs/extract_routes.py [--root /home/codex/dev/nexus]
"""
import argparse
import json
import os
import re

ROOT = "/home/codex/dev/nexus"

VERB_RE = re.compile(r"\.(get|post|put|patch|delete)\s*\(\s*['\"`]")
USE_RE = re.compile(r"\.use\s*\(\s*['\"`]([^'\"`]+)['\"`]\s*,\s*([A-Za-z_$][\w$]*(?:\s*\([^)]*\))?)")
ROUTER_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:express\.)?Router\s*\(")
IMPORT_RE = re.compile(
    r"^\s*import\s+(?:(?P<default>[A-Za-z_$][\w$]*)\s*(?:,\s*)?)?"
    r"(?:\{(?P<named>[^}]+)\})?\s*from\s*['\"](?P<mod>[^'\"]+)['\"]"
)
DEFAULT_EXPORT_RE = re.compile(r"^\s*export\s+default\s+([A-Za-z_$][\w$]*)")
FASTAPI_RE = re.compile(r"@(app|router)\.(get|post|put|patch|delete|options)\s*\(\s*['\"]([^'\"]+)")

# Spring (Java): class-level @RequestMapping("/base") + method-level
# @GetMapping/@PostMapping/@PutMapping/@PatchMapping/@DeleteMapping("/path").
# Single-line annotations only (repo convention); array-valued paths are not
# handled (none in the codebase).
SPRING_MAPPING_RE = re.compile(
    r"@(Get|Post|Put|Patch|Delete)Mapping\s*\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]")
SPRING_MAPPING_NOARG_RE = re.compile(r"@(Get|Post|Put|Patch|Delete)Mapping\s*(?:\(\s*\))?$")
SPRING_REQMAPPING_PATH_RE = re.compile(
    r"@RequestMapping\s*\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]")

# JVM port modules covered by the apidocs drift machinery. Keys follow the
# "<base>/<service>" shape; values are repo-root-relative module dirs.
JVM_SERVICES = {
    "jvm/nexus-core-aegis": "jvm/spring/nexus-core/nexus-core-aegis",
    "jvm/nexus-core-shrapnel": "jvm/spring/nexus-core/nexus-core-shrapnel",
}

# Moleculer apps (moleculer-web gateways). Keys follow the "<base>/<app>"
# shape; values are repo-root-relative module dirs. Their route surface is not
# Express `router.get(...)` calls but the gateway's `aliases:` map
# (`"GET /path": "svc.action"`) relative to each route's `path:` prefix —
# parsed by process_moleculer_service() below. Which incumbent contract each
# app is pinned to lives in check_drift.MOLLECULER_MIRRORS.
MOLLECULER_SERVICES = {
    "moleculer/voyager": "moleculer/voyager",
    "moleculer/cascade": "moleculer/cascade",
    "moleculer/kernel": "moleculer/kernel",
    "moleculer/draft": "moleculer/draft",
}

# moleculer-web alias entries: "GET /path": "svc.action" (single-quoted forms
# too; both appear in the codebase).
ALIAS_RE = re.compile(r"['\"](GET|POST|PUT|PATCH|DELETE)\s+(/[^'\"]*)['\"]\s*:\s*['\"]([^'\"]+)['\"]")
ALIASES_OPEN_RE = re.compile(r"\baliases\s*:\s*\{")
ROUTE_PATH_RE = re.compile(r"\bpath\s*:\s*['\"]([^'\"]*)['\"]")

KNOWN_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def comment_above(lines, idx, max_lines=6):
    """Join the `//` comment lines immediately above line idx (if any)."""
    parts = []
    j = idx - 1
    # Skip a leading blank line (e.g. `// comment`, blank, `code`) so the
    # scan reaches the comment block instead of stopping at the blank.
    while j >= 0 and not lines[j].strip():
        j -= 1
    while j >= 0 and len(parts) < max_lines:
        s = lines[j].strip()
        if s.startswith("//"):
            parts.append(s.lstrip("/").strip())
            j -= 1
        elif not s and parts:
            j -= 1  # allow a single blank line between comments and code
        else:
            break
    text = " ".join(reversed(parts)).strip()
    text = re.sub(r"[─═\u2500-\u257f]+", " ", text)  # strip box-drawing decoration
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


class FileInfo:
    def __init__(self, rel):
        self.rel = rel
        self.lines = []
        self.routes = {}      # var -> [(verb, path, summary)]
        self.mounts = {}      # var -> [(prefix, target_expr)]
        self.imports = {}     # localName -> (moduleCandidates, kind, exportName)
        self.default_var = None
        self.router_vars = set()


def resolve_candidates(spec, file_rel):
    """Resolve a module specifier to candidate service-relative paths.

    Node/TS resolution: exact path, then with common extensions, then as a
    directory (index file). Also handles `.js`-suffixed specifiers that resolve
    to `.ts` source files (ESM-compiled TS projects).
    """
    if not spec.startswith("."):
        return []
    base = os.path.dirname(file_rel)
    p = os.path.normpath(os.path.join(base, spec))
    stem = p
    for ext in KNOWN_EXTS:
        if p.endswith(ext):
            stem = p[: -len(ext)]
            break
    out = [p]
    if stem != p:
        out.append(stem)
    for ext in KNOWN_EXTS:
        out.append(stem + ext)
        out.append(stem + "/index" + ext)
    out.append(stem + "/index")
    return list(dict.fromkeys(out))


def merge_imports(lines):
    """Merge multi-line import statements into single lines."""
    merged = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\n")
        s = line.strip()
        if s.startswith("import") and "from" not in line and "require(" not in line:
            buf = s
            j = i + 1
            while j < n and "from" not in lines[j]:
                buf += " " + lines[j].strip()
                j += 1
            if j < n:
                buf += " " + lines[j].strip().rstrip(";")
                i = j
            merged.append(buf)
        else:
            merged.append(line)
        i += 1
    return merged


def join_route_path(prefix, path):
    """Join a moleculer-web route `path:` prefix with an alias path."""
    if not prefix or prefix == "/":
        return path if path.startswith("/") else "/" + path
    return prefix.rstrip("/") + "/" + path.lstrip("/")


def process_moleculer_service(svc_dir, key):
    """Route inventory for a moleculer-web app (aliases map, not Express verbs).

    Walks services/ for .ts/.js gateway files and reads each `aliases: {...}`
    block, prefixing its entries with the enclosing route's `path:` value.
    Commented-out alias lines are skipped (they would otherwise parse as live
    endpoints and show up as phantom drift).
    """
    files = []
    scan_root = os.path.join(svc_dir, "services")
    if not os.path.isdir(scan_root):
        scan_root = svc_dir
    for root, dirs, names in os.walk(scan_root):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "test", "__tests__", "coverage")]
        for name in sorted(names):
            if name.endswith((".ts", ".js", ".mjs")) and not name.endswith(".d.ts"):
                files.append(os.path.join(root, name))

    endpoints = []
    for fp in files:
        with open(fp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        prefix = None
        in_aliases = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if in_aliases:
                if stripped.startswith("}"):
                    in_aliases = False
                    continue
                am = ALIAS_RE.search(line)
                if am:
                    endpoints.append({
                        "method": am.group(1).upper(),
                        "path": join_route_path(prefix or "", am.group(2)),
                        "summary": comment_above(lines, i),
                    })
                continue
            if ALIASES_OPEN_RE.search(line):
                in_aliases = True
                continue
            pm = ROUTE_PATH_RE.search(line)
            if pm:
                prefix = pm.group(1)
    return dedupe(endpoints)


def parse_file(fp, rel):
    info = FileInfo(rel)
    with open(fp, encoding="utf-8", errors="replace") as f:
        raw_lines = f.readlines()
    lines = merge_imports(raw_lines)
    info.lines = lines
    for i, line in enumerate(lines):
        m = IMPORT_RE.match(line.strip())
        if m:
            mods = resolve_candidates(m.group("mod"), rel)
            if not mods:
                continue
            if m.group("default"):
                info.imports[m.group("default")] = (mods, "default", None)
            if m.group("named"):
                for part in m.group("named").split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if " as " in part:
                        orig, _, alias = part.partition(" as ")
                        info.imports[alias.strip()] = (mods, "named", orig.strip())
                    else:
                        info.imports[part] = (mods, "named", part)
            continue
        rm = ROUTER_RE.match(line)
        if rm:
            info.router_vars.add(rm.group(1))
            info.routes.setdefault(rm.group(1), [])
            info.mounts.setdefault(rm.group(1), [])
            continue
        dm = DEFAULT_EXPORT_RE.match(line)
        if dm:
            info.default_var = dm.group(1)
            continue
        vm = VERB_RE.search(line)
        if vm:
            var = line[: vm.start()].split(".")[-1].strip()
            verb = vm.group(1).lower()
            rest = line[vm.end():]
            pm = re.match(r"([^,)]+)", rest)
            path = pm.group(1).strip().strip("\"'`") if pm else None
            if not path or not path.startswith("/"):
                continue
            if "router" in var or var in info.router_vars or var == "app":
                info.routes.setdefault(var, []).append((verb, path, comment_above(lines, i)))
            continue
        um = USE_RE.search(line)
        if um:
            var = line[: um.start()].split(".")[-1].strip()
            if var == "app" or var in info.router_vars:
                info.mounts.setdefault(var, []).append((um.group(1), um.group(2)))
    return info


def find_factory_router_var(lines, fn_name):
    """Inside the named factory function, find the Router() var it builds."""
    start = None
    for i, line in enumerate(lines):
        if re.search(r"(?:export\s+)?(?:async\s+)?function\s+" + re.escape(fn_name) + r"\s*\(", line) or \
           re.search(r"(?:export\s+)?const\s+" + re.escape(fn_name) + r"\s*=", line):
            start = i
            break
    if start is None:
        return None
    depth = 0
    for j in range(start, len(lines)):
        line = lines[j]
        depth += line.count("{") - line.count("}")
        if depth > 0:
            rm = ROUTER_RE.match(line.strip())
            if rm:
                return rm.group(1)
        if depth <= 0 and j > start:
            break
    return None


def resolve_target(target_expr, files, cur_file_info):
    """Resolve a mount target expression to (router_var, FileInfo) or None."""
    name = re.match(r"[A-Za-z_$][\w$]*", target_expr.strip())
    if not name:
        return None
    name = name.group(0)
    imp = cur_file_info.imports.get(name)
    if imp is None:
        if name in cur_file_info.router_vars:
            return name, cur_file_info
        return None
    mods, kind, export_name = imp
    for mod in mods:
        target = files.get(mod)
        if target is None:
            continue
        if kind == "default":
            if target.default_var:
                return target.default_var, target
            continue
        if export_name and export_name in target.router_vars:
            return export_name, target
        if export_name:
            var = find_factory_router_var(target.lines, export_name)
            if var:
                return var, target
    return None


def flatten(index_file, files, prefix="", depth=0):
    out = []
    info = files[index_file]
    for verb, path, summary in info.routes.get("app", []):
        joined = (prefix.rstrip("/") + "/" + path.lstrip("/")) if path != "/" else (prefix or "/")
        out.append({"method": verb.upper(), "path": joined, "summary": summary})
    if depth > 8:
        return out
    for pfx, target_expr in info.mounts.get("app", []):
        base = prefix.rstrip("/") + "/" + pfx.lstrip("/")
        res = resolve_target(target_expr, files, info)
        if res is None:
            continue
        var, tinfo = res
        for verb, path, summary in tinfo.routes.get(var, []):
            joined = (base.rstrip("/") + "/" + path.lstrip("/")) if path != "/" else (base or "/")
            out.append({"method": verb.upper(), "path": joined, "summary": summary})
        for subpfx, subtarget in tinfo.mounts.get(var, []):
            subbase = base.rstrip("/") + "/" + subpfx.lstrip("/")
            r2 = resolve_target(subtarget, files, tinfo)
            if r2:
                v2, t2 = r2
                for verb, path, summary in t2.routes.get(v2, []):
                    joined = (subbase.rstrip("/") + "/" + path.lstrip("/")) if path != "/" else (subbase or "/")
                    out.append({"method": verb.upper(), "path": joined, "summary": summary})
    return out


def extract_fastapi(lines):
    out = []
    for raw in lines:
        m = FASTAPI_RE.search(raw)
        if m:
            out.append({"method": m.group(2).upper(), "path": m.group(3), "summary": ""})
    return out


def _spring_join(base, path):
    if not path.startswith("/"):
        path = "/" + path
    if not base or base == "/":
        return path
    return base.rstrip("/") + path


def extract_spring(lines):
    """Extract Spring @RequestMapping/@XxxMapping routes from Java source lines.

    The class-level @RequestMapping value is remembered as the base path for
    subsequent method mappings in the file. @ExceptionHandler-only advice
    classes contribute nothing (no mapping annotations).
    """
    out = []
    base = ""
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("@RequestMapping"):
            pm = SPRING_REQMAPPING_PATH_RE.search(s)
            if pm and "method" not in s:
                base = pm.group(1)
            continue
        m = SPRING_MAPPING_RE.search(s)
        if m:
            out.append({
                "method": m.group(1).upper(),
                "path": _spring_join(base, m.group(2)),
                "summary": comment_above(lines, i),
            })
            continue
        m2 = SPRING_MAPPING_NOARG_RE.search(s)
        if m2:
            out.append({
                "method": m2.group(1).upper(),
                "path": base or "/",
                "summary": comment_above(lines, i),
            })
    return out


def process_spring_service(svc_path, name):
    """Endpoint inventory for a JVM Spring module (src/main/java/**/*.java)."""
    endpoints = []
    for dirpath, dirnames, filenames in os.walk(svc_path):
        dirnames[:] = [d for d in dirnames if d not in ("target", ".git")]
        for fn in sorted(filenames):
            if not fn.endswith(".java") or "test" in fn.lower():
                continue
            fp = os.path.join(dirpath, fn)
            with open(fp, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            endpoints.extend(extract_spring(lines))
    return dedupe(endpoints)


def process_service(svc_path, name):
    files = {}
    fastapi = []
    for dirpath, dirnames, filenames in os.walk(svc_path):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist", ".git", ".venv", "venv", "__pycache__")]
        for fn in sorted(filenames):
            if not fn.endswith((".ts", ".js", ".py")):
                continue
            if fn.endswith(".py"):
                fp = os.path.join(dirpath, fn)
                with open(fp, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                if fn in ("main.py", "app.py"):
                    fastapi.extend(extract_fastapi(lines))
                continue
            if "test" in fn.lower() or ".spec." in fn:
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, svc_path)
            files[rel] = parse_file(fp, rel)
    if fastapi:
        return dedupe(fastapi)
    index = None
    for candidate in ("src/index.ts", "src/index.js", "src/index.mjs", "index.ts", "index.js", "src/server.ts", "src/server.js"):
        if candidate in files:
            index = candidate
            break
    if index is None:
        return []
    return dedupe(flatten(index, files))


def dedupe(endpoints):
    seen = set()
    out = []
    for e in endpoints:
        k = (e["method"], e["path"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return sorted(out, key=lambda e: (e["path"], e["method"]))


def main():
    ap = argparse.ArgumentParser(description="Extract REST endpoint inventories from *-srv projects.")
    ap.add_argument("--root", default=ROOT, help="nexus repo root")
    ap.add_argument("--out", default="/tmp/api_inventory.json", help="output JSON path")
    args = ap.parse_args()
    result = {}
    for base in ("typescript", "python"):
        d = os.path.join(args.root, base)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            full = os.path.join(d, name)
            if os.path.isdir(full) and name.endswith("-srv"):
                result[f"{base}/{name}"] = process_service(full, name)
    for key, rel in sorted(JVM_SERVICES.items()):
        full = os.path.join(args.root, rel)
        if os.path.isdir(full):
            result[key] = process_spring_service(full, key)
    for key, rel in sorted(MOLLECULER_SERVICES.items()):
        full = os.path.join(args.root, rel)
        if os.path.isdir(full):
            result[key] = process_moleculer_service(full, key)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1)
    for k, v in result.items():
        print(f"{k}: {len(v)} endpoints")


if __name__ == "__main__":
    main()
