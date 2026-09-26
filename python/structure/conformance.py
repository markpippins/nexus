"""Cross-runtime conformance for the Structure contract (Structure S6).

Spec: to-do thread 18e17032 — conformance fixtures and a machine-readable
verdict for Structure output, with the parser/grammar revision, source
fingerprint, fact-set fingerprint, and implementation identity established
per run; acceptance cases are replay, tampered-source, parser-drift,
unsupported-syntax, and authority-escalation; the suite must be suitable
for future TypeSpec/SOLScript/JVM producers.

Design: **golden vectors are the portability mechanism, not a parallel
parser.** A vector is a self-describing, runtime-neutral JSON object that
any implementation of the S1 contract can verify with nothing but the
contract itself (sha256 + canonical JSON + the digest formulas + the
validator rules):

- ``contract_fingerprint`` / ``source_fact_id`` / ``observation_id`` /
  ``candidate_id`` / ``read_set_fingerprint`` vectors carry the exact inputs
  and the expected digest. The verifier re-derives the digest and compares.
- ``machine`` vectors carry a parsed source (text + content hash), the
  pinned grammar revision, the run's read-set fingerprint, and the parser's
  observations (full provenance included). The verifier checks the chain:
  sha256(text) == content_hash; read_set_fingerprint(entries) matches; every
  observation validates and re-derives to its own source_fact_id and
  observation_id; expected kinds/facts are present. No parsing required —
  a parserless runtime can still verify the whole identity chain.
- ``invalid_observation`` vectors carry an observation that MUST be rejected
  (e.g. authority escalation). Verifying means proving the validator refuses.

The Python reference additionally (tests only) re-parses machine-vector
sources through the real parser and pins the vector to the live output.

The verdict is machine-readable: deterministic key set, per-vector results,
the implementation identity of the verifier, and a SHA-256 ``verdict_hash``
over the results alone — two different implementations (today CPython and
Node/TypeScript; tomorrow JVM) must produce byte-identical ``verdict_hash``
values for the same vectors. That is the cross-runtime proof the S1
contract is implementable.

Vector schema (structure-conformance-vectors, schema_version 1):
    {"schema_version": 1, "contract_revision": ..., "contract_fingerprint":
     ..., "grammar_revision": ..., "vectors": [...]}
Each vector:
    {"name": str, "kind": contract_fingerprint | source_fact_id |
     observation_id | candidate_id | read_set_fingerprint | machine |
     invalid_observation, "expectation": str, "inputs": {...},
     "expected": <sha256 hex> (digest kinds only)}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any

try:
    from . import contract as c  # package context
except ImportError:  # pragma: no cover - direct-run context
    import contract as c  # type: ignore[no-redef]

try:
    from . import sql_parser as sp  # package context
except ImportError:  # pragma: no cover - direct-run context
    import sql_parser as sp  # type: ignore[no-redef]

CONFORMANCE_SUITE_VERSION = 1

_VERDICT_HASH_KEYS = ("vector_results", "suite_version")


class ConformanceError(ValueError):
    """Malformed vectors or verdict inputs."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def implementation_identity() -> str:
    """Identity of the verifying implementation (CPython reference)."""
    return f"python-cpython-{sys.version_info.major}.{sys.version_info.minor}"


# --------------------------------------------------------------- vectors


def load_vectors(path: str) -> dict[str, Any]:
    """Load and structurally validate a vectors file."""
    with open(path, encoding="utf-8") as fh:
        vectors = json.load(fh)
    _check_vector_file(vectors)
    return vectors


def _check_vector_file(vectors: dict[str, Any]) -> None:
    if not isinstance(vectors, dict):
        raise ConformanceError("vectors file must be a JSON object")
    if vectors.get("schema_version") != CONFORMANCE_SUITE_VERSION:
        raise ConformanceError(
            f"schema_version must be {CONFORMANCE_SUITE_VERSION}, "
            f"got {vectors.get('schema_version')!r}"
        )
    for field in ("contract_revision", "contract_fingerprint", "grammar_revision", "vectors"):
        if field not in vectors:
            raise ConformanceError(f"vectors file missing required field {field!r}")
    if not vectors.get("contract_revision") or not vectors.get("grammar_revision"):
        raise ConformanceError(
            "contract_revision and grammar_revision must be non-empty strings"
        )
    if not isinstance(vectors["vectors"], list) or not vectors["vectors"]:
        raise ConformanceError("vectors must be a non-empty array")
    names = set()
    for v in vectors["vectors"]:
        for field in ("name", "kind", "expectation", "inputs"):
            if field not in v:
                raise ConformanceError(f"vector missing required field {field!r}: {v!r}")
        if v["name"] in names:
            raise ConformanceError(f"duplicate vector name {v['name']!r}")
        names.add(v["name"])


