#!/usr/bin/env python3
"""planner-mcp: Knowledge graph queries for Planner backlog grooming.

The Planner doesn't scan the database itself — it queries the knowledge
graph for evidence of completion, deduplication, and relevance. Evidence
becomes open questions that block promotion until resolved.

Usage:
    python3 planner_mcp_server.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
from mcp.server.fastmcp import FastMCP

# ── logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("planner-mcp")

# ── server ───────────────────────────────────────────────────────────
mcp = FastMCP("planner-mcp")

# ── DB config ────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": os.getenv("PG_HOST", "localhost"),
    "port": int(os.getenv("PG_PORT", "5432")),
    "database": os.getenv("PG_DATABASE", "nexus"),
    "user": os.getenv("PG_USER", "pguser"),
    "password": os.getenv("PG_PASSWORD", "pgpass"),
}


def _get_conn():
    """Open a psycopg2 connection to the nexus database."""
    return psycopg2.connect(**DB_CONFIG)


# ── Knowledge Graph Queries ─────────────────────────────────────────

@mcp.tool()
def check_completion_evidence(candidate_id: str) -> str:
    """Check if similar work has been completed.
    
    Queries the knowledge graph for:
    - Completed WorkRequests with similar titles
    - Completed execution requests
    - Recent cascade events about similar work
    
    Returns evidence that can be used to create open questions.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Get candidate details
            cur.execute(
                "SELECT id, title, system_id, subsystem_id FROM nebula.harvest_candidates WHERE id = %s::uuid",
                (candidate_id,),
            )
            candidate = cur.fetchone()
            if not candidate:
                return json.dumps({"error": "Candidate not found"})
            
            cand_id, title, system_id, subsystem_id = candidate
            
            evidence = []
            
            # 1. Check completed WorkRequests
            cur.execute("""
                SELECT id, title, business_status, consumed_at
                FROM nebula.work_requests
                WHERE business_status IN ('COMPLETED', 'DISPATCHED')
                AND (
                    similarity(lower(title), lower(%s)) > 0.3
                    OR lower(title) LIKE '%%' || lower(%s) || '%%'
                    OR lower(%s) LIKE '%%' || lower(title) || '%%'
                )
                ORDER BY consumed_at DESC NULLS LAST
                LIMIT 5
            """, (title, title, title))
            
            for row in cur.fetchall():
                evidence.append({
                    "type": "work_request_completed",
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                    "completed_at": row[3].isoformat() if row[3] else None,
                    "confidence": "high" if row[2] == "COMPLETED" else "medium",
                })
            
            # 2. Check completed execution requests
            cur.execute("""
                SELECT er.id, er.business_key, er.status, er.updated_at
                FROM execution.requests er
                WHERE er.status = 'COMPLETED'
                AND (
                    similarity(lower(er.business_key), lower(%s)) > 0.3
                    OR lower(er.business_key) LIKE '%%' || lower(%s) || '%%'
                )
                ORDER BY er.updated_at DESC NULLS LAST
                LIMIT 5
            """, (title, title))
            
            for row in cur.fetchall():
                evidence.append({
                    "type": "execution_completed",
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                    "completed_at": row[3].isoformat() if row[3] else None,
                    "confidence": "medium",
                })
            
            # 3. Check promoted candidates (potential duplicates)
            cur.execute("""
                SELECT id, title, compilation_readiness, status
                FROM nebula.harvest_candidates
                WHERE status IN ('promoted', 'linked')
                AND id != %s::uuid
                AND (
                    similarity(lower(title), lower(%s)) > 0.4
                    OR lower(title) LIKE '%%' || lower(%s) || '%%'
                    OR lower(%s) LIKE '%%' || lower(title) || '%%'
                )
                ORDER BY compilation_readiness DESC
                LIMIT 5
            """, (cand_id, title, title, title))
            
            for row in cur.fetchall():
                evidence.append({
                    "type": "duplicate_candidate",
                    "id": str(row[0]),
                    "title": row[1],
                    "cpf_score": float(row[2]) if row[2] else None,
                    "status": row[3],
                    "confidence": "high" if float(row[2] or 0) >= 0.7 else "medium",
                })
            
            # 4. Check recent cascade events about completion
            cur.execute("""
                SELECT event_type, payload, event_timestamp
                FROM cascade.events
                WHERE event_type IN ('candidate.promoted', 'requirement.promoted_to_plan')
                AND (
                    similarity(lower(payload->>'title'::text), lower(%s)) > 0.3
                    OR lower(payload->>'title'::text) LIKE '%%' || lower(%s) || '%%'
                )
                ORDER BY event_timestamp DESC
                LIMIT 5
            """, (title, title))
            
            for row in cur.fetchall():
                evidence.append({
                    "type": "cascade_event",
                    "event_type": row[0],
                    "payload": row[1],
                    "timestamp": row[2].isoformat() if row[2] else None,
                    "confidence": "medium",
                })
            
            # 5. Check agent_records (engineering logs, decisions, reports)
            cur.execute("""
                SELECT ar.id, ar.title, ar.record_type, ar.created_at
                FROM nebula.agent_records ar
                WHERE ar.record_type IN ('engineering_log', 'report', 'decision')
                AND (
                    similarity(lower(ar.title), lower(%s)) > 0.35
                    OR lower(ar.content) LIKE '%%' || lower(%s) || '%%'
                )
                ORDER BY ar.created_at DESC
                LIMIT 5
            """, (title, title))
            
            for row in cur.fetchall():
                evidence.append({
                    "type": "agent_record_completed",
                    "id": str(row[0]),
                    "title": row[1],
                    "record_type": row[2],
                    "completed_at": row[3].isoformat() if row[3] else None,
                    "confidence": "medium" if row[2] == "engineering_log" else "low",
                })
            
            # 6. Check Done requirements
            cur.execute("""
                SELECT r.id, r.title, r.status
                FROM nebula.requirements r
                WHERE r.status = 'Done'
                AND (
                    similarity(lower(r.title), lower(%s)) > 0.4
                    OR lower(r.title) LIKE '%%' || lower(%s) || '%%'
                )
                ORDER BY r.created_at DESC
                LIMIT 3
            """, (title, title))
            
            for row in cur.fetchall():
                evidence.append({
                    "type": "requirement_completed",
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                    "confidence": "high",
                })
            
            return json.dumps({
                "candidate_id": candidate_id,
                "title": title,
                "evidence_count": len(evidence),
                "evidence": evidence,
                "recommendation": "escalate" if len(evidence) > 0 else "clear",
            }, default=str)
            
    finally:
        conn.close()


