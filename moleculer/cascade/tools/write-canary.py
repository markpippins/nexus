#!/usr/bin/env python3
"""Write-path canary: cascade (incumbent :3106 vs twin :4106).

Ruling 59f8e2af (Q-B): the toggle IS cascade's entire write surface, so the
full governed toggle runs; the 404 case runs regardless of the fixture.

Governed fixture (DBA-seeded, per Q-B): subject_pattern='canary-wp-test',
enabled=false initially, persists for the campaign. If the fixture row is
missing, this script runs ONLY the 404 case and exits 2 with a clear reason.

Sequence (ruling): off via A -> on via B -> off via A. Final DB state must
equal the initial (enabled=false); every response envelope byte-compared.
"""
from __future__ import annotations

import os
import argparse
import json
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from write_canary_lib import (  # noqa: E402
    Canary, Case, ab_write_pair, pg, sql_lit,
)

FIXTURE_PATTERN = "canary-wp-test"
TOGGLE_PATH = "/api/cascade/subscribers/{pattern}"
NOT_FOUND_PATTERN = "canary-wp-no-such-pattern"


def fixture_present() -> bool:
    rows = pg(
        f"SELECT enabled FROM cascade.subscriptions "
        f"WHERE subject_pattern = {sql_lit(FIXTURE_PATTERN)}"
    )
    return len(rows) == 1


def fixture_enabled() -> bool:
    rows = pg(
        f"SELECT enabled FROM cascade.subscriptions "
        f"WHERE subject_pattern = {sql_lit(FIXTURE_PATTERN)}"
    )
    if len(rows) != 1:
        raise AssertionError("fixture row vanished mid-run — DBA fixture changed")
    return rows[0]["col0"] == "t"


def case_patch_404(canary: Canary, case: Case) -> None:
    """Unknown pattern: both twins 404 {error:'Subscriber not found'}; zero state."""
    ab_write_pair(
        canary, "PATCH", TOGGLE_PATH.format(pattern=NOT_FOUND_PATTERN),
        body_fn=lambda _t: {"enabled": True},
        expect_status=404,
    )


def case_toggle_sequence(canary: Canary, case: Case) -> None:
    """off(A) -> on(B) -> off(A); DB state and envelopes compared per step."""
    if fixture_enabled():
        raise AssertionError(
            "fixture starts enabled=true — the ruling requires enabled=false initially; "
            "ask DBA to reset before running"
        )
    # step 1: off via incumbent (no-op state write, envelope still compared)
    ra, _ = ab_write_pair(
        canary, "PATCH", TOGGLE_PATH.format(pattern=FIXTURE_PATTERN),
        body_fn=lambda _t: {"enabled": False},
        expect_status=200,
    )
    # step 2: on via twin
    rb, _ = ab_write_pair(
        canary, "PATCH", TOGGLE_PATH.format(pattern=FIXTURE_PATTERN),
        body_fn=lambda _t: {"enabled": True},
        expect_status=200,
    )
    # step 3: off via incumbent — restores the initial governed state
    rc, _ = ab_write_pair(
        canary, "PATCH", TOGGLE_PATH.format(pattern=FIXTURE_PATTERN),
        body_fn=lambda _t: {"enabled": False},
        expect_status=200,
    )
    # final state must equal initial (false)
    if fixture_enabled():
        canary.note_residue(f"{FIXTURE_PATTERN} left enabled=true")
        raise AssertionError("final fixture state is enabled=true (residue)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port-a", type=int, default=3106)
    ap.add_argument("--port-b", type=int, default=4106)
    args = ap.parse_args()

    canary = Canary("cascade", args.port_a, args.port_b)

    canary.cases.append(Case("patch-404-zero-state", case_patch_404))

    if fixture_present():
        canary.cases.append(
            Case(
                "toggle-sequence-aba",
                case_toggle_sequence,
                cleanup=lambda: pg(
                    f"UPDATE cascade.subscriptions SET enabled = FALSE "
                    f"WHERE subject_pattern = {sql_lit(FIXTURE_PATTERN)}"
                ),
                notes="governed fixture (ruling 59f8e2af Q-B)",
            )
        )
    else:
        print(
            f"fixture {FIXTURE_PATTERN} not present — running 404 case only, exit 2 "
            "(ruling: full toggle required for the cascade gate; ping DBA)",
            flush=True,
        )
        canary.run()
        return 2

    return canary.run()


if __name__ == "__main__":
    raise SystemExit(main())
