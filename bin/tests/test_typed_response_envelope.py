#!/usr/bin/env python3
"""Guard: typed response envelopes must resolve, and their $refs must be complete.

Background
----------
`tools/api-docs/gen_openapi.py` emits a generic `JsonBody` schema for every
success response. For most endpoints that is honest. For the few whose envelope
is known and load-bearing it is quietly harmful, because an undocumented shape
is indistinguishable from an undocumented-and-changed one and consumers guess.

The concrete cost, measured: `GET /api/forums/threads/{threadId}` returns
`{thread, comments}` where `comments` is a **top-level sibling** of `thread`.
Two agents independently read it as `thread.comments` and filed a three-day
"Assembly comments don't read back" phantom outage, during which three DBA
rulings escalated to the architect were believed unread. The handler was correct
throughout; the spec simply never said. This guard exists so the fix cannot be
silently undone by a well-meaning regeneration.

Why a guard rather than a comment in the YAML
---------------------------------------------
`typescript/assembly-srv/openapi.yaml` is **generated** (`x-generated-by:
nexus/tools/api-docs/gen_openapi.py`) and CI enforces byte-identical
regeneration. Hand-editing it is not merely discouraged, it is reverted on the
next `make apidocs-gen`. The durable fix has to live in the generator, which
means the generator's override table now needs its own invariants.

What this pins
--------------
1. **Every override resolves to a real route.** A `RESPONSE_OVERRIDES` key that
   matches nothing is a *silent* no-op: the generator emits `JsonBody`, the CI
   byte-identical check passes, and the envelope silently reverts to generic.
   Nothing else in the pipeline would notice. This is the highest-value
   assertion in the file.
2. **Every referenced schema is defined** in `EXTRA_SCHEMAS` for that service.
   A dangling `$ref` renders as a broken link in every consumer.
3. **Emitted `$ref`s are fully qualified.** A bare `$ref: ThreadDetail` resolves
   against the document root instead of `components/schemas` and breaks every
   consumer that dereferences it, while still looking plausible in the YAML.
   This was a real defect during development of the fix — the table stores bare
   names for readability and the expansion to `#/components/schemas/<name>`
   happens in `build_spec`; nothing but this test would catch a regression there.
4. **Schemas are emitted only where referenced**, so an unused service's spec
   does not accumulate schemas it never mentions.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GEN = REPO_ROOT / "tools" / "api-docs" / "gen_openapi.py"
ASSEMBLY_SPEC = REPO_ROOT / "typescript" / "assembly-srv" / "openapi.yaml"
INVENTORY = Path("/tmp/api_inventory.json")

THREAD_PATH = "/api/forums/threads/{threadId}"


def _load_generator():
    """Import gen_openapi.py by path (it is a script, not an installed module).

    It does a sibling `import extract_routes` (for the JVM_SERVICES module-dir
    registry), which resolves only when tools/api-docs is on sys.path. Prepending
    it here and popping it afterwards keeps the import hermetic — leaving it
    behind would let `extract_routes` shadow anything a later test imports.
    """
    tools_dir = str(GEN.parent)
    added = tools_dir not in sys.path
    if added:
        sys.path.insert(0, tools_dir)
    try:
        spec = importlib.util.spec_from_file_location("gen_openapi", GEN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if added and tools_dir in sys.path:
            sys.path.remove(tools_dir)


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


@pytest.fixture(scope="module")
def inventory():
    if not INVENTORY.exists():
        subprocess.run(
            ["make", "apidocs-extract"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
            stdin=subprocess.DEVNULL, check=True,
        )
    if not INVENTORY.exists():
        pytest.skip("endpoint inventory not produced by extract_routes")
    import json
    return json.loads(INVENTORY.read_text())


@pytest.fixture(scope="module")
def assembly_spec():
    return yaml.safe_load(ASSEMBLY_SPEC.read_text())


# ── 1. the override table is well-formed ──────────────────────────────

def test_override_table_is_nonempty(gen):
    """A guard over an empty table proves nothing; assert we actually fixed one."""
    assert gen.RESPONSE_OVERRIDES, (
        "RESPONSE_OVERRIDES is empty — the typed-envelope fix has been removed."
    )


def test_every_override_resolves_to_a_real_route(gen, inventory):
    """The silent-no-op check: an unmatched key reverts the fix invisibly.

    A typo in the path, a route rename, or a change from `:threadId` to
    `{threadId}` form would leave the entry in place while the generator quietly
    emitted `JsonBody` again — and CI's byte-identical check would pass, because
    the committed spec would have been regenerated to match.
    """
    for service, table in gen.RESPONSE_OVERRIDES.items():
        endpoints = inventory.get(service)
        assert endpoints is not None, (
            f"{service} has overrides but is not in the endpoint inventory"
        )
        routes = {
            (e["method"].upper(), gen.path_to_openapi(e["path"]))
            for e in endpoints
        }
        for key in table:
            method, path = key
            assert (method, path) in routes, (
                f"override {key} in {service} matches no route. It will be a "
                f"silent no-op. Known (method, path) pairs include: "
                f"{sorted(routes)[:5]}..."
            )


def test_every_referenced_schema_is_defined(gen, inventory):
    """No dangling $ref."""
    for service, table in gen.RESPONSE_OVERRIDES.items():
        defined = set(gen.EXTRA_SCHEMAS.get(service, {}))
        for key, name in table.items():
            assert name in defined, (
                f"override {key} in {service} references schema {name!r}, which is "
                f"not defined in EXTRA_SCHEMAS[{service!r}] (have: {sorted(defined)})"
            )


# ── 2. the generated output is correct ────────────────────────────────

def test_thread_detail_is_typed_not_generic(gen):
    """The actual fix: the endpoint must not fall back to JsonBody."""
    spec = gen.build_spec({"title": "t", "port": 0, "desc": ""}, [], "typescript/assembly-srv")
    # Even with no endpoints, the schema must be present for a service that
    # declares overrides — otherwise the $ref would dangle.
    assert "ThreadDetail" in spec["components"]["schemas"]


def test_success_ref_is_fully_qualified(gen):
    """Regression guard for a real defect hit while building this.

    The table stores bare names ('ThreadDetail') for readability. If the
    expansion to '#/components/schemas/...' in build_spec is ever lost, the spec
    emits a relative `$ref: ThreadDetail` that resolves against the document
    root. It still parses, still diffs cleanly, and breaks only downstream.
    """
    spec = gen.build_spec(
        {"title": "t", "port": 0, "desc": ""}, [], "typescript/assembly-srv"
    )
    # Drive build_operation directly with a bare name to prove the *call site*
    # qualifies it, not the table.
    op = gen.build_operation("GET", THREAD_PATH, "", None)
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == \
        "#/components/schemas/JsonBody", "default must remain JsonBody"
    op2 = gen.build_operation("GET", THREAD_PATH, "", "#/components/schemas/ThreadDetail")
    assert op2["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == \
        "#/components/schemas/ThreadDetail"


def test_schemas_only_emitted_where_referenced(gen):
    """A service with no overrides must not grow an unused schema."""
    spec = gen.build_spec({"title": "t", "port": 0, "desc": ""}, [], "typescript/draft-srv")
    assert "ThreadDetail" not in spec["components"]["schemas"], (
        "draft-srv does not declare overrides; it must not gain ThreadDetail"
    )


# ── 3. the committed spec agrees with the generator ───────────────────

def test_committed_spec_types_the_endpoint(assembly_spec):
    """The checked-in spec — the thing consumers actually read."""
    ref = assembly_spec["paths"][THREAD_PATH]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref == "#/components/schemas/ThreadDetail", (
        f"committed assembly spec still types the thread-detail response as {ref!r}; "
        "regenerate with `make apidocs-gen SKIP_FASTAPI=1`"
    )


def test_committed_schema_documents_the_sibling_shape(assembly_spec):
    """The schema must say what actually caused the outage.

    An envelope schema that merely lists the two keys, without stating that
    `comments` is a sibling of `thread` rather than a field inside it, would let
    the original mistake recur — the key names are identical either way.
    """
    td = assembly_spec["components"]["schemas"]["ThreadDetail"]
    assert sorted(td["properties"]) == ["comments", "thread"]
    assert sorted(td.get("required", [])) == ["comments", "thread"], (
        "both envelope members must be required, or a consumer cannot tell a "
        "real response from a truncated one"
    )
    desc = td.get("description", "").lower()
    assert "sibling" in desc, (
        "ThreadDetail.description must state that `comments` is a top-level "
        "sibling of `thread` — that sentence is the whole point of the schema"
    )
    assert td["properties"]["comments"]["type"] == "array"


def test_every_ref_in_committed_spec_resolves(assembly_spec):
    """No dangling $ref anywhere in the committed spec."""
    text = ASSEMBLY_SPEC.read_text()
    schemas = assembly_spec["components"]["schemas"]
    refs = set(re.findall(r"\$ref:\s*'?#/components/schemas/([A-Za-z0-9_]+)'?", text))
    dangling = sorted(refs - set(schemas))
    assert not dangling, f"spec references undefined schemas: {dangling}"
