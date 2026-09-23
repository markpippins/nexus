#!/usr/bin/env python3
"""Governance approval surface for aspects tag bindings.

Lists proposed bindings and records approve/reject decisions with
attribution, completing the G2 lifecycle's governance loop
(engineer intent 7923c595):

    governance_bindings.py list [--status proposed] [--all] [--json]
    governance_bindings.py approve <binding_id> --actor WHO [--note TEXT]
    governance_bindings.py reject  <binding_id> --actor WHO [--note TEXT]
    governance_bindings.py show    <binding_id>

Attribution rules (enforced here and in the port, backed by the V199
decision-coherence CHECK):
  - approve/reject REQUIRE --actor (who made the decision); the optional
    --note is stored as the recorded rationale.
  - bound_by stays the PROPOSER's identity and is never overwritten.
  - expiry is lifecycle, not a decision: the port handles it without an
    actor and preserves any prior decision attribution.

The tool enforces WHO-ATTRIBUTED, not WHO-MAY-DECIDE; role gating for
approval authority is a policy question parked with the architect (I2).

Connection: DSN comes from $NEXUS_ASPECTS_DSN, defaulting to the local
nexus database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

DEFAULT_DSN = os.environ.get(
    "NEXUS_ASPECTS_DSN",
    "postgresql://pguser:pgpass@localhost:5432/nexus",
)

DECISION_STATUSES = ("approved", "rejected")
DECISION_COMMANDS = {"approve": "approved", "reject": "rejected"}


# ── Driver presence (independent guard; mirrors #502's port-level one) ──

def check_driver() -> Optional[str]:
    """Return an actionable error message when asyncpg is missing."""
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return (
            "asyncpg is not installed; the governance binding surface "
            "requires it to reach the database. Fix: "
            "pip install -r python/aspects/requirements.txt"
        )
    return None


# ── Port construction ────────────────────────────────────────────────────

PortFactory = Callable[[str], Awaitable[Any]]


async def default_port_factory(dsn: str) -> Any:
    import asyncpg

    # Self-locate the repo so the tool runs from any cwd: the aspects
    # package lives under <repo>/python, bin-local helpers under <repo>/bin.
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for candidate in (str(repo_root / "python"), str(repo_root)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    from aspects.binding_port import AspectsBindingPort

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    return AspectsBindingPort(pool)


# ── Rendering ────────────────────────────────────────────────────────────

def _short(value: Optional[str], width: int) -> str:
    value = value or "-"
    return value if len(value) <= width else value[: width - 1] + "…"


def render_bindings(bindings: List[Any]) -> str:
    if not bindings:
        return "no bindings matched"
    header = (
        f"{'ID':8} {'STATUS':9} {'GOVERNED TAG':22} {'SOURCE':24} "
        f"{'PROPOSER':18} {'DECIDED BY':18} {'DECIDED AT':17} NOTE"
    )
    lines = [header]
    for b in bindings:
        tag = f"{b.namespace}/{b.tag_key}={b.normalized_value}"
        decided_at = (
            b.decided_at.strftime("%Y-%m-%d %H:%M") if b.decided_at else "-"
        )
        lines.append(
            f"{str(b.id)[:8]:8} {b.status:9} {_short(tag, 22):22} "
            f"{_short(b.source_identity, 24):24} {_short(b.bound_by, 18):18} "
            f"{_short(b.decided_by, 18):18} {decided_at:17} "
            f"{_short(b.decision_note, 28)}"
        )
    return "\n".join(lines)


def render_json(bindings: List[Any]) -> str:
    """Machine-readable rows. Built via getattr so duck-typed bindings
    (test fakes, asyncpg Rows mapped by _row_to_tag_binding) serialize
    without requiring a real dataclass."""
    import json

    fields = (
        "governed_tag_name", "governed_normalized_name", "member_kind",
        "applies_to", "source_identity", "source_revision", "namespace",
        "tag_key", "normalized_value", "expression_observation_id",
        "status", "bound_by", "decided_by", "decision_note",
    )

    def _iso(dt: Any) -> Optional[str]:
        return dt.isoformat() if dt else None

    return json.dumps(
        [
            {
                "id": str(b.id),
                "governed_tag_id": str(b.governed_tag_id),
                **{f: getattr(b, f, None) for f in fields},
                "created_at": _iso(getattr(b, "created_at", None)),
                "decided_at": _iso(getattr(b, "decided_at", None)),
                "expired_at": _iso(getattr(b, "expired_at", None)),
            }
            for b in bindings
        ],
        indent=2,
        default=str,
    )


# ── Commands (port-injectable for hermetic tests) ───────────────────────

async def cmd_list(port: Any, args: argparse.Namespace) -> int:
    status = None if args.all else (args.status or "proposed")
    bindings = await port.list_bindings(
        status=status, active_only=not args.all
    )
    if args.json:
        print(render_json(bindings))
    else:
        scope = "all statuses" if args.all else f"status={status}"
        print(f"bindings ({scope}):")
        print(render_bindings(bindings))
    return 0


async def _resolve_binding(port: Any, ref: str) -> Optional[Any]:
    """Accept a full UUID or an unambiguous id prefix.

    The port has no by-id fetch, so resolution scans the full (including
    expired) binding list — governance-scale data, single indexed table.
    Raises ValueError for an ambiguous prefix; returns None for no match.
    """
    candidates = await port.list_bindings(active_only=False)
    matches = [b for b in candidates if str(b.id).startswith(ref.lower())]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"binding id prefix {ref!r} is ambiguous ({len(matches)} matches)"
        )
    return None


async def cmd_decide(
    port: Any, args: argparse.Namespace, decision: str
) -> int:
    assert decision in DECISION_STATUSES
    if getattr(args, "actor", None) is None:
        print(
            f"REFUSED: {decision} requires --actor (who made the decision)",
            file=sys.stderr,
        )
        return 1
    binding = await _resolve_binding(port, args.binding_id)
    if binding is None:
        print(
            f"REFUSED: no active binding matches {args.binding_id!r} "
            "(already expired, or unknown id)",
            file=sys.stderr,
        )
        return 1
    updated = await port.update_binding_status(
        binding.id, decision, decided_by=args.actor, decision_note=args.note
    )
    if updated is None:
        print(
            f"REFUSED: binding {args.binding_id!r} vanished or is expired; "
            "nothing changed",
            file=sys.stderr,
        )
        return 1
    print(
        f"{decision.upper()} binding {str(updated.id)[:8]} "
        f"({updated.namespace}/{updated.tag_key}={updated.normalized_value}): "
        f"decided_by={updated.decided_by} at "
        f"{updated.decided_at.strftime('%Y-%m-%dT%H:%M:%SZ') if updated.decided_at else '?'}"
        + (f" note={updated.decision_note!r}" if updated.decision_note else "")
        + f" (proposer {updated.bound_by or 'unknown'} preserved)"
    )
    return 0


async def cmd_show(port: Any, args: argparse.Namespace) -> int:
    binding = await _resolve_binding(port, args.binding_id)
    if binding is None:
        print(f"no active binding matches {args.binding_id!r}", file=sys.stderr)
        return 1
    print(render_json([binding]))
    return 0


# ── CLI wiring ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance_bindings.py",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--dsn",
        default=DEFAULT_DSN,
        help="postgres DSN (default: $NEXUS_ASPECTS_DSN or local nexus)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list bindings (default: proposed)")
    p_list.add_argument("--status", choices=["proposed", "approved", "rejected", "expired"])
    p_list.add_argument("--all", action="store_true", help="include expired rows")
    p_list.add_argument("--json", action="store_true", help="machine-readable output")

    for command, decision in DECISION_COMMANDS.items():
        p = sub.add_parser(
            command, help=f"{decision} a binding with attribution"
        )
        p.add_argument("binding_id", help="binding UUID (or unambiguous prefix)")
        p.add_argument(
            "--actor", default=None,
            help=f"who is making the {decision} decision (REQUIRED; "
                 f"a {decision} without attribution is refused)",
        )
        p.add_argument("--note", default=None, help="decision rationale")

    p_show = sub.add_parser("show", help="show one binding as JSON")
    p_show.add_argument("binding_id")

    return parser


async def run(
    argv: Optional[List[str]] = None,
    port_factory: PortFactory = default_port_factory,
    driver_check: Callable[[], Optional[str]] = check_driver,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    driver_error = driver_check()
    if driver_error:
        print(driver_error, file=sys.stderr)
        return 2

    try:
        port = await port_factory(args.dsn)
        if args.command == "list":
            return await cmd_list(port, args)
        if args.command in DECISION_COMMANDS:
            return await cmd_decide(port, args, DECISION_COMMANDS[args.command])
        if args.command == "show":
            return await cmd_show(port, args)
        parser.error(f"unknown command {args.command!r}")
    except ValueError as exc:  # illegal transitions, ambiguous prefixes
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # DB down, driver errors — fail closed, no traceback
        print(f"ERROR: {exc!r} (fail closed)", file=sys.stderr)
        return 2
    return 2


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