@mcp.tool()
def check_duplicate_candidates(candidate_id: str) -> str:
    """Check if similar candidates already exist.
    
    Queries for candidates with similar titles that are already
    promoted, linked, or have high CPF scores.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Get candidate details
            cur.execute(
                "SELECT id, title FROM nebula.harvest_candidates WHERE id = %s::uuid",
                (candidate_id,),
            )
            candidate = cur.fetchone()
            if not candidate:
                return json.dumps({"error": "Candidate not found"})
            
            cand_id, title = candidate
            
            # Find similar candidates
            cur.execute("""
                SELECT id, title, compilation_readiness, status,
                       similarity(lower(title), lower(%s)) as sim
                FROM nebula.harvest_candidates
                WHERE id != %s::uuid
                AND (
                    similarity(lower(title), lower(%s)) > 0.3
                    OR lower(title) LIKE '%%' || lower(%s) || '%%'
                    OR lower(%s) LIKE '%%' || lower(title) || '%%'
                )
                ORDER BY sim DESC
                LIMIT 10
            """, (title, cand_id, title, title, title))
            
            duplicates = []
            for row in cur.fetchall():
                duplicates.append({
                    "id": str(row[0]),
                    "title": row[1],
                    "cpf_score": float(row[2]) if row[2] else None,
                    "status": row[3],
                    "similarity": float(row[4]) if row[4] else None,
                })
            
            return json.dumps({
                "candidate_id": candidate_id,
                "title": title,
                "duplicate_count": len(duplicates),
                "duplicates": duplicates,
            }, default=str)
            
    finally:
        conn.close()


@mcp.tool()
def check_work_request_overlap(candidate_id: str) -> str:
    """Check if candidate overlaps with existing work requests.
    
    Queries work_requests, execution requests, and requirements
    for overlapping work.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Get candidate details
            cur.execute(
                "SELECT id, title FROM nebula.harvest_candidates WHERE id = %s::uuid",
                (candidate_id,),
            )
            candidate = cur.fetchone()
            if not candidate:
                return json.dumps({"error": "Candidate not found"})
            
            cand_id, title = candidate
            
            overlaps = []
            
            # Check nebula.work_requests
            cur.execute("""
                SELECT id, title, business_status, created_at
                FROM nebula.work_requests
                WHERE similarity(lower(title), lower(%s)) > 0.25
                ORDER BY created_at DESC
                LIMIT 5
            """, (title,))
            
            for row in cur.fetchall():
                overlaps.append({
                    "table": "nebula.work_requests",
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                    "created_at": row[3].isoformat() if row[3] else None,
                })
            
            # Check execution.requests
            cur.execute("""
                SELECT id, business_key, status, created_at
                FROM execution.requests
                WHERE similarity(lower(business_key), lower(%s)) > 0.25
                ORDER BY created_at DESC
                LIMIT 5
            """, (title,))
            
            for row in cur.fetchall():
                overlaps.append({
                    "table": "execution.requests",
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                    "created_at": row[3].isoformat() if row[3] else None,
                })
            
            return json.dumps({
                "candidate_id": candidate_id,
                "title": title,
                "overlap_count": len(overlaps),
                "overlaps": overlaps,
            }, default=str)
            
    finally:
        conn.close()


