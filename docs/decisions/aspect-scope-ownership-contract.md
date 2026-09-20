# Aspect Scope and Ownership Contract (A1)

**Status:** Active — Singular scope and ownership declaration for the Aspects project.  
**Authority:** Analyst decision `e1b17881-f067-4d1f-bdb1-6176f267af8b` (2026-09-20).  
**Sequencing:** A1 is the foundational contract; A2–A5 depend on it.  
**Governance:** Architect owns the contract; Analyst proposes; DBA executes schema changes; Engineer implements.

---

## 1. Purpose

This document declares the **singular Aspect scope and ownership contract** for the Aspects project. It is the root authority declaration from which all Aspect governance, vocabulary, bindings, projections, and concept packages derive.

> **Principle:** Do not describe Aspect authority — declare it. Do not suggest constraints — enforce them.

---

## 2. Aspect Scope Declaration

### 2.1 What Aspects Is

Aspects is the **umbrella governance project** for the governed tag vocabulary, tag bindings, projected metadata boundary, and named concept-package/query substrate. It is the authoritative layer that converts Expression's non-authoritative, source-owned projected tags into governed, source-referenced, versioned bindings that Resolution and downstream consumers can trust.

> **From Analyst decision `b741dbea`:** "Aspects is the umbrella for the governed SOLScript tag vocabulary, tag bindings, projected metadata boundary, and named concept-package/query substrate."

### 2.2 Aspect Scope (What Is Inside)

| Component | Description | Authority |
|-----------|-------------|-----------|
| **Governed Tag Vocabulary** | Versioned, governed SOLScript tag definitions (canonical names, normalized forms, member kinds, hierarchy) | Architect owns definitions; DBA owns schema; Engineer implements |
| **Tag Bindings** | Source-referenced, versioned bindings from Expression's projected tags to governed vocabulary entries | Analyst proposes; Architect approves; DBA enforces via CHECK constraints |
| **Projected Metadata Boundary** | The explicit boundary separating Expression's non-authoritative projected tags from governed bindings | Architect declares; Engineer enforces via boundary validation |
| **Concept Packages** | Named, versioned, reproducible query bundles on the existing Expression IR/compiler | Analyst proposes; Architect approves; Engineer implements |

### 2.3 What Is Explicitly Outside Aspect Scope

| Excluded | Reason |
|----------|--------|
| V182 `relation_vocabulary` | Separate relation-predicate vocabulary; not renamed; separate ownership (A2) |
| Expression projected tags (pre-binding) | Remain source-owned, non-authoritative until bound; `governed_tag_id = None` until bound |
| Identity resolution / semantic inference | Resolution's canonical authority; Aspects does not create competing identity authority |
| Graph writes / proposition admission / authority changes | Resolution's canonical authority (A1 defers to Resolution) |

---

## 3. Ownership Contract

### 3.1 Ownership Model

| Asset | Owner | Authority | Constraint |
|-------|-------|-----------|------------|
| **Governed Tag Vocabulary** | Architect (definitions), DBA (schema), Engineer (impl) | Architect approves definitions; DBA enforces schema; Engineer implements CRUD | No vocabulary change without Architect approval + DBA migration |
| **Tag Bindings** | Analyst proposes, Architect approves, DBA enforces | Analyst proposes bindings; Architect approves; DBA enforces CHECK constraints | Bindings immutable once committed; updates = new version |
| **Projected Metadata Boundary** | Architect declares; Engineer enforces | Architect declares boundary; Engineer validates via boundary checks | Expression boundary remains `non_authoritative`; Aspects boundary = `governed` |
| **Concept Packages** | Analyst proposes; Architect approves; Engineer implements | Analyst defines package scope; Architect approves scope; Engineer implements | Packages are versioned, reproducible, source-referenced |

### 3.2 Authority Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│                        RESOLUTION (Canonical)                   │
│  Identity, Lineage, Disposition, Governed Evaluation Joins     │
└─────────────────────────────────────────────────────────────────┘
                                 ▲
                                 │ governed evaluation joins
                                 │ (V191 bcrypt, CHECK constraints)
┌─────────────────────────────────────────────────────────────────┐
│                        ASPECTS (Governed)                       │
│  Tag Vocabulary  |  Tag Bindings  |  Metadata Boundary  |  Packages  │
│      Architect         Analyst/Arch      Architect           Analyst/Arch │
└─────────────────────────────────────────────────────────────────┘
                                 ▲
                                 │ binds / governs
                                 │ (A1 contract)
┌─────────────────────────────────────────────────────────────────┐
│                    EXPRESSION (Non-Authoritative)               │
│  Projected Tags  |  Projected Metadata  |  Concept Candidates  │
│     status: observed/projected    authority_status: non_authoritative    │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 Ownership Invariants

1. **No dual authority.** No asset has dual ownership. Every governed asset has exactly one owner.
2. **No authority escalation.** A child component cannot have broader authority than its parent.
3. **Binding constraints cascade.** A child component inherits all constraints from its parent.
4. **No shadow governance.** Two governance components cannot have overlapping authority domains.
4. **Source referential integrity.** Every governed binding must reference a stable source identity and revision from Expression.

---

## 3.4 Ownership Transfer Protocol

Ownership transfer requires:
1. **Analyst proposal** — written proposal with rationale
2. **Architect approval** — explicit approval recorded in decisions forum
3. **DBA execution** — schema/migration execution with rollback plan
4. **Engineer implementation** — code changes with tests
5. **Architect ratification** — post-deployment verification and sign-off

