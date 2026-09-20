#!/usr/bin/env python3
r"""
regenerate_memory_seed.py — Regenerate seedMemoryProcedures() card bodies from
the live tackle.memory table (the DB is canonical).

A fresh-DB bootstrap was populating stale ~June card content because the seed's
committed SQL had drifted from the live cards (the seed was non-executable for
months and cards were iteratively updated by scripts since). This script
re-renders every card — slug, title, summary, body_md, tags, triggers,
mcp_tools — plus its live role_memory associations, into the
seedMemoryProcedures() template literal of the shared seed package:

    typescript/tackle-seeds/index.ts

consumed by both tackle-srv and tackle-mcp (single source of truth, so seed
edits never need to be applied twice by hand).

Escaping conventions (established by commit 4ba52cc; see agent record
93785aab — these are proven by the shadow-seed byte-compare):

  * SQL string literals are PLAIN '...' literals, never E-strings, so a
    backslash has no special meaning in the body:
      - apostrophe  '  -> ''        (SQL doubling)
  * The whole DO block lives inside ONE JS template literal, so additionally:
      - backslash   \\ -> \\      (renders as one backslash)
      - backtick    `  -> \`
      - ${          -> \${        (no accidental interpolation)
      - real newline -> \n escape  (renders as a real newline inside the
                                    SQL literal)
  * ${SQL} is emitted verbatim — it is the seed's one intentional
    interpolation (the function defines const SQL = `tackle`).

Usage:
    python3 bin/regenerate_memory_seed.py [--dry-run] [--verify]

    --dry-run  report what would change without writing files
    --verify   after writing, render each seed with Node, shadow-seed into
               pg_temp tables inside a rolled-back transaction, and
               byte-compare every card (title/summary/body/tags/triggers/
               mcp_tools + role set) against the live tackle.memory table.
               Requires `node` and a reachable local Postgres.

Env: CONDUIT_PG_DSN  (default postgresql://pguser:pgpass@localhost:5432/nexus)
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # nexus/
SEED_PACKAGE_DIR = os.path.join(REPO, "typescript", "tackle-seeds")
SEED_FILES = [
    os.path.join(SEED_PACKAGE_DIR, "index.ts"),
]

# Shared manifest helpers (hashing convention must match wr-conf-006 exactly).
sys.path.insert(0, os.path.join(REPO, "python"))
from nexus_core.wrp.seed_manifest import (  # noqa: E402
    MANIFEST_PATH,
    build_manifest,
    manifest_matches_live,
    write_manifest,
)

MANIFEST_FILES = [MANIFEST_PATH]

HEADER = """DO $$
DECLARE
    v_memory_id UUID;
    v_role TEXT;
    v_roles TEXT[];
