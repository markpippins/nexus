# Workflow lint family (`bin/wf_lint.py`)

One harness, nine rules, one CI gate (`.github/workflows/wf-lint.yml`).
Every rule in this family was born from a real incident or drift class —
the lint exists so those classes cannot quietly return.

## Run it

```bash
python3 bin/wf_lint.py                      # full repo (what CI runs)
python3 bin/wf_lint.py .github/workflows --rule maven-cache
python3 bin/wf_lint.py --list-rules
python3 bin/wf_lint.py --fix --dry-run      # preview structural fixes
```

Exit codes: `0` clean (warnings ok) · `1` violations · `2` usage error.
`--rule` takes one rule name per flag (`--rule a --rule b`).

## The rules

| Rule | Catches | Born from |
|---|---|---|
| `postgres-pin` | workflow postgres/pgvector pin < fleet standard (17) | 9 workflows silently on postgres:16 (fleet PG audit, #470/#474) |
| `dead-base` | Dockerfile `FROM` images that never existed / are discontinued | 5 dead base images reached PR CI (#456) |
| `eol-runtime` | setup-* runtimes past EOL (warns within 180 days) | Python 3.10 matrix legs EOL (#490 era) |
| `action-ref` | floating `uses:` refs (`@main`, bare) | pin-hygiene sweep |
| `job-hardening` | jobs without `timeout-minutes`; workflows without `permissions:` | #507 gate |
| `npm-ci` | `npm install` where a committed lockfile applies | lockfile adoption #518 |
| `maven-cache` | runner `mvn`/`mvnw` job with uncached `setup-java` | Maven Central 429 killed main CI (2026-09-25, #569) |
| `node-cache` | lock-bearing `npm` job with uncached `setup-node` | same 429 class; fleet audit found 2 |
| `pip-cache` | `pip install -r <file>` job with uncached `setup-python` | same 429 class; fleet audit (ad-hoc installs out of scope) |

## Cache-first: writing a new workflow

Every dependency-ecosystem setup step should carry its cache input from
day one. The cache rules fire only where the remediation is *satisfiable*
— GitHub's own actions hard-fail without their key files — so if your job
meets the gate, add the cache:

- **Java/Maven** — any `mvn`/`mvnw` on the runner:
  ```yaml
  - uses: actions/setup-java@v4
    with:
      java-version: '21'
      distribution: 'temurin'
      cache: maven
  ```
- **Node/npm** — any `npm ci`/`npm install` in a lock-bearing directory:
  ```yaml
  - uses: actions/setup-node@v4
    with:
      node-version: '22'
      cache: npm
      cache-dependency-path: path/to/package-lock.json   # required when it's not the repo root lock
  ```
- **Python/pip** — any `pip install -r <requirements file>` that exists in
  the repo:
  ```yaml
  - uses: actions/setup-python@v5
    with:
      python-version: '3.11'
      cache: pip
      cache-dependency-path: requirements-dev.txt
  ```

Out of scope by construction: Maven inside Docker builds (a host cache
cannot reach the container), lockless npm installs (opt-in per #518),
ad-hoc 1–2 package pip installs (marginal win, arbitrary key file), and
self-hosted runners that warm their own stores (e.g. vanadium's pip cache).

## Suppressing a finding (the escape hatch)

An allow marker on the exact anchor line suppresses the finding — bare,
selective, or with a documented reason:

```yaml
# wf-lint-allow                       # suppresses every rule on this line
# wf-lint-allow: npm-ci,maven-cache   # suppresses only the named rules
# wf-lint-allow: deliberate cold-pull # reason text suppresses all + documents
```

For the cache rules the anchor is the `uses: actions/setup-*` line itself.
Markers are counted and reported (`N allow-marker line(s)`), so suppressed
findings stay visible in aggregate.

## Adding a rule

Subclass `Rule` in `bin/wf_lint.py`, set `name`, implement `applies` +
`scan_line` (per-line) or `scan_file` (structural/missing-block), register
in `RULES`, and add behavioral tests in `bin/tests/test_wf_lint.py`
(subprocess-based fixtures, real-world shapes). Findings anchor to a line
so markers work; `--fix` passes are opt-in per rule and only for
mechanical structural remediations. Then extend the table above.
