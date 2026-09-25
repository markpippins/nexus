#!/usr/bin/env python3
"""wf_lint.py — shared harness for the workflow-lint rule family.

One harness, several small rules, one CI gate. Born from two real drift
classes: the 9 stale postgres:16 CI pins (fleet PG audit, thread ecb91216;
fixed via #470, guarded by #474) and the 5 dead base images that reached
PR CI (4x eclipse-temurin:21-jdk-slim — a tag family Docker Hub never
published, verified 404 — plus openjdk:21-jdk-slim from the discontinued
openjdk repo; architect record 1790131197330, fixed via #456-fixes/#481).

Rules
  postgres-pin   workflow postgres/pgvector pin major >= fleet standard
                 (default 17; WF_LINT_PG_MIN_MAJOR, legacy
                 PG_PIN_MIN_MAJOR honored). Unversioned/latest warns.
  dead-base      Dockerfile FROM images whose repo/tag is known dead:
                 openjdk:* (repo discontinued) and eclipse-temurin:*-slim
                 (never published). Table is extensible.
  eol-runtime    setup-* runtime versions past EOL fail; within WARN_DAYS
                 (180) of EOL warns. node/python/go tables are release
                 facts; go end-dates are cadence estimates (+2 months
                 past next release). java has a hard floor of 17.
                 WF_LINT_AS_OF=YYYY-MM-DD freezes the clock (tests).
  action-ref     floating branch refs (uses: x@main, master, develop,
                 HEAD) and bare uses: without @ fail. Major-tag pins
                 (@v4) are the house convention and pass; set
                 WF_LINT_REQUIRE_SHA=1 to require full 40-hex SHAs.
  job-hardening  structural: every job carries timeout-minutes (GitHub's
                 default 360 min means a wedged job holds a runner for
                 hours) and every workflow declares a permissions block
                 (least-privilege; house default is a top-level
                 `contents: read` above jobs:). Findings anchor to the
                 job header / on: line so the allow marker works there.
                 Reusable-workflow caller jobs (job-level uses:) are
                 exempt — GitHub rejects timeout-minutes on them.
  npm-ci         `npm install` is non-reproducible wherever a committed
                 package-lock.json applies, and lockfile adoption (#518)
                 is opt-in per service — so enforcement is exactly as
                 opt-in: the rule only fires where a lock exists. There:
                 workflows fail when a run block executes `npm install`
                 with an effective working-directory (step-level
                 working-directory:, the job's defaults.run one, or an
                 in-block `cd`) holding a lock; Dockerfiles fail when an
                 `npm install` RUN's effective dir (last `cd` on the
                 chain, else the Dockerfile's own dir) is lock-bearing —
                 remediation is `COPY package-lock.json` + `npm ci`.
                 Exempt: `-g/--global` (no project manifest) and
                 `--package-lock-only` (deliberate lock regeneration).
                 `npm i` shorthand is not recognized (house code writes
                 the long form).
  maven-cache    jobs that run mvn/mvnw on the runner must cache the Maven
                 repository: an actions/setup-java step without `cache:
                 maven` cold-pulls Central, and concurrent cold pulls trip
                 its rate limiter (the 2026-09-25 main 429 incident, run
                 36088456372; fixed in #569, guarded here). Fires once per
                 job, anchored at the first uncached setup-java step.
                 Docker-internal Maven builds (docker-gate shape) are out
                 of scope — no runner-level mvn, and the host cache would
                 not apply. mvnw counts (same cold-pull class); a wrong-
                 ecosystem cache (gradle/sbt) does not satisfy it.
  node-cache     jobs that run npm (ci|install) inside a lock-bearing
                 directory must cache the npm store: actions/setup-node
                 without `cache: npm` re-downloads the dependency tree
                 every run. The gate mirrors GitHub's own semantics —
                 `cache: npm` hard-fails without a lockfile — so the rule
                 fires only where the remediation is satisfiable: a
                 package-lock.json must exist at the scan root or in one
                 of the job's working-directory/cd targets. Lockless
                 installs (deliberate, per npm-ci's opt-in stance) stay
                 silent; the message names the lock so
                 cache-dependency-path is copy-pasteable.
  pip-cache      jobs that pip install from a requirements file (-r/--
                 requirement) must cache the pip wheel store:
                 actions/setup-python without `cache: pip` re-downloads
                 wheels every run, and a -r install supplies a natural
                 cache-dependency-path. Ad-hoc 1-2 package installs
                 (pytest, psycopg2) stay silent on purpose: the wheel-
                 cache win is marginal there and the key file would be
                 arbitrary. The rule checks the requirements file actually
                 exists (repo root or a job working-directory) so the
                 remediation is real.
  cache-dep-path a CACHED setup-node/setup-python whose key file is not
                 the action default must declare cache-dependency-path.
                 Node: exempt when a root package-lock.json exists (the
                 default target is correct). Pip: exempt when the -r file
                 is in pip's default search (requirements.txt /
                 pyproject.toml at any depth). The live instance was
                 mesh-pytest: cache: 'pip' hashing the default repo-wide
                 set while installing from requirements-dev.txt — pin
                 changes could never bust the cache. Declaration present
                 always satisfies; nothing to hash = the other rules'
                 subject, silent here.

Escape hatch: '# wf-lint-allow' (bare) suppresses every rule for that
line; '# wf-lint-allow: rule1,rule2' suppresses only the named rules;
'# wf-lint-allow: <free-text reason>' documents and suppresses all rules
on the line. The postgres rule also honors its legacy '# pg-pin-allow'
marker.

Scope: postgres-pin / eol-runtime / action-ref / job-hardening /
maven-cache scan .github/workflows YAML only; dead-base scans
Dockerfiles anywhere under the target; npm-ci judges both surfaces
(workflow run blocks with working-directory/cd resolution, and
Dockerfile RUNs with build-context lock resolution — repo-side
approximation of the build context, since the actual -f/--context
flags are unknowable from the files alone).
Compose-file PG pins are host-stack concerns (fleet rulings R1/R3) and
deliberately out of scope here.

Exit codes: 0 clean (warnings ok) | 1 violations | 2 usage error

Usage:
  python3 bin/wf_lint.py [target] [--rule NAME ...] [--label LABEL]
                         [--list-rules]
  python3 bin/wf_lint.py [target] [--rule NAME ...] --fix [--dry-run]

--fix: rules that define a fix_file pass (structural rules — job-hardening
today) auto-apply their remediations. A fix anchored to a line carrying an
allow marker is suppressed exactly as the finding would be; non-fixable
findings are reported untouched. The written diff IS the review surface.
--dry-run previews without writing and exits 1 while fixes are pending.
After a write, the tree is re-scanned: exit 0 only when nothing violates.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime

DEFAULT_DIR = "."
DEFAULT_MIN_MAJOR = 17
WARN_DAYS = 180
JAVA_FLOOR = 17


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def as_of() -> date:
    """The clock the EOL rule judges against (freezable for tests)."""
    env = os.environ.get("WF_LINT_AS_OF")
    if env:
        return _parse_date(env)
    return date.today()


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

class Finding:
    __slots__ = ("severity", "rel", "lineno", "rule", "message")

    def __init__(self, severity: str, rel: str, lineno: int, rule: str, message: str):
        self.severity = severity  # "violation" | "warning"
        self.rel = rel
        self.lineno = lineno
        self.rule = rule
        self.message = message

    @property
    def where(self) -> str:
        return f"{self.rel}:{self.lineno}"

    def annotation(self) -> str:
        kind = "error" if self.severity == "violation" else "warning"
        return f"::{kind} file={self.rel},line={self.lineno}::[{self.rule}] {self.message}"

    def plain(self, label: str) -> str:
        tag = "" if self.severity == "violation" else "WARNING "
        return f"{label}: {tag}{self.where}: [{self.rule}] {self.message}"


class Rule:
    """Base: a named line-scanner over a filtered file set.

    Most rules judge what is WRONG with individual lines (scan_line).
    Rules that judge what a file is MISSING (missing blocks, missing
    declarations) implement the optional scan_file hook instead — it
    receives the whole file once, after the line pass, and anchors its
    findings wherever is most useful (typically a block header).
    """

    name = "rule"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        raise NotImplementedError

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        raise NotImplementedError

    def scan_file(self, rel: str, lines: list[str]) -> list[Finding]:
        """Optional structural pass — default: no findings."""
        return []

    def fix_file(self, rel: str, lines: list[str]) -> list["Edit"]:
        """Optional --fix pass — default: no fixes. Edits are computed against
        the file as-read; the applier applies them in descending line order."""
        return []


class Edit:
    """One auto-applied remediation from a rule's fix_file pass.

    kind "insert": insert `text` immediately BEFORE 1-based line `lineno`
    (len(lines)+1 appends). kind "replace": overwrite line `lineno`.
    `anchor_lineno` is the line the parent finding anchored to — the applier
    suppresses the fix when that line carries an allow marker for `rule`,
    mirroring scan-time suppression. All edits are computed against the
    original file, then applied in descending line order so earlier
    positions never shift (the index-staleness failure mode of ad-hoc
    fixers, designed out here).
    """

    __slots__ = ("kind", "lineno", "text", "reason", "rule", "anchor_lineno")

    def __init__(self, kind: str, lineno: int, text: str, reason: str, rule: str, anchor_lineno: int):
        self.kind = kind
        self.lineno = lineno
        self.text = text
        self.reason = reason
        self.rule = rule
        self.anchor_lineno = anchor_lineno


# --------------------------------------------------------------------------
# rule: postgres-pin (ported from lint_postgres_pins.py)
# --------------------------------------------------------------------------

PIN_PATTERNS = (
    re.compile(r"\bpostgres:(\d+)"),            # postgres:16, postgres:17-alpine
    re.compile(r"pgvector/pgvector:pg(\d+)"),   # pgvector/pgvector:pg17
)
UNVERSIONED_RE = re.compile(
    r"\bimage:\s*['\"]?postgres(?:\s|['\"]|$)"  # bare `image: postgres`
    r"|\bpostgres:['\"]?latest\b"               # `postgres:latest`
)


def _in_workflows(rel: str, target: str) -> bool:
    """True when `rel` lives in a .github/workflows tree (or the scan target
    IS a workflows dir — the old lint's direct-dir usage)."""
    norm = rel.replace(os.sep, "/")
    if ".github/workflows" in norm:
        return True
    return os.path.abspath(target).rstrip("/").endswith("workflows")


class PostgresPinRule(Rule):
    name = "postgres-pin"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        out: list[Finding] = []
        min_major = _pg_min_major()
        stripped = raw.strip()
        for pattern in PIN_PATTERNS:
            for m in pattern.finditer(raw):
                major = int(m.group(1))
                if major < min_major:
                    out.append(Finding(
                        "violation", rel, lineno, self.name,
                        f"postgres pin major {major} < fleet standard {min_major}: {stripped[:100]}",
                    ))
        if UNVERSIONED_RE.search(raw):
            out.append(Finding(
                "warning", rel, lineno, self.name,
                f"unversioned/latest postgres image (drifts silently): {stripped[:100]}",
            ))
        return out


def _pg_min_major() -> int:
    raw = os.environ.get("WF_LINT_PG_MIN_MAJOR") or os.environ.get("PG_PIN_MIN_MAJOR")
    return int(raw) if raw else DEFAULT_MIN_MAJOR


# --------------------------------------------------------------------------
# rule: dead-base
# --------------------------------------------------------------------------

# (repo regex, tag regex or None-for-no-tag-only, human reason)
DEAD_BASES = (
    (
        re.compile(r"^openjdk$", re.I),
        None,  # any tag, or no tag
        "openjdk Docker Hub images discontinued (use eclipse-temurin or a vendor JDK)",
    ),
    (
        re.compile(r"^eclipse-temurin$", re.I),
        re.compile(r"-slim$", re.I),
        "eclipse-temurin never published -slim tags (404 on Docker Hub; use 21-jdk / 21-jre)",
    ),
)
FROM_RE = re.compile(r"^\s*FROM\s+(?P<img>.+)$", re.I)


class DeadBaseRule(Rule):
    name = "dead-base"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        return (
            filename == "Dockerfile"
            or filename.startswith("Dockerfile.")
            or filename.endswith(".dockerfile")
        )

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        m = FROM_RE.match(raw)
        if not m:
            return []
        tokens = m.group("img").split()
        img = next((t for t in tokens if not t.startswith("--")), None)  # skip flags
        if not img:
            return []
        img = img.strip("'\"")
        repo, _, tag = img.partition(":")

        for repo_re, tag_re, reason in DEAD_BASES:
            if not repo_re.match(repo):
                continue
            if tag_re is None or (tag and tag_re.search(tag)) or (tag_re is None and not tag):
                dead = img if tag else f"{repo}:<no-tag>"
                return [Finding(
                    "violation", rel, lineno, self.name,
                    f"dead base image `{dead}` — {reason}: {raw.strip()[:100]}",
                )]
        return []


# --------------------------------------------------------------------------
# rule: eol-runtime
# --------------------------------------------------------------------------

# End-of-support dates. node/python are release facts; go end-dates are
# cadence estimates (support ends ~2 months after the next release ships).
RUNTIME_EOL = {
    "node": {18: "2025-04-30", 20: "2026-04-30", 22: "2027-04-30", 24: "2028-04-30"},
    "python": {
        39: "2025-10-31", 310: "2026-10-04", 311: "2027-10-24",
        312: "2028-10-02", 313: "2029-10-31",
    },
    "go": {
        22: "2025-08-05", 23: "2026-02-10", 24: "2026-08-04",
        25: "2027-02-01", 26: "2027-08-03", 27: "2028-02-01",
    },
}
RUNTIME_PATTERNS = (
    ("node", re.compile(r"\bnode-version:\s*(?P<vals>.+?)\s*$"), re.compile(r"\d+")),
    ("python", re.compile(r"\bpython-version:\s*(?P<vals>.+?)\s*$"), re.compile(r"\d+\.\d+")),
    ("go", re.compile(r"\bgo-version:\s*(?P<vals>.+?)\s*$"), re.compile(r"\d+\.\d+")),
    ("java", re.compile(r"\bjava-version:\s*(?P<vals>.+?)\s*$"), re.compile(r"\d+")),
)


def _runtime_key(runtime: str, token: str) -> int:
    if runtime == "python":
        parts = token.split(".")
        return int(parts[0]) * 100 + int(parts[1])  # 3.11 -> 311
    if runtime == "go":
        return int(token.split(".")[1])             # 1.22 -> 22
    return int(token.split(".")[0])                 # node/java majors


class EolRuntimeRule(Rule):
    name = "eol-runtime"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        out: list[Finding] = []
        stripped = raw.strip()
        if stripped.startswith("#"):
            return out
        today = as_of()
        for runtime, line_re, token_re in RUNTIME_PATTERNS:
            m = line_re.search(raw)
            if not m:
                continue
            for tok in token_re.findall(m.group("vals")):
                key = _runtime_key(runtime, tok)
                label = f"{runtime} {tok}"
                if runtime == "java":
                    if key < JAVA_FLOOR:
                        out.append(Finding(
                            "violation", rel, lineno, self.name,
                            f"{label} below fleet floor (java {JAVA_FLOOR}+): {stripped[:100]}",
                        ))
                    continue
                eol = RUNTIME_EOL.get(runtime, {}).get(key)
                if eol is None:
                    continue  # unassessed version: cannot judge, not a finding
                eol_date = _parse_date(eol)
                if today > eol_date:
                    out.append(Finding(
                        "violation", rel, lineno, self.name,
                        f"{label} is past EOL ({eol}) — bump the pin: {stripped[:100]}",
                    ))
                else:
                    days = (eol_date - today).days
                    if days <= WARN_DAYS:
                        out.append(Finding(
                            "warning", rel, lineno, self.name,
                            f"{label} EOL {eol} — in {days} days, schedule the bump: {stripped[:100]}",
                        ))
        return out


# --------------------------------------------------------------------------
# rule: action-ref
# --------------------------------------------------------------------------

USES_RE = re.compile(r"\buses:\s*['\"]?([A-Za-z0-9_./-]+?)(?:@([^'\"\s#]+))?['\"]?\s*$")
FLOATING = {"main", "master", "develop", "head"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.I)


class ActionRefRule(Rule):
    name = "action-ref"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        m = USES_RE.search(raw)
        if not m:
            return []
        action, ref = m.group(1), m.group(2)
        if action.startswith(("./", "../")):
            return []  # local action / reusable-workflow ref: @ref not applicable by design
        if not ref:
            return [Finding(
                "violation", rel, lineno, self.name,
                f"`uses: {action}` has no @ref (floats default branch) — pin a tag or SHA",
            )]
        if ref.lower() in FLOATING:
            return [Finding(
                "violation", rel, lineno, self.name,
                f"`uses: {action}@{ref}` floats a branch — pin a tag or SHA",
            )]
        if os.environ.get("WF_LINT_REQUIRE_SHA") == "1" and not SHA_RE.match(ref):
            return [Finding(
                "violation", rel, lineno, self.name,
                f"`uses: {action}@{ref}` is not a full 40-hex SHA (WF_LINT_REQUIRE_SHA=1)",
            )]
        return []


# --------------------------------------------------------------------------
# rule: job-hardening (structural — the first scan_file rule)
# --------------------------------------------------------------------------

# Job headers are the second indent level under `jobs:`; every GitHub
# workflow in the house style uses 2-space indents, so the first
# non-comment entry under jobs: fixes the expected depth and any
# shallower key (on:, name:, permissions:) ends the block.
KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+):\s*(?:#.*)?$")      # block keys (empty value)
VALKEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+):(?:\s.*)?$")     # keys w/ scalar value