def build_vectors() -> dict[str, Any]:
    """Generate the golden vectors from the fixture corpus.

    Runs the real parser over the built-in fixture sources so the committed
    vectors always describe what the reference implementation actually
    produces. Deterministic: same inputs, byte-identical file.
    """
    import pathlib

    fixtures = pathlib.Path(__file__).resolve().parent / "fixtures" / "sql"
    v178_text = (fixtures / "V178__role_memory.sql").read_text(encoding="utf-8")
    neg_text = (fixtures / "negative_unsupported.sql").read_text(encoding="utf-8")

    def _h(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    v178_hash = _h(v178_text)
    neg_hash = _h(neg_text)

    # --- parse the machine sources with the real parser
    src178 = sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="v178-fixture",
        text=v178_text,
    )
    run178 = sp.build_run([src178])
    srcneg = sp.SqlSource(
        source_uri="db/migrations/V900__negative.sql",
        revision="v900-fixture",
        text=neg_text,
    )
    runneg = sp.build_run([srcneg])

    parser_block = {
        "parser_identity": sp.PARSER_IDENTITY,
        "parser_revision": sp.PARSER_REVISION,
        "grammar_revision": sp.GRAMMAR_REVISION,
    }

    def _compact(obs: dict[str, Any]) -> dict[str, Any]:
        """Full observation minus nothing — vectors carry full provenance."""
        return obs

    # --- reference digests for the pure-digest vectors
    sfid = c.source_fact_id_v1(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="v178-fixture",
        content_hash=v178_hash,
        node_path="statements[1].table.role_memory",
        fact_kind="table",
    )
    rsf = c.read_set_fingerprint([
        {"source_uri": "db/migrations/V900__other.sql", "revision": "r2",
         "content_hash": _h("CREATE TABLE other (id int);\n")},
        {"source_uri": "db/migrations/V178__role_memory.sql", "revision": "v178-fixture",
         "content_hash": v178_hash},
        {"source_uri": "db/migrations/V901__third.sql", "revision": "r3",
         "content_hash": _h("-- third\n")},
    ])
    obs_id = c.observation_id_v1(
        source_fact_id=sfid,
        parser_identity=parser_block["parser_identity"],
        parser_revision=parser_block["parser_revision"],
        grammar_revision=parser_block["grammar_revision"],
        payload={"fact_kind": "table", "statement_index": 1, "table": "role_memory"},
        read_set_fingerprint=rsf,
    )
    cand_id = c.candidate_id_v1(
        source_content_hash=v178_hash,
        parser_identity=parser_block["parser_identity"],
        parser_revision=parser_block["parser_revision"],
        grammar_revision=parser_block["grammar_revision"],
        node_path="statements[3].table.role_memory_tag.fks[1]",
        reason="ambiguous",
    )

    vectors: list[dict[str, Any]] = [
        {
            "name": "contract-fingerprint-stable",
            "kind": "contract_fingerprint",
            "expectation": (
                "the contract fingerprint re-derives from the semantic "
                "manifest alone; any conforming implementation computes "
                "the same value from its own manifest assembly"
            ),
            "inputs": {},
            "expected": c.contract_fingerprint(),
        },
        {
            "name": "unicode-is-raw-utf8-in-canonical-json",
            "kind": "canonical_json",
            "expectation": (
                "canonical JSON emits non-ASCII characters raw (no \\u "
                "escapes) and sorts object keys by unicode code point — "
                "a conforming runtime must byte-match the reference"
            ),
            "inputs": {
                "value": {"table": "rol\u00ea_mem\u00f3ria", "z": 1,
                          "a": None, "ch\u00eene": ["x", "y"], "b": True},
            },
            "expected": c._digest(
                {"table": "rol\u00ea_mem\u00f3ria", "z": 1,
                 "a": None, "ch\u00eene": ["x", "y"], "b": True}
            ),
        },
        {
            "name": "source-fact-id-is-location-identity",
            "kind": "source_fact_id",
            "expectation": (
                "source_fact_id binds (source_uri, revision, content_hash, "
                "node_path, fact_kind) and nothing else — parser-independent "
                "location identity per the two-identities ruling"
            ),
            "inputs": {
                "source_uri": "db/migrations/V178__role_memory.sql",
                "revision": "v178-fixture",
                "content_hash": v178_hash,
                "node_path": "statements[1].table.role_memory",
                "fact_kind": "table",
            },
            "expected": sfid,
        },
        {
            "name": "read-set-fingerprint-is-order-independent",
            "kind": "read_set_fingerprint",
            "expectation": (
                "read_set_fingerprint sorts entries by (source_uri, "
                "revision) before digesting — input order must not change "
                "the value"
            ),
            "inputs": {
                "read_set": [
                    {"source_uri": "db/migrations/V900__other.sql", "revision": "r2",
                     "content_hash": _h("CREATE TABLE other (id int);\n")},
                    {"source_uri": "db/migrations/V178__role_memory.sql",
                     "revision": "v178-fixture", "content_hash": v178_hash},
                    {"source_uri": "db/migrations/V901__third.sql", "revision": "r3",
                     "content_hash": _h("-- third\n")},
                ]
            },
            "expected": rsf,
        },
        {
            "name": "observation-id-binds-parser-grammar-and-read-set",
            "kind": "observation_id",
            "expectation": (
                "observation_id is a function of source_fact_id, parser "
                "identity/revision, grammar revision, canonical payload, "
                "and the run's read-set fingerprint — two replays under the "
                "same pinned revisions agree; a grammar change does not"
            ),
            "inputs": {
                "source_fact_id": sfid,
                "parser": parser_block,
                "payload": {"fact_kind": "table", "statement_index": 1,
                            "table": "role_memory"},
                "read_set_fingerprint": rsf,
            },
            "expected": obs_id,
        },
        {
            "name": "candidate-id-pins-ambiguous-fk-shape",
            "kind": "candidate_id",
            "expectation": (
                "unresolved candidates are first-class: the same ambiguous "
                "shape re-emits the same candidate_id on rerun (reason "
                "'ambiguous', FK from role_memory_tag to governed_tag)"
            ),
            "inputs": {
                "source_content_hash": v178_hash,
                "parser": parser_block,
                "node_path": "statements[3].table.role_memory_tag.fks[1]",
                "reason": "ambiguous",
            },
            "expected": cand_id,
        },
        {
            "name": "machine-parse-v178-identity-chain",
            "kind": "machine",
            "expectation": (
                "the parsed run's identity chain verifies without a parser: "
                "text hashes to content_hash, the read-set fingerprint "
                "re-derives, every observation validates under the S1 "
                "contract and re-derives to its own source_fact_id and "
                "observation_id, and the expected inventory is present"
            ),
            "inputs": {
                "source_uri": "db/migrations/V178__role_memory.sql",
                "revision": "v178-fixture",
                "role": "migration",
                "language": "sql",
                "source_kind": "sql_migration",
                "text": v178_text,
                "content_hash": v178_hash,
                "grammar_revision": parser_block["grammar_revision"],
                "parser": parser_block,
                "read_set_fingerprint": run178["read_set_fingerprint"],
                "observations": [_compact(o) for o in run178["observations"]],
            },
            "expected": None,
            "checks": {
                "expected_fact_kinds": [
                    "table", "column", "named_constraint", "foreign_key",
                    "index", "operation", "seed_value", "enum_type",
                ],
                "expected_structural_facts": [
                    {"fact_kind": "table", "payload": {"table": "role_memory"}},
                    {"fact_kind": "column", "payload": {"name": "key"}},
                    {"fact_kind": "column", "payload": {"name": "value"}},
                    {"fact_kind": "seed_value",
                     "payload": {"table": "role_memory", "values": ["bootstrap", "v1"]}},
                ],
                "expected_unsupported": [],
                "min_observations": 20,
            },
        },
        {
            "name": "machine-parse-unsupported-syntax-is-explicit",
            "kind": "machine",
            "expectation": (
                "constructs outside the grammar yield explicit observations "
                "carrying unsupported_syntax diagnostics — absence never "
                "asserts non-existence"
            ),
            "inputs": {
                "source_uri": "db/migrations/V900__negative.sql",
                "revision": "v900-fixture",
                "role": "migration",
                "language": "sql",
                "source_kind": "sql_migration",
                "text": neg_text,
                "content_hash": neg_hash,
                "grammar_revision": parser_block["grammar_revision"],
                "parser": parser_block,
                "read_set_fingerprint": runneg["read_set_fingerprint"],
                "observations": [_compact(o) for o in runneg["observations"]],
            },
            "expected": None,
            "checks": {
                "expected_fact_kinds": ["operation"],
                "expected_structural_facts": [],
                "expected_unsupported": [{"reason_code": "unsupported_syntax"}],
                "min_observations": 3,
            },
        },
        {
            "name": "authority-escalation-is-rejected",
            "kind": "invalid_observation",
            "expectation": (
                "the validator refuses an observation claiming governed "
                "authority — Structure is a reader; the founding defect "
                "this layer exists to prevent must be mechanically "
                "unrepresentable"
            ),
            "inputs": {
                "observation": {
                    "observation_id": "a" * 64,
                    "source_fact_id": "b" * 64,
                    "source": {
                        "source_uri": "db/migrations/V178__role_memory.sql",
                        "revision": "v178-fixture",
                        "content_hash": v178_hash,
                        "language": "sql",
                        "source_kind": "sql_migration",
                    },
                    "parser": parser_block,
                    "anchor": {"node_path": "statements[1].table.role_memory"},
                    "fact_kind": "table",
                    "payload": {"table": "role_memory"},
                    "parse_status": "complete",
                    "read_set_fingerprint": rsf,
                    "authority_status": "authoritative",
                },
                "must_reject_with": "authority_status",
            },
            "expected": None,
        },
    ]

    return {
        "schema_version": CONFORMANCE_SUITE_VERSION,
        "contract_revision": c.STRUCTURE_CONTRACT_REVISION,
        "contract_fingerprint": c.contract_fingerprint(),
        # the full semantic manifest: a second runtime re-derives the
        # fingerprint from this alone — no Python involvement
        "contract_manifest": c.contract_manifest(),
        "grammar_revision": sp.GRAMMAR_REVISION,
        "parser": parser_block,
        "vectors": vectors,
        "expected_verdict_hash": _digest(
            {
                "suite_version": CONFORMANCE_SUITE_VERSION,
                "vector_results": [
                    {
                        "name": v["name"],
                        "kind": v["kind"],
                        "expectation": v["expectation"],
                        "ok": True,
                    }
                    for v in vectors
                ],
            }
        ),
    }


