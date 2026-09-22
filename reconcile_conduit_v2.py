#!/usr/bin/env python3
"""
Reconcile Conduit TypeSpec operations against:
1. conduit-mcp conduit-client.ts (caller)
2. python/conduit/app/api/routes_*.py + main.py (actual FastAPI implementation)
"""

import ast
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

TYPESPEC_DIR = Path("/home/codex/dev/nexus-worktrees/conduit-typespec-capture/typespec/v1/conduit-kernel/python")
CONDUIT_DIR = Path("/home/codex/dev/nexus-worktrees/conduit-typespec-capture/python/conduit/app")
CONDUIT_MCP_DIR = Path("/home/codex/dev/nexus-worktrees/conduit-typespec-capture/typescript/conduit-mcp/src")

# Known router prefixes from main.py app.include_router calls
ROUTER_PREFIXES = {
    "receipts_router": "/api",
    "sessions_router": "/api",
    "breaker_router": "/api",
    "admin_router": "/admin",
    "state_router": "/state",
    "replay_router": "/replay",
    "delta_router": "/delta",
    "sessions_router": "/api",
}

def parse_typespec_operations(tsp_file: Path) -> Dict[str, List[Dict]]:
    content = tsp_file.read_text()
    operations = {"get": [], "post": [], "patch": [], "delete": [], "put": []}
    
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('@route("'):
            route_match = re.match(r'@route\("([^"]+)"\)', line)
            if route_match:
                route = route_match.group(1)
                j = i + 1
                method = None
                while j < len(lines) and lines[j].strip().startswith('@'):
                    if lines[j].strip().startswith('@get'):
                        method = 'GET'
                    elif lines[j].strip().startswith('@post'):
                        method = 'POST'
                    elif lines[j].strip().startswith('@patch'):
                        method = 'PATCH'
                    elif lines[j].strip().startswith('@delete'):
                        method = 'DELETE'
                    elif lines[j].strip().startswith('@put'):
                        method = 'PUT'
                    j += 1
                
                op_name = None
                while j < len(lines):
                    op_match = re.match(r'op\s+(\w+)\s*\(', lines[j].strip())
                    if op_match:
                        op_name = op_match.group(1)
                        break
                    if lines[j].strip() == '':
                        j += 1
                        continue
                    break
                
                if method and op_name:
                    operations[method.lower()].append({
                        "route": route,
                        "op": op_name,
                        "method": method
                    })
        i += 1
    
    return operations

def parse_fastapi_router(py_file: Path, router_name: str) -> Dict[str, List[Dict]]:
    """Parse FastAPI router, extracting routes with known prefix."""
    content = py_file.read_text()
    tree = ast.parse(content)
    
    operations = {"get": [], "post": [], "patch": [], "delete": [], "put": [], "websocket": []}
    
    # Get prefix from known mapping
    prefix = ROUTER_PREFIXES.get(router_name, "")
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and hasattr(decorator.func, 'attr'):
                    attr = decorator.func.attr
                    if attr in ['get', 'post', 'patch', 'delete', 'put', 'websocket']:
                        method = attr.upper()
                        route = None
                        if decorator.args and isinstance(decorator.args[0], ast.Constant):
                            route = decorator.args[0].value
                        if route:
                            full_route = prefix + route
                            operations[attr].append({
                                "route": full_route,
                                "op": node.name,
                                "method": method
                            })
    
    return operations

def parse_main_routes(py_file: Path) -> Dict[str, List[Dict]]:
    """Parse main.py for root-level routes (not under any router)."""
    content = py_file.read_text()
    tree = ast.parse(content)
    
    operations = {"get": [], "post": [], "patch": [], "delete": [], "put": [], "websocket": []}
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and hasattr(decorator.func, 'attr'):
                    attr = decorator.func.attr
                    if attr in ['get', 'post', 'patch', 'delete', 'put', 'websocket']:
                        method = attr.upper()
                        route = None
                        if decorator.args and isinstance(decorator.args[0], ast.Constant):
                            route = decorator.args[0].value
                        if route:
                            operations[attr].append({
                                "route": route,
                                "op": node.name,
                                "method": method
                            })
    
    return operations

