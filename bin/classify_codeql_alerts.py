#!/usr/bin/env python3
"""Copied-vs-novel CodeQL classification for moleculer twins (Ruling 1/4).

Implements architect record 1be07075 mechanically: for every OPEN CodeQL
alert under a twin directory, resolve the twin path to the incumbent via the
port registry (moleculer/ports.yaml canary rows), evaluate the same rule at
the incumbent location, and enforce:

  copied     the alert's rule is open at the incumbent service tree (the
             port registry relates twin SERVICE to incumbent SERVICE;
             twins may reorganize files, so matching is rule-level within
             the pair). Does NOT block — but (service, rule_id,
             incumbent_path) MUST have an entry in
             moleculer/codeql-backfill-ledger.yaml, else CI fails.
  novel      the rule is not open at the incumbent. BLOCKS.
             Fix in twin and backfill to the incumbent in the same change.
             A ledger entry with status: open is the only accepted
             classification for a currently-firing novel alert (a
             deliberate, owned, time-boxed divergence — "permanent
             deliberate divergence" is not an available outcome).
  unclassified  neither — CI fails. Silence is not classification.

An alert whose twin path cannot be resolved to a registry service fails too
(a twin that ships code outside its declared directory is a registry bug,
not an excuse).

Exit code: 0 = every twin alert classified and ledger-compliant;
           1 = violations (each named with path, rule, and reason);
           2 = setup error (registry/ledger schema violation, gh failure).

The core is pure and hermetically tested (bin/tests/
test_classify_codeql_alerts.py); only fetch_gh_open_alerts touches the
network, via the `gh` CLI (auth + pagination handled there), so CI needs
`security-events: read` and nothing else.

Usage:
    python3 bin/classify_codeql_alerts.py                 # classify main
    python3 bin/classify_codeql_alerts.py --ref pr/123    # another ref
    python3 bin/classify_codeql_alerts.py --json          # machine summary
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field

import yaml

DEFAULT_REPO = "markpippins/nexus"
DEFAULT_REF = "main"
LEDGER_PATH = os.path.join("moleculer", "codeql-backfill-ledger.yaml")
REGISTRY_PATH = os.path.join("moleculer", "ports.yaml")
LEDGER_STATUSES = ("open", "backfilled", "refuted")
LEDGER_REQUIRED_FIELDS = (
    "service",
    "rule_id",
    "incumbent_path",
    "twin_path",
    "status",
    "owner",
    "opened",
    "timebox",
    "note",
)


class SetupError(Exception):
    """Registry/ledger schema violation or tooling failure — exit 2."""


# ── registry ─────────────────────────────────────────────────────────────────
def load_registry(root: str) -> dict[str, dict]:
    """Canary rows of moleculer/ports.yaml: service name -> incumbent info."""
    path = os.path.join(root, REGISTRY_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise SetupError(f"port registry not found: {path}") from e
    except yaml.YAMLError as e:
        raise SetupError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("canary"), list):
        raise SetupError(f"{path}: expected a top-level 'canary:' list")
    reg: dict[str, dict] = {}
    for row in data["canary"]:
        for field_name in ("port", "name", "incumbent", "incumbent_port"):
            if field_name not in row:
                raise SetupError(f"{path}: canary row missing '{field_name}': {row}")
        name = str(row["name"])
        if name in reg:
            raise SetupError(f"{path}: duplicate canary service '{name}'")
        reg[name] = {
            "port": int(row["port"]),
            "incumbent": str(row["incumbent"]).rstrip("/"),
            "incumbent_port": int(row["incumbent_port"]),
        }
    if not reg:
        raise SetupError(f"{path}: canary list is empty")
    return reg


def resolve_twin_path(path: str, registry: dict[str, dict]) -> tuple[str, str] | None:
    """moleculer/<name>/rest -> (name, rest) using the longest declared name.

    Returns None when the path is not under any declared twin directory.
    """
    if not path.startswith("moleculer/"):
        return None
    rest = path[len("moleculer/"):]
    for name in sorted(registry, key=len, reverse=True):
        prefix = name + "/"
        if rest.startswith(prefix):
            return name, rest[len(prefix):]
    return None


def incumbent_matches(
    service: str, rule_id: str, incumbent_open: set[tuple[str, str]],
    registry: dict[str, dict],
) -> list[str]:
    """Incumbent paths (sorted, deterministic) where the same rule is open.

    Matching is at RULE level within the registry's service pair: the port
    registry relates twin SERVICE to incumbent SERVICE, and the port-wave
    twins deliberately reorganize the incumbent's files (substance bundles
    the incumbent's src/index.ts app into services/express-app.ts;
    dispatch-through-Express twins copy the whole route stack). File-level
    path mapping would silently miss every such reorganization — the exact
    silent-classification failure Ruling 1/4 forbids. A twin alert is
    copied iff its rule is open anywhere under the incumbent service tree;
    the ledger entry's incumbent_path pins the backfill target.
    """
    root = registry[service]["incumbent"] + "/"
    return sorted(p for (p, r) in incumbent_open if r == rule_id and p.startswith(root))


# ── ledger ───────────────────────────────────────────────────────────────────
def load_ledger(root: str) -> list[dict]:
    """Validated entries of the backfill ledger; schema violations exit 2."""
    path = os.path.join(root, LEDGER_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise SetupError(
            f"backfill ledger not found: {path} "
            f"(create it before the classification gate runs)"
        ) from e
    except yaml.YAMLError as e:
        raise SetupError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise SetupError(f"{path}: expected a top-level 'entries:' list")
    seen: set[tuple[str, str, str]] = set()
    for i, e in enumerate(data["entries"]):
        where = f"{path}: entry #{i + 1}"
        for field_name in LEDGER_REQUIRED_FIELDS:
            if field_name not in e:
                raise SetupError(f"{where}: missing required field '{field_name}'")
        if e["status"] not in LEDGER_STATUSES:
            raise SetupError(
                f"{where}: status must be one of {LEDGER_STATUSES}, "
                f"got {e['status']!r}"
            )
        if not str(e["owner"]).strip():
            raise SetupError(f"{where}: 'owner' must be non-empty")
        for field_name in ("incumbent_path", "twin_path"):
            if not str(e[field_name]).startswith(("typescript/", "moleculer/", "jvm/")):
                raise SetupError(
                    f"{where}: {field_name} must be a repo path, "
                    f"got {e[field_name]!r}"
                )
        key = (str(e["service"]), str(e["rule_id"]), str(e["incumbent_path"]))
        if key in seen:
            raise SetupError(f"{where}: duplicate ledger key {key} (dedup is mandatory)")
        seen.add(key)
    return data["entries"]


def ledger_lookup(
    ledger: list[dict], service: str, rule_id: str, incumbent_path: str
) -> dict | None:
    for e in ledger:
        if (
            str(e["service"]) == service
            and str(e["rule_id"]) == rule_id
            and str(e["incumbent_path"]) == incumbent_path
        ):
            return e
    return None


# ── classification ───────────────────────────────────────────────────────────
@dataclass
class Alert:
    number: int | None
    path: str
    rule_id: str


@dataclass
class Classification:
    alert: Alert
    outcome: str  # copied_ledgered | copied_unledgered | novel | unresolved
    service: str | None = None
    candidates: list[str] = field(default_factory=list)
    matched_incumbent: str | None = None
    ledger_entry: dict | None = None
    reason: str = ""


def classify(
    twin_alerts: list[Alert],
    registry: dict[str, dict],
    ledger: list[dict],
    incumbent_open: set[tuple[str, str]],
) -> tuple[list[Classification], list[Classification]]:
    """Pure classification. Returns (oks, violations)."""
    oks: list[Classification] = []
    bad: list[Classification] = []
    for alert in twin_alerts:
        resolved = resolve_twin_path(alert.path, registry)
        if resolved is None:
            bad.append(
                Classification(
                    alert,
                    "unresolved",
                    reason=(
                        "twin path does not resolve to any ports.yaml canary "
                        "service — registry bug, not an excuse"
                    ),
                )
            )
            continue
        service, _rel = resolved
        matched = incumbent_matches(service, alert.rule_id, incumbent_open, registry)
        if matched:
            entry = ledger_lookup(ledger, service, alert.rule_id, matched[0])
            if entry is not None:
                oks.append(
                    Classification(
                        alert,
                        "copied_ledgered",
                        service,
                        matched,
                        matched[0],
                        entry,
                        "copied: rule open at the incumbent "
                        f"({len(matched)} site(s)) AND ledger entry present",
                    )
                )
            else:
                bad.append(
                    Classification(
                        alert,
                        "copied_unledgered",
                        service,
                        matched,
                        matched[0],
                        None,
                        "copied: rule open at the incumbent ("
                        + ", ".join(matched[:3])
                        + (") but (service, rule_id, incumbent_path) has no "
                          f"entry in {LEDGER_PATH} — add it with an owner and "
                          "a timebox"
                        if len(matched) <= 3
                        else f", +{len(matched) - 3} more) but (service, rule_id, "
                        f"incumbent_path) has no entry in {LEDGER_PATH} — add it "
                        "with an owner and a timebox"),
                    )
                )
        else:
            entry = next(
                (
                    e
                    for e in ledger
                    if str(e["service"]) == service
                    and str(e["rule_id"]) == alert.rule_id
                    and e["status"] == "open"
                ),
                None,
            )
            if entry is not None:
                oks.append(
                    Classification(
                        alert,
                        "novel_ledgered",
                        service,
                        [],
                        None,
                        entry,
                        "novel (rule not open at the incumbent) but an open, "
                        "owned, time-boxed ledger entry classifies it",
                    )
                )
            else:
                bad.append(
                    Classification(
                        alert,
                        "novel",
                        service,
                        [],
                        None,
                        None,
                        "novel: rule not open at the incumbent and no ledger "
                        "entry — fix in twin and backfill to the incumbent in "
                        "the same change (Ruling 1/4)",
                    )
                )
    return oks, bad


# ── github source ────────────────────────────────────────────────────────────
def normalize_ref(ref: str) -> str:
    """Short refs -> fully-qualified: 'main' -> refs/heads/main,
    'pr/123' -> refs/pull/123/head (where default-setup PR analyses live)."""
    if ref.startswith("refs/"):
        return ref
    if ref.startswith("pr/"):
        return f"refs/pull/{ref[3:]}/head"
    return f"refs/heads/{ref}"


def fetch_gh_open_alerts(
    repo: str, ref: str, gh: str = "gh"
) -> tuple[list[Alert], set[tuple[str, str]]]:
    """All OPEN code-scanning alerts on `ref` via the gh CLI.

    `ref` is normalized (normalize_ref) and fully-qualified for the API.
    Returns (alerts, open_pairs) where open_pairs is the set of
    (path, rule_id) used for incumbent matching.
    """
    url = (
        f"repos/{repo}/code-scanning/alerts"
        f"?ref={urllib.parse.quote(normalize_ref(ref), safe='')}&per_page=100&state=open"
    )
    proc = subprocess.run(
        [gh, "api", url, "--paginate"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise SetupError(
            f"gh api failed for {url}: {(proc.stderr or proc.stdout).strip()[:400]}"
        )
    alerts: list[Alert] = []
    open_pairs: set[tuple[str, str]] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        page = json.loads(line)
        if isinstance(page, dict):
            page = page.get("items", [page])
        for a in page:
            if a.get("state") and a["state"] != "open":
                continue
            mri = a.get("most_recent_instance") or {}
            loc = mri.get("location") or {}
            path, rule_id = loc.get("path"), (a.get("rule") or {}).get("id")
            if not path or not rule_id:
                continue
            alerts.append(Alert(a.get("number"), path, rule_id))
            open_pairs.add((path, rule_id))
    return alerts, open_pairs


# ── report ───────────────────────────────────────────────────────────────────
def filter_twin_alerts(
    alerts: list[Alert], registry: dict[str, dict]
) -> list[Alert]:
    """The subset of alerts whose path lies under a declared twin directory."""
    return [a for a in alerts if resolve_twin_path(a.path, registry) is not None]


def run(args: argparse.Namespace) -> tuple[list[Classification], list[Classification]]:
    registry = load_registry(args.root)
    ledger = load_ledger(args.root)
    all_alerts, open_pairs = fetch_gh_open_alerts(args.repo, args.ref, gh=args.gh)
    twin_alerts = filter_twin_alerts(all_alerts, registry)
    return classify(twin_alerts, registry, ledger, open_pairs)


def render_report(oks: list[Classification], bad: list[Classification]) -> str:
    lines = [
        "CodeQL twin copied-vs-novel classification (Ruling 1/4)",
        f"  classified ok : {len(oks)}",
        f"  violations    : {len(bad)}",
        "",
    ]
    for c in oks:
        lines.append(
            f"[ok]  {c.alert.path}:{c.alert.rule_id}"
            + (f" (#{c.alert.number})" if c.alert.number else "")
            + f" -> {c.outcome}; incumbent={c.matched_incumbent or c.candidates}"
        )
    for c in bad:
        lines.append(
            f"[FAIL] {c.alert.path}:{c.alert.rule_id}"
            + (f" (#{c.alert.number})" if c.alert.number else "")
            + f" -> {c.outcome}: {c.reason}"
        )
    if bad:
        lines.append("")
        lines.append(
            "Copied debt belongs in moleculer/codeql-backfill-ledger.yaml "
            "keyed (service, rule_id, incumbent_path); novel alerts must be "
            "fixed in the twin and backfilled to the incumbent."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    ap.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME", DEFAULT_REF))
    ap.add_argument("--gh", default="gh", help="gh CLI binary (overridable for tests)")
    ap.add_argument("--root", default=os.getcwd(), help="repo root")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)
    try:
        oks, bad = run(args)
    except SetupError as e:
        print(f"SETUP ERROR: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not bad,
                    "ok_count": len(oks),
                    "violations": [
                        {
                            "outcome": c.outcome,
                            "path": c.alert.path,
                            "rule_id": c.alert.rule_id,
                            "alert": c.alert.number,
                            "service": c.service,
                            "reason": c.reason,
                        }
                        for c in bad
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render_report(oks, bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
