"""Tests for the Jev benchmark corpus (Jev 3).

Acceptance criteria:
- Reproducible fixture with train/evaluation separation
- No secrets
- Baseline for agreement, calibration, latency, and cache reuse
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from solscript.benchmarks.jev_corpus import (
    JevBenchmarkCase,
    JevPrimitive,
    CorpusSplit,
    CORPUS,
    CORPUS_DICTS,
    get_corpus,
    get_corpus_by_type,
    get_case_by_id,
    get_corpus_stats,
)


class TestCorpusStructure:
    """Test corpus structure and integrity."""
    
    def test_corpus_not_empty(self):
        assert len(CORPUS) > 0
    
    def test_all_cases_have_unique_ids(self):
        ids = [case.id for case in CORPUS]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"
    
    def test_all_cases_have_required_fields(self):
        for case in CORPUS:
            assert case.id, "Missing id"
            assert case.question, "Missing question"
            assert case.expected_outcome_type in JevPrimitive, f"Invalid primitive: {case.expected_outcome_type}"
            assert case.evidence_scope is not None, "Missing evidence_scope"
            assert case.split in CorpusSplit, f"Invalid split: {case.split}"
    
    def test_no_secrets_in_corpus(self):
        """Ensure no secrets (API keys, passwords, real credentials) in corpus."""
        secret_patterns = ["api_key", "password", "secret", "token", "credential", "private_key"]
        for case in CORPUS:
            text = json.dumps({
                "question": case.question,
                "reference_answer": case.reference_answer,
                "metadata": case.metadata,
                "score_rubric": case.score_rubric,
            }).lower()
            for pattern in secret_patterns:
                assert pattern not in text, f"Potential secret pattern '{pattern}' found in case {case.id}"
    
    def test_corpus_dicts_match_objects(self):
        """Verify CORPUS_DICTS is a faithful serialization."""
        assert len(CORPUS_DICTS) == len(CORPUS)
        for obj, dct in zip(CORPUS, CORPUS_DICTS):
            assert obj.id == dct["id"]
            assert obj.question == dct["question"]
            assert obj.expected_outcome_type.value == dct["expected_outcome_type"]
            assert obj.split.value == dct["split"]


class TestCorpusSplits:
    """Test train/eval separation."""
    
    def test_train_eval_split_exists(self):
        train = get_corpus(CorpusSplit.TRAIN)
        eval_cases = get_corpus(CorpusSplit.EVAL)
        
        assert len(train) > 0, "No train cases"
        assert len(eval_cases) > 0, "No eval cases"
        
        # Verify no overlap
        train_ids = {c.id for c in train}
        eval_ids = {c.id for c in eval_cases}
        assert train_ids.isdisjoint(eval_ids), "Train/eval overlap detected"
    
    def test_split_distribution_reasonable(self):
        stats = get_corpus_stats()
        # At least 30% in each split
        assert stats["train_cases"] >= len(CORPUS) * 0.3
        assert stats["eval_cases"] >= len(CORPUS) * 0.3


class TestCorpusByType:
    """Test filtering by primitive type."""
    
    def test_noul_cases(self):
        noul_cases = get_corpus_by_type(JevPrimitive.NOUL)
        assert len(noul_cases) == 8  # noul_001 through noul_008
        
        for case in noul_cases:
            assert case.expected_outcome_type == JevPrimitive.NOUL
            assert case.choice_labels is None
            assert case.score_rubric is None
    
    def test_choice_cases(self):
        choice_cases = get_corpus_by_type(JevPrimitive.CHOICE)
        assert len(choice_cases) == 6  # choice_001 through choice_006
        
        for case in choice_cases:
            assert case.expected_outcome_type == JevPrimitive.CHOICE
            assert case.choice_labels is not None
            assert len(case.choice_labels) >= 2
            assert case.score_rubric is None
    
    def test_score_cases(self):
        score_cases = get_corpus_by_type(JevPrimitive.SCORE)
        assert len(score_cases) == 5  # score_001 through score_005
        
        for case in score_cases:
            assert case.expected_outcome_type == JevPrimitive.SCORE
            assert case.score_rubric is not None
            assert len(case.score_rubric) > 50  # Non-trivial rubric
            assert case.choice_labels is None
    
    def test_split_filtering_by_type(self):
        noul_train = get_corpus_by_type(JevPrimitive.NOUL, CorpusSplit.TRAIN)
        noul_eval = get_corpus_by_type(JevPrimitive.NOUL, CorpusSplit.EVAL)
        
        assert len(noul_train) + len(noul_eval) == 8
        for case in noul_train:
            assert case.split == CorpusSplit.TRAIN
        for case in noul_eval:
            assert case.split == CorpusSplit.EVAL


class TestCorpusUtilities:
    """Test utility functions."""
    
    def test_get_case_by_id(self):
        case = get_case_by_id("noul_001")
        assert case is not None
        assert case.id == "noul_001"
        assert case.question == "Is the entity's readiness_score greater than 0.85?"
        
        # Non-existent
        assert get_case_by_id("nonexistent") is None
    
    def test_corpus_stats(self):
        stats = get_corpus_stats()
        
        assert stats["total_cases"] == len(CORPUS)
        assert stats["train_cases"] + stats["eval_cases"] == stats["total_cases"]
        
        # Check by_type sums
        total_by_type = sum(v["total"] for v in stats["by_type"].values())
        assert total_by_type == stats["total_cases"]
        
        # Check difficulty distribution
        diff_total = sum(stats["difficulty_distribution"].values())
        assert diff_total == stats["total_cases"]
    
    def test_json_fixture_loadable(self):
        """Test that the JSON fixture file loads correctly."""
        fixture_path = Path(__file__).parent.parent / "solscript" / "benchmarks" / "jev_corpus" / "corpus.json"
        assert fixture_path.exists(), "corpus.json fixture not found"
        
        with open(fixture_path) as f:
            data = json.load(f)
        
        assert len(data) == len(CORPUS)
        for item in data:
            assert "id" in item
            assert "question" in item
            assert "expected_outcome_type" in item
            assert "evidence_scope" in item
            assert "split" in item


class TestReferenceAnswers:
    """Test that reference answers are well-formed."""
    
    def test_noul_reference_answers(self):
        for case in get_corpus_by_type(JevPrimitive.NOUL):
            if case.reference_answer:
                assert "probability" in case.reference_answer
                prob = case.reference_answer["probability"]
                assert 0.0 <= prob <= 1.0, f"Invalid probability in {case.id}: {prob}"
                assert "rationale" in case.reference_answer
    
    def test_choice_reference_answers(self):
        for case in get_corpus_by_type(JevPrimitive.CHOICE):
            if case.reference_answer:
                assert "label_probabilities" in case.reference_answer
                assert "top_label" in case.reference_answer
                assert "flag_for_review" in case.reference_answer
                
                probs = case.reference_answer["label_probabilities"]
                assert abs(sum(probs.values()) - 1.0) < 0.01, f"Probabilities don't sum to 1 in {case.id}"
                assert case.reference_answer["top_label"] in probs
    
    def test_score_reference_answers(self):
        for case in get_corpus_by_type(JevPrimitive.SCORE):
            if case.reference_answer:
                assert "expected_value" in case.reference_answer
                ev = case.reference_answer["expected_value"]
                assert 0.0 <= ev <= 1.0, f"Invalid expected_value in {case.id}: {ev}"
                assert "legend" in case.reference_answer
                assert len(case.reference_answer["legend"]) >= 3
    
    def test_choice_labels_match_reference(self):
        for case in get_corpus_by_type(JevPrimitive.CHOICE):
            if case.reference_answer and case.choice_labels:
                probs = case.reference_answer["label_probabilities"]
                for label in probs:
                    assert label in case.choice_labels, f"Label '{label}' not in choice_labels for {case.id}"
                assert case.reference_answer["top_label"] in case.choice_labels


class TestEvidenceScopes:
    """Test evidence scopes are well-formed."""
    
    def test_evidence_scope_fields(self):
        for case in CORPUS:
            scope = case.evidence_scope
            assert isinstance(scope.required_state_fields, list)
            assert isinstance(scope.frame_dimensions, list)
            assert 0.0 <= scope.evidence_completeness_threshold <= 1.0
    
    def test_evidence_completeness_thresholds_reasonable(self):
        for case in CORPUS:
            threshold = case.evidence_scope.evidence_completeness_threshold
            assert 0.5 <= threshold <= 1.0, f"Threshold out of range for {case.id}: {threshold}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])