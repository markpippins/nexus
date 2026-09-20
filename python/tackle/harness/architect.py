"""architect.py — Architect harness: deterministic check + LLM inference.

The architect harness:
1. Deterministically checks for ToDo requirements (no LLM needed)
2. Builds rich context for each requirement
3. Invokes LLM to write implementation plans
4. Invokes LLM to triage and create open questions
5. Persists results to the database

Usage:
    from tackle.harness import ArchitectHarness
    import psycopg2

    conn = psycopg2.connect("postgresql://pguser:pgpass@localhost:5432/nexus")
    harness = ArchitectHarness(conn)
    result = harness.run_cycle(limit=10)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

import psycopg2

from .base import Harness, ModelConfig

log = logging.getLogger("architect")


# ── System Prompt ──────────────────────────────────────────────────────

ARCHITECT_SYSTEM_PROMPT = """You are the Architect agent in the Nexus WorkRequest pipeline.

Your job is to analyze requirements and produce two deliverables:

## 1. Implementation Plan

For each requirement, write a detailed implementation plan that:
- Describes the technical approach (what to build, how to structure it)
- Lists specific files to create or modify
- Defines acceptance criteria as concrete, testable statements
- Identifies dependencies and risks
- Is detailed enough that a builder can implement without creative decisions

## 2. Open Questions

