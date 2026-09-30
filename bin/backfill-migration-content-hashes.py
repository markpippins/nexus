#!/usr/bin/env python3
"""Backfill nebula.schema_version.content_hash for already-applied migrations.

Decision 23 (ruling 7f2b377a) property 2: the ledger records the content hash
of what was applied, so divergence is auditable from the database alone. The
forward runner (migrate.ts, this branch) stamps hashes from 069 onward; this
script fills the historical rows 042-068 from the committed attestation
manifest (migrations/attestations.json), the sha256 of each file at the
attested/merged bytes (provenance per DBA record 7c5fe000).

Caveat recorded there applies: a backfilled hash attests to TODAY's bytes —
only versions applied after forward stamping exists carry true apply-time
provenance. The backfill makes the ledger uniform and the audit query
workable; it does not retroactively create history that was never recorded.

Safety: DRY RUN BY DEFAULT; `--apply` mutates. Refuses to run if the
content_hash column does not exist (069 not applied yet). Refuses to touch a
row whose ledger hash already disagrees with the manifest — that is a
divergence FINDING, not a cleanup; only --force overwrites, loudly.

Connection: standard libpq env vars (PGHOST/PGPORT/PGUSER/PGDATABASE/
PGPASSWORD). The repo's .env is NOT read by this script; export PGPASSWORD
first, e.g.:
  export PGPASSWORD=$(grep -m1 '^GATEWAY_PG_PASSWORD=' nexus/.env | cut -d= -f2-)
  python3 bin/backfill-migration-content-hashes.py            # dry run
  python3 bin/backfill-migration-content-hashes.py --apply    # write

Uses the psql binary (no python PG driver required — deliberate: this must
run on any host that can operate the database at all).
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "typescript" / "nebula-srv" / "migrations"
MANIFEST = MIGRATIONS / "attestations.json"
FILE_RE = re.compile(r"^\d{3}-.*\.sql$")


def psql(sql: str, tuples: bool = True) -> str:
    """Run one psql -c, fail loudly on error, return stripped stdout.

    Tuple output uses `|` as the field separator, NOT psql's -F with a
    backslash escape: `-F '\t'` passes a literal backslash-t to libpq, which
    produced 4-field-looking lines and a parse crash (found in end-to-end
    testing). `|` is safe here: version is an integer, hashes are validated
    64-hex below, and descriptions containing `|` abort the run up front.
    """
    cmd = ["psql", "-X", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql] if tuples \
        else ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FATAL: psql failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill ledger content hashes (dry run by default)")
    ap.add_argument("--apply", action="store_true", help="actually UPDATE the ledger")
    ap.add_argument("--force", action="store_true",
                    help="overwrite non-NULL disagreeing content_hash values (prints divergence first)")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    files = sorted(p.name for p in MIGRATIONS.glob("*.sql") if FILE_RE.match(p.name))

    # Guard: 069 must be applied (column exists) before any of this makes sense.
    col = psql("SELECT 1 FROM information_schema.columns "
               "WHERE table_schema='nebula' AND table_name='schema_version' "
               "AND column_name='content_hash'")
    if col != "1":
        print("FATAL: nebula.schema_version.content_hash does not exist — "
              "apply migration 069 first.", file=sys.stderr)
        return 2

    # Sentinel safety: if any ledger description contains `|` the field split
    # below would be ambiguous — abort and ask for a different sentinel rather
    # than guess. (Hashes cannot contain `|`: they are validated 64-hex.)
    pipes = psql("SELECT count(*) FROM nebula.schema_version WHERE description LIKE '%|%'")
    if pipes != "0":
        print(f"FATAL: {pipes} ledger row(s) contain '|' in description — "
              f"field separator would be ambiguous.", file=sys.stderr)
        return 2

    rows = []
    raw = psql("SELECT version, description, coalesce(content_hash,'') "
               "FROM nebula.schema_version ORDER BY version")
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 3 or not parts[0].isdigit():
            print(f"FATAL: unparseable ledger row {line!r} — aborting rather than "
                  f"mis-stamping.", file=sys.stderr)
            return 2
        ver, desc, h = int(parts[0]), parts[1], parts[2]
        rows.append((ver, desc, h))
    versions = {v for v, _, _ in rows}

    updates, divergences, missing, renames = [], [], [], []
    for name in files:
        ver = int(name[:3])
        if ver not in versions:
            continue  # not applied on this database; nothing to backfill
        digest = manifest.get(name)
        if digest is None:
            missing.append(name)
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            print(f"FATAL: manifest hash for {name} is not 64 lowercase hex — "
                  f"regenerate the manifest.", file=sys.stderr)
            return 2
        # The runner derives the ledger description the same way; a mismatch
        # means the file was renamed since it was applied — warn, don't block.
        desc = name[4:-4].replace("-", " ")
        existing = next((h for v, _, h in rows if v == ver), "")
        if existing and existing != digest:
            divergences.append((ver, existing, digest))
            continue
        if existing == digest:
            continue
        ledger_desc = next((d for v, d, _ in rows if v == ver), "")
        if ledger_desc != desc:
            renames.append((ver, ledger_desc, desc))
        updates.append((ver, digest, name))

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] {len(updates)} row(s) to stamp, {len(divergences)} divergence(s), "
          f"{len(missing)} file(s) missing from the manifest")
    for ver, digest, name in updates:
        print(f"  v{ver:03d} <- {digest[:12]}…  ({name})")
    for ver, existing, digest in divergences:
        print(f"  DIVERGENCE v{ver:03d}: ledger {existing[:12]}… != manifest {digest[:12]}… "
              f"(investigate; --force overwrites)")
    for name in missing:
        print(f"  MISSING from manifest: {name}")
    for ver, ledger_desc, desc in renames:
        print(f"  NOTE v{ver:03d}: ledger description {ledger_desc!r} != filename-derived "
              f"{desc!r} (file renamed since apply?)")

    if divergences and not args.force:
        print("Refusing: divergences present. Investigate, then --force.", file=sys.stderr)
        return 1
    if not updates and not divergences:
        print("Nothing to do.")
        return 0
    if args.apply:
        stmts = ["BEGIN;"]
        # Backfill only NULL rows: a stamped hash is apply-time provenance that
        # a backfill has no business touching. Every statement is terminated —
        # multi-statement -c dies at the second statement otherwise (found in
        # end-to-end testing).
        for ver, digest, _name in updates:
            stmts.append(
                f"UPDATE nebula.schema_version SET content_hash='{digest}' "
                f"WHERE version={ver} AND content_hash IS NULL;"
            )
        # --force overwrites DISAGREEING hashes, loudly (already printed above).
        for ver, _existing, digest in divergences:
            if args.force:
                stmts.append(
                    f"UPDATE nebula.schema_version SET content_hash='{digest}' "
                    f"WHERE version={ver};"
                )
        stmts.append("COMMIT;")
        psql("\n".join(stmts), tuples=False)
        stamped = len(updates) + (len(divergences) if args.force else 0)
        print(f"[APPLY] stamped {stamped} row(s) "
              f"({len(updates)} backfill, {len(divergences) if args.force else 0} forced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
