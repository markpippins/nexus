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

Escape hatch: '# wf-lint-allow' (bare) suppresses every rule for that
line; '# wf-lint-allow: rule1,rule2' suppresses only the named rules;
'# wf-lint-allow: <free-text reason>' documents and suppresses all rules
on the line. The postgres rule also honors its legacy '# pg-pin-allow'
marker.

Scope: postgres-pin / eol-runtime / action-ref / job-hardening scan
.github/workflows YAML only; dead-base scans Dockerfiles anywhere under
the target.
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
]


def scan(target: str, rules: list[Rule]) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    allowed = 0
    for dirpath, _dirnames, filenames in sorted(os.walk(target)):
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
    for dirpath, _dirnames, filenames in sorted(os.walk(target)):
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
