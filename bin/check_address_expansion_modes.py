#!/usr/bin/env python3
"""Phase C alias-expansion guard: branch on expansion MODE, not on `targets`.

THE HAZARD THIS EXISTS TO AVOID
-------------------------------
Ruling 26 §5 says: *"targets absent or empty => `alias-unmapped`. On write: no
expansion, WARN `alias-unmapped`, never reject."*  Read literally, that rule
fires on **every alias without a `targets` key** -- which includes `all`,
`all-roles`, `assembly`, `user` and `self`.  Those five are **decided**, not
unmapped: Ruling 26 §1-§4 gave them expansion modes that legitimately carry no
target list (`all` fans out over roles.json; `assembly` is a forum broadcast;
`user` is an operator notification; `self` resolves to the writer's role).

A guard that tested for `targets` presence would therefore raise
`alias-unmapped` against five decided aliases, which is the exact false
positive that would make the Phase C guard untrustworthy on day one.

So the rule is stated as: **every alias declares an `expansion.mode` from a
closed vocabulary, and each mode carries its own invariants.**  `alias-unmapped`
is a property of ONE mode (`targets-expand`), not of the absence of a key.

MODE VOCABULARY (all six are closed; nothing else is accepted)
-------------------------------------------------------------
  fan-out-all-roles     §1  expand-and-replace over every roles.json
                             kind:role key. Single-hop: an alias is NEVER an
                             expansion source, so fan-out is flat and cycles
                             are unexpressible. Snapshot-at-write. Dedupe
                             against explicit tags.
  forum-broadcast       §2  one record -> one Assembly thread. No per-role
                             fan-out, NO inbox-pointer movement (tag retained
                             as provenance). Never reject.
  operator-notification §3  single recipient, non-blocking; if the operator is
                             absent the notification queues/deferrs and absence
                             is observable. Never reject.
  writer-role           §4  resolves to the creating record's own role.
                             Unresolvable => WARN `unresolvable-self`; never
                             falls back to `to:all` or `to:user`.
  targets-expand        §5  explicit role targets. Single-hop. Empty or absent
                             targets => WARN `alias-unmapped`, never reject.
  none                  R19 carries no delivery obligation (model identity).

SEVERITIES
----------
  ERROR  a broken registry: unknown/undeclared mode, a `targets` key on a mode
         that must not carry one, an empty list on `targets-expand`, a target
         that is not a roles.json `kind: role` key, a target that is itself an
         alias (cycle risk), or duplicate targets.
  WARN   a declared, sanctioned state: `targets-expand` with no targets
         (`alias-unmapped`), or `writer-role` on an address whose writer may be
         absent (`unresolvable-self` is a runtime condition, not a registry
         one, so it is not raised here).

WARNs do not fail the build by default: Ruling 26 §5 states the four unmapped
aliases "fail loudly and correctly until [the lookup] lands" and that "Phase C
is not blocked on it". `--strict` promotes them, for whoever wants the
countdown enforced.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does not check whether the registry's `_comment` prose is current, and it
does not touch `roles.json`.  The `_comment` correction owed on this file
(Ruling 24 §2 / Ruling 26 §6.4: the equality claim appears in BOTH
`roles.json._comment` and `address-kinds.json._comment`) is an engineer-owned
amendment to #715 and is out of scope here.

Exit codes: 0 = no errors (warns may be printed), 1 = errors, 2 = tool error.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ERROR = "error"
WARN = "warn"

# The closed vocabulary. Anything not in here is an unknown mode.
MODES = frozenset({
    "fan-out-all-roles",
    "forum-broadcast",
    "operator-notification",
    "writer-role",
    "targets-expand",
    "none",
})

# Modes that carry an explicit target list, and therefore are the ONLY ones
# where a missing/empty `targets` means `alias-unmapped`.
MODES_WITH_TARGETS = frozenset({"targets-expand"})

# Modes that must NOT carry a `targets` key. Their targets are derived, or the
# mode is a non-fan-out delivery path. A `targets` key here would be a second,
# silently-ignored source of truth -- the same class of bug Ruling 13 §2 was
# written to prevent.
MODES_FORBIDDEN_TARGETS = frozenset({
    "fan-out-all-roles",
    "forum-broadcast",
    "operator-notification",
    "writer-role",
    "none",
})

VALID_KINDS = frozenset({"alias", "telemetry"})


@dataclass
class Finding:
    address: str
    severity: str
    rule: str
    detail: str


def _mode_of(entry: dict) -> str | None:
    """Read the declared mode.

    `expansion` may be a bare token ("targets-expand") or an object
    ({"mode": "...", ...}). Anything else -- notably the legacy free-text
    strings such as "pending-architect (Phase C)" -- yields None.
    """
    exp = entry.get("expansion")
    if isinstance(exp, str):
        return exp if exp in MODES else None
    if isinstance(exp, dict):
        mode = exp.get("mode")
        return mode if isinstance(mode, str) and mode in MODES else None
    return None


def check_registry(addresses: dict, role_keys) -> list[Finding]:
    """Validate every address in the registry. `role_keys` = roles.json kind:role."""
    role_keys = set(role_keys)
    alias_names = {n for n, e in addresses.items() if e.get("kind") == "alias"}
    findings: list[Finding] = []

    for name, entry in sorted(addresses.items()):
        kind = entry.get("kind")
        if kind not in VALID_KINDS:
            findings.append(Finding(name, ERROR, "unknown-kind",
                                    f"kind={kind!r} is not in {sorted(VALID_KINDS)}"))
            continue

        # Telemetry is archival: recorded, never delivered, never expanded.
        # It has no expansion contract at all, so it skips the mode checks.
        if kind == "telemetry":
            if entry.get("delivery") != "never":
                findings.append(Finding(name, ERROR, "telemetry-must-be-never",
                                        "telemetry must declare delivery='never'"))
            continue

        mode = _mode_of(entry)
        if mode is None:
            exp = entry.get("expansion")
            findings.append(Finding(
                name, ERROR, "undeclared-expansion-mode",
                f"expansion={exp!r} is not a mode from the closed vocabulary "
                f"{sorted(MODES)}. Declare expansion.mode explicitly so the "
                f"guard can branch on the mode instead of guessing from key "
                f"presence."))
            # A stale free-text expansion may also carry a stray `targets`;
            # report that too rather than letting it hide behind the above.
            if "targets" in entry:
                findings.append(Finding(
                    name, ERROR, "targets-without-mode",
                    "carries `targets` but declares no recognised mode"))
            continue

        targets = entry.get("targets")

        if mode in MODES_FORBIDDEN_TARGETS:
            if "targets" in entry:
                findings.append(Finding(
                    name, ERROR, "targets-on-mode-without-targets",
                    f"mode={mode!r} derives its targets; an explicit `targets` "
                    f"key here would be a second, silently-ignored source of "
                    f"truth ({targets!r})"))
            continue

        # mode == targets-expand
        if not targets:
            findings.append(Finding(
                name, WARN, "alias-unmapped",
                "mode='targets-expand' with no targets => WARN alias-unmapped "
                "on write, never reject. This is the DECLARED unmapped state "
                "(Ruling 26 §5), not the §0 silent-drop."))
            continue

        if not isinstance(targets, list):
            findings.append(Finding(name, ERROR, "targets-not-a-list",
                                    f"targets={targets!r} is not a list"))
            continue

        seen = set()
        for t in targets:
            if t in seen:
                findings.append(Finding(name, ERROR, "duplicate-target",
                                        f"target {t!r} listed more than once"))
            seen.add(t)
            if t in alias_names:
                findings.append(Finding(
                    name, ERROR, "alias-as-target",
                    f"target {t!r} is itself a registered alias. Ruling 26 §1 "
                    f"single-hop: aliases are never expansion targets, which is "
                    f"what makes alias cycles inexpressible."))
            elif t not in role_keys:
                findings.append(Finding(
                    name, ERROR, "target-not-a-role",
                    f"target {t!r} is not a roles.json key with kind='role'"))

    return findings


def load_registry(config_dir: Path):
    """Load both registries from config/roles/.

    Takes the DIRECTORY, not two file paths. An earlier draft took
    (roles_path, kinds_path) and a caller passed them the other way round --
    which did not raise, it silently yielded an empty registry and made every
    "shipped registry" test pass vacuously. Deriving both paths from one
    argument removes the swap.
    """
    kinds = json.loads((config_dir / "address-kinds.json").read_text(encoding="utf-8"))
    roles = json.loads((config_dir / "roles.json").read_text(encoding="utf-8"))
    addresses = kinds.get("addresses", {})
    role_keys = {k for k, v in roles.get("roles", {}).items()
                 if v.get("kind") == "role"}
    if not addresses:
        raise ValueError(f"no `addresses` in {config_dir / 'address-kinds.json'}")
    return addresses, role_keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--strict", action="store_true",
                    help="treat WARN findings as failures")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    config_dir = repo / "config" / "roles"

    for name in ("address-kinds.json", "roles.json"):
        if not (config_dir / name).is_file():
            print(f"error: registry not found: {config_dir / name}", file=sys.stderr)
            return 2
    try:
        addresses, role_keys = load_registry(config_dir)
    except json.JSONDecodeError as exc:
        print(f"error: registry is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings = check_registry(addresses, role_keys)
    errors = [f for f in findings if f.severity == ERROR]
    warns = [f for f in findings if f.severity == WARN]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in errors:
            print(f"  ERROR {f.address}: [{f.rule}] {f.detail}")
        for f in warns:
            print(f"  WARN  {f.address}: [{f.rule}] {f.detail}")
        print(f"\naddress expansion modes: {len(addresses)} addresses, "
              f"{len(errors)} error(s), {len(warns)} warn(s)")

    if errors:
        return 1
    return 1 if (warns and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())