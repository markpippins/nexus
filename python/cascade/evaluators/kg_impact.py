"""KGArtifactImpactEvaluator — knowledge graph connectivity analysis.

This evaluator answers "what is connected to the artifact referenced in
this observation?" by traversing the knowledge graph.

V1 is deliberately dumb — it counts connected artifacts across domains
and reports counts as findings. No semantic weighting yet.

Traversal rules (V1):
  1. If the observation has a `candidate_id`, follow candidate links:
       harvest_candidate → requirements → specifications → implementation_plans → work_requests
  2. If the observation has a `source_artifact_id` (generic), probe
       cross_references for that source.
  3. If neither, return empty dimension (low confidence).

Future versions will:
  - Use cross_references as the primary linking mechanism
  - Apply semantic weighting based on rel_type
  - Surface path depth and connectedness scores
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any

from .protocol import AssessmentDimension, AssessmentEvaluator, EvidenceRef

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ArtifactCluster:
    """A cluster of artifacts connected to the observation's subject."""
    requirements: list[dict[str, Any]]
    specifications: list[dict[str, Any]]
    implementation_plans: list[dict[str, Any]]
    work_requests: list[dict[str, Any]]
    cross_references: list[dict[str, Any]]


def _get_candidate_cluster(cur: Any, candidate_id: str) -> ArtifactCluster:
    """Traverse from a harvest_candidate through the artifact graph."""
    cur.execute(
        """SELECT id, title, status FROM nebula.requirements WHERE candidate_id = %s::uuid""",
        (candidate_id,),
    )
    reqs = [{"id": str(r[0]), "title": r[1], "status": r[2]} for r in cur.fetchall()]
    req_ids = [r["id"] for r in reqs]

    specs: list[dict[str, Any]] = []
    impl_plans: list[dict[str, Any]] = []
    work_reqs: list[dict[str, Any]] = []

    # Requirements → implementation_plans
    if req_ids:
        cur.execute(
            """SELECT id, plan_number, title, status FROM nebula.implementation_plans
               WHERE requirement_id = ANY(%s::uuid[])""",
            (req_ids,),
        )
        impl_plans = [{"id": str(r[0]), "plan_number": r[1], "title": r[2], "status": r[3]} for r in cur.fetchall()]

    # Implementation plans → work_requests
    # work_requests link through the normalized request_requirement_link table
    if req_ids:
        cur.execute(
            """SELECT DISTINCT wr.id, wr.title, wr.status, rrl.requirement_id
               FROM nebula.work_requests wr
               JOIN resolution.request_requirement_link rrl
                 ON rrl.request_id = wr.id
                AND rrl.relation = 'source'
                AND rrl.valid_until = 'infinity'::timestamptz
                AND rrl.recorded_until_dt = 'infinity'::timestamptz
               WHERE rrl.requirement_id = ANY(%s::uuid[])""",
            (req_ids,),
        )
        work_reqs = [
            {"id": str(r[0]), "title": r[1], "status": r[2], "requirement_id": str(r[3]) if r[3] else None}
            for r in cur.fetchall()
        ]

    # Requirements → specifications (via agenda, indirect — skip in V1)
    # Cross-references for this candidate
    cur.execute(
        """SELECT source_type, source_id, target_type, target_id, rel_type
           FROM nebula.cross_references
           WHERE (source_type = 'harvest_candidate' AND source_id = %s)
              OR (target_type = 'harvest_candidate' AND target_id = %s)""",
        (candidate_id, candidate_id),
    )
    xrefs = [
        {"source_type": r[0], "source_id": r[1], "target_type": r[2], "target_id": r[3], "rel_type": r[4]}
        for r in cur.fetchall()
    ]

    return ArtifactCluster(
        requirements=reqs,
        specifications=specs,
        implementation_plans=impl_plans,
        work_requests=work_reqs,
        cross_references=xrefs,
    )


@dataclasses.dataclass(frozen=True)
class KGArtifactImpactEvaluator:
    """Evaluates the connectivity of an artifact in the knowledge graph.

    Reports artifact counts as findings. The coordinator uses these
    counts as one dimension when resolving the outcome.
    """

    name: str = "kg_artifact_impact"

    def evaluate(
        self,
        cur: Any,
        observation_id: str,
        observation_payload: dict[str, Any],
    ) -> AssessmentDimension:
        try:
            return self._do_evaluate(cur, observation_id, observation_payload)
        except Exception:
            logger.exception(
                "KGArtifactImpactEvaluator failed for observation %s", observation_id
            )
            return AssessmentDimension(
                evaluator=self.name,
                confidence=0.0,
                evidence=[],
                findings={"error": True},
                signals={"evaluator_failure": True},
            )

    def _do_evaluate(
        self,
        cur: Any,
        observation_id: str,
        observation_payload: dict[str, Any],
    ) -> AssessmentDimension:
        # Determine the entry point into the KG
        candidates = observation_payload.get("candidates") or []
        candidate_id: str | None = None

        if candidates:
            # Take the first candidate with an ID
            for c in candidates:
                if isinstance(c, dict) and c.get("id"):
                    candidate_id = c["id"]
                    break
                elif isinstance(c, str):
                    candidate_id = c
                    break

        if not candidate_id:
            # Maybe the observation itself has a source_artifact_id
            candidate_id = observation_payload.get("source_artifact_id") or (
                observation_payload.get("metadata") or {}
            ).get("source_artifact_id")

        if not candidate_id:
            return AssessmentDimension(
                evaluator=self.name,
                confidence=0.0,
                evidence=[],
                findings={
                    "heuristic": "no_entry_point",
                    "reason": "No harvest candidate or source artifact ID in observation payload",
                },
                signals={"kg_no_entry": True},
            )

        # Traverse the graph
        cluster = _get_candidate_cluster(cur, candidate_id)

        total_artifacts = (
            len(cluster.requirements)
            + len(cluster.specifications)
            + len(cluster.implementation_plans)
            + len(cluster.work_requests)
            + len(cluster.cross_references)
        )

        evidence_refs = [
            EvidenceRef(
                source_type="candidate",
                source_id=candidate_id,
                description=f"Connected to {len(cluster.requirements)} requirement(s)",
                confidence=0.8 if cluster.requirements else 0.3,
            ),
        ]
        if cluster.requirements:
            first_req = cluster.requirements[0]
            evidence_refs.append(
                EvidenceRef(
                    source_type="requirement",
                    source_id=first_req["id"],
                    description=f"First requirement: {first_req['title']} (status={first_req['status']})",
                    confidence=0.8,
                )
            )

        findings = {
            "heuristic": "kg_artifact_count",
            "candidate_id": candidate_id,
            "artifact_counts": {
                "requirements": len(cluster.requirements),
                "specifications": len(cluster.specifications),
                "implementation_plans": len(cluster.implementation_plans),
                "work_requests": len(cluster.work_requests),
                "cross_references": len(cluster.cross_references),
                "total": total_artifacts,
            },
            "has_downstream": total_artifacts > 0,
        }

        # Compute a simple confidence from artifact density
        # (still primitive — will evolve with semantic weighting)
        if total_artifacts == 0:
            kg_confidence = 0.2
        elif total_artifacts <= 2:
            kg_confidence = 0.4
        elif total_artifacts <= 5:
            kg_confidence = 0.6
        else:
            kg_confidence = 0.8

        return AssessmentDimension(
            evaluator=self.name,
            confidence=kg_confidence,
            evidence=evidence_refs,
            findings=findings,
            signals={
                "kg_traversal_depth": "direct_links_v1",
                "evaluated_at": __import__("datetime").datetime.utcnow().isoformat(),
            },
        )
