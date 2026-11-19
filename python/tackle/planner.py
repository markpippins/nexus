#!/usr/bin/env python3
"""planner.py — Planner workflow orchestrator with cascade events.

This script runs the Planner's backlog grooming cycle:
1. Scan candidates for CPF assessment
2. Compute CPF scores
3. Check knowledge graph for completion evidence (via MCP server)
4. Generate open questions from CPF gaps AND evidence
5. Emit cascade events at each decision point

Events emitted:
  - candidate.assessed — CPF scoring completed
  - question.created — open question generated from CPF gaps or evidence
  - candidate.greenlit — ready for promotion (CPF >= 0.7, risk != CRITICAL, no evidence)
  - candidate.escalated — needs human review (risk == CRITICAL OR evidence found)

Usage:
    # The mcp package lives in the user site of the anaconda interpreter,
    # not in python/rover/.venv, so run with that interpreter explicitly.
    PYTHONPATH=/home/codex/dev/nexus/python \
        /home/codex/opt/anaconda3/bin/python3 python/tackle/planner.py
    
    # Assess specific candidate
    python3 python/tackle/planner.py --candidate <uuid>
    
    # Dry run (no DB writes, no events)
    python3 python/tackle/planner.py --dry-run
    
    # Generate questions only (no promotion)
    python3 python/tackle/planner.py --questions-only
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

# Ensure parent dir (python/) is on path so rover.* and tackle.* are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rover.event_emitter import (
    emit_candidate_assessed,
    emit_candidate_escalated,
    emit_candidate_greenlit,
    emit_question_created,
    emit_ripple_assessed,
)
from planner_mcp_server import (
    assess_candidate_relevance,
    write_duplicate_question,
    write_evidence_question,
    get_candidate_questions,
    check_hierarchy_evidence,
    check_tag_evidence,
    check_completion_status,
)

log = logging.getLogger("planner")

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)


def psql(sql: str, timeout: int = 30) -> tuple[int, str]:
    """Execute SQL via docker psql."""
    try:
        result = subprocess.run(
            DOCKER_PSQL + ["-t", "-A"],
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, "(timeout)"


def assess_candidate(candidate_id: str, dry_run: bool = False) -> dict | None:
    """Run CPF assessment on a single candidate."""
    log.info("Assessing candidate: %s", candidate_id[:8])
    
    sql = f"SELECT nebula.assess_cpf('{candidate_id}'::uuid);"
    rc, out = psql(sql)
    
    if rc != 0 or not out:
        log.error("Failed to assess candidate %s", candidate_id[:8])
        return None
    
    try:
        assessment = json.loads(out)
    except json.JSONDecodeError:
        log.error("Failed to parse assessment for %s", candidate_id[:8])
        return None
    
    score = assessment.get("score", 0)
    promotable = assessment.get("promotable", False)
    components = assessment.get("components", {})
    questions = assessment.get("suggested_questions", [])
    
    log.info(
        "  CPF=%.3f %s [intent=%.2f hier=%.2f tags=%.2f art=%.2f rec=%.2f deps=%.2f]",
        score,
        "⚡READY" if promotable else "  gap",
        components.get("intent_filled", 0),
        components.get("hierarchy_mapped", 0),
        components.get("tagged", 0),
        components.get("has_artifacts", 0),
        components.get("reconciled", 0),
        components.get("deps_resolved", 0),
    )
    
    if not dry_run:
        emit_candidate_assessed(
            candidate_id=candidate_id,
            cpf_score=score,
            components=components,
            promotable=promotable,
        )
    
    return assessment


def check_linked_evidence(candidate_id: str) -> dict:
    """Check if there are agent_records linked to this candidate via cross_references.
    
    Returns dict with linked record count and titles, so the Planner can skip
    questions when evidence already exists.
    """
    sql = f"""
        SELECT count(*)::int as cnt,
               array_agg(ar.title ORDER BY ar.created_at DESC) as titles
        FROM nebula.cross_references cr
        JOIN nebula.agent_records ar ON ar.id::text = cr.source_id
        WHERE cr.target_id = '{candidate_id}'
          AND cr.target_type = 'harvest_candidate'
          AND cr.source_type = 'agent_record'
          AND cr.rel_type = 'ag:evidences_candidate';
    """
    rc, out = psql(sql)
    
    if rc != 0 or not out:
        return {"count": 0, "titles": []}
    
    try:
        parts = out.split("|")
        count = int(parts[0]) if parts[0] else 0
        titles_str = parts[1] if len(parts) > 1 else ""
        # Parse PostgreSQL array format {title1,title2,...}
        titles = [t.strip() for t in titles_str.strip('{}').split(',') if t.strip()]
        return {"count": count, "titles": titles}
    except Exception:
        return {"count": 0, "titles": []}


def create_questions(candidate_id: str, assessment: dict, dry_run: bool = False) -> list[dict]:
    """Generate open questions from CPF gaps.
    
    Pre-checks available evidence to skip questions that can be auto-resolved:
    - has_artifacts: skipped when linked agent_records exist
    - hierarchy_mapped (system/subsystem): skipped when related artifacts have hierarchy
    - tagged: skipped when parent harvest or similar requirements have tags
    - reconciled: skipped when work_requests or requirements show completion
    """
    questions = assessment.get("suggested_questions", [])
    
    if not questions:
        log.info("  No gaps — candidate is ready")
        return []
    
    # Check for linked evidence — skip has_artifacts if records exist
    linked = check_linked_evidence(candidate_id)
    if linked["count"] > 0:
        log.info("  Found %d linked agent_records — skipping has_artifacts questions", linked["count"])
        questions = [q for q in questions if q.get("component") != "has_artifacts"]
    
    # Check hierarchy evidence — skip system/subsystem questions if inferable
    try:
        hierarchy = json.loads(check_hierarchy_evidence(candidate_id))
        if hierarchy.get("inferred_system_id"):
            log.info("  Inferred system from related artifacts — skipping system question")
            questions = [q for q in questions if not (
                q.get("component") == "hierarchy_mapped"
                and "system" in q.get("question", "").lower()
                and "subsystem" not in q.get("question", "").lower()
            )]
        if hierarchy.get("inferred_subsystem_id"):
            log.info("  Inferred subsystem from related artifacts — skipping subsystem question")
            questions = [q for q in questions if not (
                q.get("component") == "hierarchy_mapped"
                and "subsystem" in q.get("question", "").lower()
            )]
    except Exception as e:
        log.warning("  Hierarchy evidence check failed: %s", e)
    
    # Check tag evidence — skip tag questions if harvest or similar artifacts have tags
    try:
        tags = json.loads(check_tag_evidence(candidate_id))
        if tags.get("tags_added", 0) > 0 and len(tags.get("inferred_tags", [])) >= 2:
            log.info("  Inferred %d tags from harvest/related artifacts — skipping tag questions", tags["tags_added"])
            questions = [q for q in questions if q.get("component") != "tagged"]
    except Exception as e:
        log.warning("  Tag evidence check failed: %s", e)
    
    # Check completion status — skip reconciled question if work is already done
    try:
        completion = json.loads(check_completion_status(candidate_id))
        if completion.get("is_completed"):
            log.info("  Work already completed (WR/requirement found) — skipping reconciled question")
            questions = [q for q in questions if q.get("component") != "reconciled"]
    except Exception as e:
        log.warning("  Completion status check failed: %s", e)
    
    if not questions:
        log.info("  All gaps answered by evidence")
        return []
    
    log.info("  Creating %d open questions...", len(questions))
    created = []
    
    for q in questions:
        if dry_run:
            log.info("    [DRY RUN] Would create: %s (%s)", q["question"][:60], q["category"])
            created.append(q)
            continue
        
        # Use MCP server's write functions for idempotent creation
        try:
            if q.get("category") == "DUPLICATE_DETECTED":
                raw = write_duplicate_question(
                    candidate_id=candidate_id,
                    duplicate_of_title=q.get("component", "unknown"),
                    duplicate_of_id="",
                    source="cpf_assessment",
                )
                result = json.loads(raw) if isinstance(raw, str) else raw
                question_id = result.get("question_id") if isinstance(result, dict) else None
            elif q.get("category") in ("WORK_COMPLETED", "DUPLICATE_CANDIDATE"):
                raw = write_evidence_question(
                    candidate_id=candidate_id,
                    evidence_type=q.get("category", "unknown"),
                    evidence_title=q.get("question", "")[:100],
                    evidence_id="",
                    confidence="medium",
                )
                result = json.loads(raw) if isinstance(raw, str) else raw
                question_id = result.get("question_id") if isinstance(result, dict) else None
            else:
                # Generic question — dedup then write
                title_clean = q["question"].replace("'", "''")
                category = q.get("category", "GENERAL")
                dedup_sql = f"""
                    SELECT id FROM nebula.open_questions
                    WHERE candidate_id = '{candidate_id}'::uuid
                      AND category = '{category}'
                      AND title = '{title_clean}'
                      AND status = 'OPEN'
                    LIMIT 1;
                """
                rc, out = psql(dedup_sql)
                if rc == 0 and out and out.strip():
                    question_id = out.strip()
                    log.info("    Already exists: %s", question_id[:8])
                else:
                    sql = f"""
                        INSERT INTO nebula.open_questions (
                            requirement_id, candidate_id, title, description,
                            category, status, blocking, created_by
                        ) VALUES (
                            NULL, '{candidate_id}'::uuid,
                            '{title_clean}',
                            'Auto-generated from CPF assessment. Component: {q.get("component", "unknown")}.',
                            '{category}',
                            'OPEN', true, 'planner'
                        )
                        RETURNING id;
                    """
                    rc, out = psql(sql)
                    question_id = out.strip() if rc == 0 and out else None
            
            if question_id:
                log.info("    Created question: %s", question_id[:8])
                emit_question_created(
                    question_id=question_id,
                    candidate_id=candidate_id,
                    category=q.get("category", "GENERAL"),
                    title=q["question"],
                    causation_id=None,
                )
                created.append({"id": question_id, **q})
            else:
                log.error("    Failed to create question: %s", q["question"][:40])
        except Exception as e:
            log.error("    Error creating question: %s", e)
    
    return created


def check_knowledge_graph_evidence(candidate_id: str, dry_run: bool = False) -> dict:
    """Check knowledge graph for completion evidence via MCP server.
    
    Returns evidence dict with recommendation (clear/escalate/block).
    """
    log.info("  Checking knowledge graph for completion evidence...")
    
    try:
        result = assess_candidate_relevance(candidate_id, write_questions=False)
        evidence = json.loads(result) if isinstance(result, str) else result
    except Exception as e:
        log.warning("  Evidence check failed: %s", e)
        return {"recommendation": "clear", "signals": {}}
    
    recommendation = evidence.get("recommendation", "clear")
    has_completion = evidence.get("has_completion_evidence", False)
    has_duplicates = evidence.get("has_duplicate_candidates", False)
    has_overlaps = evidence.get("has_work_request_overlaps", False)
    
    log.info("  Evidence: completion=%s, duplicates=%s, overlaps=%s → %s",
             has_completion, has_duplicates, has_overlaps, recommendation)
    
    return {
        "recommendation": recommendation,
        "has_completion_evidence": has_completion,
        "has_duplicate_candidates": has_duplicates,
        "has_work_request_overlaps": has_overlaps,
        "completion": evidence.get("completion", {}),
        "duplicates": evidence.get("duplicates", {}),
        "overlaps": evidence.get("overlaps", {}),
    }


def create_evidence_questions(candidate_id: str, evidence: dict, dry_run: bool = False) -> list[dict]:
    """Create open questions from knowledge graph evidence via MCP server.
    
    When recommendation is 'auto_close', questions are inserted as RESOLVED
    (non-blocking) to preserve the audit trail without blocking the pipeline.
    """
    recommendation = evidence.get("recommendation", "clear")
    
    if recommendation == "clear":
        return []
    
    auto_close = (recommendation == "auto_close")
    questions = []
    
    # Create question for each evidence type
    if evidence.get("completion", {}).get("evidence", 0) > 0:
        ev = evidence["completion"]["evidence"][0] if evidence["completion"].get("evidence") else {}
        questions.append({
            "question": f"Similar work found: {ev.get('title', 'unknown')} — is this a duplicate?",
            "category": "WORK_COMPLETED",
            "evidence_type": "work_request",
        })
    
    if evidence.get("duplicates", {}).get("duplicates", 0) > 0:
        dup = evidence["duplicates"]["duplicates"][0] if evidence["duplicates"].get("duplicates") else {}
        questions.append({
            "question": f"Candidate '{dup.get('title', 'unknown')}' already promoted — is this a duplicate?",
            "category": "DUPLICATE_CANDIDATE",
            "evidence_type": "candidate",
        })
    
    if evidence.get("overlaps", {}).get("overlaps", 0) > 0:
        overlap = evidence["overlaps"]["overlaps"][0] if evidence["overlaps"].get("overlaps") else {}
        questions.append({
            "question": f"WorkRequest '{overlap.get('title', 'unknown')}' completed — is this work already done?",
            "category": "WORK_COMPLETED",
            "evidence_type": "work_request",
        })
    
    if not questions:
        return []
    
    log.info("  Creating %d evidence questions%s...", len(questions), " (auto-closed)" if auto_close else "")
    created = []
    
    for q in questions:
        if dry_run:
            log.info("    [DRY RUN] Would create: %s (%s) %s", q["question"][:60], q["category"],
                     "[auto-closed]" if auto_close else "")
            created.append(q)
            continue
        
        try:
            question_id = write_evidence_question(
                candidate_id=candidate_id,
                evidence_type=q.get("evidence_type", "unknown"),
                evidence_title=q["question"][:100],
                evidence_id="",
                confidence="high" if auto_close else "medium",
                auto_close=auto_close,
            )
            
            if question_id:
                log.info("    Created question: %s%s", question_id[:8], " [auto-closed]" if auto_close else "")
                emit_question_created(
                    question_id=question_id,
                    candidate_id=candidate_id,
                    category=q["category"],
                    title=q["question"],
                    causation_id=None,
                )
                created.append({"id": question_id, "auto_closed": auto_close, **q})
            else:
                log.error("    Failed to create question: %s", q["question"][:40])
        except Exception as e:
            log.error("    Error creating question: %s", e)
    
    return created


def check_ripple_assessment(requirement_id: str, dry_run: bool = False) -> dict | None:
    """Run ripple assessment if requirement exists."""
    sql = f"SELECT nebula.assess_ripple('{requirement_id}'::uuid);"
    rc, out = psql(sql)
    
    if rc != 0 or not out:
        return None
    
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        return None
    
    risk = result.get("risk_level", "UNKNOWN")
    blast = result.get("blast_radius", {})
    blocking = result.get("questions", {}).get("total_blocking", 0)
    
    if not dry_run:
        emit_ripple_assessed(
            requirement_id=requirement_id,
            risk_level=risk,
            blast_radius=blast,
            blocking_questions=blocking,
        )
    
    return result


def promote_candidate(candidate_id: str, assessment: dict, risk_level: str, dry_run: bool = False) -> bool:
    """Promote candidate to requirement."""
    score = assessment.get("score", 0)
    
    if dry_run:
        log.info("  [DRY RUN] Would promote %s (CPF=%.3f, risk=%s)", candidate_id[:8], score, risk_level)
        return True
    
    # Advance status so candidate is not re-groomed
    psql(f"""
        UPDATE nebula.harvest_candidates
        SET status = 'promoted', updated_at = NOW()
        WHERE id = '{candidate_id}'::uuid
    """)
    
    event_id = emit_candidate_greenlit(
        candidate_id=candidate_id,
        cpf_score=score,
        risk_level=risk_level,
    )
    
    log.info("  ✓ Greenlit: %s (event: %s)", candidate_id[:8], event_id[:8])
    return True


def escalate_candidate(candidate_id: str, assessment: dict, risk_level: str, reason: str, dry_run: bool = False) -> bool:
    """Escalate candidate for human review."""
    score = assessment.get("score", 0)
    
    if dry_run:
        log.info("  [DRY RUN] Would escalate %s (CPF=%.3f, risk=%s, reason=%s)", 
                 candidate_id[:8], score, risk_level, reason[:40])
        return True
    
    # Advance status so candidate is not re-groomed
    psql(f"""
        UPDATE nebula.harvest_candidates
        SET status = 'useful', updated_at = NOW()
        WHERE id = '{candidate_id}'::uuid
    """)
    
    emit_candidate_escalated(
        candidate_id=candidate_id,
        cpf_score=score,
        risk_level=risk_level,
        reason=reason,
    )
    
    log.info("  ⚠ Escalated: %s (reason: %s)", candidate_id[:8], reason[:40])
    return True


def get_candidates_for_grooming(limit: int = 50) -> list[dict]:
    """Get candidates that need CPF assessment."""
    sql = f"""
        SELECT id, title, compilation_readiness, status
        FROM nebula.harvest_candidates
        WHERE status = 'pending'
        ORDER BY compilation_readiness DESC NULLS LAST
        LIMIT {limit};
    """
    rc, out = psql(sql)
    
    if rc != 0 or not out:
        return []
    
    candidates = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            candidates.append({
                "id": parts[0],
                "title": parts[1],
                "compilation_readiness": float(parts[2]) if parts[2] else None,
                "status": parts[3],
            })
    
    return candidates


def run_grooming_cycle(args):
    """Run a full grooming cycle."""
    log.info("=" * 60)
    log.info("Planner Grooming Cycle")
    log.info("Time: %s", datetime.now().isoformat())
    log.info("Mode: %s", "DRY RUN" if args.dry_run else "LIVE")
    log.info("=" * 60)
    
    if args.candidate:
        candidates = [{"id": args.candidate, "title": "manual"}]
    else:
        candidates = get_candidates_for_grooming(args.limit)
        log.info("Candidates for grooming: %d", len(candidates))
    
    if not candidates:
        log.info("Nothing to groom.")
        return
    
    stats = {
        "assessed": 0, 
        "questions_created": 0, 
        "evidence_questions_created": 0,
        "greenlit": 0, 
        "escalated": 0,
        "blocked_by_evidence": 0,
    }
    
    for c in candidates:
        cid = c["id"]
        log.info("-" * 40)
        log.info("Processing: %s", c.get("title", cid[:8])[:60])
        
        # 1. CPF Assessment
        assessment = assess_candidate(cid, args.dry_run)
        if not assessment:
            continue
        stats["assessed"] += 1
        
        score = assessment.get("score", 0)
        promotable = assessment.get("promotable", False)
        
        # 2. Check knowledge graph for completion evidence (via MCP server)
        evidence = check_knowledge_graph_evidence(cid, args.dry_run)
        evidence_recommendation = evidence.get("recommendation", "clear")
        
        # 3. Generate questions from CPF gaps
        if not args.no_questions:
            questions = create_questions(cid, assessment, args.dry_run)
            stats["questions_created"] += len(questions)
        
        # 4. Generate questions from evidence (blocks promotion)
        if evidence_recommendation != "clear" and not args.no_questions:
            evidence_questions = create_evidence_questions(cid, evidence, args.dry_run)
            stats["evidence_questions_created"] += len(evidence_questions)
        
        if args.questions_only:
            continue
        
        # 5. Decision point
        if promotable:
            # Evidence blocks promotion
            if evidence_recommendation == "block":
                escalate_candidate(cid, assessment, "UNKNOWN", 
                                 f"Evidence suggests work is completed: {evidence}",
                                 args.dry_run)
                stats["escalated"] += 1
                stats["blocked_by_evidence"] += 1
                continue
            
            # Check ripple assessment if requirement exists
            ripple = check_ripple_assessment(cid, args.dry_run)
            
            if ripple:
                risk = ripple.get("risk_level", "UNKNOWN")
                blocking = ripple.get("questions", {}).get("total_blocking", 0)
                
                if risk == "CRITICAL" or blocking > 0:
                    escalate_candidate(cid, assessment, risk, 
                                     f"Risk={risk}, blocking_questions={blocking}",
                                     args.dry_run)
                    stats["escalated"] += 1
                else:
                    promote_candidate(cid, assessment, risk, args.dry_run)
                    stats["greenlit"] += 1
            else:
                # No ripple assessment available — greenlight based on CPF alone
                promote_candidate(cid, assessment, "LOW", args.dry_run)
                stats["greenlit"] += 1
        else:
            log.info("  Not promotable (CPF < 0.7)")
    
    # Summary
    log.info("=" * 60)
    log.info("Grooming Summary:")
    log.info("  Assessed: %d", stats["assessed"])
    log.info("  Questions created: %d", stats["questions_created"])
    log.info("  Evidence questions created: %d", stats["evidence_questions_created"])
    log.info("  Greenlit: %d", stats["greenlit"])
    log.info("  Escalated: %d", stats["escalated"])
    log.info("  Blocked by evidence: %d", stats["blocked_by_evidence"])
    log.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Planner backlog grooming with cascade events")
    parser.add_argument("--candidate", type=str, help="Assess a specific candidate UUID")
    parser.add_argument("--limit", type=int, default=50, help="Max candidates to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without DB writes or events")
    parser.add_argument("--questions-only", action="store_true", help="Generate questions only, no promotion")
    parser.add_argument("--no-questions", action="store_true", help="Skip question generation")
    args = parser.parse_args()
    
    run_grooming_cycle(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