class JobHardeningRule(Rule):
    """Every job: timeout-minutes; every file: a permissions block.

    Structural by nature (missing blocks), so it runs as one pass per
    file via scan_file. Anchoring: timeout findings point at the job
    header line, the missing-permissions finding at the `on:` line —
    which keeps the per-line allow marker usable on the anchor line.
    Caller jobs (job-level uses:) are exempt from the timeout check:
    GitHub rejects timeout-minutes on reusable-workflow calls.
    """

    name = "job-hardening"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        return []  # structural rule: judges missing blocks via scan_file only

    def scan_file(self, rel: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        # Only judge what GitHub would actually run: a file with no trigger
        # (on: / true:) is rejected by GitHub outright, so scratch fixtures
        # and malformed files are out of scope for the whole rule.
        # VALKEY_RE (not KEY_RE) — triggers usually carry a value: `on: push`.
        if not (
            _has_top_level_key(lines, "on")
            or _has_top_level_key(lines, "true")
            or _has_top_level_key(lines, "true:")
        ):
            return []
        jobs = _locate_jobs(lines)
        for name, header_idx, job_indent, uses_key, block in jobs:
            if uses_key:
                continue  # reusable-workflow caller: timeout-minutes forbidden
            if not _has_job_key(block, job_indent, "timeout-minutes"):
                out.append(Finding(
                    "violation", rel, header_idx + 1, self.name,
                    f"job `{name}` has no timeout-minutes (GitHub default is 360 — "
                    f"a wedged job holds a runner for hours); add `timeout-minutes: 10` "
                    f"under {name}:",
                ))
        # Least-privilege gate: a top-level permissions block is the house
        # style, but per-job permissions on EVERY job pins the token just as
        # fully — only a workflow with neither is violating.
        if not _has_top_level_key(lines, "permissions") and not (
            jobs
            and all(
                _has_job_key(body, indent, "permissions")
                for _name, _idx, indent, _uses, body in jobs
            )
        ):
            on_idx = next(
                (
                    i
                    for i, l in enumerate(lines)
                    if (m := VALKEY_RE.match(l)) and m.group(1) == "" and m.group(2) in ("on", "true")
                ),
                0,
            )
            out.append(Finding(
                "violation", rel, on_idx + 1, self.name,
                "workflow declares no permissions block — jobs inherit the token's "
                "default scope; add a top-level `permissions: contents: read` "
                "(house convention) above jobs:",
            ))
        return out

    def fix_file(self, rel: str, lines: list[str]) -> list[Edit]:
        """Auto-remediate the same violations scan_file finds.

        timeout-minutes: inserted directly under each offending job header at
        the job's own indent (10 minutes — house default; raise for heavy
        jobs in review). permissions: a top-level contents: read block
        directly above jobs: (house convention). The permissions fix
        intentionally narrows the default token scope — review the diff to
        confirm no job relied on write scopes (none do in this repo as of
        #507). Anchors mirror scan_file so allow markers suppress fixes on
        the same lines they suppress findings.
        """
        edits: list[Edit] = []
        if not (
            _has_top_level_key(lines, "on")
            or _has_top_level_key(lines, "true")
            or _has_top_level_key(lines, "true:")
        ):
            return []
        jobs = _locate_jobs(lines)
        for name, header_idx, job_indent, uses_key, block in jobs:
            if uses_key:
                continue  # GitHub rejects timeout-minutes on caller jobs
            if not _has_job_key(block, job_indent, "timeout-minutes"):
                edits.append(Edit(
                    "insert", header_idx + 2,
                    f"{' ' * (job_indent + 2)}timeout-minutes: 10\n",
                    f"job `{name}` had no timeout-minutes",
                    self.name, header_idx + 1,
                ))
        if not _has_top_level_key(lines, "permissions") and not (
            jobs
            and all(
                _has_job_key(body, indent, "permissions")
                for _n, _i, indent, _u, body in jobs
            )
        ):
            ji = next(
                (i for i, l in enumerate(lines)
                 if (m := KEY_RE.match(l)) and m.group(1) == "" and m.group(2) == "jobs"),
                None,
            )
            if ji is not None:
                on_anchor = next(
                    (i + 1 for i, l in enumerate(lines)
                     if (m := VALKEY_RE.match(l)) and m.group(1) == "" and m.group(2) in ("on", "true")),
                    1,
                )
                edits.append(Edit(
                    "insert", ji + 1,
                    "permissions:\n  contents: read\n\n",
                    "workflow had no permissions block — added top-level contents: read",
                    self.name, on_anchor,
                ))
        return edits


def _locate_jobs(lines: list[str]) -> list[tuple[str, int, int, bool, list[str]]]:
    """Return (name, header_idx, job_indent, is_caller, body_lines) per job."""
    ji = next((i for i, l in enumerate(lines) if KEY_RE.match(l) and l.split(":")[0].strip() == "jobs"), None)
    if ji is None:
        return []
    job_indent = None
    jobs: list[tuple[str, int, int, bool, list[str]]] = []
    for i in range(ji + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        ind = len(lines[i]) - len(lines[i].lstrip())
        if job_indent is None:
            job_indent = ind
        if ind == job_indent:
            m = KEY_RE.match(lines[i])
            if not m:
                break
            jobs.append((m.group(2), i, job_indent, False, []))
        elif ind < job_indent:
            break
    # second pass: fill bodies + detect job-level uses: (reusable-workflow call)
    for k, (name, idx, indent, _uses, _body) in enumerate(jobs):
        end = jobs[k + 1][1] if k + 1 < len(jobs) else len(lines)
        body = lines[idx + 1:end]
        uses = any(
            (m := VALKEY_RE.match(l)) and len(m.group(1)) == indent + 2
            and m.group(2) == "uses"
            for l in body
        )
        jobs[k] = (name, idx, indent, uses, body)
    return jobs


def _has_job_key(body: list[str], job_indent: int, key: str) -> bool:
    """True when a direct child of the job sets `key` (job-level, not nested)."""
    want = " " * (job_indent + 2)
    return any(
        (m := VALKEY_RE.match(l)) and m.group(1) == want and m.group(2) == key
        for l in body
    )


def _has_top_level_key(lines: list[str], key: str) -> bool:
    """True when `key` appears as a zero-indent top-level key (value optional:
    VALKEY_RE, so both `on:` and `on: push` count)."""
    return any(
        (m := VALKEY_RE.match(l)) and m.group(1) == "" and m.group(2) == key
        for l in lines
    )


# --------------------------------------------------------------------------
# rule: npm-ci (lockfile-aware install policy)
# --------------------------------------------------------------------------

NPM_INSTALL_RE = re.compile(r"\bnpm\s+install\b")
# Real global flags anywhere in the command (-g / --global as standalone
# tokens). Must NOT match arbitrary dashed tokens that merely contain a 'g'
# (the first draft `-\S*g\S*` exempted `--no-package-lock` — caught by the
# nebula-srv fixture before it shipped).
NPM_INSTALL_GLOBAL_RE = re.compile(r"(?:^|\s)(?:-g|--global)(?:\s|$)")
# `cd` after line start, a shell chain operator, an inline `run:`/`RUN` prefix
# (case-insensitive covers both YAML and Dockerfile spellings). NOT after
# `npm run <script>` — the \b and required cd make that a non-match.
CD_RE = re.compile(r"(?:^|[;&|]|\brun\s*:?\s)\s*cd\s+(\S+)", re.I)
RUN_BLOCK_RE = re.compile(r"^(\s*)(?:-\s+)?run:\s*(\|)?")
WD_RE = re.compile(r"^\s*(?:-\s+)?working-directory:\s*['\"]?([^'\"\s#]+)")
STEPS_KEY_RE = re.compile(r"^\s*steps:\s*$")
STEP_ITEM_RE = re.compile(r"^\s*-\s+([A-Za-z_-]+)\s*:")
LOCKFILE = "package-lock.json"


def _npm_install_exempt(raw: str) -> bool:
    """Global installs touch no project manifest; --package-lock-only IS the
    lock-regeneration flow. Both are legitimate `npm install` uses."""
    return bool(NPM_INSTALL_GLOBAL_RE.search(raw)) or "--package-lock-only" in raw


def _nearest_lock(start_dir: str, root: str) -> str | None:
    """Nearest package-lock.json at or above start_dir, stopping at the scan
    root — the repo-side approximation of the build context (the real
    docker -f/--context flags are unknowable from the files alone; every
    house Dockerfile builds from the service's own tree or its parent)."""
    cur = os.path.normpath(os.path.abspath(start_dir))
    stop = os.path.normpath(os.path.abspath(root))
    while cur == stop or cur.startswith(stop + os.sep):
        if os.path.isfile(os.path.join(cur, LOCKFILE)):
            return os.path.relpath(os.path.join(cur, LOCKFILE), stop)
        if cur == stop:
            return None
        cur = os.path.dirname(cur)
    return None


class NpmCiRule(Rule):
    """With a committed package-lock.json, `npm ci` is the only reproducible
    install. Opt-in enforcement mirrors #518's opt-in adoption: silent where
    no lock applies, loud where one does. Workflows are judged per run block
    (step working-directory: / defaults.run / in-block cd resolve the effective
    dir); Dockerfiles per RUN line (last `cd` on the chain, else the
    Dockerfile's dir). Both walk the repo-side ancestor chain for the nearest
    lock, mirroring the two real build-context shapes in this repo.
    """

    name = "npm-ci"

    def __init__(self) -> None:
        self._target = "."

    def applies(self, rel: str, filename: str, target: str) -> bool:
        self._target = target
        if (
            filename == "Dockerfile"
            or filename.startswith("Dockerfile.")
            or filename.endswith(".dockerfile")
        ):
            return True
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        return []  # structural: run blocks and build contexts are per-file facts

    def scan_file(self, rel: str, lines: list[str]) -> list[Finding]:
        filename = os.path.basename(rel)
        if (
            filename == "Dockerfile"
            or filename.startswith("Dockerfile.")
            or filename.endswith(".dockerfile")
        ):
            return self._scan_dockerfile(rel, lines)
        return self._scan_workflow(rel, lines)

    # -- workflows -------------------------------------------------------------

    def _scan_workflow(self, rel: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        default_wd: str | None = None  # defaults.run.working-directory (pre-steps)
        step_wd: str | None = None     # step-level; resets at each new step item
        in_steps = False
        run_indent: int | None = None
        cd_stack: list[str] = []       # `cd`-affected dirs inside the open block

        def effective_dir(wd: str | None) -> str | None:
            if not wd:
                return None
            return os.path.join(self._target, wd)

        def lock_for(wd: str | None) -> str | None:
            d = effective_dir(wd)
            return _nearest_lock(d, self._target) if d else None

        for i, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if run_indent is not None:
                ind = len(raw) - len(raw.lstrip())
                if ind <= run_indent:
                    run_indent = None  # dedent closes the block scalar
                    cd_stack = []
                else:
                    m = CD_RE.search(raw)
                    if m:
                        base = cd_stack[-1] if cd_stack else (step_wd or default_wd or ".")
                        cd_stack.append(os.path.normpath(os.path.join(base, m.group(1).strip("'\""))))
                    if NPM_INSTALL_RE.search(raw) and not _npm_install_exempt(raw):
                        wd = cd_stack[-1] if cd_stack else (step_wd or default_wd)
                        lock = lock_for(wd)
                        if lock:
                            out.append(Finding(
                                "violation", rel, i + 1, self.name,
                                f"`npm install` while `{lock}` applies here — use `npm ci` "
                                f"for reproducible installs: {stripped[:90]}",
                            ))
                continue
            if STEPS_KEY_RE.match(raw):
                in_steps = True
                continue
            wm = WD_RE.match(raw)
            if wm:
                wd = wm.group(1).strip("'\"")
                if in_steps:
                    step_wd = wd
                else:
                    default_wd = wd
                continue
            sm = STEP_ITEM_RE.match(raw)
            if sm and in_steps and sm.group(1) != "working-directory":
                step_wd = None  # new step: step-level working-directory resets
            m = RUN_BLOCK_RE.match(raw)
            if m and m.group(2):
                # block scalar opens; key indent = leading spaces + the "- "
                # prefix when the run key hangs off a step item
                run_indent = len(m.group(1)) + (
                    2 if raw[len(m.group(1)):len(m.group(1)) + 2] == "- " else 0
                )
                cd_stack = []
                continue
            # inline `run: cmd` (and any stray top-level line): judge directly,
            # honoring an in-line `cd` relative to the step/defaults wd
            if NPM_INSTALL_RE.search(raw) and not _npm_install_exempt(raw):
                wd = step_wd or default_wd
                m_cd = CD_RE.search(raw)
                if m_cd:
                    wd = os.path.normpath(os.path.join(wd or ".", m_cd.group(1).strip("'\"")))
                lock = lock_for(wd)
                if lock:
                    out.append(Finding(
                        "violation", rel, i + 1, self.name,
                        f"`npm install` while `{lock}` applies here — use `npm ci` "
                        f"for reproducible installs: {stripped[:90]}",
                    ))
        return out

    # -- Dockerfiles -----------------------------------------------------------

    def _scan_dockerfile(self, rel: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        df_dir = os.path.dirname(os.path.join(self._target, rel)) or self._target
        copies_lock = any(
            l.strip().upper().startswith("COPY")
            and "--from=" not in l
            and LOCKFILE in l
            for l in lines
        )
        for i, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.upper().startswith("RUN"):
                continue
            if not (NPM_INSTALL_RE.search(stripped) and not _npm_install_exempt(stripped)):
                continue
            eff_dir = df_dir
            last_cd = None
            for m in CD_RE.finditer(stripped):  # shell chains left-to-right: last wins
                last_cd = m
            if last_cd:
                eff_dir = os.path.normpath(os.path.join(df_dir, last_cd.group(1).strip("'\"")))
            lock = _nearest_lock(eff_dir, self._target)
            if lock:
                hint = (
                    "lock already COPYed — switch to `npm ci`"
                    if copies_lock
                    else f"COPY {LOCKFILE} and switch to `npm ci`"
                )
                out.append(Finding(
                    "violation", rel, i + 1, self.name,
                    f"`npm install` under a build context holding `{lock}` ({hint}) — "
                    f"reproducible installs: {stripped[:90]}",
                ))
        return out


# --------------------------------------------------------------------------
# rule: maven-cache (Central rate-limit hygiene)
# --------------------------------------------------------------------------

# `mvn`/`mvnw` as a standalone token followed by whitespace + an argument.
# Word-boundary form so ./mvnw, `run: mvn ...`, and `x && mvn ...` all match,
# while maven-the-word, mvn-repo paths, and bare `mvn` (no args) do not.
MVN_RUN_RE = re.compile(r"\b(?:mvn|mvnw)\s+\S")
SETUP_JAVA_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*actions/setup-java\b")
CACHE_MAVEN_RE = re.compile(r"^\s*cache:\s*['\"]?maven['\"]?\s*(?:#.*)?$")


def _job_runs_maven(body: list[str]) -> bool:
    """True when any executable line of the job body invokes mvn/mvnw on the
    runner. Comment lines (shell comments inside run blocks included) never
    count. Docker-internal Maven (docker build / docker run ... mvn) is out
    of scope for the same reason the docker-gate is exempt: a host-runner
    cache does not reach inside the container."""
    for raw in body:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "docker build" in stripped or "docker run" in stripped:
            continue
        if MVN_RUN_RE.search(stripped):
            return True
    return False


def _setup_step_windows(
    body: list[str], setup_re
) -> list[tuple[int, int, int]]:
    """(body_offset, start, end) windows of setup-* steps matching `setup_re`:
    start/end bracket the step's dash-item window (cache/with judgment runs
    inside it only)."

    Step anatomy (house 2-space style): a list item's first property carries
    the `- ` marker (e.g. `- name:`), so its key sits two columns LEFT of the
    step's other properties (`uses:`, `with:`, `run:` all share one indent);
    when `uses:` is itself the first property, it owns the dash. The step's
    window therefore runs from its `- ` item line to the next dash item or
    job-level key, and `with:` lives INSIDE that window at the property
    indent — the naive "stop at the uses: line's own indent" read amputates
    the with: block and false-positives every correctly cached step (caught
    live against the #569-fixed workflows before this shipped).

    `cache:` is judged only among the `with:` block's children, so another
    step's cache, or text inside a run block, can never satisfy this step.
    A step with no `with:` at all is uncached by definition. Quoted values
    and trailing comments are accepted.
    """
    out: list[tuple[int, int, int]] = []
    for i, raw in enumerate(body):
        if not setup_re.match(raw):
            continue
        u = len(raw) - len(raw.lstrip())
        # Step start: walk back to the `- ` item line at the property indent's
        # dash column — present unless uses: itself carries the dash.
        start = i
        if not raw.lstrip().startswith("- "):
            k = i
            while k > 0:
                line = body[k]
                s = line.strip()
                if s:
                    ind = len(line) - len(line.lstrip())
                    if ind < u and s.startswith("- "):
                        start = k
                        break
                    if ind < u:
                        break  # job-level key: degenerate, stay at i
                k -= 1
        # Step end: next dash item at the step level, or any dedent below it.
        end = len(body)
        j = start + 1
        while j < len(body):
            line = body[j]
            s = line.strip()
            if s:
                ind = len(line) - len(line.lstrip())
                if ind < u or (ind == u and s.startswith("- ")):
                    end = j
                    break
            j += 1
        out.append((i, start, end))
    return out


def _step_with_block(body: list[str], start: int, end: int) -> int | None:
    """Body offset of the step's `with:` key line within [start, end)."""
    for j in range(start, end):
        m = VALKEY_RE.match(body[j])
        if m and m.group(2) == "with":
            return j
    return None


def _uncached_setup_step_offsets(
    body: list[str], setup_re, cache_ok
) -> list[int]:
    """Body offsets of setup-* steps matching `setup_re` whose with: block
    carries no line satisfying `cache_ok` (the ecosystem's cache predicate).

    The step's with: block lives INSIDE its dash-item window at the property
    indent; cache: is judged only among that with:'s children, so another
    step's cache or run-block text can never satisfy this step. A step with
    no with: at all is uncached by definition. Quoted values and trailing
    comments are accepted.
    """
    out: list[int] = []
    for i, start, end in _setup_step_windows(body, setup_re):
        with_idx = _step_with_block(body, start, end)
        cached = False
        if with_idx is not None:
            w = len(body[with_idx]) - len(body[with_idx].lstrip())
            for j in range(with_idx + 1, end):
                line = body[j]
                if line.strip():
                    if len(line) - len(line.lstrip()) <= w:
                        break  # dedent: with: block closed
                    if cache_ok(line):
                        cached = True
                        break
        if not cached:
            out.append(i)
    return out


def _step_with_value(body: list[str], start: int, end: int, key: str) -> str | None:
    """Scalar value of `key:` among the step's with: children (first match,
    quotes stripped), or None when the step doesn't declare it."""
    with_idx = _step_with_block(body, start, end)
    if with_idx is None:
        return None
    w = len(body[with_idx]) - len(body[with_idx].lstrip())
    for j in range(with_idx + 1, end):
        line = body[j]
        if line.strip():
            if len(line) - len(line.lstrip()) <= w:
                break  # dedent: with: block closed
            m = re.match(rf"^\s*{re.escape(key)}:\s*['\"]?([^'\"#\s]+)", line)
            if m:
                return m.group(1)
    return None


class MavenCacheRule(Rule):
    """A job that runs mvn/mvnw with an uncached setup-java step cold-pulls
    Maven Central; concurrent cold pulls trip its 429 rate limiter (2026-09-25
    main incident, run 36088456372). One finding per offending job, anchored
    at the first uncached setup-java step so the allow marker works there.
    Structural (scan_file): needs the job body plus step windows, which no
    single line can reveal. No --fix pass on purpose: the remediation depends
    on whether the step already has a with: block, and the edit is one line —
    review should see it in context.
    """

    name = "maven-cache"

    def applies(self, rel: str, filename: str, target: str) -> bool:
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        return []  # structural rule: judged via scan_file only

    def scan_file(self, rel: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        # Same GitHub-reality gate as job-hardening: no trigger, not a file
        # GitHub would run — scratch fixtures and malformed files are out of
        # scope for the whole rule.
        if not (
            _has_top_level_key(lines, "on")
            or _has_top_level_key(lines, "true")
            or _has_top_level_key(lines, "true:")
        ):
            return []
        for name, header_idx, _job_indent, uses_key, body in _locate_jobs(lines):
            if uses_key:
                continue  # reusable-workflow caller: steps live elsewhere
            if not _job_runs_maven(body):
                continue
            uncached = _uncached_setup_step_offsets(body, SETUP_JAVA_RE, CACHE_MAVEN_RE.match)
            if uncached:
                anchor = header_idx + uncached[0] + 2  # 1-based lineno of body[uncached[0]]
                out.append(Finding(
                    "violation", rel, anchor, self.name,
                    f"job `{name}` runs mvn/mvnw but its actions/setup-java step has "
                    f"no `cache: maven` — concurrent cold pulls hit Maven Central's "
                    f"429 rate limiter (2026-09-25 incident, #569); add `cache: maven` "
                    f"to the step's with: block",
                ))
        return out


# --------------------------------------------------------------------------
# rules: node-cache / pip-cache (npm + pip cold-pull hygiene)
# --------------------------------------------------------------------------

SETUP_NODE_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*actions/setup-node\b")
SETUP_PYTHON_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*actions/setup-python\b")
CACHE_NPM_RE = re.compile(r"^\s*cache:\s*['\"]?npm['\"]?\s*(?:#.*)?$")
CACHE_PIP_RE = re.compile(r"^\s*cache:\s*['\"]?pip['\"]?\s*(?:#.*)?$")
CACHE_DEP_PATH_RE = re.compile(r"^\s*cache-dependency-path:\s*['\"]?(\S+?)['\"]?\s*(?:#.*)?$")
NPM_RUN_RE = re.compile(r"\bnpm\s+(?:ci|install)\b")
PIP_R_RUN_RE = re.compile(r"\bpip(?:\d(?:\.\d+)?)?\s+(?:install\s+)?-r\s+|--requirement\s+(\S+)")
PIP_R_ALT_RE = re.compile(r"(?:pip|pip3|python3?(?:\.\d+)?)\s+-m\s+pip\s+install.*(?:-r|--requirement)\s+(\S+)")
RUN_LINE_RE = re.compile(r"^\s*(?:-\s+)?run:\s*")


def _cd_targets_in_body(body: list[str]) -> set[str]:
    """Directories the job's run blocks cd into (in-block `cd x`), plus any
    step-level working-directory: values — the same resolution npm-ci uses,
    approximated for the lock-presence gate."""
    dirs: set[str] = set()
    for raw in body:
        m = WD_RE.match(raw)
        if m:
            dirs.add(m.group(1))
        for cd in CD_RE.finditer(raw):
            dirs.add(cd.group(1))
    return dirs


def _pip_requirements_files(body: list[str]) -> set[str]:
    """Requirement files named by `-r`/`--requirement` in the job's run
    blocks (both `pip install -r f` and `python3 -m pip install -r f`)."""
    files: set[str] = set()
    for raw in body:
        for m in PIP_R_ALT_RE.finditer(raw):
            files.add(m.group(1).strip("'\""))
    return files


def _root_of(rule: "NodeCacheRule | PipCacheRule") -> str:
    return getattr(rule, "_target", ".")


def _contained_path(rule, d: str) -> str | None:
    """`d` resolved against the scan root, or None when it escapes the root
    (mirror of _nearest_lock's stop-at-root). Without this, a cleanup
    `cd ../..` in a run block made the sonar job's gate match the *dev
    checkout's* lock one level above the repo — an existence check that
    would silently leak host state into CI linting."""
    root = os.path.abspath(_root_of(rule))
    p = os.path.normpath(os.path.join(root, d))
    if p != root and not p.startswith(root + os.sep):
        return None
    return p


def _exists_under_target(rule, path: str) -> bool:
    root = _root_of(rule)
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    return os.path.isfile(candidate)


class _SetupCacheRuleBase(Rule):
    """Shared machinery for the setup-node/setup-python cache rules: gate on
    GitHub-reality (trigger present), skip caller jobs, find uncached setup
    steps via the generalized step-window helper, then decide per job whether
    the ecosystem's remediation is satisfiable (the npm-ci philosophy — the
    rule never demands what GitHub's own action would reject)."""

    setup_re: re.Pattern
    cache_ok: object
    _target = "."

    def applies(self, rel: str, filename: str, target: str) -> bool:
        self._target = target
        return _in_workflows(rel, target) and filename.endswith((".yml", ".yaml"))

    def scan_line(self, raw: str, rel: str, lineno: int) -> list[Finding]:
        return []  # structural rule: judged via scan_file only

    def _gate_ok(self, name: str, header_idx: int, body: list[str]) -> bool:
        raise NotImplementedError

    def scan_file(self, rel: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        if not (
            _has_top_level_key(lines, "on")
            or _has_top_level_key(lines, "true")
            or _has_top_level_key(lines, "true:")
        ):
            return []
        for name, header_idx, _job_indent, uses_key, body in _locate_jobs(lines):
            if uses_key:
                continue
            uncached = _uncached_setup_step_offsets(body, self.setup_re, self.cache_ok)
            if not uncached:
                continue
            if not self._gate_ok(name, header_idx, body):
                continue
            anchor = header_idx + uncached[0] + 2
            out.append(Finding(
                "violation", rel, anchor, self.name,
                self._message(name, body),
            ))
        return out

    def _message(self, name: str, body: list[str]) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class NodeCacheRule(_SetupCacheRuleBase):
    """npm cold-pulls in lock-bearing dirs: setup-node must cache the npm
    store. Gate: a package-lock.json exists at the scan root, in a job
    working-directory, or in an in-block cd target — mirroring npm-ci's
    lock-presence trigger, and matching GitHub's own `cache: npm` hard
    requirement so the rule never demands an unremediable fix."""

    name = "node-cache"
    setup_re = SETUP_NODE_RE
    cache_ok = staticmethod(CACHE_NPM_RE.match)

    def _lock_dirs(self, body: list[str]) -> list[str]:
        """Job directories (root-relative, scan-root-contained) holding a
        package-lock.json — the dirs `cache: npm` + cache-dependency-path
        could key on. Escapees (`cd ../..`) are dropped by _contained_path."""
        out = []
        for d in _cd_targets_in_body(body):
            p = _contained_path(self, d)
            if p and os.path.isfile(os.path.join(p, "package-lock.json")):
                out.append(os.path.relpath(p, os.path.abspath(self._target)).replace(os.sep, "/"))
        return sorted(out)

    def _gate_ok(self, name: str, header_idx: int, body: list[str]) -> bool:
        # npm-run half-gate (mirrors maven's _job_runs_maven): a setup-node
        # job that never runs npm ci/install is not a cold-pull subject.
        # Comment lines never count; NPM_RUN_RE is npm-ci's own pattern.
        if not any(
            NPM_RUN_RE.search(l.strip())
            for l in body
            if l.strip() and not l.strip().startswith("#")
        ):
            return False
        if os.path.isfile(os.path.join(self._target, "package-lock.json")):
            return True
        return bool(self._lock_dirs(body))

    def _message(self, name: str, body: list[str]) -> str:
        dirs = self._lock_dirs(body)
        path = f"{dirs[0]}/package-lock.json" if dirs else "package-lock.json"
        return (
            f"job `{name}` runs npm in a lock-bearing directory but its actions/setup-node "
            f"step has no `cache: npm` — every run re-downloads the dependency tree; add "
            f"`cache: npm` with `cache-dependency-path: {path}` to the "
            f"step's with: block"
        )


class PipCacheRule(_SetupCacheRuleBase):
    """pip cold-pulls from requirements files: setup-python must cache the
    pip wheel store. Gate: the job installs with `pip install -r <file>` AND
    that file exists at the scan root or inside a job working-directory —
    giving the cache a natural cache-dependency-path. Ad-hoc 1-2 package
    installs stay silent (marginal wheel-cache win; arbitrary key file)."""

    name = "pip-cache"
    setup_re = SETUP_PYTHON_RE
    cache_ok = staticmethod(CACHE_PIP_RE.match)

    def _gate_ok(self, name: str, header_idx: int, body: list[str]) -> bool:
        reqs = _pip_requirements_files(body)
        if not reqs:
            return False
        for f in reqs:
            if _exists_under_target(self, f):
                return True
            # relative to any contained job working-directory
            for d in _cd_targets_in_body(body):
                p = _contained_path(self, d)
                if p and os.path.isfile(os.path.join(p, f)):
                    return True
        return False

    def _message(self, name: str, body: list[str]) -> str:
        reqs = sorted(_pip_requirements_files(body))
        return (
            f"job `{name}` pip-installs from {reqs[0]} but its actions/setup-python step "
            f"has no `cache: pip` — every run re-downloads the wheels; add `cache: pip` "
            f"with `cache-dependency-path: {reqs[0]}` to the step's with: block"
        )


# --------------------------------------------------------------------------
# rule: cache-dep-path (the silent partial-cache failure mode)
# --------------------------------------------------------------------------

SETUP_NODEPY_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*actions/setup-(?:node|python)\b")
# pip's default dependency search (setup-python README + runtime-verified):
# a repo-wide glob over requirements.txt / pyproject.toml. Runtime proof:
# mesh-pytest has NO root-level default file, yet its cache: pip runs green
# and hit — so the default search found repo files at depth (the multiple
# python/*/requirements.txt + pyproject.toml). Its two matrix jobs hashed
# IDENTICAL keys — repo-constant, i.e. not the file the job installs from.
DEFAULT_PIP_BASENAME_RE = re.compile(r"(?:requirements\.txt|pyproject\.toml)$")


def _pip_default_covered(rule, reqs: set[str]) -> bool:
    """True when any -r file the job installs from is in pip's default
    search set (basename requirements.txt / pyproject.toml at any repo
    depth) — the default hash then includes that exact file."""
    return any(DEFAULT_PIP_BASENAME_RE.search(f) for f in reqs)


class CacheDepPathRule(_SetupCacheRuleBase):
    """A CACHED setup-node/setup-python step whose dependency key file is not
    the action's default must declare cache-dependency-path — otherwise the
    action hashes the wrong file (or a repo-wide grab-bag) and dependency
    changes silently stop busting the cache. The live instance: mesh-pytest
    installs from requirements-dev.txt but its cache: 'pip' key hashed the
    default repo-wide requirements.txt/pyproject.toml set — a pytest pin
    bump would not invalidate it.

    Exemptions (the family's satisfiability discipline):
    - node: a root package-lock.json exists (default path is correct) — the
      declaration is unnecessary by definition.
    - pip: every -r file the job installs from is default-search-covered
      (requirements.txt/pyproject.toml at any depth).
    - no lock / no existing requirements file: the pip/node-cache rules
      govern that side; this rule stays silent (their subjects).
    """

    name = "cache-dep-path"
    setup_re = SETUP_NODEPY_RE
    cache_ok = staticmethod(lambda line: bool(CACHE_NPM_RE.match(line) or CACHE_PIP_RE.match(line)))

    def scan_file(self, rel: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        if not (
            _has_top_level_key(lines, "on")
            or _has_top_level_key(lines, "true")
            or _has_top_level_key(lines, "true:")
        ):
            return []
        for name, header_idx, _job_indent, uses_key, body in _locate_jobs(lines):
            if uses_key:
                continue
            uncached = set(_uncached_setup_step_offsets(body, self.setup_re, self.cache_ok))
            for i, start, end in _setup_step_windows(body, self.setup_re):
                if i in uncached:
                    continue  # cache absent entirely: node/pip-cache's subject
                with_idx = _step_with_block(body, start, end)
                if with_idx is None:
                    continue
                w = len(body[with_idx]) - len(body[with_idx].lstrip())
                eco = None
                for j in range(with_idx + 1, end):
                    line = body[j]
                    if line.strip():
                        if len(line) - len(line.lstrip()) <= w:
                            break
                        if CACHE_NPM_RE.match(line):
                            eco = "npm"
                        elif CACHE_PIP_RE.match(line):
                            eco = "pip"
                if eco is None:
                    continue
                declared = _step_with_value(body, start, end, "cache-dependency-path")
                if declared:
                    continue  # explicit path: the step owns its key file
                if eco == "npm":
                    node_helper = NodeCacheRule()
                    node_helper._target = self._target
                    locks = node_helper._lock_dirs(body)
                    if not locks:
                        continue  # no lock anywhere: loud runtime failure, not a silent one
                    if any(d == "." for d in locks):
                        continue  # root lock: default path is correct
                    out.append(Finding(
                        "violation", rel, header_idx + i + 2, self.name,
                        f"job `{name}` caches npm but its lock lives outside the repo root "
                        f"({locks[0]}/package-lock.json) — the default hash target is wrong; "
                        f"add `cache-dependency-path: {locks[0]}/package-lock.json`",
                    ))
                else:
                    reqs = _pip_requirements_files(body)
                    if not reqs:
                        continue
                    pip_helper = PipCacheRule()
                    pip_helper._target = self._target
                    existing = [
                        f for f in sorted(reqs)
                        if _exists_under_target(pip_helper, f)
                        or any(
                            (p := _contained_path(pip_helper, d))
                            and os.path.isfile(os.path.join(p, f))
                            for d in _cd_targets_in_body(body)
                        )
                    ]
                    if not existing:
                        continue  # nothing real to key on: pip-cache's subject
                    if _pip_default_covered(self, reqs):
                        continue  # -r file is in the default search: hash already includes it
                    out.append(Finding(
                        "violation", rel, header_idx + i + 2, self.name,
                        f"job `{name}` caches pip but installs from {existing[0]}, which is "
                        f"NOT in pip's default dependency search (requirements.txt / "
                        f"pyproject.toml) — the cache key will not change when that file "
                        f"does; add `cache-dependency-path: {existing[0]}`",
                    ))
        return out


# --------------------------------------------------------------------------
# allow markers
# --------------------------------------------------------------------------

ALLOW_RE = re.compile(r"#\s*wf-lint-allow(?::\s*([\w,\s-]+))?", re.I)
LEGACY_PG_ALLOW = "pg-pin-allow"


def _known_rule_names() -> set[str]:
    return {r.name for r in RULES}


def _suppressed(raw: str, rule_name: str) -> bool:
    m = ALLOW_RE.search(raw)
    if m:
        spec = m.group(1)
        if spec is None:
            return True  # bare `# wf-lint-allow`
        tokens = {t.strip() for t in spec.split(",")}
        if tokens and tokens <= _known_rule_names():
            return rule_name in tokens  # selective rule list
        return True  # reason text: document + suppress every rule on this line
    if rule_name == "postgres-pin" and LEGACY_PG_ALLOW in raw:
        return True
    return False


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

RULES: list[Rule] = [
    PostgresPinRule(),
    DeadBaseRule(),
    EolRuntimeRule(),
    ActionRefRule(),
    JobHardeningRule(),
    NpmCiRule(),
    MavenCacheRule(),
    NodeCacheRule(),
    PipCacheRule(),
    CacheDepPathRule(),
]


def scan(target: str, rules: list[Rule]) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    allowed = 0
    for dirpath, dirnames, filenames in os.walk(target):
        # node_modules is guaranteed third-party: vendored packages ship their
        # own Dockerfiles/workflows that would flood every rule with findings
        # after any local npm ci. Never scanned (CI checkouts don't have it).
        # NB: iterate os.walk DIRECTLY — the old sorted(os.walk(...)) wrapper
        # consumed the whole generator before the loop body ran, so in-place
        # dirnames mutation pruned nothing. Findings are re-sorted below.
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, target).replace(os.sep, "/")
            applicable = [r for r in rules if r.applies(rel, filename, target)]
            if not applicable:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.readlines()
            except OSError as exc:
                findings.append(Finding("warning", rel, 0, "harness", f"unreadable ({exc})"))
                continue
            # structural pass: rules that judge missing blocks, not bad lines
            # (base Rule.scan_file is a no-op default — harmless to call)
            for rule in applicable:
                for finding in rule.scan_file(rel, lines):
                    anchor = lines[finding.lineno - 1] if 0 < finding.lineno <= len(lines) else ""
                    if _suppressed(anchor, rule.name):
                        allowed += 1
                        continue
                    findings.append(finding)
            for lineno, raw in enumerate(lines, start=1):
                if raw.strip().startswith("#"):
                    continue  # whole-line comment
                if _suppressed(raw, "*"):
                    allowed += 1
                    continue
                for rule in applicable:
                    if _suppressed(raw, rule.name):
                        continue
                    findings.extend(rule.scan_line(raw, rel, lineno))
    findings.sort(key=lambda f: (f.rel, f.lineno, f.rule))
    return findings, allowed


def _apply_edits(path: str, lines: list[str], edits: list[Edit]) -> None:
    """Apply one file's edits (computed against the as-read lines) in
    descending line order so earlier positions never shift."""
    for edit in sorted(edits, key=lambda e: e.lineno, reverse=True):
        if edit.kind == "insert":
            idx = min(max(edit.lineno - 1, 0), len(lines))
            if idx == len(lines) and lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"  # never glue a fix onto an unterminated line
            lines.insert(idx, edit.text)
        elif edit.kind == "replace":
            if 1 <= edit.lineno <= len(lines):
                lines[edit.lineno - 1] = edit.text
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def apply_fixes(target: str, rules: list[Rule], write: bool = True) -> tuple[int, int]:
    """Run every rule's fix_file pass; write when `write` (dry-run previews).

    Returns (applied, suppressed). Edits anchored to allow-marker lines are
    suppressed exactly as their findings would be. Unreadable files are
    skipped silently — scan() reports them.
    """
    applied = suppressed = 0
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]  # see scan()
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, target).replace(os.sep, "/")
            applicable = [r for r in rules if r.applies(rel, filename, target)]
            if not applicable:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            edits: list[Edit] = []
            for rule in applicable:
                for edit in rule.fix_file(rel, lines):
                    anchor = (
                        lines[edit.anchor_lineno - 1]
                        if 0 < edit.anchor_lineno <= len(lines) else ""
                    )
                    if _suppressed(anchor, edit.rule):
                        suppressed += 1
                        continue
                    edits.append(edit)
            if edits:
                if write:
                    _apply_edits(path, lines, edits)
                applied += len(edits)
    return applied, suppressed


def main(argv: list[str]) -> int:
    target = DEFAULT_DIR
    label = "wf-lint"
    fix = dry_run = False
    selected: list[str] = []
    i = 0
    args = argv[1:]
    while i < len(args):
        arg = args[i]
        if arg == "--rule":
            i += 1
            if i >= len(args):
                print("wf-lint: --rule needs a value", file=sys.stderr)
                return 2
            selected.append(args[i])
        elif arg == "--label":
            i += 1
            if i >= len(args):
                print("wf-lint: --label needs a value", file=sys.stderr)
                return 2
            label = args[i]
        elif arg == "--list-rules":
            for rule in RULES:
                print(rule.name)
            return 0
        elif arg == "--fix":
            fix = True
        elif arg == "--dry-run":
            dry_run = True
        elif arg.startswith("-"):
            print(f"wf-lint: unknown option: {arg}", file=sys.stderr)
            return 2
        else:
            target = arg
        i += 1

    if not os.path.isdir(target):
        print(f"{label}: target dir not found: {target}", file=sys.stderr)
        return 2

    try:
        _pg_min_major()
    except ValueError:
        print(f"{label}: WF_LINT_PG_MIN_MAJOR / PG_PIN_MIN_MAJOR must be an integer", file=sys.stderr)
        return 2

    rules = RULES if not selected else [r for r in RULES if r.name in selected]
    unknown = set(selected) - {r.name for r in RULES}
    if unknown:
        print(f"{label}: unknown rule(s): {', '.join(sorted(unknown))} — see --list-rules", file=sys.stderr)
        return 2

    if dry_run and not fix:
        print(f"{label}: --dry-run only makes sense with --fix", file=sys.stderr)
        return 2

    applied = 0
    suppressed = 0
    if fix:
        applied, suppressed = apply_fixes(target, rules, write=not dry_run)

    findings, allowed = scan(target, rules)
    violations = [f for f in findings if f.severity == "violation"]
    warnings = [f for f in findings if f.severity == "warning"]

    for f in findings:
        print(f.annotation())          # GitHub annotation channel (stdout)
        print(f.plain(label), file=sys.stderr)  # plain file:line channel

    rule_names = ",".join(r.name for r in rules)
    standard = f" — standard: PG {_pg_min_major()}" if any(
        isinstance(r, PostgresPinRule) for r in rules
    ) else ""
    print(
        f"{label}: {len(violations)} violation(s), {len(warnings)} warning(s), "
        f"{allowed} allow-marker line(s) — rules: {rule_names}{standard}"
    )
    if fix:
        verb = "would apply" if dry_run else "applied"
        print(
            f"{label}: {verb} {applied} fix(es), {suppressed} fix(es) suppressed "
            f"by allow markers"
        )
        if dry_run and applied:
            return 1  # signal: fixes are pending
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
