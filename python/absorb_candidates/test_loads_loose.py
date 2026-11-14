"""Tests for consumer._loads_loose — tolerant parsing of model output.

The function exists because the local model reliably wraps JSON in prose or
code fences, and because a truncated response must be retried rather than
recorded as a permanent failure. These cases pin both behaviours, including the
fence stripping whose anchored alternation is now explicitly grouped.
"""

from __future__ import annotations

import pytest

from absorb_candidates.consumer import AbsorbError, _loads_loose


def test_plain_json_parses():
    assert _loads_loose('{"candidates": []}') == {"candidates": []}


def test_json_fence_is_stripped():
    fenced = '```json\n{"candidates": [{"title": "x"}]}\n```'
    assert _loads_loose(fenced) == {"candidates": [{"title": "x"}]}


def test_bare_fence_is_stripped():
    fenced = '```\n{"candidates": []}\n```'
    assert _loads_loose(fenced) == {"candidates": []}


def test_prose_wrapped_object_is_recovered():
    prose = 'Here is the result:\n{"candidates": [{"title": "y"}]}\nHope that helps.'
    assert _loads_loose(prose) == {"candidates": [{"title": "y"}]}


def test_truncated_output_is_retryable():
    with pytest.raises(AbsorbError) as exc:
        _loads_loose('{"candidates": [{"title": "cut off')
    assert exc.value.error_code == "E_TRANSIENT_LLM_TRUNCATED"


def test_malformed_but_closed_output_is_permanent():
    with pytest.raises(AbsorbError) as exc:
        _loads_loose('{"candidates": [}{] }')
    assert exc.value.error_code == "E_PERMANENT_LLM_BAD_JSON"