BEGIN
"""

FOOTER = """    RAISE NOTICE 'Memory procedures seeded.';
END $$;"""


# ── escaping ────────────────────────────────────────────────────────────────


def esc(s: str) -> str:
    """Escape content for a plain SQL '...' literal inside the JS template literal.

    Order matters: backslash doubling MUST precede the ${} guard (otherwise a
    literal backslash followed by ${ would get double-escaped and render wrong).
    """
    s = s.replace("\\", "\\\\")  # JS template literal: backslash
    s = s.replace("'", "''")  # SQL string literal: apostrophe doubling
    s = s.replace("`", "\\`")  # JS template literal: backtick
    s = s.replace("${", "\\${")  # JS template literal: no interpolation
    return s


def render_text(value: str) -> str:
    """Render a text column as adjacent plain SQL literals, one per line.

    The last literal carries the trailing comma (closes the column). Real
    newlines are emitted as the \\n escape, which the JS template literal
    renders back to a real newline inside the SQL string.
    """
    value = value or ""
    parts = esc(value).split("\n")
    out = []
    for i, ln in enumerate(parts):
        if i < len(parts) - 1:
            out.append(f"        '{ln}\\n'")
        else:
            out.append(f"        '{ln}',")
    return "\n".join(out)


def render_array(values) -> str:
    """Render a text[] column as ARRAY[...] (or '{}' when empty)."""
    if not values:
        return "'{}'"
    inner = ", ".join("'" + esc(v) + "'" for v in values)
    return "ARRAY[" + inner + "]"


# ── generation ──────────────────────────────────────────────────────────────


def render_card(idx: int, card: dict) -> str:
    b = []
    b.append("")
    b.append("    -- " + "─" * 58)
    b.append(f"    -- {idx:2d}. {card['title']}")
    b.append("    -- " + "─" * 58)
    b.append("    v_memory_id := NULL;")
    b.append("    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)")
    b.append("    VALUES (")
    b.append(f"        '{esc(card['slug'])}',")
    b.append(f"        '{esc(card['title'])}',")
    b.append(render_text(card["summary"]))
    b.append(render_text(card["body_md"]))
    b.append(f"        {render_array(card['tags'])},")
    b.append(f"        {render_array(card['triggers'])},")
    b.append(f"        {render_array(card['mcp_tools'])}")
    b.append("    )")
    b.append("    ON CONFLICT (slug) DO NOTHING")
    b.append("    RETURNING id INTO v_memory_id;")
    b.append("    IF v_memory_id IS NOT NULL THEN")
    roles = card.get("roles") or []
    if roles:
        role_list = ", ".join("'" + esc(r) + "'" for r in roles)
        b.append(f"        v_roles := ARRAY[{role_list}];")
    else:
        b.append("        v_roles := ARRAY[]::TEXT[];")
    b.append("        FOREACH v_role IN ARRAY v_roles LOOP")
    b.append("            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)")
    b.append("            VALUES (v_memory_id, v_role, NOW(), NULL);")
    b.append("        END LOOP;")
    b.append("    END IF;")
    return "\n".join(b)


def generate_seed_body(cards) -> str:
    parts = [HEADER]
    for idx, c in enumerate(cards, 1):
        parts.append(render_card(idx, c))
    parts.append("\n" + FOOTER)
    return "".join(parts)


# ── file surgery ────────────────────────────────────────────────────────────


def locate_seed_template(src: str):
    """Return (open_backtick_idx, close_backtick_idx) of the seed template.

    open_backtick_idx points AT the backtick that opens the template literal
    (the one in `return `); slice ranges are [open, close) so the replacement
    body goes between them and the `return ` prefix survives intact.
    """
    fn = src.find("function seedMemoryProcedures")
    if fn == -1:
        raise SystemExit("seedMemoryProcedures not found in file")
    open_tick = src.index("return `", fn) + 7  # position of the backtick itself
    i = open_tick + 1
    while i < len(src):
        if src[i] == "`":
            bs = 0
            j = i - 1
            while src[j] == "\\":
                bs += 1
                j -= 1
            if bs % 2 == 0:  # unescaped backtick
                return open_tick, i
        i += 1
    raise SystemExit("could not find the closing backtick of the seed template")


def splice_seed(src: str, new_body: str) -> str:
    open_tick, close_tick = locate_seed_template(src)
    return src[: open_tick + 1] + new_body + src[close_tick:]


def seed_slug_order(src: str):
    open_tick, close_tick = locate_seed_template(src)
    body = src[open_tick + 1 : close_tick]
    return re.findall(r"VALUES \(\n        '([a-z0-9-]+)'", body)


# ── data ────────────────────────────────────────────────────────────────────


def load_cards(conn):
    cur = conn.cursor()
    cur.execute(
        """SELECT slug, title, summary, body_md, tags, triggers, mcp_tools
           FROM tackle.memory ORDER BY slug"""
    )
    cards = []
    for slug, title, summary, body_md, tags, triggers, mcp_tools in cur.fetchall():
        cards.append(
            {
                "slug": slug,
                "title": title,
                "summary": summary or "",
                "body_md": body_md or "",
                "tags": tags or [],
                "triggers": triggers or [],
                "mcp_tools": mcp_tools or [],
            }
        )
    for c in cards:
        cur.execute(
            """SELECT DISTINCT role FROM tackle.role_memory
               WHERE memory_id = (SELECT id FROM tackle.memory WHERE slug = %s)
               ORDER BY role""",
            (c["slug"],),
        )
        c["roles"] = [r[0] for r in cur.fetchall()]
    return cards


def order_cards(cards, seed_slugs):
    """Preserve the current seed's curated card order; append live-only cards."""
    by_slug = {c["slug"]: c for c in cards}
    ordered, seen = [], set()
    for s in seed_slugs:
        if s in by_slug and s not in seen:
            ordered.append(by_slug[s])
            seen.add(s)
    for c in cards:
        if c["slug"] not in seen:
            ordered.append(c)
    return ordered


# ── verification (Node render + shadow-seed byte-compare) ───────────────────

RENDER_MJS = r"""import fs from 'node:fs';
const src = fs.readFileSync(process.argv[2], 'utf8');
const fn = src.indexOf('function seedMemoryProcedures');
let open = -1;
let searchFrom = fn;
while (true) {
  const i = src.indexOf('return `', searchFrom);
  if (i === -1) break;
  const tick = src.indexOf('`', i + 7);
  if (src.slice(tick + 1, tick + 40).trimStart().startsWith('DO $$')) { open = tick; break; }
  searchFrom = i + 1;
}
if (open === -1) { console.error('no seed template'); process.exit(1); }
let close = -1;
for (let i = open + 1; i < src.length; i++) {
  if (src[i] === '`') {
    let bs = 0; let j = i - 1;
    while (src[j] === '\\') { bs++; j--; }
    if (bs % 2 === 0) { close = i; break; }
  }
}
const body = src.slice(open + 1, close);
const escaped = body.replace(/\$\{SQL\}/g, '\\${SQL}');
const templated = '`' + escaped + '`';
const rendered = eval(templated).replace(/\$\{SQL\}/g, 'tackle');
fs.writeFileSync(process.argv[3], rendered);
console.log('rendered bytes:', rendered.length);
"""


def verify_file(path: str, tag: str):
    with tempfile.TemporaryDirectory() as td:
        mjs = os.path.join(td, "render.mjs")
        with open(mjs, "w", encoding="utf-8") as f:
            f.write(RENDER_MJS)
        rendered = os.path.join(td, "seed.sql")
        r = subprocess.run(["node", mjs, path, rendered], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"Node render failed for {tag}:\n{r.stderr}")
        print(f"  [node render] {tag}: {r.stdout.strip()}")

        with open(rendered, encoding="utf-8") as f:
            sql = f.read()
        # Only the two seed tables are re-pointed at pg_temp; any mention of
        # tackle.* inside card body text must stay untouched.
        sql = sql.replace("tackle.memory", "pg_temp.memory").replace(
            "tackle.role_memory", "pg_temp.role_memory"
        )
        shadow = os.path.join(td, "seed-shadow.sql")
        with open(shadow, "w", encoding="utf-8") as f:
            f.write(sql)

        compare_sql = f"""BEGIN;
CREATE TEMP TABLE memory (LIKE tackle.memory INCLUDING ALL);
CREATE TEMP TABLE role_memory (LIKE tackle.role_memory INCLUDING ALL);
\\i {shadow}
SELECT 'MISMATCH', m.slug FROM pg_temp.memory m JOIN tackle.memory l ON m.slug = l.slug
WHERE m.title IS DISTINCT FROM l.title OR m.summary IS DISTINCT FROM l.summary
   OR m.body_md IS DISTINCT FROM l.body_md OR m.tags IS DISTINCT FROM l.tags
   OR m.triggers IS DISTINCT FROM l.triggers OR m.mcp_tools IS DISTINCT FROM l.mcp_tools;
SELECT 'MISSING_FROM_SEED', l.slug FROM tackle.memory l
LEFT JOIN pg_temp.memory m ON m.slug = l.slug WHERE m.slug IS NULL;
SELECT 'EXTRA_IN_SEED', m.slug FROM pg_temp.memory m
LEFT JOIN tackle.memory l ON m.slug = l.slug WHERE l.slug IS NULL;
SELECT 'ROLE_MISMATCH', m.slug FROM pg_temp.memory m
WHERE (SELECT array_agg(role ORDER BY role) FROM pg_temp.role_memory r WHERE r.memory_id = m.id)
   IS DISTINCT FROM
   (SELECT array_agg(role ORDER BY role) FROM tackle.role_memory r
     WHERE r.memory_id = (SELECT id FROM tackle.memory WHERE slug = m.slug));
SELECT 'SEEDED', count(*) FROM pg_temp.memory;
SELECT 'LIVE', count(*) FROM tackle.memory;
SELECT 'ROLES_SEEDED', count(*) FROM pg_temp.role_memory;
SELECT 'ROLES_LIVE', count(*) FROM tackle.role_memory;
ROLLBACK;
"""
        p = subprocess.run(
            ["psql", "-v", "ON_ERROR_STOP=1", DSN],
            input=compare_sql, capture_output=True, text=True,
        )
        if p.returncode != 0:
            print(p.stdout)
            print(p.stderr, file=sys.stderr)
            raise SystemExit(f"shadow-seed compare failed for {tag} (see output above)")
        failures = [
            line.strip()
            for line in p.stdout.splitlines()
            if re.match(r"^(MISMATCH|MISSING_FROM_SEED|EXTRA_IN_SEED|ROLE_MISMATCH)\s*\|", line)
        ]
        counts = {
            m.group(1): m.group(2)
            for line in p.stdout.splitlines()
            if (m := re.match(r"^\s*(SEEDED|LIVE|ROLES_SEEDED|ROLES_LIVE)\s*\|\s*(\d+)", line))
        }
        if failures:
            print(p.stdout)
            raise SystemExit(f"VERIFY FAILED for {tag}: {len(failures)} drift rows")
        print(f"  [shadow-compare] {tag}: OK — {counts}")
        print("  [shadow-compare] %s: %d card(s), %d role(s) seeded — byte-identical to live"
              % (tag, int(counts.get("SEEDED", 0)), int(counts.get("ROLES_SEEDED", 0))))


def build_tackle_seeds():
    """Rebuild tackle-seeds/dist after writing the seed.

    Runtime (tsx dev, node prod) resolves `tackle-seeds` to the built
    dist/index.js, so the seed is only actually served after this rebuild.
    Uses the package's own tsc if installed, else a sibling project's.
    """
    candidates = [
        os.path.join(SEED_PACKAGE_DIR, "node_modules", ".bin", "tsc"),
        os.path.join(REPO, "typescript", "tackle-srv", "node_modules", ".bin", "tsc"),
        os.path.join(REPO, "typescript", "tackle-mcp", "node_modules", ".bin", "tsc"),
    ]
    tsc = next((c for c in candidates if os.path.exists(c)), None)
    if tsc is None:
        print("  WARNING: tsc not found — tackle-seeds dist NOT rebuilt;"
              " rebuild it before starting tackle-srv/tackle-mcp")
        return
    r = subprocess.run([tsc, "-p", SEED_PACKAGE_DIR], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"tackle-seeds build failed:\n{r.stderr}")
    print("  [build] tackle-seeds dist rebuilt (runtime now serves the fresh seed)")


# ── main ────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate seedMemoryProcedures() from live tackle.memory")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--verify", action="store_true", help="shadow-seed byte-compare after writing")
    ap.add_argument("--exclude-slug", action="append", default=[],
                    help="drop a card slug from the regenerated seed + manifest "
                         "(repeatable). Use to keep the committed seed at a curated "
                         "subset when a live-only card is not yet seed-safe "
                         "(e.g. carries escaping that breaks dollar-quote execution).")
    args = ap.parse_args()

    import psycopg2

    conn = psycopg2.connect(DSN)
    try:
        cards = load_cards(conn)
    finally:
        conn.close()

    if args.exclude_slug:
        ex = set(args.exclude_slug)
        cards = [c for c in cards if c["slug"] not in ex]
        print(f"  --exclude-slug: dropped {sorted(ex)}")

    seed_slugs = seed_slug_order(open(SEED_FILES[0], encoding="utf-8").read())
    cards = order_cards(cards, seed_slugs)
    new_body = "\n" + generate_seed_body(cards)

    live_slugs = {c["slug"] for c in cards}
    print(f"live cards: {len(cards)}  | seed had: {len(seed_slugs)}  | generated body: {len(new_body)} chars")
    added = [c["slug"] for c in cards if c["slug"] not in set(seed_slugs)]
    if added:
        print(f"  cards ADDED (live-only): {', '.join(added)}")
    removed = [s for s in seed_slugs if s not in live_slugs]
    if removed:
        print(f"  cards REMOVED (not in DB): {', '.join(removed)}")

    for path in SEED_FILES:
        src = open(path, encoding="utf-8").read()
        o, c = locate_seed_template(src)
        old_len = c - o - 1
        rel = os.path.relpath(path, REPO)
        print(f"  {rel}: template body {old_len} -> {len(new_body)} chars")
        if not args.dry_run:
            open(path, "w", encoding="utf-8").write(splice_seed(src, new_body))
            print(f"    wrote")

    # The manifest is the second canonical projection: card count, role count,
    # per-card sha256 + role sets — consumed by wr-conf-006's CI guard as the
    # no-live-DB reference (see nexus_core/wrp/seed_manifest.py).
    if not args.dry_run:
        conn2 = psycopg2.connect(DSN)
        try:
            manifest = build_manifest(conn2)
        finally:
            conn2.close()
        if args.exclude_slug:
            ex = set(args.exclude_slug)
            manifest["cards"] = [c for c in manifest["cards"] if c["slug"] not in ex]
            manifest["card_count"] = len(manifest["cards"])
            manifest["role_count"] = sum(len(c["roles"]) for c in manifest["cards"])
        write_manifest(manifest)
        rel = os.path.relpath(MANIFEST_PATH, REPO)
        print(f"  {rel}: wrote ({manifest['card_count']} cards, "
              f"{manifest['role_count']} roles, {os.path.getsize(MANIFEST_PATH)} bytes)")

    if not args.dry_run:
        build_tackle_seeds()

    if args.verify:
        if args.dry_run:
            print("--verify is ignored with --dry-run")
        else:
            for path in SEED_FILES:
                tag = path.split(os.sep)[-2]  # e.g. "tackle-seeds"
                verify_file(path, tag)
            # Also verify the built artifact that services actually execute.
            dist_path = os.path.join(SEED_PACKAGE_DIR, "dist", "index.js")
            if os.path.exists(dist_path):
                verify_file(dist_path, tag + "-dist")
            # Manifest must be current against live too (CI guard reference).
            conn3 = psycopg2.connect(DSN)
            try:
                ok, problems = manifest_matches_live(conn3)
            finally:
                conn3.close()
            if ok:
                print("  [manifest] committed seed-manifest.json matches live")
            else:
                print("  [manifest] COMMITTED MANIFEST IS STALE:")
                for p in problems:
                    print(f"    - {p}")
                print("  (re-run without --verify to rewrite it, then re-verify)")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
