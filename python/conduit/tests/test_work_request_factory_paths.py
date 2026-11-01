"""files_affected normalization: one plan field, three storage shapes."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import work_request_factory as factory


def test_comma_joined_string_becomes_separate_paths():
    """Plan 8261665 stored files_affected as a comma-joined string; treating it
    as one path made the evidence contract unsatisfiable."""
    paths = factory._parse_path_list(
        "docs/wc-consumer-inventory.md,docs/wc-writer-fencing.md,"
        "docs/wc-stage-d-gates.md,docs/wc-migration-runbook.md"
    )
    assert paths == [
        "docs/wc-consumer-inventory.md",
        "docs/wc-writer-fencing.md",
        "docs/wc-stage-d-gates.md",
        "docs/wc-migration-runbook.md",
    ]


def test_json_array_is_preserved():
    assert factory._parse_path_list('["docs/a.md", "docs/b.md"]') == ["docs/a.md", "docs/b.md"]


def test_postgres_array_literal_is_split():
    assert factory._parse_path_list('{docs/a.md,"docs/b.md"}') == ["docs/a.md", "docs/b.md"]


def test_single_path_and_empty_values():
    assert factory._parse_path_list("docs/a.md") == ["docs/a.md"]
    assert factory._parse_path_list("") == []
    assert factory._parse_path_list(None) == []
    assert factory._parse_path_list([]) == []


def test_built_produced_files_match_contract_entries():
    plan = {"id": "p1", "files_affected": "docs/a.md,docs/b.md"}
    produced = factory._build_files_affected(plan)
    assert [item.path for item in produced] == ["docs/a.md", "docs/b.md"]