@mcp.tool()
def get_candidate_questions(candidate_id: str) -> str:
    """Get existing open questions for a candidate.
    
    Returns blocking and non-blocking questions already written
    for this candidate. Useful for checking whether the planner
    has already flagged this candidate.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, category, status, blocking, created_by, created_at
                FROM nebula.open_questions
                WHERE candidate_id = %s::uuid
                ORDER BY created_at DESC
            """, (candidate_id,))
            
            questions = []
            for row in cur.fetchall():
                questions.append({
                    "id": str(row[0]),
                    "title": row[1],
                    "category": row[2],
                    "status": row[3],
                    "blocking": row[4],
                    "created_by": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                })
            
            return json.dumps({
                "candidate_id": candidate_id,
                "question_count": len(questions),
                "blocking_count": sum(1 for q in questions if q["blocking"]),
                "questions": questions,
            }, default=str)
    finally:
        conn.close()


@mcp.tool()
def write_duplicate_question(candidate_id: str, duplicate_of_title: str,
                              duplicate_of_id: str, source: str,
                              cpf_score: float = 0.0) -> str:
    """Write a blocking open_question when a candidate has duplicates.
    
    Creates a record in nebula.open_questions with candidate_id set
    and requirement_id NULL. The question blocks promotion until resolved.
    
    Args:
        candidate_id: The candidate being flagged
        duplicate_of_title: Title of the duplicate
        duplicate_of_id: ID of the duplicate (candidate, intent_record, or plan)
        source: Where the duplicate was found (candidate, intent_record, plan, work_request)
        cpf_score: CPF score of the duplicate
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Check if this exact question already exists (idempotent)
            cur.execute("""
                SELECT id FROM nebula.open_questions
                WHERE candidate_id = %s::uuid
                  AND category = 'DUPLICATE_CANDIDATE'
                  AND title LIKE %s
                  AND status = 'OPEN'
                LIMIT 1
            """, (candidate_id, f'%{duplicate_of_title[:40]}%'))
            
            if cur.fetchone():
                return json.dumps({
                    "status": "already_exists",
                    "message": f"Question already exists for duplicate of '{duplicate_of_title[:50]}'",
                })
            
            cpf_str = f"{cpf_score:.2f}"
            title = (
                f"Duplicate detected: '{duplicate_of_title}' "
                f"({source}:{duplicate_of_id[:8]}, CPF={cpf_str}) "
                f"— resolve before promotion"
            )
            
            description = (
                f"Auto-generated by Planner. "
                f"Similar {source} found with CPF={cpf_str}."
            )
            
            cur.execute("""
                INSERT INTO nebula.open_questions
                    (requirement_id, candidate_id, title, description,
                     category, status, blocking, created_by)
                VALUES (
                    NULL, %s::uuid, %s, %s,
                    'DUPLICATE_CANDIDATE', 'OPEN', true, 'planner'
                )
                RETURNING id
            """, (candidate_id, title, description))
            
            qid = cur.fetchone()[0]
            conn.commit()
            
            return json.dumps({
                "status": "created",
                "question_id": str(qid),
                "title": title,
                "candidate_id": candidate_id,
            })
    finally:
        conn.close()


@mcp.tool()
def write_evidence_question(candidate_id: str, evidence_type: str,
                             evidence_title: str, evidence_id: str,
                             confidence: str = "medium",
                             auto_close: bool = False) -> str:
    """Write an open_question when completion evidence is found.
    
    Creates a record in nebula.open_questions indicating that work
    may already be done, blocking promotion until confirmed.
    
    When auto_close=true, the question is inserted as RESOLVED (not OPEN)
    and non-blocking. This preserves the audit trail without blocking the
    pipeline when evidence is strong (e.g., COMPLETED work_request).
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Idempotent check
            cur.execute("""
                SELECT id FROM nebula.open_questions
                WHERE candidate_id = %s::uuid
                  AND category = 'WORK_COMPLETED'
                  AND title LIKE %s
                  AND status = 'OPEN'
                LIMIT 1
            """, (candidate_id, f'%{evidence_title[:40]}%'))
            
            if cur.fetchone():
                return json.dumps({
                    "status": "already_exists",
                    "message": f"Question already exists for evidence '{evidence_title[:50]}'",
                })
            
            label = {
                "work_request_completed": "WorkRequest",
                "execution_completed": "execution request",
                "duplicate_candidate": "promoted candidate",
                "cascade_event": "cascade event",
                "agent_record_completed": "agent record",
                "requirement_completed": "requirement",
            }.get(evidence_type, evidence_type)
            
            status = "RESOLVED" if auto_close else "OPEN"
            blocking = not auto_close
            
            if auto_close:
                title = (
                    f"Auto-resolved: {label} '{evidence_title}' "
                    f"({evidence_id[:8]}) — high-confidence completion evidence"
                )
                description = (
                    f"Auto-generated by Planner. "
                    f"Evidence type: {evidence_type}, confidence: {confidence}. "
                    f"Auto-closed: strong evidence that this work is already done."
                )
            else:
                title = (
                    f"Possible completed work: {label} '{evidence_title}' "
                    f"({evidence_id[:8]}) — confirm before promotion"
                )
                description = (
                    f"Auto-generated by Planner. "
                    f"Evidence type: {evidence_type}, confidence: {confidence}."
                )
            
            cur.execute("""
                INSERT INTO nebula.open_questions
                    (requirement_id, candidate_id, title, description,
                     category, status, blocking, created_by)
                VALUES (
                    NULL, %s::uuid, %s, %s,
                    'WORK_COMPLETED', %s, %s, 'planner'
                )
                RETURNING id
            """, (candidate_id, title, description, status, blocking))
            
            qid = cur.fetchone()[0]
            conn.commit()
            
            return json.dumps({
                "status": "created",
                "auto_closed": auto_close,
                "question_id": str(qid),
                "title": title,
                "candidate_id": candidate_id,
            })
    finally:
        conn.close()