For each requirement, identify blocking questions that must be answered before implementation:
- Ambiguities in the requirement that need clarification
- Missing information (API specs, data models, integration points)
- Scope questions (what's in/out of bounds)
- Technical decisions that need upstream approval

## Output Format

Return a JSON object with this structure:
```json
{
  "plans": [
    {
      "requirement_id": "uuid",
      "title": "Plan title",
      "goal": "What this plan achieves",
      "approach": "Technical approach description",
      "files_affected": ["path/to/file1.py", "path/to/file2.py"],
      "acceptance_criteria": ["Criterion 1", "Criterion 2"],
      "risks": ["Risk 1", "Risk 2"],
      "dependencies": ["Dependency 1"]
    }
  ],
  "questions": [
    {
      "requirement_id": "uuid",
      "question": "What needs clarification?",
      "category": "AMBIGUITY|SCOPE|TECHNICAL|MISSING_INFO",
      "priority": "HIGH|MEDIUM|LOW",
      "blocking": true
    }
  ]
}
```

Be precise, technical, and actionable. Avoid vague language."""


# ── Context Building ──────────────────────────────────────────────────

def fetch_todo_requirements(conn, limit: int = 50) -> list[dict]:
    """Fetch requirements in ToDo status with context."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            req.id, req.title, req.description, req.status,
            req.priority, req.req_type, req.parent_id,
            req.system_id, req.subsystem_id, req.feature_id,
            req.acceptance_criteria,
            (SELECT count(*) FROM nebula.open_questions oq
             WHERE oq.requirement_id = req.id
             AND oq.blocking = true AND oq.status = 'OPEN') as blocking_questions
        FROM nebula.requirements req
        WHERE req.status = 'ToDo'
        ORDER BY req.created_at ASC
        LIMIT %s
    """, (limit,))
    cols = [d.name for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def build_requirement_context(conn, req: dict) -> dict:
    """Build rich context for a single requirement."""
    req_id = req["id"]

    # Ripple assessment
    cur = conn.cursor()
    cur.execute("SELECT nebula.assess_ripple(%s::uuid)", (req_id,))
    ripple_row = cur.fetchone()
    if ripple_row and ripple_row[0]:
        raw = ripple_row[0]
        ripple = raw if isinstance(raw, dict) else json.loads(raw)
    else:
        ripple = None

    # Existing questions
    cur.execute("""
        SELECT id, title, category, blocking, status
        FROM nebula.open_questions
        WHERE requirement_id = %s AND status = 'OPEN'
        ORDER BY blocking DESC, created_at DESC
    """, (req_id,))
    cols = [d.name for d in cur.description]
    questions = [dict(zip(cols, r)) for r in cur.fetchall()]

    # System/subsystem names
    system_name = None
    subsystem_name = None
    if req.get("system_id"):
        cur.execute("SELECT name FROM nebula.systems WHERE id = %s", (req["system_id"],))
        row = cur.fetchone()
        system_name = row[0] if row else None
    if req.get("subsystem_id"):
        cur.execute("SELECT name FROM nebula.subsystems WHERE id = %s", (req["subsystem_id"],))
        row = cur.fetchone()
        subsystem_name = row[0] if row else None

    cur.close()

    return {
        "requirement": req,
        "ripple": ripple,
        "existing_questions": questions,
        "system_name": system_name,
        "subsystem_name": subsystem_name,
    }


# ── Architect Harness ─────────────────────────────────────────────────

class ArchitectHarness(Harness):
    """Architect harness: deterministic check + LLM inference.

    The harness:
    1. Checks for ToDo requirements (deterministic)
    2. Builds context for each (deterministic)
    3. Invokes LLM to write plans and questions
    4. Persists results to DB
    """

    def __init__(self, conn: psycopg2.extensions.connection, dsn: str = "postgresql://pguser:pgpass@localhost:5432/nexus"):
        super().__init__(role="architect")
        self._conn = conn
        self._dsn = dsn

    def _ensure_connection(self):
        """Reconnect if the connection was closed during an LLM call."""
        try:
            if self._conn.closed:
                raise psycopg2.OperationalError("connection closed")
            # Quick ping
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        except Exception:
            log.info("Reconnecting to database...")
            self._conn = psycopg2.connect(self._dsn)

    def build_prompt(self, context: dict) -> str:
        """Build the LLM prompt from requirement contexts."""
        requirements = context.get("requirements", [])

        if not requirements:
            return ""

        parts = [ARCHITECT_SYSTEM_PROMPT, "", "## Requirements to Process", ""]

        for req_ctx in requirements:
            req = req_ctx["requirement"]
            ripple = req_ctx.get("ripple")

            parts.append(f"### Requirement: {req['title']}")
            parts.append(f"- **ID**: {req['id']}")
            parts.append(f"- **Type**: {req.get('req_type', 'Story')}")
            parts.append(f"- **Priority**: {req.get('priority', 'Medium')}")
            parts.append(f"- **System**: {req_ctx.get('system_name', 'unspecified')}")
            parts.append(f"- **Description**: {req.get('description', 'No description')}")
            parts.append("")

            if req.get("acceptance_criteria"):
                parts.append("**Acceptance Criteria:**")
                criteria = req["acceptance_criteria"]
                if isinstance(criteria, list):
                    for c in criteria:
                        parts.append(f"- {c}")
                elif isinstance(criteria, dict):
                    for k, v in criteria.items():
                        parts.append(f"- {k}: {v}")
                parts.append("")

            if ripple:
                parts.append(f"**Risk Level**: {ripple.get('risk_level', 'UNKNOWN')}")
                blast = ripple.get("blast_radius", {})
                parts.append(f"**Blast Radius**: {blast.get('direct_children', 0)} children, depth {blast.get('max_depth', 0)}")
                parts.append("")

            if req_ctx.get("existing_questions"):
                parts.append("**Existing Open Questions:**")
                for q in req_ctx["existing_questions"]:
                    parts.append(f"- [{q.get('category', '?')}] {q['title']} (blocking={q.get('blocking', False)})")
                parts.append("")

            parts.append("---")
            parts.append("")

        return "\n".join(parts)

    def build_need_spec_prompt(self, context: dict) -> str:
        """Build the LLM prompt for NEEDS_SPEC escalations."""
        requirement = context.get("requirement", {})
        escalation = context.get("escalation", {})

        parts = [
            ARCHITECT_SYSTEM_PROMPT,
            "",
            "## NEEDS_SPEC Escalation",
            "",
            "The Planner has escalated a question that requires a spec and implementation plan.",
            "",
            f"### Original Question",
            f"- **Title**: {escalation.get('question_title', 'Unknown')}",
            f"- **Description**: {escalation.get('question_description', 'No description')}",
            f"- **Created by**: {escalation.get('created_by', 'planner')}",
            "",
            f"### Linked Requirement",
            f"- **ID**: {requirement.get('id', 'Unknown')}",
            f"- **Title**: {requirement.get('title', 'Unknown')}",
            f"- **Description**: {requirement.get('description', 'No description')}",
            f"- **Status**: {requirement.get('status', 'unknown')}",
            f"- **Priority**: {requirement.get('priority', 'Medium')}",
            "",
        ]

        if requirement.get("acceptance_criteria"):
            parts.append("**Acceptance Criteria:**")
            criteria = requirement["acceptance_criteria"]
            if isinstance(criteria, list):
                for c in criteria:
                    parts.append(f"- {c}")
            elif isinstance(criteria, dict):
                for k, v in criteria.items():
                    parts.append(f"- {k}: {v}")
            parts.append("")

        parts.extend([
            "## Your Task",
            "",
            "Write a spec and implementation plan for this requirement.",
            "The spec should describe what to build and why.",
            "The plan should describe how to build it, including files to create/modify.",
            "",
            "## Output Format",
            "",
            "Return a JSON object with this structure:",
            "```json",
            "{",
            '  "spec_title": "Title for the spec",',
            '  "spec_content": "Markdown content of the spec",',
            '  "plan_title": "Title for the implementation plan",',
            '  "plan_goal": "What this plan achieves",',
            '  "acceptance_criteria": ["Criterion 1", "Criterion 2"],',
            '  "files_affected": ["path/to/file1.py", "path/to/file2.py"]',
            "}",
            "```",
        ])

        return "\n".join(parts)

    def handle_response(self, response: str, context: dict) -> dict:
        """Parse LLM response and persist results to DB.

        Returns a result dict with plans_written, questions_created, and
        the completion envelope that proves inference succeeded.
        """
        # Try to extract JSON from response
        parsed = self._extract_json(response)
        if not parsed:
            return {
                "plans_written": 0,
                "questions_created": 0,
                "completion_envelope": None,
                "error": "Could not parse LLM response as JSON",
            }

        plans = parsed.get("plans", [])
        questions = parsed.get("questions", [])

        result = {"plans_written": 0, "questions_created": 0}

        # Persist plans
        for plan in plans:
            req_id = plan.get("requirement_id")
            if not req_id:
                continue

            try:
                self._write_plan(req_id, plan, context)
                result["plans_written"] += 1
            except Exception as e:
                log.error("Failed to write plan for %s: %s", req_id[:8], e)

        # Persist questions
        for question in questions:
            req_id = question.get("requirement_id")
            if not req_id:
                continue

            try:
                self._write_question(req_id, question)
                result["questions_created"] += 1
            except Exception as e:
                log.error("Failed to write question for %s: %s", req_id[:8], e)

        # Build completion envelope — the proof that inference succeeded
        result["completion_envelope"] = {
            "evaluation_status": "COMPLETED",
            "evaluated_by": f"architect-{self.preferred_model.model_name}" if self.preferred_model else "architect-unknown",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "open_question_count": result["questions_created"],
            "plans_written": result["plans_written"],
        }

        return result

    def _extract_json(self, response: str) -> dict | None:
        """Extract JSON from LLM response, handling markdown fences."""
        # Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code fences
        import re
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in text
        brace_start = response.find('{')
        brace_end = response.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(response[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _write_plan(self, req_id: str, plan: dict, context: dict):
        """Write an implementation plan to the database.

        The plan metadata includes a completion envelope that proves
        inference succeeded. Cron checks this before promoting.
        """
        self._ensure_connection()
        cur = self._conn.cursor()

        # Check if plan already exists
        cur.execute("""
            SELECT id FROM nebula.implementation_plans
            WHERE requirement_id = %s AND status = 'pending'
            LIMIT 1
        """, (req_id,))
        if cur.fetchone():
            cur.close()
            log.info("Plan already exists for %s", req_id[:8])
            return

        now = datetime.now(timezone.utc).isoformat()
        plan_id = f"plan-{req_id[:8]}"

        # Completion envelope: proof that inference completed successfully
        envelope = {
            "evaluation_status": "COMPLETED",
            "evaluated_by": f"architect-{self.preferred_model.model_name}" if self.preferred_model else "architect-unknown",
            "completed_at": now,
            "model": self.preferred_model.model_identifier if self.preferred_model else "unknown",
        }

        metadata = {
            "source": "architect_harness",
            "model": self.preferred_model.model_name if self.preferred_model else "unknown",
            "completion_envelope": envelope,
        }

        # blueprints_history is the canonical plan surface (V171); the legacy
        # implementation_plans view is read-only over the folded payload and
        # the V171 mirror trigger retired in V176, so writes must fold into
        # payload (same mapping as conduit's upsertPlan, PR #302).
        cur.execute("""
            INSERT INTO nebula.blueprints_history
            (id, plan_number, title, payload, blueprint_status)
            VALUES (
                gen_random_uuid(), NULL, %s, %s::jsonb, 'pending'
            )
        """, (
            plan.get("title", f"Plan for {req_id[:8]}"),
            json.dumps({
                "requirement_ref": req_id,
                "spec_ref": None,
                "goal": plan.get("goal", ""),
                "content": plan.get("approach", ""),
                "files_affected": plan.get("files_affected", []),
                "acceptance_criteria": plan.get("acceptance_criteria", []),
                "tags": ["architect-generated"],
                "metadata": metadata,
            }),
        ))

        self._conn.commit()
        cur.close()
        log.info("Plan written for %s (envelope: COMPLETED)", req_id[:8])

    def _write_question(self, req_id: str, question: dict):
        """Write an open question to the database."""
        self._ensure_connection()
        cur = self._conn.cursor()

        # Idempotent check
        cur.execute("""
            SELECT id FROM nebula.open_questions
            WHERE requirement_id = %s::uuid
              AND title = %s
              AND status = 'OPEN'
            LIMIT 1
        """, (req_id, question.get("question", "")))
        if cur.fetchone():
            cur.close()
            return

        cur.execute("""
            INSERT INTO nebula.open_questions
            (requirement_id, title, category, blocking, status, created_by)
            VALUES (%s::uuid, %s, %s, %s, 'OPEN', 'architect')
        """, (
            req_id,
            question.get("question", ""),
            question.get("category", "GENERAL"),
            question.get("blocking", True),
        ))

        self._conn.commit()
        cur.close()
        log.info("Question written for %s: %s", req_id[:8], question.get("category", "?"))

    # ── Cycle ─────────────────────────────────────────────────────────

    def check_for_work(self, limit: int = 50) -> list[dict]:
        """Deterministic check for ToDo requirements. No LLM needed."""
        self._ensure_connection()
        return fetch_todo_requirements(self._conn, limit)

    def run_cycle(self, limit: int = 50, dry_run: bool = False) -> dict:
        """Run a full architect cycle.

        1. Check for ToDo requirements (deterministic)
        2. Build context for each
        3. Invoke LLM to write plans and questions
        4. Persist results
        """
        log.info("=" * 60)
        log.info("Architect Harness Cycle")
        log.info("Time: %s", datetime.now().isoformat())
        log.info("Model: %s", self.preferred_model.model_name if self.preferred_model else "none")
        log.info("=" * 60)

        # 1. Check for work (deterministic)
        requirements = self.check_for_work(limit)
        log.info("ToDo requirements: %d", len(requirements))

        if not requirements:
            log.info("Nothing to process.")
            return {"processed": 0}

        # 2. Build context for each
        contexts = []
        for req in requirements:
            ctx = build_requirement_context(self._conn, req)
            contexts.append(ctx)

        # 3. Prepare context for LLM
        full_context = {"requirements": contexts}

        if dry_run:
            prompt = self.build_prompt(full_context)
            log.info("Prompt length: %d chars", len(prompt))
            log.info("[DRY RUN] Would invoke LLM")
            return {"processed": len(contexts), "dry_run": True}

        if not self.preferred_model:
            self.load_model_info()

        if not self.preferred_model:
            log.error("No model configured — cannot run LLM")
            return {"processed": 0, "error": "no models"}

        # 4. Invoke LLM
        prompt = self.build_prompt(full_context)
        response = self.invoke_llm(prompt)

        if not response:
            return {"processed": 0, "error": "no llm response"}

        # 5. Handle response
        result = self.handle_response(response, full_context)

        log.info("=" * 60)
        log.info("Architect Summary:")
        log.info("  Plans written: %d", result.get("plans_written", 0))
        log.info("  Questions created: %d", result.get("questions_created", 0))
        log.info("=" * 60)

        return {"processed": len(contexts), **result}
