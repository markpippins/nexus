#!/usr/bin/env python3
"""Guard: opencode agent permission rulesets stay runnable without a human.

Background
----------
Worker-launched opencode sessions are non-interactive. When a permission rule
evaluates to `ask`, there is nobody to answer it, the call is auto-rejected, and
the denial surfaces as a tool-call error that ends the model loop — the process
exits non-zero and the whole run is lost. On 2026-10-30 that killed three
consecutive builder runs (plan 8261665, refs 48f213bc): the builder read
`/proc/<pid>/cmdline` to prove what it was doing, which is `external_directory`
outside the run's working tree, which evaluated to `ask`, which aborted.

Two separate hazards are pinned here, and they are easy to confuse.

1. ORDER. opencode resolves a permission request with
   `findLast(rule => match(permission, rule.permission) && match(pattern, rule.pattern))`
   — LAST matching rule wins. A ruleset written as

       external_directory:
         /tmp/opencode: allow
         '*': ask

   therefore asks for everything, including `/tmp/opencode`, because the
   catch-all comes last and overrides the specific allow. That is exactly how
   the files were written before this test existed. Correct ordering puts the
   catch-all FIRST and the allows after it.

2. COVERAGE. The read-only diagnostic roots a non-interactive run genuinely
   needs — `/tmp` (scratch, temp artifacts) and `/proc` (its own cmdline,
   environ, cwd that the worker cannot inject into the prompt) — must be
   allowed. opencode asks with the pattern `dirname(file) + "/*"`, so the
   patterns are matched against subjects like `/proc/7603/*`; the `**` forms
   are what actually match, with the single-`*` forms kept as defence in depth.

Scope
-----
Only agents that DECLARE an `external_directory` block are checked. An agent
with no such block inherits opencode's own ruleset (which allows external
reads), so it never hits this failure mode — asserting on it would be asserting
on upstream's defaults, not on ours. If a new agent file adds a restrictive
block it is picked up automatically.

Agents whose catch-all is `deny` rather than `ask` are NOT covered by the
coverage assertions: `deny` never reaches a human, so it cannot cause the
silent abort this test exists to prevent. `supervisor.md` declares exactly that
shape, and its two specific allows are dead for the same last-match-wins reason
— but broadening them would change another role's authority, which is an owner
decision, not a test's. That agent is pinned by name in
DENY_CATCH_ALL_AGENTS so the anomaly stays visible and a NEW deny-based agent
forces an explicit decision instead of sliding in unnoticed.

The block count is asserted non-zero so this test cannot pass vacuously if the
agent files are moved or renamed.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO / "config" / "harnesses" / "opencode" / "agents"

# Frontmatter opens with `---`, or with the banner form `>---` used by the agent
# files that carry the "aspirational architecture" notice above their metadata.
OPENERS = ("---", ">---")

# Agents that deny external reads wholesale. Their dead specific allows are a
# known, documented anomaly (see module docstring); adding one here is a
# deliberate act, not an accident.
DENY_CATCH_ALL_AGENTS = {"supervisor.md"}


def _frontmatter(path: pathlib.Path) -> dict:
    lines = path.read_text().splitlines()
    start = next((i for i, line in enumerate(lines[:12]) if line.strip() in OPENERS), None)
    if start is None:
        pytest.fail(f"{path.name}: no frontmatter opener found")
    end = next((i for i, line in enumerate(lines) if i > start and line.strip() == "---"), None)
    if end is None:
        pytest.fail(f"{path.name}: frontmatter never closed")
    try:
        data = yaml.safe_load("\n".join(lines[start + 1:end]))
    except yaml.YAMLError as exc:
        pytest.fail(f"{path.name}: frontmatter is not valid YAML: {exc}")
    assert isinstance(data, dict), f"{path.name}: frontmatter is not a mapping"
    return data


def _agents_with_external_directory() -> list[tuple[pathlib.Path, dict]]:
    found = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        permission = (_frontmatter(path).get("permission") or {})
        external = permission.get("external_directory")
        if external is None:
            continue
        assert isinstance(external, dict), f"{path.name}: external_directory must be a mapping"
        found.append((path, external))
    return found


AGENTS = _agents_with_external_directory()
ASK_AGENTS = [(p, e) for p, e in AGENTS if e.get("*") == "ask"]


def test_agent_files_were_actually_found() -> None:
    """Non-vacuity: the scan must see the files this guard is about."""
    assert ASK_AGENTS, (
        f"no agent under {AGENTS_DIR} declares an 'ask' external_directory block — "
        "either the agents moved, or the block was dropped. Both need a human look."
    )


def test_deny_catch_all_agents_are_known() -> None:
    """A deny-based ruleset is out of scope by design, but not invisible."""
    deny_agents = {p.name for p, e in AGENTS if e.get("*") == "deny"}
    unexpected = deny_agents - DENY_CATCH_ALL_AGENTS
    assert not unexpected, (
        "new agent(s) deny external reads wholesale: " + ", ".join(sorted(unexpected)) +
        ". Deny cannot trigger the non-interactive abort, but any specific allow in "
        "such a block is dead (last-match-wins). Decide deliberately, then record it "
        "in DENY_CATCH_ALL_AGENTS."
    )
    stale_pins = DENY_CATCH_ALL_AGENTS - deny_agents
    assert not stale_pins, (
        "pinned deny agent(s) no longer deny external reads: " + ", ".join(sorted(stale_pins)) +
        " — update or drop the pin."
    )


def test_catch_all_precedes_specific_allows() -> None:
    """Last matching rule wins, so the ask catch-all must come before the allows."""
    offenders = []
    for path, external in ASK_AGENTS:
        keys = list(external)  # YAML mapping order is source order
        if "*" not in keys:
            offenders.append(f"{path.name}: no '*' catch-all rule")
            continue
        catch_all = keys.index("*")
        if catch_all != 0:
            offenders.append(
                f"{path.name}: '*' catch-all at position {catch_all}, "
                f"before specific rules {keys[:catch_all]} — those are silently overridden"
            )
    assert not offenders, (
        "opencode resolves permissions last-match-wins; a trailing catch-all "
        "overrides every allow above it:\n  " + "\n  ".join(offenders)
    )


def test_diagnostic_roots_are_allowed() -> None:
    """A non-interactive run must be able to read its own /proc and /tmp."""
    required = ("/tmp/**", "/proc/**")
    offenders = []
    for path, external in ASK_AGENTS:
        missing = [pattern for pattern in required if external.get(pattern) != "allow"]
        if missing:
            offenders.append(f"{path.name}: missing allow for {', '.join(missing)}")
    assert not offenders, (
        "worker-launched sessions cannot answer an 'ask'; these roots must be "
        "allowed or the run aborts:\n  " + "\n  ".join(offenders)
    )


def test_no_rule_is_left_at_ask_below_the_allows() -> None:
    """Sanity: the trailing rules are allows, not a second catch-all at ask."""
    offenders = []
    for path, external in ASK_AGENTS:
        trailing = [k for k in list(external)[1:] if external[k] != "allow"]
        if trailing:
            offenders.append(f"{path.name}: non-allow rule(s) after the catch-all: {trailing}")
    assert not offenders, (
        "rules after the catch-all must be allows, or they re-introduce the "
        "auto-reject path:\n  " + "\n  ".join(offenders)
    )
