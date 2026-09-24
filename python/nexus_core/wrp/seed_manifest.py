"""
seed_manifest.py — shared helpers for the tackle-seeds seed manifest.

The committed manifest (typescript/tackle-seeds/seed-manifest.json) is a
deterministic snapshot of the CANONICAL live DB state (tackle.memory +
tackle.role_memory): the card count, the role count, and per-card sha256
hashes plus role sets. It is emitted by bin/regenerate_memory_seed.py from
the live DB (the DB is canonical; the seed and the manifest are both derived
projections of it) and consumed by the wr-conf-006 conformance guard
(TestAc5ManifestGuard) so CI can detect seed drift WITHOUT a live
tackle.memory: the seed is rendered from source, executed against a scratch
schema, and byte-compared against this committed manifest.

The hashing convention lives HERE so the generator and the test can never
drift from each other on what "matches" means.

Usage:
    from nexus_core.wrp.seed_manifest import card_sha256, build_manifest, read_manifest
"""

import hashlib
import json
import os

# typescript/tackle-seeds/seed-manifest.json relative to the nexus/ repo root.
# This module lives at nexus/python/nexus_core/wrp/seed_manifest.py, so the
# repo root is three levels up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
MANIFEST_PATH = os.path.join(
    _REPO_ROOT, "typescript", "tackle-seeds", "seed-manifest.json"
)


