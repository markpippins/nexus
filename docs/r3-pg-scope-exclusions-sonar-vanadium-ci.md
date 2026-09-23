# R3 — PostgreSQL Scope Exclusions: Sonar stack (sonar 15.19 / vanadium-ci `postgres:15-alpine`)

> **Provenance:** Architect ruling `619049d6` (2026-09-23, interim ownership per precedent `627da746`), item **R3**, on fleet-audit thread `ecb91216`:
> *"R3 — sonar 15.19: DOCUMENT as out-of-fleet-scope (no nexus coupling; schedule before PG 15 EOL Nov 2027 or document retention). Same for vanadium-ci postgres:15-alpine."*
>
> **What this doc is:** the standing exception register required by the fleet PG standard (D1: `postgres:17`), per the ruling. **What it is not:** an upgrade plan or a license to defer — see the [hard rule](#6-the-hard-rule) and the [2027-05 checkpoint](#5-disposition--exit-path).

---

## 1. Ground truth — the two named exceptions are one physical stack

The audit named the exception twice (once by observed runtime version, once by repo pin). Live-probed 2026-09-23 (read-only SSH to vanadium):

| Surface | Value |
|---|---|
| Container | `vd-ci-sonar-db` (compose project `vanadium-ci`, service `sonar-db`) |
| Repo pin | `postgres:15-alpine` — `docker/vanadium-ci/docker-compose.yml:6` |
| Live server version | **15.19** (Debian-less alpine build; probed in-container) |
| Application it serves | `vd-ci-sonarqube` — `sonarqube:lts-community` = **SonarQube 9.9.8.100196** |
| Data | named volume `vanadium-ci_sonar-db-data` |
| Schema owner | SonarQube (application-managed migrations) — **no nexus schema, no nexus code touches this database** |

**Registry entries:** what looks like two exceptions is **one runtime exception (§3, R3-EX1)** plus its **repo-pin manifestation (§4, R3-EX2)**, with one adjacent dormant item the audit did not name (§4, R3-EX3).

## 2. Why the exclusion is real (the technical rationale)

**SonarQube 9.9 LTS supports PostgreSQL 11–15.** PG 16/17 are outside its supported-platform matrix (verified 2026-09-23 against the SonarQube 9.9→2025.1 upgrade guidance, which states 9.9.x supports PG 11–15 while 2025.1+ supports PG 13–17; re-verify against sonarsource.com's supported-platforms page at execution time).

Consequences:

- Bumping this DB to the fleet-standard 17 **without** upgrading SonarQube first would move the stack to an **unsupported application/database pair** — a worse risk posture than a supported PG 15.
- Therefore the correct unit of change is the **SonarQube application upgrade**, with the PG bump as its consequence — an application-lifecycle decision owned by the DBA/sysadmin lane, not a mechanical version-pin change.
- This is precisely why the ruling classes it as *out-of-fleet-scope* rather than *non-compliant*: the fleet standard (D1) governs nexus-owned stores; the sonar stack's version is **constrained by its application**, is fully isolated, and carries its own migration calendar.

## 3. R3-EX1 — Runtime exception: `vd-ci-sonar-db` (PG 15.19)

| Field | Value |
|---|---|
| Object | `vd-ci-sonar-db` on vanadium, `postgres:15-alpine`, version 15.19 |
| Owner | DBA (currently routed to interim owner: architect, precedent `627da746`) |
| Coupling — CI | `.github/workflows/vanadium-sonar.yml` (quality-gate scans on the self-hosted vanadium runner). Outage-resilient **by design** (DBA record `f8b7f689`): the preflight probe skips scans with a warning when the server is unreachable, so the required `sonar` check stays green through stack downtime — CI cannot hard-block on this stack's availability. |
| Coupling — nexus | **None.** Repo-wide grep: only the compose files and the two backup scripts reference the sonar DB. No application code, no shared schema, no federation. |
| Backup coverage | **Daily, verified script path**: `bin/vanadium-ci-backup.sh` (timer `backup-vanadium-ci.timer`, 04:22 local) takes `pg_dump -Fc` of the `sonar` database plus SonarQube data/extensions tars. The exception has real data protection. |
| Known caveat (recorded, not blocking) | The base image is **alpine/musl**. The fleet's alpine-collation doctrine (seed manifests are glibc-baked; musl ordering differs) does not bite here — the sonar DB never runs seed-guard — but any future base-image switch for this DB must be treated as a **collation-changing migration** (index rebuild risk), not a tag bump. |
| Disposition | **Retain PG 15 on SonarQube 9.9 LTS.** Exit path in §5. |

## 4. R3-EX2 / R3-EX3 — Pin manifestations and the dormant twin

**R3-EX2 — the repo pin.** `docker/vanadium-ci/docker-compose.yml:6` (`image: postgres:15-alpine`). The workflow-lint family (`bin/wf_lint.py`, PR #490) deliberately scans `.github/workflows` only — compose files are documented out of its scope. This doc **is** the register entry that pins stand on: the R3-EX1 exception legitimizes this pin until the §5 exit path executes. Any compose change that alters the pin without updating this doc is a violation of the registry.

**R3-EX3 — dormant twin (flagged, not ruled).** `docker/sonar/docker-compose.yml` on titanium (staged "rehome from vanadium, 2026-08-26", not currently running — verified via `docker ps`): `postgres:16-alpine` + `sonarqube:9.9-community`. Two problems at activation time:

1. **16-alpine would be a NEW out-of-standard pin** (below fleet standard 17) with no exception behind it.
2. **16 is also outside SonarQube 9.9's supported matrix (11–15)** — the staged config is internally inconsistent today.

Standing directive: if the rehome ever runs, it must land on the §5 target architecture (SonarQube release supporting PG 16/17 + PG 17), or it re-enters this register as a live exception requiring its own ruling.

## 5. Disposition & exit path

**Runway math:** PG 15 upstream EOL is **November 2027** (final release series per the upstream second-Thursday cadence; the ruling's anchor). From 2026-09 that is ~14 months.

**Checkpoint (calendar, not aspiration): 2027-05-01.** Six months before EOL, whichever of these is true defines the state:

- **Preferred — upgrade path executed or scheduled:**
  1. Upgrade SonarQube 9.9 LTS → a release supporting PG 16/17 (2025.1+ line per current guidance; at execution time, take SonarQube's then-current LTS and its supported-PG matrix as authoritative — do not act on this doc's 2026 snapshot).
  2. Migrate the DB to PG 17 via **fresh volume + restore from the existing backup tier** (`sonar-db__*.dump` from `vanadium-ci-backup.sh`), not in-place major upgrade — small database, clean lineage, and the restore doubles as a live test of the backup tier's integrity.
  3. Bump the compose pin to `postgres:17` (or the sonar-recommended variant), update this doc, close R3-EX1/EX2.
  4. Sequence note: the same window is the natural place to settle R3-EX3 (dormant twin) — one sonar migration effort, both stacks resolved.
- **Fallback — documented retention:** if SonarQube's upgrade economics have changed by 2027-05, the operator/DBA re-rules explicitly: retention-past-EOL (accepting unpatched-PG risk on an isolated, backing-up-covered, zero-nexus-coupling store) or an earlier decommission of the self-hosted sonar tier. Silence is not a disposition — the checkpoint **must** produce one of the two.

## 6. The hard rule

This doc does not convert the exception into permission for PG 15 to persist by default. The exclusion holds **only** while:

1. SonarQube 9.9 LTS is the serving application (its matrix constrains the DB), **and**
2. the backup tier keeps covering the sonar DB (spot-verify `sonar-db__*.dump` freshness at any review), **and**
3. the **2027-05 checkpoint** produces an explicit disposition.

Any of the three lapsing re-opens the exception as a compliance finding (Inspector lane per I2).

## 7. Governance

- **Owner:** DBA (interim: architect per `627da746`); execution work routes to engineer on ruling.
- **Change control:** material edits to this register (new exclusions, changed dispositions) are a new R3 revision — record + PR, never silent edits.
- **Sources of record:** ruling `619049d6` (thread `ecb91216`), fleet-audit records (DBA/architect, 2026-09-22/23), live probes of 2026-09-23 (vanadium SSH, read-only), SonarQube upgrade-guidance snapshot (re-verify before execution).
