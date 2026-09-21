"""Expression E8.3 — bounded live evaluation end-to-end driver.

Runs one bounded evaluation pass over DB-loaded propositions:

1. Load the interpreter via the E8.1-fixed ``DatabaseLoader`` (real
   dispositions, assertions, frame values).
2. Pin the Expression digest → DB UUID identity seam (E8.2).
3. Evaluate through the E7 adapter and build the E5 artifact.
4. Persist canonical receipts through the E6 writer.

Every receipt carries the ``loaded_population_fingerprint`` so replay can
detect population drift. The driver itself owns no transaction: the caller
supplies the connection factory (transaction-scoped for canaries,
autocommit for real runs). Read-only against the resolution schema except
for the E6 receipt writes.
"""
from __future__ import annotations

from typing import Any

from .loaded_interpreter import LoadedInterpreter
from .persistence import ResolutionReceiptWriter


E8_REVISION = "expression-e8.3-v0.1"


def run_bounded_evaluation(
    loaded: LoadedInterpreter,
    bundle: dict[str, Any],
    *,
    read_set: dict[str, Any],
    source_run_id: str,
    ontology_revision: str,
    authority_owner: str = "resolution",
    context: dict[str, Any] | None = None,
    retention_class: str = "operational_review",
    receipt_writer: ResolutionReceiptWriter | None = None,
) -> dict[str, Any]:
    """Evaluate one bundle against the loaded interpreter and persist receipts.

    Returns a deterministic run report:

    - ``envelope`` — the E7/E8.2 evaluation envelope (with population
      fingerprint and registrations);
    - ``artifact`` — the E5 artifact (receipts / projection / context);
    - ``persistence`` — the E6 outcome report (or ``skipped`` when no
      writer was supplied).
    """
    if not source_run_id:
        raise ValueError("source_run_id is required")
    if not read_set:
        raise ValueError("read_set is required")

    envelope = loaded.evaluate_bundle(
        bundle,
        read_set=read_set,
        ontology_revision=ontology_revision,
        authority_owner=authority_owner,
        context=context,
    )
    envelope["e8_revision"] = E8_REVISION
    envelope["source_run_id"] = source_run_id

    from .e5 import build_e5_slice

    artifact = build_e5_slice(
        bundle,
        envelope,
        read_set=read_set,
        source_run_id=source_run_id,
        retention_class=retention_class,
    )

    persistence: dict[str, Any] = {"status": "skipped", "reason": "no receipt writer supplied"}
    if receipt_writer is not None:
        persistence = receipt_writer.record_artifact(artifact)
        persistence["status"] = "recorded"

    return {
        "e8_revision": E8_REVISION,
        "source_run_id": source_run_id,
        "loaded_population_fingerprint": envelope["loaded_population_fingerprint"],
        "registrations": envelope["registrations"],
        "envelope": envelope,
        "artifact": artifact,
        "persistence": persistence,
        "results_summary": _summarize(envelope),
    }


def _summarize(envelope: dict[str, Any]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for result in envelope.get("results", []):
        disposition = result.get("disposition") or "unknown"
        summary[disposition] = summary.get(disposition, 0) + 1
    return summary