# ------------------------------------------------------------- verdicts


def _check_digest_json(value: Any, path: str) -> None:
    """Mirror of contract._check_digest_json for vector/verdict material."""
    if isinstance(value, float):
        raise ConformanceError(
            f"canonical JSON digest material must not contain floats ({path})"
        )
    if isinstance(value, dict):
        for k, v in value.items():
            _check_digest_json(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_digest_json(v, f"{path}[{i}]")


def verify_vectors(
    vectors: dict[str, Any], *, implementation: str | None = None
) -> dict[str, Any]:
    """Verify every vector and return the machine-readable verdict."""
    _check_vector_file(vectors)
    impl = implementation if implementation is not None else implementation_identity()
    results = []
    for v in vectors["vectors"]:
        try:
            ok, detail = _verify_one(vectors, v)
            result = {
                "name": v["name"],
                "kind": v["kind"],
                "expectation": v["expectation"],
                "ok": ok,
            }
            if detail:
                result["detail"] = detail
        except Exception as exc:  # noqa: BLE001 — verifier must not crash
            result = {
                "name": v.get("name", "?"),
                "kind": v.get("kind", "?"),
                "expectation": v.get("expectation", ""),
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
    return {
        "suite_version": CONFORMANCE_SUITE_VERSION,
        "vector_file": vectors.get("vector_file", "structure-conformance-vectors.json"),
        "implementation": impl,
        "contract": {
            "contract_revision": vectors.get("contract_revision"),
            "contract_fingerprint": vectors.get("contract_fingerprint"),
            "grammar_revision": vectors.get("grammar_revision"),
        },
        "vector_results": results,
        "verdict_hash": _digest(
            {"suite_version": CONFORMANCE_SUITE_VERSION, "vector_results": results}
        ),
    }


def _verify_one(vectors: dict[str, Any], v: dict[str, Any]) -> tuple[bool, str | None]:
    kind = v["kind"]
    inputs = v.get("inputs") or {}
    if kind == "canonical_json":
        got = c._digest(inputs["value"])
        if got != v["expected"]:
            return False, f"canonical json digest mismatch: got {got[:16]}…"
        return True, None
    if kind == "contract_fingerprint":
        got = c.contract_fingerprint()
        if got != v["expected"]:
            return False, f"contract fingerprint mismatch: got {got[:16]}…"
        return True, None
    if kind == "source_fact_id":
        got = c.source_fact_id_v1(
            source_uri=inputs["source_uri"],
            revision=inputs["revision"],
            content_hash=inputs["content_hash"],
            node_path=inputs["node_path"],
            fact_kind=inputs["fact_kind"],
        )
        if got != v["expected"]:
            return False, f"source_fact_id mismatch: got {got[:16]}…"
        return True, None
    if kind == "read_set_fingerprint":
        got = c.read_set_fingerprint(inputs["read_set"])
        if got != v["expected"]:
            return False, f"read_set_fingerprint mismatch: got {got[:16]}…"
        return True, None
    if kind == "observation_id":
        parser = inputs["parser"]
        got = c.observation_id_v1(
            source_fact_id=inputs["source_fact_id"],
            parser_identity=parser["parser_identity"],
            parser_revision=parser["parser_revision"],
            grammar_revision=parser["grammar_revision"],
            payload=inputs["payload"],
            read_set_fingerprint=inputs["read_set_fingerprint"],
        )
        if got != v["expected"]:
            return False, f"observation_id mismatch: got {got[:16]}…"
        return True, None
    if kind == "candidate_id":
        parser = inputs["parser"]
        got = c.candidate_id_v1(
            source_content_hash=inputs["source_content_hash"],
            parser_identity=parser["parser_identity"],
            parser_revision=parser["parser_revision"],
            grammar_revision=parser["grammar_revision"],
            node_path=inputs["node_path"],
            reason=inputs["reason"],
        )
        if got != v["expected"]:
            return False, f"candidate_id mismatch: got {got[:16]}…"
        return True, None
    if kind == "machine":
        return _verify_machine(inputs, v.get("checks") or {})
    if kind == "invalid_observation":
        obs = inputs["observation"]
        errors = c.validate_structural_observation(obs)
        needle = inputs.get("must_reject_with", "")
        if not errors:
            return False, "validator accepted an observation it must reject"
        if needle and not any(needle in e for e in errors):
            return False, f"validator rejected but not for {needle!r}: {errors[:2]}"
        return True, None
    raise ConformanceError(f"unknown vector kind {kind!r}")


def _verify_machine(
    inputs: dict[str, Any], checks: dict[str, Any]
) -> tuple[bool, str | None]:
    """Verify one machine vector — parserless, contract-only.

    Both the CPython reference and the Node/TypeScript implementation run
    exactly this chain, so the TS runtime never needs a SQL parser.
    """
    # 1. tamper-evident source: the text must hash to the pinned content_hash
    text = inputs.get("text", "")
    got_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if got_hash != inputs.get("content_hash"):
        return False, "source content_hash mismatch (tampered or stale source)"

    # 2. read-set fingerprint re-derives from the identity entries
    rsf = c.read_set_fingerprint([
        {
            "source_uri": inputs.get("source_uri", ""),
            "revision": inputs.get("revision", ""),
            "content_hash": inputs.get("content_hash", ""),
            "role": inputs.get("role", ""),
        }
    ])
    if rsf != inputs.get("read_set_fingerprint"):
        return False, "read_set_fingerprint mismatch (stale source revision?)"

    # 3. every observation validates and re-derives its two identities
    observations = inputs.get("observations") or []
    if not observations:
        return False, "machine vector carries no observations"
    parser = inputs.get("parser") or {}
    kinds_seen: set[str] = set()
    for obs in observations:
        errors = c.validate_structural_observation(obs)
        if errors:
            return False, f"observation {obs.get('observation_id', '?')[:12]}… invalid: {errors[0]}"
        sfid = c.source_fact_id_v1(
            source_uri=obs["source"]["source_uri"],
            revision=obs["source"]["revision"],
            content_hash=obs["source"]["content_hash"],
            node_path=obs["anchor"]["node_path"],
            fact_kind=obs["fact_kind"],
        )
        if sfid != obs["source_fact_id"]:
            return False, (
                f"observation {obs['observation_id'][:12]}… source_fact_id "
                "does not re-derive from its own provenance"
            )
        p = obs.get("parser") or {}
        oid = c.observation_id_v1(
            source_fact_id=obs["source_fact_id"],
            parser_identity=p.get("parser_identity", ""),
            parser_revision=p.get("parser_revision", ""),
            grammar_revision=p.get("grammar_revision", ""),
            payload=obs.get("payload"),
            read_set_fingerprint=obs.get("read_set_fingerprint", ""),
        )
        if oid != obs["observation_id"]:
            return False, (
                f"observation {obs['observation_id'][:12]}… observation_id "
                "does not re-derive from its own provenance"
            )
        if p.get("grammar_revision") != inputs.get("grammar_revision"):
            return False, (
                f"observation {obs['observation_id'][:12]}… grammar_revision "
                f"{p.get('grammar_revision')!r} != pinned "
                f"{inputs.get('grammar_revision')!r} (parser drift)"
            )
        if p.get("parser_identity") != parser.get("parser_identity"):
            return False, (
                f"observation {obs['observation_id'][:12]}… parser_identity "
                "differs from the pinned implementation (parser drift)"
            )
        kinds_seen.add(obs["fact_kind"])

    # 4. expected inventory
    missing_kinds = sorted(set(checks.get("expected_fact_kinds", [])) - kinds_seen)
    if missing_kinds:
        return False, f"expected fact kinds missing: {missing_kinds}"

    unsupported = [
        (o, d) for o in observations for d in (o.get("diagnostics") or [])
        if d.get("code") == "unsupported_syntax"
    ]
    for want in checks.get("expected_unsupported", []):
        code = want.get("reason_code")
        if code and not any(d.get("code") == code for _, d in unsupported):
            return False, f"expected unsupported diagnostic {code!r} not found"

    for want in checks.get("expected_structural_facts", []):
        wkind = want["fact_kind"]
        wpayload = want.get("payload") or {}
        found = any(
            o["fact_kind"] == wkind
            and all((o.get("payload") or {}).get(k) == v for k, v in wpayload.items())
            for o in observations
        )
        if not found:
            return False, f"expected structural fact missing: {want}"

    min_obs = checks.get("min_observations", 0)
    if len(observations) < min_obs:
        return False, f"expected at least {min_obs} observations, got {len(observations)}"
    return True, None


# ------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generate", metavar="OUT_JSON", nargs="?", const="-",
                    help="regenerate the golden vectors (default: stdout; "
                         "pass a path to write the file)")
    ap.add_argument("--verify", metavar="VECTORS_JSON",
                    help="verify a vectors file and print the verdict JSON")
    ap.add_argument("--implementation", metavar="ID",
                    help="override the implementation identity in the verdict")
    args = ap.parse_args(argv)
    if args.generate:
        vectors = build_vectors()
        rendered = json.dumps(vectors, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.generate == "-":
            sys.stdout.write(rendered)
        else:
            with open(args.generate, "w", encoding="utf-8") as fh:
                fh.write(rendered)
            print(f"vectors written: {args.generate}", file=sys.stderr)
        return 0
    if not args.verify:
        ap.print_help()
        return 2
    vectors = load_vectors(args.verify)
    verdict = verify_vectors(vectors, implementation=args.implementation)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if all(r["ok"] for r in verdict["vector_results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
