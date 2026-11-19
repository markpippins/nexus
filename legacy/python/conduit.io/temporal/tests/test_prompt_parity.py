import json
import sys
from pathlib import Path

# Ensure the repo root is on sys.path for imports
repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from conduit.prompt_renderer import build_opencode_prompt
from conduit.prompt_renderer import build_opencode_prompt as legacy_build, build_opencode_prompt

def load_sample_dco() -> dict:
    # Minimal realistic DCO for testing purposes
    return {
        "intent": {
            "problem_statement": "Fix bug X",
            "desired_outcome": "Feature works correctly",
            "priority": "high",
            "abstraction_level": "task",
        },
        "decomposition": {"steps": [{"type": "execution", "description": "Do something"}]},
        "requirements": {"functional": ["Must do Y"]},
        "constraints": {"safety_constraints": ["No unsafe ops"]},
        "success_criteria": {"completion_conditions": [{"condition": "All tests pass"}]},
        "artifacts": {"produced_files": [{"path": "src/main.py"}]},
        "lineage": {"derived_from": []},
        "metadata": {"role": "builder"},
    }

def test_prompt_parity():
    dco = load_sample_dco()
    working_path = "/tmp/workdir"
    legacy_prompt = legacy_build(dco, working_path)
    new_prompt = build_opencode_prompt(dco, working_path)
    assert legacy_prompt == new_prompt, "Legacy and new prompt rendering differ"
