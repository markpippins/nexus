"""Declared `files_affected` entries become repo-relative evidence targets.

Plan 8261639 stored prose area descriptions where other plans store paths.
Passed through unchanged they produced an unsatisfiable evidence contract:
every entry became "missing artifact", and the `nexus/` repo-name prefix
collapsed against a repo-root working path into `nexus/nexus/...`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import work_request_factory as factory

WORKING_PATH = "/home/codex/dev/nexus"


def test_prose_entry_is_not_a_target():
    assert factory._normalise_declared_path("nexus/resolution schema migrations", WORKING_PATH) is None


def test_repo_name_prefix_is_stripped_against_repo_root():
    assert factory._normalise_declared_path("nexus/moleculer", WORKING_PATH) == "moleculer"
    assert factory._normalise_declared_path(
        "nexus/python/conduit", WORKING_PATH
    ) == "python/conduit"


def test_parenthetical_qualifier_is_trimmed():
    assert factory._normalise_declared_path(
        "nexus/typescript/lilac-srv (new service boundary)", WORKING_PATH
    ) == "typescript/lilac-srv"


def test_repo_relative_paths_are_untouched():
    for raw in (
        "typescript/conduit-mcp/src/db.ts",
        "sql/ci-bootstrap/nexus-ci-bootstrap.sql",
    ):
        assert factory._normalise_declared_path(raw, WORKING_PATH) == raw


def test_absolute_and_escaping_paths_are_rejected():
    assert factory._normalise_declared_path("/etc/passwd", WORKING_PATH) is None
    assert factory._normalise_declared_path("../other/repo/file.ts", WORKING_PATH) is None


def test_plan_8261639_shape_yields_only_the_real_directory():
    plan = {
        "id": "8261639",
        "files_affected": (
            "{nexus/resolution schema migrations,"
            "nexus/typescript/lilac-srv (new service boundary),"
            "nexus/python/conduit execution and MCP adapters,"
            "nexus/typescript/wind-srv consumers and adapters}"
        ),
    }
    produced = factory._build_files_affected(plan, WORKING_PATH)
    assert [item.path for item in produced] == ["typescript/lilac-srv"]


def test_duplicates_collapse_after_normalisation():
    plan = {
        "id": "8261645",
        "files_affected": "{nexus/moleculer,moleculer,nexus/moleculer}",
    }
    produced = factory._build_files_affected(plan, WORKING_PATH)
    assert [item.path for item in produced] == ["moleculer"]