@mcp.tool()
def assess_candidate_relevance(candidate_id: str, write_questions: bool = False) -> str:
    """Full relevance assessment combining all knowledge graph queries.
    
    Returns a comprehensive assessment with:
    - Completion evidence
    - Duplicate candidates
    - Work request overlaps
    - Recommendation (clear/escalate/block)
    
    If write_questions=true, creates blocking open_questions in the
    database for any duplicate or evidence findings (idempotent).
    """
    # Run all checks
    completion = json.loads(check_completion_evidence(candidate_id))
    duplicates = json.loads(check_duplicate_candidates(candidate_id))
    overlaps = json.loads(check_work_request_overlap(candidate_id))
    
    # Determine recommendation
    has_completion = completion.get("evidence_count", 0) > 0
    has_duplicates = duplicates.get("duplicate_count", 0) > 0
    has_overlaps = overlaps.get("overlap_count", 0) > 0
    
    if has_completion:
        high_confidence = any(
            e.get("confidence") == "high"
            for e in completion.get("evidence", [])
        )
        # Auto-close: when evidence is strong (COMPLETED work_request or Done requirement),
        # resolve the question instead of blocking. The audit trail is preserved.
        very_high = any(
            e.get("type") in ("work_request_completed", "requirement_completed")
            and e.get("confidence") == "high"
            for e in completion.get("evidence", [])
        )
        if very_high:
            recommendation = "auto_close"
        elif high_confidence:
            recommendation = "block"
        else:
            recommendation = "escalate"
    elif has_duplicates and has_overlaps:
        recommendation = "escalate"
    elif has_duplicates or has_overlaps:
        recommendation = "escalate"
    else:
        recommendation = "clear"
    
    questions_written = []
    
    if write_questions and recommendation != "clear":
        auto_close = (recommendation == "auto_close")
        
        # Write duplicate questions (top 3 most similar)
        for dup in duplicates.get("duplicates", [])[:3]:
            if dup.get("cpf_score", 0) >= 0.5:
                result = json.loads(write_duplicate_question(
                    candidate_id=candidate_id,
                    duplicate_of_title=dup["title"],
                    duplicate_of_id=dup["id"],
                    source="candidate",
                    cpf_score=dup.get("cpf_score", 0),
                ))
                questions_written.append(result)
        
        # Write evidence questions (top 2)
        for ev in completion.get("evidence", [])[:2]:
            result = json.loads(write_evidence_question(
                candidate_id=candidate_id,
                evidence_type=ev["type"],
                evidence_title=ev.get("title", "unknown"),
                evidence_id=ev.get("id", "unknown"),
                confidence=ev.get("confidence", "medium"),
                auto_close=auto_close,
            ))
            questions_written.append(result)
    
    return json.dumps({
        "candidate_id": candidate_id,
        "completion_evidence": completion,
        "duplicate_candidates": duplicates,
        "work_request_overlaps": overlaps,
        "recommendation": recommendation,
        "signals": {
            "completion": has_completion,
            "duplicates": has_duplicates,
            "overlaps": has_overlaps,
        },
        "questions_written": len(questions_written) if write_questions else 0,
        "question_details": questions_written if write_questions else None,
    }, default=str)


