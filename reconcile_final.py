#!/usr/bin/env python3
"""
Reconcile TypeSpec operations against losm-host /api compat surface.
Handles router prefixes correctly.
"""

import ast
import json
import re
from pathlib import Path
from typing import Dict, List, Set

TYPESPEC_DIR = Path("/home/codex/dev/nexus-worktrees/vision-typespec-capture/typespec/v1/vision-srv/python")
LOSM_HOST_DIR = Path("/home/codex/dev/nexus-worktrees/vision-typespec-capture/python/vision/losm-host/losm/api")

def parse_typespec_operations(tsp_file: Path) -> Dict[str, List[Dict]]:
    content = tsp_file.read_text()
    operations = {"get": [], "post": [], "patch": [], "delete": []}
    
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

def parse_fastapi_router(py_file: Path) -> Dict[str, List[Dict]]:
    """Parse FastAPI router, extracting router prefix and all routes."""
    content = py_file.read_text()
    tree = ast.parse(content)
    
    operations = {"get": [], "post": [], "patch": [], "delete": [], "websocket": []}
    
    # First, find router definitions and their prefixes
    router_prefixes = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'router':
                    if isinstance(node.value, ast.Call) and hasattr(node.value.func, 'id') and node.value.func.id == 'APIRouter':
                        # Extract prefix from keyword arguments
                        for kw in node.value.keywords:
                            if kw.arg == 'prefix' and isinstance(kw.value, ast.Constant):
                                router_prefixes[target.id] = kw.value.value
    
    # Default prefix if no router found
    default_prefix = router_prefixes.get('router', '')
    
    # Parse routes with prefix
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
                            full_route = default_prefix + route
                            operations[attr].append({
                                "route": full_route,
                                "op": node.name,
                                "method": method
                            })
    
    return operations

def merge_operations(*op_dicts: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    merged = {"get": [], "post": [], "patch": [], "delete": [], "websocket": []}
    for d in op_dicts:
        for method, ops in d.items():
            merged[method].extend(ops)
    return merged

def normalize_route(route: str) -> str:
    route = re.sub(r'\{[^}]+\}', '{param}', route)
    return route.rstrip('/')

def main():
    print("=" * 80)
    print("TypeSpec /api ↔ losm-host compat.py /api Reconciliation")
    print("=" * 80)
    print()
    
    # Parse TypeSpec /api operations
    ts_operations = parse_typespec_operations(TYPESPEC_DIR / "operations.tsp")
    print(f"TypeSpec /api declared operations ({sum(len(v) for v in ts_operations.values())}):")
    for method, ops in ts_operations.items():
        for op in ops:
            print(f"  {method.upper():6} {op['route']} -> {op['op']}")
    print()
    
    # Parse losm-host compat.py with router prefix
    losm_operations = {}
    compat_file = LOSM_HOST_DIR / "compat.py"
    if compat_file.exists():
        ops = parse_fastapi_router(compat_file)
        losm_operations = merge_operations(losm_operations, ops)
    
    print(f"losm-host compat.py /api actual routes ({sum(len(v) for v in losm_operations.values())}):")
    for method, ops in losm_operations.items():
        for op in ops:
            print(f"  {method.upper():6} {op['route']} -> {op['op']}")
    print()
    
    # Compare
    print("=" * 80)
    print("/api COMPAT SURFACE RECONCILIATION REPORT (Final)")
    print("=" * 80)
    print()
    
    ts_routes = set()
    for method, ops in ts_operations.items():
        for op in ops:
            ts_routes.add((method.upper(), normalize_route(op['route'])))
    
    losm_routes = set()
    for method, ops in losm_operations.items():
        for op in ops:
            losm_routes.add((method.upper(), normalize_route(op['route'])))
    
    only_in_typespec = ts_routes - losm_routes
    only_in_losm = losm_routes - ts_routes
    in_both = ts_routes & losm_routes
    
    print(f"\n✅ MATCHING ({len(in_both)}):")
    for method, route in sorted(in_both):
        print(f"  {method:6} {route}")
    
    print(f"\n⚠️  ONLY IN TYPESPEC ({len(only_in_typespec)}):")
    for method, route in sorted(only_in_typespec):
        print(f"  {method:6} {route}")
    
    print(f"\n❌ ONLY IN LOSM-HOST COMPAT ({len(only_in_losm)}):")
    for method, route in sorted(only_in_losm):
        print(f"  {method:6} {route}")
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"TypeSpec /api declared:     {len(ts_routes)} endpoints")
    print(f"losm-host compat /api actual: {len(losm_routes)} endpoints")
    print(f"Matching:                    {len(in_both)} endpoints")
    print(f"TypeSpec only:               {len(only_in_typespec)} endpoints")
    print(f"losm-host compat only:       {len(only_in_losm)} endpoints")
    
    report = {
        "timestamp": "2026-09-21T15:00:00Z",
        "surface": "/api compat (vision-srv wire contract per 5d8e10fd)",
        "matching": [{"method": m, "route": r} for m, r in sorted(in_both)],
        "only_in_typespec": [{"method": m, "route": r} for m, r in sorted(only_in_typespec)],
        "only_in_losm": [{"method": m, "route": r} for m, r in sorted(only_in_losm)],
        "counts": {
            "typespec": len(ts_routes),
            "losm_host_compat": len(losm_routes),
            "matching": len(in_both),
            "typespec_only": len(only_in_typespec),
            "losm_compat_only": len(only_in_losm)
        }
    }
    
    output_file = Path("/home/codex/dev/nexus-worktrees/vision-typespec-capture/reconcile_final_report.json")
    output_file.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to: {output_file}")

if __name__ == "__main__":
    main()
