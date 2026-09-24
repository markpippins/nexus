"""
wr-conf-006: Seed rendering + shadow-seed byte-compare — the procedure-card
seed can never silently corrupt.

The seedMemoryProcedures() template literal in typescript/tackle-seeds/index.ts
(shared by tackle-srv and tackle-mcp) is generated from the live DB but can be
damaged by a hand-edit or a bad regeneration: an unescaped backtick breaks the
JS template literal, a `\\'` (single-backslash-quote) renders to a BARE quote in
the SQL text and terminates the string literal, and a misplaced ARRAY/fence
line breaks the VALUES clause. All of those failure modes are silent to tsc
(the seed is just string content) and only surface when the SQL actually runs.

This test renders the seed from the SOURCE file with real JS semantics (node),
executes it against pg_temp shadow tables inside the live DB (nothing
persists), and asserts every seeded card byte-matches the live tackle.memory
table — plus the role_memory associations. It also asserts the escape
conventions hold structurally in the source.

CARD-BODY AUTHORING CAUTION (read before writing any card): the shadow
substitution in _shadow_seed() re-points the seed's INSERT targets by
string-replacing the two exact qualified table names (the schema constant
from `const SQL = ...`, currently `tackle`, plus `.memory` /
`.role_memory`) across the ENTIRE rendered SQL. A card body that spells
either qualified name as one unbroken token — in prose, a code fence, or
backticks — gets that text corrupted inside its VALUES clause and
false-fails every byte-compare as though the seed had drifted. This really
happened: the seed-integrity card's body named the canonical tables in
qualified form and had to be reworded (commit 7253675). Refer to the
canonical tables descriptively in card prose (e.g. "the canonical
procedure-card table"), never with the schema-qualified name as a single
token.

Tested invariants:
  AC1 — Render integrity: the source is locatable, renders through node to
        executable SQL, and the DO block executes cleanly against shadow
        tables (catches the bare-quote and ARRAY-position corruption classes)
        with the same card/role counts as live.
  AC2 — Card byte-identity: every seeded card (slug/title/summary/body_md/
        tags/triggers/mcp_tools) byte-matches the live table; none missing,
        none extra; the operator/investigation cards are present.
  AC3 — Role byte-identity: per-card role sets match live role_memory.
  AC4 — Escape conventions: only `${SQL}` interpolation is allowed, no
        backslash-quote that would render to a bare SQL quote, no raw
        backticks inside the template, and escaping is actually in use.
  AC5 — Manifest guard (CI): the committed typescript/tackle-seeds/
        seed-manifest.json (emitted by bin/regenerate_memory_seed.py from the
        live DB — card count, role count, per-card sha256 + role sets) is the
        no-live-DB reference. The rendered seed is executed against a scratch
        schema created entirely by this test (no live tackle.memory needed —
        only any Postgres + node), and the seeded state is byte-compared
        against the manifest. This is what CI runs (see
        .github/workflows/seed-guard.yml) so seed drift is caught
        automatically without a live DB.

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_conformance_seed_guard.py -v
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# Ensure nexus/python is on path for any shared imports.
_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_NEXUS_PYTHON = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", ".."))
if _NEXUS_PYTHON not in sys.path:
    sys.path.insert(0, _NEXUS_PYTHON)

from nexus_core.wrp.seed_manifest import (  # noqa: E402
    MANIFEST_PATH,
    apply_manifest,
    build_manifest,
    card_sha256,
    manifest_matches_live,
    manifest_self_consistent,
    read_manifest,
)

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
# Canonical shape fragment (single source of truth, V178-ratified).
from pathlib import Path as _Path
_CANONICAL_SHAPE_PATH = (_Path(__file__).resolve().parents[4]
                        / 'sql' / 'canonical'
                        / 'tackle_role_memory_shape.sql')

# Repo root = nexus/ (parent of nexus/python).
_REPO_ROOT = os.path.abspath(os.path.join(_NEXUS_PYTHON, ".."))
SEED_SOURCE = os.path.join(_REPO_ROOT, "typescript", "tackle-seeds", "index.ts")

# Cards that were historically missing from the seed (added in df19012) — a
# regression back to the stale seed would drop these.
KNOWN_LIVE_ONLY_CARDS = [
    "investigation-resources",
    "knowledge-graph-pipeline",
    "operator-audit-search",
    "operator-no-hallucination-rule",
    "operator-pipeline-query",
    "operator-requirement-lookup",
    "operator-workrequest-lifecycle",
]

# Renders the seed template literal from a .ts source using real JS semantics.
# Mirrors the generator's --verify renderer (bin/regenerate_memory_seed.py).
RENDER_MJS = (
    "const fs = require('fs');\n"
    "const src = fs.readFileSync(process.argv[2], 'utf8');\n"
    "const fn = src.indexOf('function seedMemoryProcedures');\n"
    "let open = -1, searchFrom = fn;\n"
    "while (true) {\n"
    "  const i = src.indexOf('return `', searchFrom);\n"
    "  if (i === -1) break;\n"
    "  const tick = src.indexOf('`', i + 7);\n"
    "  if (src.slice(tick + 1, tick + 40).trimStart().startsWith('DO $mem$')) { open = tick; break; }\n"
    "  searchFrom = i + 1;\n"
    "}\n"
    "if (open === -1) { console.error('seed template not found'); process.exit(1); }\n"
    "let close = -1;\n"
    "for (let i = open + 1; i < src.length; i++) {\n"
    "  if (src[i] === '`') {\n"
    "    let bs = 0, j = i - 1;\n"
    "    while (src[j] === '\\\\') { bs++; j--; }\n"
    "    if (bs % 2 === 0) { close = i; break; }\n"
    "  }\n"
    "}\n"
    "const schema = process.argv[3];\n"
    "const body = src.slice(open + 1, close);\n"
    "const rendered = eval('`' + body.replace(/\\$\\{SQL\\}/g, '\\\\${SQL}') + '`').replace(/\\$\\{SQL\\}/g, schema);\n"
    "process.stdout.write(rendered);\n"
)

_RENDER_CACHE: dict = {}
_SCHEMA: str | None = None


def _schema_name() -> str:
    """Derive the target schema from the source's `const SQL = ...` declaration.

    The seed must follow its own schema constant — never a hardcoded 'tackle'
    (if the constant were renamed, the render would silently test the wrong
    tables).
    """
    global _SCHEMA
    if _SCHEMA is None:
        src = open(SEED_SOURCE, encoding="utf-8").read()
        m = re.search(r"const SQL = `([a-z_]+)`", src)
        if not m:
            raise AssertionError(
                "cannot find `const SQL = `...`;` declaration in the seed source"
            )
        _SCHEMA = m.group(1)
    return _SCHEMA


def _render_seed(path: str = SEED_SOURCE) -> str:
    """Render seedMemoryProcedures() from a .ts/.js seed file (cached per path)."""
    if path in _RENDER_CACHE:
        return _RENDER_CACHE[path]
    if shutil.which("node") is None:
        raise unittest.SkipTest("node is required to render the seed template")
    if not os.path.exists(path):
        raise AssertionError(f"seed source missing: {path}")
    # .cjs so node runs it as CommonJS (require() works); .mjs would be ESM.
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False) as f:
        f.write(RENDER_MJS)
        script_path = f.name
    try:
        proc = subprocess.run(
            ["node", script_path, path, _schema_name()],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"node render of {path} failed:\n{proc.stderr[:2000]}"
            )
        _RENDER_CACHE[path] = proc.stdout
        return proc.stdout
    finally:
        os.unlink(script_path)


def _template_body() -> str:
    """Extract the template literal body (between the backticks) from the source."""
    src = open(SEED_SOURCE, encoding="utf-8").read()
    fn = src.index("function seedMemoryProcedures")
    open_tick = src.index("return `", fn) + 7
    i = open_tick + 1
    while i < len(src):
        if src[i] == "`":
            bs = 0
            j = i - 1
            while src[j] == "\\":
                bs += 1
                j -= 1
            if bs % 2 == 0:
                return src[open_tick + 1 : i]
        i += 1
    raise AssertionError("closing backtick of seed template not found")


def _db():
    import psycopg2

    return psycopg2.connect(DSN)


def _shadow_seed(rendered_sql: str) -> dict:
    """Execute the rendered seed into pg_temp shadow tables, compare vs live.

    Temp tables die with the connection, so nothing persists. Only the two
    exact qualified table names (the `const SQL = ...` schema, currently
    `tackle`, plus `.memory` / `.role_memory`) are re-pointed at pg_temp
    via plain string replacement over the WHOLE rendered SQL.

    CARD-BODY CONSTRAINT: because the replacement is textual, a card body
    that contains either qualified table name as one unbroken token (prose,
    code fence, or backticks) gets that text corrupted inside its VALUES
    clause and false-fails every byte-compare. The seed-integrity card hit
    this and required a reword (commit 7253675) — card bodies must refer to
    the canonical tables descriptively, never with the schema-qualified
    name spelled out.
    """
    conn = _db()
    try:
        cur = conn.cursor()
        schema = _schema_name()
        shadow_sql = (
            rendered_sql
            .replace(f"{schema}.memory", "pg_temp.memory")
            .replace(f"{schema}.role_memory", "pg_temp.role_memory")
        )
        cur.execute("CREATE TEMP TABLE memory (LIKE tackle.memory INCLUDING ALL)")
        cur.execute("CREATE TEMP TABLE role_memory (LIKE tackle.role_memory INCLUDING ALL)")
        cur.execute(shadow_sql)  # DO block — no params → simple query protocol
        conn.commit()

        def rows(q: str):
            cur.execute(q)
            return [r[0] for r in cur.fetchall()]

        result = {
            "mismatches": rows(
                "SELECT m.slug FROM pg_temp.memory m JOIN tackle.memory l ON m.slug = l.slug "
                "WHERE m.title IS DISTINCT FROM l.title OR m.summary IS DISTINCT FROM l.summary "
                "OR m.body_md IS DISTINCT FROM l.body_md OR m.tags IS DISTINCT FROM l.tags "
                "OR m.triggers IS DISTINCT FROM l.triggers OR m.mcp_tools IS DISTINCT FROM l.mcp_tools "
            ),
            "missing": rows(
                "SELECT l.slug FROM tackle.memory l "
                "LEFT JOIN pg_temp.memory m ON m.slug = l.slug WHERE m.slug IS NULL "
            ),
            "extra": rows(
                "SELECT m.slug FROM pg_temp.memory m "
                "LEFT JOIN tackle.memory l ON m.slug = l.slug WHERE l.slug IS NULL "
            ),
            "role_mismatch": rows(
                "SELECT m.slug FROM pg_temp.memory m "
                "WHERE (SELECT array_agg(role ORDER BY role) FROM pg_temp.role_memory r "
                "       WHERE r.memory_id = m.id) "
                "IS DISTINCT FROM "
                "(SELECT array_agg(role ORDER BY role) FROM tackle.role_memory r "
                " WHERE r.memory_id = (SELECT id FROM tackle.memory WHERE slug = m.slug)) "
            ),
            "seeded_slugs": set(rows("SELECT slug FROM pg_temp.memory")),
            "live_slugs": set(rows("SELECT slug FROM tackle.memory")),
        }
        for label, q in (
            ("seeded_cards", "SELECT count(*) FROM pg_temp.memory"),
            ("live_cards", "SELECT count(*) FROM tackle.memory"),
            ("seeded_roles", "SELECT count(*) FROM pg_temp.role_memory"),
            ("live_roles", "SELECT count(*) FROM tackle.role_memory"),
        ):
            cur.execute(q)
            result[label] = cur.fetchone()[0]
        return result
    finally:
        conn.close()


def _scratch_seed(rendered_sql: str) -> dict:
    """Execute the rendered seed against a scratch schema with NO live tables.

    Creates tackle.memory + tackle.role_memory from scratch (DDL embedded
    below, mirroring tackle-srv/src/db.ts), runs the DO block, and returns
    the seeded state keyed for manifest comparison: {"seeded_cards",
    "seeded_roles", "by_slug": {slug: {"sha256", "roles"}}}.

    This is the CI path — it needs only ANY reachable Postgres (a scratch
    schema in a throwaway DB), not the live tackle schema.
    """
    conn = _db()
    try:
        cur = conn.cursor()
        schema = _schema_name()
        shadow_sql = (
            rendered_sql
            .replace(f"{schema}.memory", "pg_temp.memory")
            .replace(f"{schema}.role_memory", "pg_temp.role_memory")
        )
        # Minimal faithful mirror of the live DDL (tackle-srv/src/db.ts):
        # memory + role_memory with the FK, PK, UNIQUE and the btree_gist
        # EXCLUDE constraint so role-assignment semantics match live.
        cur.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        # Shape consumed from the canonical fragment (single source of truth,
        # V178-ratified) — rendered for pg_temp so the shadow-seed world uses
        # temp tables. DO NOT restate the DDL inline (parity test enforces).
        frag = _CANONICAL_SHAPE_PATH.read_text()
        cur.execute(
            frag.replace("__SCHEMA__", "pg_temp")
                .replace("__REFTABLE__", "pg_temp.memory")
                .replace("__TABLE_SUFFIX__", "")
        )
        cur.execute(shadow_sql)  # DO block — no params → simple query protocol
        conn.commit()

        cur.execute(
            "SELECT slug, title, summary, body_md, tags, triggers, mcp_tools "
            "FROM pg_temp.memory ORDER BY slug"
        )
        by_slug = {}
        for slug, title, summary, body_md, tags, triggers, mcp_tools in cur.fetchall():
            cur.execute(
                "SELECT role FROM pg_temp.role_memory rm "
                "JOIN pg_temp.memory m ON rm.memory_id = m.id "
                "WHERE m.slug = %s ORDER BY role",
                (slug,),
            )
            roles = [r[0] for r in cur.fetchall()]
            by_slug[slug] = {
                "sha256": card_sha256(slug, title, summary, body_md, tags, triggers, mcp_tools),
                "roles": roles,
            }
        cur.execute("SELECT count(*) FROM pg_temp.memory")
        seeded_cards = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_temp.role_memory")
        seeded_roles = cur.fetchone()[0]
        return {"seeded_cards": seeded_cards, "seeded_roles": seeded_roles, "by_slug": by_slug}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════
#  AC1 — Render integrity: source → node render → clean shadow execution
# ═══════════════════════════════════════════════════════════════════════

class TestAc1RenderIntegrity(unittest.TestCase):
    """The source renders to executable SQL and runs cleanly against shadow tables."""

    def test_source_file_locatable(self):
        """The shared seed source exists and contains the function + template."""
        self.assertTrue(os.path.exists(SEED_SOURCE), f"missing: {SEED_SOURCE}")
        src = open(SEED_SOURCE, encoding="utf-8").read()
        self.assertIn("function seedMemoryProcedures", src)
        self.assertIn("return `", src)
        # The seed lives ONLY in the shared package — no stale copies remain.
        for dup in ("tackle-srv/src/db.ts", "tackle-mcp/src/db.ts"):
            dup_path = os.path.join(_REPO_ROOT, "typescript", dup)
            dup_src = open(dup_path, encoding="utf-8").read()
            self.assertNotIn(
                "function seedMemoryProcedures", dup_src,
                f"stale seed copy still in {dup} — extract to tackle-seeds first",
            )

    def test_render_produces_executable_do_block(self):
        """Rendered SQL is a DO block with one INSERT per live card."""
        sql = _render_seed()
        self.assertTrue(
            sql.lstrip().startswith("DO $mem$"),
            "rendered SQL must be a tagged DO block ($mem$ — bare $$ dies on bodies quoting '$$...$$')",
        )
        self.assertIn("END $mem$;", sql)
        self.assertIn("RAISE NOTICE 'Memory procedures seeded.';", sql)
        schema = _schema_name()
        # One INSERT per live card — derived from the DB so the guard never
        # rots as cards are added (a removed card still breaks this equality).
        conn = _db()
        try:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM tackle.memory")
            live_count = c.fetchone()[0]
        finally:
            conn.close()
        memory_inserts = sql.count(
            f"INSERT INTO {schema}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)"
        )
        self.assertEqual(memory_inserts, live_count,
                         f"expected {live_count} memory inserts, got {memory_inserts}")
        self.assertEqual(sql.count("ON CONFLICT (slug) DO NOTHING"), live_count)
        # The role_memory insert is a single statement inside the per-card loop.
        self.assertIn(
            f"INSERT INTO {schema}.role_memory (memory_id, role, as_of_dt, expiration_dt)", sql
        )

    def test_shadow_seed_executes_cleanly(self):
        """The DO block runs in a temp schema with live-matching counts."""
        result = _shadow_seed(_render_seed())
        self.assertEqual(result["seeded_cards"], result["live_cards"],
                         "seeded card count must match live")
        self.assertEqual(result["seeded_roles"], result["live_roles"],
                         "seeded role rows must match live")
        self.assertGreaterEqual(result["seeded_cards"], 1)
        self.assertGreaterEqual(result["seeded_roles"], 1)

    def test_built_dist_artifact_matches_live(self):
        """The BUILT dist (what runtime actually executes) is byte-identical too.

        dist/index.js is gitignored (heartbeat-client precedent) and may be
        absent on a fresh clone — skip in that case. When present, a source
        edit that was never rebuilt is caught here (runtime would otherwise
        serve the stale seed).
        """
        dist = os.path.join(os.path.dirname(SEED_SOURCE), "dist", "index.js")
        if not os.path.exists(dist):
            self.skipTest("tackle-seeds dist not built (gitignored) — "
                          "run bin/regenerate_memory_seed.py or npm run build")
        result = _shadow_seed(_render_seed(dist))
        self.assertEqual(result["mismatches"], [],
                         f"built dist cards differ from live: {result['mismatches']}")
        self.assertEqual(result["missing"], [], "built dist misses live cards")
        self.assertEqual(result["role_mismatch"], [],
                         f"built dist role sets differ from live: {result['role_mismatch']}")


# ═══════════════════════════════════════════════════════════════════════
#  AC2 — Card byte-identity: every seeded card matches live, nothing extra
# ═══════════════════════════════════════════════════════════════════════

class TestAc2CardByteIdentity(unittest.TestCase):
    """Every seeded card row byte-matches the live table."""

    def test_no_card_mismatches(self):
        result = _shadow_seed(_render_seed())
        self.assertEqual(
            result["mismatches"], [],
            f"cards whose seeded row differs from live: {result['mismatches']}",
        )

    def test_no_missing_or_extra_cards(self):
        result = _shadow_seed(_render_seed())
        self.assertEqual(result["missing"], [], "live cards absent from the seed")
        self.assertEqual(result["extra"], [], "seeded cards absent from live")

    def test_known_cards_present(self):
        """The historically-missing cards are actually produced by the seed."""
        result = _shadow_seed(_render_seed())
        for slug in KNOWN_LIVE_ONLY_CARDS:
            self.assertIn(
                slug, result["seeded_slugs"],
                f"historically-missing card not seeded: {slug}",
            )
            self.assertIn(
                slug, result["live_slugs"],
                f"test assumption broken: {slug} not in live table",
            )


# ═══════════════════════════════════════════════════════════════════════
#  AC3 — Role byte-identity: per-card role sets match live
# ═══════════════════════════════════════════════════════════════════════

class TestAc3RoleByteIdentity(unittest.TestCase):
    """Per-card role_memory associations match live."""

    def test_role_sets_match_per_card(self):
        result = _shadow_seed(_render_seed())
        self.assertEqual(
            result["role_mismatch"], [],
            f"cards whose seeded role set differs from live: {result['role_mismatch']}",
        )

    def test_every_card_has_at_least_one_role(self):
        """No seeded card is left without role_memory associations."""
        conn = _db()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT count(*) FROM tackle.memory m "
                "WHERE NOT EXISTS (SELECT 1 FROM tackle.role_memory r WHERE r.memory_id = m.id)"
            )
            orphaned = c.fetchone()[0]
            self.assertEqual(orphaned, 0, "live table has cards with no roles")
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
#  AC4 — Escape conventions hold structurally in the source
# ═══════════════════════════════════════════════════════════════════════

class TestAc4EscapeConventions(unittest.TestCase):
    """Static probes on the template body encoding the escaping contract."""

    def test_only_sql_interpolation_allowed(self):
        body = _template_body()
        interps = re.findall(r"\$\{[^}]*\}", body)
        bad = [m for m in interps if m != "${SQL}"]
        self.assertEqual(
            bad, [],
            f"template must only interpolate ${{SQL}}, found: {bad}",
        )

    def test_no_backslash_quote_rendering_bare_quote(self):
        """A backslash-quote in the template renders to a BARE quote in SQL
        (JS template literals eat the backslash) — terminating the string
        literal. This is the corruption class fixed in commit 4ba52cc.

        Note: the regex is intentionally narrow — it misses the even-run
        case (\\' -> renders \\' in SQL, also broken), which is caught by the
        dynamic shadow-execution guard (AC1). The static probe is a fast-fail
        nicety, not the backstop.
        """
        body = _template_body()
        bad = re.findall(r"(?<!\\)\\'(?!')", body)
        self.assertEqual(bad, [], f"backslash-quote patterns that break SQL: {bad}")

    def test_no_raw_backticks_inside_template(self):
        """Every backtick inside the template is escaped (\\`). A raw one
        would terminate the template literal early and break the file.
        """
        body = _template_body()
        raw = 0
        i = 0
        while i < len(body):
            if body[i] == "`":
                bs = 0
                j = i - 1
                while j >= 0 and body[j] == "\\":
                    bs += 1
                    j -= 1
                if bs % 2 == 0:
                    raw += 1
            i += 1
        self.assertEqual(raw, 0, "raw (unescaped) backtick inside the seed template")

    def test_escaping_is_in_use(self):
        """The conventions are exercised: escaped backticks and doubled
        apostrophes must actually appear (a seed with none would indicate the
        escaping path is dead).
        """
        body = _template_body()
        self.assertGreaterEqual(body.count("\\`"), 1, "no escaped backticks found")
        self.assertGreaterEqual(body.count("''"), 1, "no doubled apostrophes found")


# ═══════════════════════════════════════════════════════════════════════
#  AC5 — Manifest guard (CI): scratch-schema seed vs committed manifest
# ═══════════════════════════════════════════════════════════════════════

class TestAc5ManifestGuard(unittest.TestCase):
    """The rendered seed executed against a scratch schema must byte-match the
    committed seed-manifest.json (the no-live-DB reference). This is the CI
    path — CI has no live tackle.memory, only the manifest."""

    def test_manifest_committed_and_parseable(self):
        """The manifest exists, parses, and is internally consistent."""
        self.assertTrue(os.path.exists(MANIFEST_PATH), f"missing: {MANIFEST_PATH}")
        manifest = read_manifest()
        self.assertIsInstance(manifest.get("card_count"), int)
        self.assertIsInstance(manifest.get("role_count"), int)
        self.assertEqual(manifest["card_count"], len(manifest.get("cards", [])))
        by_slug = {c["slug"] for c in manifest["cards"]}
        self.assertEqual(len(by_slug), manifest["card_count"], "duplicate slugs in manifest")
        for c in manifest["cards"]:
            self.assertRegex(c["slug"], r"^[a-z0-9-]+$")
            self.assertRegex(c["sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(c.get("roles"), list)
            # Full-content snapshot: every field needed to reconstruct the
            # schema (CI bootstrap) must be present.
            for field in ("title", "summary", "body_md", "tags", "triggers", "mcp_tools"):
                self.assertIn(field, c, f"{c['slug']}: manifest missing '{field}' content field")

    def test_manifest_content_self_consistent(self):
        """Each stored sha256 equals sha256 of its embedded content — a
        hand-edit to the manifest that changed content without recomputing the
        hash (a lying reference) is caught here."""
        ok, problems = manifest_self_consistent(read_manifest())
        self.assertTrue(ok, "manifest content/hash mismatch:\n  " + "\n  ".join(problems))

    def test_bootstrap_roundtrip_reconstructs_manifest(self):
        """apply_manifest (the CI bootstrap) into a scratch schema, then
        build_manifest from it, must reproduce the committed manifest exactly —
        the full-content snapshot round-trips through a real schema."""
        manifest = read_manifest()
        conn = _db()
        try:
            schema = "seed_roundtrip"
            cur = conn.cursor()
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()
            try:
                apply_manifest(conn, manifest, schema=schema, reset=True)
                conn.commit()
                rebuilt = build_manifest(conn, schema=schema)
                self.assertEqual(rebuilt["card_count"], manifest["card_count"])
                self.assertEqual(rebuilt["role_count"], manifest["role_count"])
                rebuilt_by_slug = {c["slug"]: c for c in rebuilt["cards"]}
                for card in manifest["cards"]:
                    rb = rebuilt_by_slug.get(card["slug"])
                    self.assertIsNotNone(rb, f"{card['slug']} missing after round-trip")
                    for field in ("title", "summary", "body_md", "tags", "triggers", "mcp_tools", "sha256", "roles"):
                        self.assertEqual(
                            rb[field], card[field],
                            f"{card['slug']}: {field} differs after round-trip",
                        )
            finally:
                cur = conn.cursor()
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                conn.commit()
        finally:
            conn.close()

    def test_scratch_seed_card_count_matches_manifest(self):
        """Scratch execution seeds exactly the manifest's card count."""
        result = _scratch_seed(_render_seed())
        manifest = read_manifest()
        self.assertEqual(
            result["seeded_cards"], manifest["card_count"],
            "seeded card count must match the committed manifest "
            "(a hand-edit to the seed that added/removed a card breaks this)",
        )

    def test_scratch_seed_role_count_matches_manifest(self):
        """Scratch execution seeds exactly the manifest's role-row count."""
        result = _scratch_seed(_render_seed())
        manifest = read_manifest()
        self.assertEqual(
            result["seeded_roles"], manifest["role_count"],
            "seeded role rows must match the committed manifest",
        )

    def test_scratch_seed_card_hashes_match_manifest(self):
        """Every seeded card's content sha256 matches the manifest — byte
        identity of slug/title/summary/body_md/tags/triggers/mcp_tools."""
        result = _scratch_seed(_render_seed())
        manifest = read_manifest()
        manifest_by_slug = {c["slug"]: c for c in manifest["cards"]}
        mismatched = []
        for slug, state in result["by_slug"].items():
            expected = manifest_by_slug.get(slug)
            if expected is None:
                mismatched.append(f"{slug}: in seed, not in manifest")
            elif expected["sha256"] != state["sha256"]:
                mismatched.append(f"{slug}: content hash differs from manifest")
        for slug in manifest_by_slug:
            if slug not in result["by_slug"]:
                mismatched.append(f"{slug}: in manifest, missing from seed")
        self.assertEqual(mismatched, [], "seed content drifted from the committed manifest")

    def test_scratch_seed_role_sets_match_manifest(self):
        """Per-card role sets match the manifest exactly."""
        result = _scratch_seed(_render_seed())
        manifest = read_manifest()
        manifest_by_slug = {c["slug"]: c for c in manifest["cards"]}
        mismatched = []
        for slug, state in result["by_slug"].items():
            expected = manifest_by_slug.get(slug)
            if expected is None:
                continue  # covered by the hash test
            if expected["roles"] != state["roles"]:
                mismatched.append(f"{slug}: roles {state['roles']} != manifest {expected['roles']}")
        self.assertEqual(mismatched, [], "role sets drifted from the committed manifest")

    def test_manifest_matches_live_when_reachable(self):
        """Locally (live DB reachable) the committed manifest must equal live.

        This is the double-check that the CI reference wasn't regenerated stale:
        the manifest is the projection CI trusts, so it must match the canonical
        DB exactly. Skips when the live tackle schema is absent (CI)."""
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'tackle' AND table_name = 'memory'"
            )
            if cur.fetchone() is None:
                self.skipTest("live tackle.memory not present (CI) — manifest-vs-live check skipped")
            ok, problems = manifest_matches_live(conn)
            self.assertTrue(ok, "committed manifest differs from live:\n  " + "\n  ".join(problems))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