# ── Hierarchy & Tag Inference ────────────────────────────────────────

@mcp.tool()
def check_hierarchy_evidence(candidate_id: str) -> str:
    """Infer system/subsystem from related artifacts.
    
    Queries agent_records, requirements, and work_requests linked to or
    similar to this candidate. When these artifacts already have system_id
    or subsystem_id set, we can propagate the hierarchy instead of asking.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, system_id, subsystem_id FROM nebula.harvest_candidates WHERE id = %s::uuid",
                (candidate_id,),
            )
            candidate = cur.fetchone()
            if not candidate:
                return json.dumps({"error": "Candidate not found"})
            
            cand_id, title, system_id, subsystem_id = candidate
            
            inferred_system = None
            inferred_subsystem = None
            evidence = []
            
            # 1. Check agent_records via cross_references
            if not system_id or not subsystem_id:
                cur.execute("""
                    SELECT ar.system_id, ar.subsystem_id, ar.title,
                           sys.name, sub.name
                    FROM nebula.cross_references cr
                    JOIN nebula.agent_records ar ON ar.id::text = cr.source_id
                    LEFT JOIN nebula.systems sys ON sys.id = ar.system_id
                    LEFT JOIN nebula.subsystems sub ON sub.id = ar.subsystem_id
                    WHERE cr.target_id = %s
                      AND cr.target_type = 'harvest_candidate'
                      AND (ar.system_id IS NOT NULL OR ar.subsystem_id IS NOT NULL)
                    ORDER BY ar.created_at DESC
                    LIMIT 5
                """, (str(cand_id),))
                for row in cur.fetchall():
                    if row[0] and not inferred_system:
                        inferred_system = row[0]
                    if row[1] and not inferred_subsystem:
                        inferred_subsystem = row[1]
                    evidence.append({
                        "source": "agent_record",
                        "title": row[2],
                        "system": row[3],
                        "subsystem": row[4],
                    })
            
            # 2. Check similar requirements (by title similarity)
            if not system_id or not subsystem_id:
                cur.execute("""
                    SELECT r.system_id, r.subsystem_id, r.title,
                           sys.name, sub.name,
                           similarity(lower(r.title), lower(%s)) as sim
                    FROM nebula.requirements r
                    LEFT JOIN nebula.systems sys ON sys.id = r.system_id
                    LEFT JOIN nebula.subsystems sub ON sub.id = r.subsystem_id
                    WHERE (similarity(lower(r.title), lower(%s)) > 0.4
                           OR lower(r.title) LIKE '%%' || lower(%s) || '%%')
                      AND (r.system_id IS NOT NULL OR r.subsystem_id IS NOT NULL)
                    ORDER BY sim DESC
                    LIMIT 3
                """, (title, title, title))
                for row in cur.fetchall():
                    if row[0] and not inferred_system:
                        inferred_system = row[0]
                    if row[1] and not inferred_subsystem:
                        inferred_subsystem = row[1]
                    evidence.append({
                        "source": "requirement",
                        "title": row[2],
                        "system": row[3],
                        "subsystem": row[4],
                        "similarity": float(row[5]) if row[5] else None,
                    })
            
            # 3. Check similar work_requests → follow the normalized requirement link
            if not system_id or not subsystem_id:
                cur.execute("""
                    SELECT rrl.requirement_id, wr.title
                    FROM nebula.work_requests wr
                    JOIN resolution.request_requirement_link rrl
                      ON rrl.request_id = wr.id
                     AND rrl.relation = 'source'
                     AND rrl.valid_until = 'infinity'::timestamptz
                     AND rrl.recorded_until_dt = 'infinity'::timestamptz
                    WHERE (similarity(lower(wr.title), lower(%s)) > 0.4
                           OR lower(wr.title) LIKE '%%' || lower(%s) || '%%')
                      AND rrl.requirement_id IS NOT NULL
                    GROUP BY rrl.requirement_id, wr.title, wr.created_at
                    ORDER BY wr.created_at DESC
                    LIMIT 3
                """, (title, title))
                for row in cur.fetchall():
                    if row[0]:
                        cur.execute("""
                            SELECT r.system_id, r.subsystem_id, sys.name, sub.name
                            FROM nebula.requirements r
                            LEFT JOIN nebula.systems sys ON sys.id = r.system_id
                            LEFT JOIN nebula.subsystems sub ON sub.id = r.subsystem_id
                            WHERE r.id = %s
                        """, (row[0],))
                        r2 = cur.fetchone()
                        if r2 and r2[0] and not inferred_system:
                            inferred_system = r2[0]
                        if r2 and r2[1] and not inferred_subsystem:
                            inferred_subsystem = r2[1]
                        if r2 and (r2[0] or r2[1]):
                            evidence.append({
                                "source": "work_request",
                                "title": row[1],
                                "system": r2[2],
                                "subsystem": r2[3],
                            })
            
            return json.dumps({
                "candidate_id": candidate_id,
                "has_system": system_id is not None,
                "has_subsystem": subsystem_id is not None,
                "inferred_system_id": str(inferred_system) if inferred_system else None,
                "inferred_subsystem_id": str(inferred_subsystem) if inferred_subsystem else None,
                "evidence": evidence,
            }, default=str)
    finally:
        conn.close()


@mcp.tool()
def check_tag_evidence(candidate_id: str) -> str:
    """Infer tags from parent harvest and similar artifacts.
    
    When a candidate has 0 or 1 tags, check the parent harvest's tags
    and tags on similar requirements for candidates to inherit.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT hc.id, hc.title, hc.tags,
                       h.tags as harvest_tags
                FROM nebula.harvest_candidates hc
                LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
                WHERE hc.id = %s::uuid
            """, (candidate_id,))
            row = cur.fetchone()
            if not row:
                return json.dumps({"error": "Candidate not found"})
            
            cand_id, title, cand_tags, harvest_tags = row
            cand_tags = cand_tags or []
            harvest_tags = harvest_tags or []
            
            # Merge candidate tags with harvest tags
            merged_tags = list(dict.fromkeys(cand_tags + harvest_tags))  # preserve order, dedup
            
            # Check tags on similar implementation_plans (requirements view has no tags)
            req_tags = []
            if len(merged_tags) < 2:
                cur.execute("""
                    SELECT ip.tags, ip.title,
                           similarity(lower(ip.title), lower(%s)) as sim
                    FROM nebula.implementation_plans ip
                    WHERE (similarity(lower(ip.title), lower(%s)) > 0.4
                           OR lower(ip.title) LIKE '%%' || lower(%s) || '%%')
                      AND array_length(ip.tags, 1) > 0
                    ORDER BY sim DESC
                    LIMIT 3
                """, (title, title, title))
                for rrow in cur.fetchall():
                    if rrow[0]:
                        req_tags.extend(rrow[0])
                        evidence_title = rrow[1]
                
                # Deduplicate
                merged_tags = list(dict.fromkeys(merged_tags + req_tags))
            
            return json.dumps({
                "candidate_id": candidate_id,
                "current_tag_count": len(cand_tags),
                "harvest_tag_count": len(harvest_tags),
                "inferred_tags": merged_tags,
                "tags_added": len(merged_tags) - len(cand_tags),
            }, default=str)
    finally:
        conn.close()


@mcp.tool()
def check_completion_status(candidate_id: str) -> str:
    """Check if work for this candidate is already done via work_requests.
    
    When a work_request for similar work has COMPLETED or DISPATCHED status,
    the candidate is effectively reconciled — no need to ask "is this done?"
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title FROM nebula.harvest_candidates WHERE id = %s::uuid",
                (candidate_id,),
            )
            candidate = cur.fetchone()
            if not candidate:
                return json.dumps({"error": "Candidate not found"})
            
            cand_id, title = candidate
            completed_wrs = []
            
            # Check completed/dispatched work_requests
            cur.execute("""
                SELECT id, title, business_status, consumed_at
                FROM nebula.work_requests
                WHERE business_status IN ('COMPLETED', 'DISPATCHED')
                AND (
                    similarity(lower(title), lower(%s)) > 0.3
                    OR lower(title) LIKE '%%' || lower(%s) || '%%'
                    OR lower(%s) LIKE '%%' || lower(title) || '%%'
                )
                ORDER BY consumed_at DESC NULLS LAST
                LIMIT 5
            """, (title, title, title))
            
            for row in cur.fetchall():
                completed_wrs.append({
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                    "completed_at": row[3].isoformat() if row[3] else None,
                })
            
            # Check Done requirements
            done_reqs = []
            cur.execute("""
                SELECT r.id, r.title, r.status
                FROM nebula.requirements r
                WHERE r.status = 'Done'
                AND (
                    similarity(lower(r.title), lower(%s)) > 0.4
                    OR lower(r.title) LIKE '%%' || lower(%s) || '%%'
                )
                ORDER BY r.created_at DESC
                LIMIT 3
            """, (title, title))
            
            for row in cur.fetchall():
                done_reqs.append({
                    "id": str(row[0]),
                    "title": row[1],
                    "status": row[2],
                })
            
            is_completed = len(completed_wrs) > 0 or len(done_reqs) > 0
            
            return json.dumps({
                "candidate_id": candidate_id,
                "is_completed": is_completed,
                "completed_work_requests": completed_wrs,
                "done_requirements": done_reqs,
            }, default=str)
    finally:
        conn.close()


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