---

## 4. Governance Model

### 4.1 Decision Rights

| Decision Type | Proposer | Approver | Executor | Verifier |
|---------------|----------|----------|----------|----------|
| Vocabulary definition | Analyst | Architect | Engineer | Architect |
| Tag binding | Analyst | Architect | DBA/Engineer | Architect |
| Metadata boundary | Architect | Architect | Engineer | Architect |
| Concept package scope | Analyst | Architect | Engineer | Architect |
| Boundary change (Expr↔Aspects) | Architect | Architect | Engineer/DBA | Architect |

### 4.2 Change Control

All changes to the Aspect scope, vocabulary, bindings, or boundary follow:

1. **Proposal** — Analyst writes proposal with rationale, scope, and acceptance criteria
2. **Review** — Architect reviews; DBA assesses schema impact; Engineer assesses impl effort
3. **Decision** — Architect records decision in decisions forum (`type:decision`, `to:architect`)
4. **Execution** — DBA (schema), Engineer (code), Analyst (proposals) execute in parallel
5. **Verification** — Architect verifies; tests pass; boundary assertions hold
6. **Ratification** — Architect posts completion record; version bumped

### 4.3 Emergency Override

In case of production incident requiring immediate boundary change:
1. Architect declares emergency (recorded in decisions forum)
2. DBA/Engineer execute minimal fix
3. Full proposal/review cycle within 48 hours
4. Post-incident review within 7 days

---

## 5. Boundary Declarations

### 5.1 Expression → Aspects Boundary

| Aspect | Expression (Non-Authoritative) | Aspects (Governed) |
|--------|-------------------------------|-------------------|
| **Tags** | `governed_tag_id: null`, `authority_status: non_authoritative` | `governed_tag_id: <uuid>`, `authority_status: governed` |
| **Metadata** | `authority_status: projected` | `governed_tag_id: <uuid>`, `authority_status: governed` |
| **Storage** | `staging` (regenerable) | `canonical` (durable, versioned) |
| **Authority** | `non_authoritative` | `governed` |
| **Evaluation** | Not admissible | Admissible via Resolution joins |

**Boundary Enforcement:** Expression boundary validation (`expression_boundary()`) asserts `authority_status: non_authoritative`. Aspects boundary validation will assert `authority_status: governed` and `governed_tag_id IS NOT NULL`.

### 5.2 Resolution → Aspects Boundary

| Aspect | Resolution (Canonical) | Aspects (Governed) |
|--------|----------------------|-------------------|
| **Identity** | Canonical | References Resolution identity |
| **Lineage** | Canonical | References Resolution lineage |
| **Disposition** | Canonical | References Resolution disposition |
| **Evaluation** | Canonical | Governed evaluation joins via Resolution |

**Evaluation Joins:** Aspects tags are admissible in governed evaluation only via Resolution joins (per V191). The CHECK constraints (`users_password_bcrypt_check` pattern) enforce that only bcrypt-hashed / governed tags are admissible.

---

## 6. Versioning and Contract Evolution

### 6.1 Versioning Scheme

| Level | Format | Trigger |
|-------|--------|---------|
| **Contract Major** | `v1.0`, `v2.0` | Boundary change, ownership transfer, scope expansion |
| **Contract Minor** | `v1.1`, `v1.2` | New vocabulary entry, new binding type, new package |
| **Vocabulary Revision** | `vocab-001`, `vocab-002` | New governed tag added/removed |
| **Binding Revision** | `bind-001`, `bind-002` | New binding added/updated |

### 6.2 Contract Evolution Rules

1. **No silent expansion.** Every scope expansion requires Architect decision.
2. **Backward compatibility.** Minor versions must not break existing bindings.
3. **Explicit deprecation.** Removed vocabulary entries marked `deprecated` with 90-day sunset.
4. **Audit trail.** Every change recorded in decisions forum with `type:decision`, `to:architect`.

---

## 7. Acceptance Criteria (A1 Complete When)

- [ ] This document published in `docs/decisions/aspect-scope-ownership-contract.md`
- [ ] Architect records ratification decision in decisions forum (`type:decision`, `to:architect`)
- [ ] Aspect scope declared in `schemas/aspect/` (vocabulary schema, binding schema)
- [ ] Expression boundary validation updated to reject governed tags in Expression bundles
- [ ] Aspects boundary validation implemented (asserts `authority_status: governed`, `governed_tag_id IS NOT NULL`)
- [ ] V191 migration applied (bcrypt backfill + CHECK constraints) — **DONE**
- [ ] Architect records ratification decision in decisions forum

---

## 8. Related Artifacts

| Artifact | Location | Relationship |
|----------|----------|--------------|
| Analyst continuation queues decision | `e1b17881` | Creates A1-A5 queues |
| Naming decision | `b741dbea` | Names "Aspects" project |
| V191 migration | `sql/V191__users_password_bcrypt.sql` | Enforces bcrypt + CHECK constraints (DONE) |
| Expression boundary | `python/expression/boundary.py` | Declares `authority_status: non_authoritative` |
| Governed projections spec | `docs/governed-projections.md` | Pattern for governed projections |
| V182 relation_vocabulary | `docs/decisions/terrain-vs-registry-boundary.md` | Separate ownership (A2) |

---

## 9. Ratification

**Architect ratification required.** Upon Architect approval, this contract becomes binding. All subsequent Aspect work (A2–A5) must conform to this contract.

---

*End of Aspect Scope and Ownership Contract v1.0 — 2026-09-20*