def parse_conduit_client(ts_file: Path) -> Dict[str, List[Dict]]:
    """Parse conduit-mcp conduit-client.ts for called endpoints."""
    content = ts_file.read_text()
    operations = {"get": [], "post": [], "patch": [], "delete": [], "put": []}
    
    # Parse fetch calls
    fetch_pattern = re.compile(r'(get|post|patch|delete|put|del)\s*\(\s*["\`](/api/[^"\`]+)["\`]')
    
    for match in fetch_pattern.finditer(content):
        method = match.group(1).upper()
        if method == 'DEL':
            method = 'DELETE'
        route = match.group(2)
        context_start = max(0, match.start() - 200)
        context = content[context_start:match.start()]
        fn_match = re.search(r'export async function (\w+)', context[::-1])
        op_name = fn_match.group(1) if fn_match else "unknown"
        
        operations[method.lower()].append({
            "route": route,
            "op": op_name,
            "method": method
        })
    
    return operations

def merge_operations(*op_dicts: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    merged = {"get": [], "post": [], "patch": [], "delete": [], "put": [], "websocket": []}
    for d in op_dicts:
        for method, ops in d.items():
            merged[method].extend(ops)
    return merged

def normalize_route(route: str) -> str:
    route = re.sub(r'\{[^}]+\}', '{param}', route)
    route = re.sub(r'\$\{.*?\}', '{param}', route)
    return route.rstrip('/')

def main():
    print("=" * 80)
    print("Conduit TypeSpec ↔ Implementation ↔ Caller Reconciliation (v2)")
    print("=" * 80)
    print()
    
    # Parse TypeSpec operations
    ts_operations = parse_typespec_operations(TYPESPEC_DIR / "operations.tsp")
    print(f"TypeSpec declared operations ({sum(len(v) for v in ts_operations.values())}):")
    for method, ops in ts_operations.items():
        for op in ops:
            print(f"  {method.upper():6} {op['route']} -> {op['op']}")
    print()
    
    # Parse actual FastAPI routes from each routes file
    conduit_operations = {}
    router_files = {
        "routes_receipts.py": "receipts_router",
        "routes_sessions.py": "sessions_router",
        "routes_breaker.py": "breaker_router",
        "routes_admin.py": "admin_router",
        "routes_state.py": "state_router",
        "routes_replay.py": "replay_router",
        "routes_delta.py": "delta_router",
    }
    
    for filename, router_name in router_files.items():
        py_file = CONDUIT_DIR / "api" / filename
        if py_file.exists():
            ops = parse_fastapi_router(py_file, router_name)
            for method, ops in ops.items():
                if method not in conduit_operations:
                    conduit_operations[method] = []
                conduit_operations[method].extend(ops)
    
    # Parse main.py for root routes
    main_ops = parse_main_routes(CONDUIT_DIR / "main.py")
    for method, ops in main_ops.items():
        if method not in conduit_operations:
            conduit_operations[method] = []
        conduit_operations[method].extend(ops)
    
    print(f"Conduit FastAPI actual routes ({sum(len(v) for v in conduit_operations.values())}):")
    for method, ops in conduit_operations.items():
        for op in ops:
            print(f"  {method.upper():6} {op['route']} -> {op['op']}")
    print()
    
    # Parse conduit-mcp caller
    caller_operations = parse_conduit_client(CONDUIT_MCP_DIR / "conduit-client.ts")
    print(f"Conduit-mcp caller routes ({sum(len(v) for v in caller_operations.values())}):")
    for method, ops in caller_operations.items():
        for op in ops:
            print(f"  {method.upper():6} {op['route']} -> {op['op']}")
    print()
    
    # Compare
    print("=" * 80)
    print("THREE-WAY RECONCILIATION REPORT")
    print("=" * 80)
    print()
    
    # Build sets for comparison
    ts_routes = set()
    for method, ops in ts_operations.items():
        for op in ops:
            ts_routes.add((method.upper(), normalize_route(op['route'])))
    
    conduit_routes = set()
    for method, ops in conduit_operations.items():
        for op in ops:
            conduit_routes.add((method.upper(), normalize_route(op['route'])))
    
    caller_routes = set()
    for method, ops in caller_operations.items():
        for op in ops:
            caller_routes.add((method.upper(), normalize_route(op['route'])))
    
    # Three-way comparison
    all_routes = ts_routes | conduit_routes | caller_routes
    
    print(f"\nSUMMARY:")
    print(f"  TypeSpec declared:     {len(ts_routes)} endpoints")
    print(f"  Conduit FastAPI actual: {len(conduit_routes)} endpoints")
    print(f"  Conduit-mcp caller:     {len(caller_routes)} endpoints")
    print(f"  Union (all):            {len(set().union(ts_routes, conduit_routes, caller_routes))} endpoints")
    
    # Three-way comparison
    all_routes = ts_routes | conduit_routes | caller_routes
    
    print()
    for route in sorted(all_routes):
        method, path = route
        in_ts = route in ts_routes
        in_conduit = route in conduit_routes
        in_caller = route in caller_routes
        
        status = []
        if in_ts: status.append("TS")
        if in_conduit: status.append("Impl")
        if in_caller: status.append("Caller")
        
        missing = []
        if not in_ts: missing.append("TypeSpec")
        if not in_conduit: missing.append("Impl")
        if not in_caller: missing.append("Caller")
        
        status_str = ', '.join(status)
        if len(missing) > 0:
            print(f"  {method:6} {path:60} [{status_str}]  MISSING: {', '.join(missing)}")
        else:
            print(f"  {method:6} {path:60} [{status_str}]  ✅")
    
    # Summary by category
    print("\n" + "=" * 80)
    print("DRIFT ANALYSIS BY CATEGORY")
    print("=" * 80)
    
    only_ts = ts_routes - conduit_routes - caller_routes
    only_impl = conduit_routes - ts_routes - caller_routes
    only_caller = caller_routes - ts_routes - conduit_routes
    ts_impl = ts_routes & conduit_routes - caller_routes
    ts_caller = ts_routes & caller_routes - conduit_routes
    impl_caller = conduit_routes & caller_routes - ts_routes
    all_three = ts_routes & conduit_routes & caller_routes
    
    print(f"\n✅ All three aligned:           {len(all_three)}")
    print(f"  TypeSpec + Impl only:         {len(ts_impl)}")
    print(f"  TypeSpec + Caller only:       {len(ts_caller)}")
    print(f"  Impl + Caller only:           {len(impl_caller)}")
    print(f"  TypeSpec only:                {len(only_ts)}")
    print(f"  Impl only:                    {len(only_impl)}")
    print(f"  Caller only:                  {len(only_caller)}")
    
    if only_ts:
        print(f"\n⚠️  TypeSpec only (not implemented, not called):")
        for m, p in sorted(only_ts):
            print(f"  {m:6} {p}")
    
    if only_impl:
        print(f"\n❌ Implementation only (not in TypeSpec, not called):")
        for m, p in sorted(only_impl):
            print(f"  {m:6} {p}")
    
    if only_caller:
        print(f"\n📞 Caller only (not in TypeSpec, not implemented):")
        for m, p in sorted(only_caller):
            print(f"  {m:6} {p}")
    
    if ts_impl:
        print(f"\n📝 TypeSpec + Impl (not called by conduit-mcp):")
        for m, p in sorted(ts_impl):
            print(f"  {m:6} {p}")
    
    # Output JSON report
    all_routes = ts_routes | conduit_routes | caller_routes
    report = {
        "timestamp": "2026-09-21T16:30:00Z",
        "surface": "Conduit kernel API",
        "three_way": [
            {"method": m, "route": r, 
             "in_typespec": (m, r) in ts_routes,
             "in_implementation": (m, r) in conduit_routes,
             "in_caller": (m, r) in caller_routes}
            for m, r in sorted(all_routes)
        ],
        "counts": {
            "typespec": len(ts_routes),
            "implementation": len(conduit_routes),
            "caller": len(caller_routes),
            "total_union": len(all_routes)
        },
        "drift": {
            "typespec_only": [{"method": m, "route": r} for m, r in sorted(only_ts)],
            "impl_only": [{"method": m, "route": r} for m, r in sorted(only_impl)],
            "caller_only": [{"method": m, "route": r} for m, r in sorted(only_caller)],
            "ts_impl_no_caller": [{"method": m, "route": r} for m, r in sorted(ts_impl)],
        }
    }
    
    output_file = Path("/home/codex/dev/nexus-worktrees/conduit-typespec-capture/reconcile_conduit_report_v2.json")
    output_file.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to: {output_file}")

if __name__ == "__main__":
    main()