def card_sha256(slug: str, title: str, summary: str, body_md: str,
                tags, triggers, mcp_tools) -> str:
    """Deterministic sha256 of a card's full content (the byte-identity key).

    Tags/triggers/mcp_tools are sorted so ordering differences in the DB array
    vs the seed ARRAY[...] render do not cause false positives — the CONTENT
    set is what must match, not array element order.
    """
    # Lists are pre-sorted for order-independence; json.dumps over a list does
    # not reorder (sort_keys only affects object keys), so no sort_keys here.
    canon = json.dumps(
        [
            slug,
            title,
            summary or "",
            body_md or "",
            sorted(tags or []),
            sorted(triggers or []),
            sorted(mcp_tools or []),
        ]
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build_manifest(conn, schema: str = "tackle") -> dict:
    """Read the canonical DB state and build the manifest dict.

    `conn` is a psycopg2 connection with access to the tackle schema.
    Returns a manifest with keys: schema, card_count, role_count, cards.
    Each card carries the FULL content (slug/title/summary/body_md/tags/
    triggers/mcp_tools) plus its sha256 and roles:[...sorted] — the manifest is
    a self-contained snapshot from which the schema can be reconstructed
    (apply_manifest), which is what CI uses to bootstrap a scratch DB and run
    the AC1-AC4 live-compare tests.
    """
    cur = conn.cursor()
    cur.execute(
        f"""SELECT slug, title, summary, body_md, tags, triggers, mcp_tools
           FROM {schema}.memory ORDER BY slug"""
    )
    cards = []
    role_count = 0
    for slug, title, summary, body_md, tags, triggers, mcp_tools in cur.fetchall():
        cur.execute(
            f"""SELECT DISTINCT role FROM {schema}.role_memory
               WHERE memory_id = (SELECT id FROM {schema}.memory WHERE slug = %s)
               ORDER BY role""",
            (slug,),
        )
        roles = [r[0] for r in cur.fetchall()]
        role_count += len(roles)
        cards.append(
            {
                "slug": slug,
                "title": title,
                "summary": summary or "",
                "body_md": body_md or "",
                "tags": list(tags or []),
                "triggers": list(triggers or []),
                "mcp_tools": list(mcp_tools or []),
                "sha256": card_sha256(
                    slug, title, summary, body_md, tags, triggers, mcp_tools
                ),
                "roles": roles,
            }
        )
    return {
        "schema": schema,
        "card_count": len(cards),
        "role_count": role_count,
        "cards": cards,
    }


# DDL consumed from the canonical fragment (sql/canonical/
# tackle_role_memory_shape.sql) — the single source of truth for the
# memory/role_memory reconstruction shape (V178-ratified). Do NOT restate
# the shape inline; bin/tests/test_canonical_shape_parity.py enforces
# fragment-consumption and shape parity across all surfaces.
from pathlib import Path as _Path

_CANONICAL_FRAGMENT = (
    _Path(__file__).resolve().parents[3]
    / "sql" / "canonical" / "tackle_role_memory_shape.sql"
)


def _load_shape_sql(schema: str) -> str:
    """Render the canonical shape fragment for the target schema."""
    sql = _CANONICAL_FRAGMENT.read_text()
    return (sql.replace("__SCHEMA__", schema)
               .replace("__REFTABLE__", f"{schema}.memory")
               .replace("__TABLE_SUFFIX__", ""))


def apply_manifest(conn, manifest: dict, schema: str = "tackle", reset: bool = True):
    """Reconstruct the tackle schema from a manifest (the CI bootstrap).

    Creates {schema}.memory + {schema}.role_memory and inserts every card and
    role row from the manifest. With reset=True (default) the two tables are
    dropped first (idempotent re-bootstrap — a re-run without reset would hit
    the slug UNIQUE constraint); only the seed tables are touched — never
    other tables in the schema. Commit is left to the caller (pass autocommit
    or commit after). Returns {"cards": n, "roles": m} inserted.
    """
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    cur.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    if reset:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.role_memory CASCADE")
        cur.execute(f"DROP TABLE IF EXISTS {schema}.memory CASCADE")
    # Use replace() not .format(): the DDL contains literal '{}' array
    # defaults which .format() would try to interpolate.
    cur.execute(_load_shape_sql(schema))
    role_rows = 0
    for card in manifest["cards"]:
        cur.execute(
            f"""INSERT INTO {schema}.memory
               (slug, title, summary, body_md, tags, triggers, mcp_tools)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                card["slug"], card["title"], card["summary"] or "",
                card["body_md"] or "",
                list(card.get("tags") or []),
                list(card.get("triggers") or []),
                list(card.get("mcp_tools") or []),
            ),
        )
        memory_id = cur.fetchone()[0]
        for role in card.get("roles") or []:
            cur.execute(
                f"""INSERT INTO {schema}.role_memory (memory_id, role, as_of_dt, expiration_dt)
                   VALUES (%s, %s, NOW(), NULL)""",
                (memory_id, role),
            )
            role_rows += 1
    return {"cards": len(manifest["cards"]), "roles": role_rows}


def manifest_self_consistent(manifest: dict) -> tuple:
    """Verify every card's stored sha256 matches its embedded content.

    Guards against a hand-edited manifest whose content was changed without
    recomputing the hash (the hash tests would then be comparing against a
    lying reference). Returns (ok, [problem strings]).
    """
    problems = []
    for card in manifest.get("cards", []):
        got = card_sha256(
            card["slug"], card.get("title", ""), card.get("summary", ""),
            card.get("body_md", ""), card.get("tags"), card.get("triggers"),
            card.get("mcp_tools"),
        )
        if got != card.get("sha256"):
            problems.append(f"{card['slug']}: stored sha256 != sha256 of embedded content")
    return (not problems, problems)


def write_manifest(manifest: dict, path: str = MANIFEST_PATH) -> None:
    """Write the manifest deterministically (sorted keys, stable formatting)."""
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)


def read_manifest(path: str = MANIFEST_PATH) -> dict:
    """Load the committed manifest."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def manifest_matches_live(conn) -> tuple:
    """Compare the committed manifest against live. Returns (ok, detail).

    detail is a human-readable list of what differs (empty when ok).
    """
    committed = read_manifest()
    live = build_manifest(conn)
    problems = []
    if committed.get("card_count") != live["card_count"]:
        problems.append(
            f"card_count: manifest={committed.get('card_count')} live={live['card_count']}"
        )
    if committed.get("role_count") != live["role_count"]:
        problems.append(
            f"role_count: manifest={committed.get('role_count')} live={live['role_count']}"
        )
    live_by_slug = {c["slug"]: c for c in live["cards"]}
    committed_by_slug = {c["slug"]: c for c in committed.get("cards", [])}
    for slug in sorted(set(live_by_slug) | set(committed_by_slug)):
        if slug not in live_by_slug:
            problems.append(f"card only in manifest: {slug}")
            continue
        if slug not in committed_by_slug:
            problems.append(f"card missing from manifest: {slug}")
            continue
        lc, cc = live_by_slug[slug], committed_by_slug[slug]
        if lc["sha256"] != cc.get("sha256"):
            problems.append(f"content hash differs: {slug}")
        if lc["roles"] != cc.get("roles"):
            problems.append(f"role set differs: {slug}")
    return (not problems, problems)
