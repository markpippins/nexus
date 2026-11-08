#!/usr/bin/env python3
"""Guards the harness procedure cards: config creation, launch, and smoke-test.

These three cards were written directly into ``tackle.memory`` (the live
canonical registry) before they existed in the seed source, so a database
restore or a fresh environment silently lost them. The seed is a derived
projection of the DB, and this test protects the projection: the three cards
must be present, complete, correctly hashed, and identical in every vendoring
copy.

No database, no network. Everything asserted here is read from the checkout
the test lives in, so a linked worktree is verified on its own content rather
than on whatever ``/home/codex/dev/nexus`` happens to contain.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the shared hashing helper from THIS checkout. bin/run_bin_tests.py
# scrubs PYTHONPATH (a CI runner has none), so relying on the ambient path
# imports either nothing or, worse, a different checkout's copy.
sys.path.insert(0, os.path.join(REPO, "python"))

SEED_DIR = os.path.join(REPO, "typescript", "tackle-seeds")
SEED_INDEX = os.path.join(SEED_DIR, "index.ts")
SEED_MANIFEST = os.path.join(SEED_DIR, "seed-manifest.json")
VENDOR_DIR = os.path.join(REPO, "moleculer", "tackle", "vendor", "tackle-seeds")
VENDOR_INDEX = os.path.join(VENDOR_DIR, "index.ts")
VENDOR_MANIFEST = os.path.join(VENDOR_DIR, "seed-manifest.json")

HARNESS_SLUGS = (
    "harness-configuration-creation",
    "harness-launch-and-monitoring",
    "harness-smoke-testing",
)

# A card whose body is a one-liner is not a procedure: the smoke card alone
# carries the ordering rule (non-mutating prompt, bounded scope). This is a
# floor against accidental truncation, not a length competition.
MIN_BODY_CHARS = 400


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _manifest() -> dict:
    return json.loads(_read(SEED_MANIFEST))


def _cards_by_slug() -> dict:
    return {card["slug"]: card for card in _manifest()["cards"]}


def test_manifest_card_count_matches_cards() -> None:
    manifest = _manifest()
    assert manifest["card_count"] == len(manifest["cards"])
    assert manifest["card_count"] >= len(HARNESS_SLUGS)


def test_non_vacuity_bogus_slug_is_absent() -> None:
    # If the lookup below ever degrades to "always passes", this fails.
    assert "harness-not-a-real-card" not in _cards_by_slug()


@pytest.mark.parametrize("slug", HARNESS_SLUGS)
def test_card_is_present_and_defined_once_in_seed(slug: str) -> None:
    assert slug in _cards_by_slug(), f"{slug} missing from the seed manifest"
    # Exactly one definition per copy: a duplicated card silently doubles its
    # role_memory rows on seed.
    assert _read(SEED_INDEX).count(f"'{slug}'") == 1


@pytest.mark.parametrize("slug", HARNESS_SLUGS)
def test_card_content_is_complete_and_hash_matches(slug: str) -> None:
    from nexus_core.wrp.seed_manifest import card_sha256  # noqa: PLC0415

    card = _cards_by_slug()[slug]
    assert card["title"].strip()
    assert card["summary"].strip()
    assert len(card["body_md"]) >= MIN_BODY_CHARS
    assert card["tags"], "a card with no tags is unreachable by tag routing"
    assert card["triggers"], "a card with no triggers cannot be loaded on demand"
    assert card["mcp_tools"], "the procedure names no tools"
    assert card["roles"], "the card is assigned to no role"
    expected = card_sha256(
        card["slug"],
        card["title"],
        card["summary"],
        card["body_md"],
        card["tags"],
        card["triggers"],
        card["mcp_tools"],
    )
    assert card["sha256"] == expected


def test_vendored_copies_are_byte_identical() -> None:
    # moleculer/tackle is a vendoring twin: services execute the copy, so a
    # divergence means live cards and the source disagree.
    for source, twin in (
        (SEED_INDEX, VENDOR_INDEX),
        (SEED_MANIFEST, VENDOR_MANIFEST),
    ):
        assert _read(source).encode() == _read(twin).encode(), (
            f"{os.path.relpath(twin, REPO)} is stale versus "
            f"{os.path.relpath(source, REPO)}"
        )
