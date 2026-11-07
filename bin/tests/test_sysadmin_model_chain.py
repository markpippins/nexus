"""Hermetic tests for sysadmin_model_chain.py — pure functions only, no I/O."""

import importlib.util
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "sysadmin_model_chain.py"
spec = importlib.util.spec_from_file_location("sysadmin_model_chain", BIN)
chain_mod = importlib.util.module_from_spec(spec)
sys.modules["sysadmin_model_chain"] = chain_mod
spec.loader.exec_module(chain_mod)


# ── fixtures/helpers ─────────────────────────────────────────────────────

NVIDIA_ID = "prov-1783906359513"
OPENCODE_ID = "prov-opencode"
OPENCODE_GO_ID = "prov-opencode-go"
OPENROUTER_ID = "prov-1782144397043"


def entry(identifier, provider_id=NVIDIA_ID, provider_name="Nvidia", provider_type="openai"):
    return {
        "model_identifier": identifier,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "provider_type": provider_type,
    }


def payload(primary, fallbacks):
    return {
        "role": "sysadmin",
        "model_identifier": primary["model_identifier"],
        "provider_id": primary["provider_id"],
        "provider_name": primary["provider_name"],
        "provider_type": primary["provider_type"],
        "fallback_models": fallbacks,
    }


# ── wire-id mapping ──────────────────────────────────────────────────────

def test_nvidia_identifier_gets_the_nvidia_namespace():
    assert (
        chain_mod.wire_id_for(entry("nvidia/nemotron-3.5-super-vl-120b-a12b"))
        == "nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b"
    )


def test_answer_is_namespaced_by_provider_not_by_first_segment():
    # z-ai/glm-5.2 is served by Nvidia; the old bash mapping produced z-ai/z-ai/...
    assert chain_mod.wire_id_for(entry("z-ai/glm-5.2")) == "nvidia/z-ai/glm-5.2"


def test_opencode_and_opencode_go_map_to_their_own_providers():
    assert (
        chain_mod.wire_id_for(entry("big-pickle", OPENCODE_ID, "OpenCode", "opencode"))
        == "opencode/big-pickle"
    )
    assert (
        chain_mod.wire_id_for(entry("glm-5.3-flash", OPENCODE_GO_ID, "OpenCode Go", "opencode"))
        == "opencode-go/glm-5.3-flash"
    )


def test_provider_name_is_used_when_the_id_is_unknown():
    assert (
        chain_mod.wire_id_for(entry("glm-5.3-flash", "prov-new", "OpenCode Go"))
        == "opencode-go/glm-5.3-flash"
    )


def test_unmapped_provider_with_namespaced_identifier_uses_first_segment():
    assert chain_mod.wire_id_for(entry("z-ai/glm-5.2", "prov-new", "Acme")) == "z-ai/z-ai/glm-5.2"


def test_bare_identifier_with_unmapped_provider_passes_through():
    assert chain_mod.wire_id_for(entry("mystery-model", "prov-new", "Acme")) == "mystery-model"


def test_missing_identifier_yields_empty_wire_id():
    assert chain_mod.wire_id_for({}) == ""


# ── provider preference ─────────────────────────────────────────────────

def test_provider_ranks_follow_nvidia_then_opencode_then_go():
    assert chain_mod.provider_rank_of_model("nvidia/nvidia/x") == 0
    assert chain_mod.provider_rank_of_model("opencode/big-pickle") == 1
    assert chain_mod.provider_rank_of_model("opencode-go/glm-5.3-flash") == 2
    assert chain_mod.provider_rank_of_model("openrouter/openrouter/free") == 3
    assert chain_mod.provider_rank_of_model("ollama/qwen2.5-coder") == 4
    assert chain_mod.provider_rank_of_model("unknown/x") == chain_mod.UNKNOWN_PROVIDER_RANK
    assert chain_mod.provider_rank_of_model(None) == chain_mod.UNKNOWN_PROVIDER_RANK


# ── catalog parsing ─────────────────────────────────────────────────────

def test_unset_catalog_uses_the_default_and_empty_disables_it():
    assert chain_mod.parse_catalog(None) == list(chain_mod.DEFAULT_CATALOG)
    assert chain_mod.parse_catalog("") == []


def test_catalog_entries_are_trimmed_and_blanks_dropped():
    assert chain_mod.parse_catalog(" a , b ,, ") == ["a", "b"]


def test_rung_cap_parsing():
    assert chain_mod.parse_max_rungs(None) == chain_mod.DEFAULT_MAX_RUNGS
    assert chain_mod.parse_max_rungs("0") == chain_mod.DEFAULT_MAX_RUNGS
    assert chain_mod.parse_max_rungs("nope") == chain_mod.DEFAULT_MAX_RUNGS
    assert chain_mod.parse_max_rungs("3") == 3


# ── chain assembly ──────────────────────────────────────────────────────

def test_primary_is_pinned_and_its_duplicate_fallback_collapses():
    # The live defect: tackle returned the primary as its own only fallback.
    chain = chain_mod.build_chain(
        "nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b",
        ["nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b"],
        [],
    )
    assert chain == ["nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b"]


def test_a_real_ladder_is_ordered_by_provider_preference():
    chain = chain_mod.build_chain(
        "nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b",
        [
            "opencode-go/glm-5.3-flash",
            "opencode/big-pickle",
            "nvidia/z-ai/glm-5.2",
        ],
        [],
    )
    assert chain == [
        "nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b",
        "nvidia/z-ai/glm-5.2",
        "opencode/big-pickle",
        "opencode-go/glm-5.3-flash",
    ]


def test_configured_order_survives_inside_one_provider():
    chain = chain_mod.build_chain("primary/x", ["opencode/a", "opencode/b", "opencode/c"], [])
    assert chain == ["primary/x", "opencode/a", "opencode/b", "opencode/c"]


def test_catalog_rungs_are_interleaved_by_rank_and_deduped():
    chain = chain_mod.build_chain(
        "opencode/big-pickle",
        ["opencode-go/glm-5.3-flash"],
        ["nvidia/nvidia/nemotron-3-ultra-550b-a55b", "opencode-go/glm-5.3-flash"],
    )
    assert chain == [
        "opencode/big-pickle",
        "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
        "opencode-go/glm-5.3-flash",
    ]


def test_cap_drops_catalog_rungs_before_configured_ones():
    configured = [
        "opencode/a",
        "opencode/b",
        "opencode/c",
    ]
    chain = chain_mod.build_chain("nvidia/nvidia/x", configured, list(chain_mod.DEFAULT_CATALOG), 4)
    assert chain[0] == "nvidia/nvidia/x"
    assert chain[1:] == ["opencode/a", "opencode/b", "opencode/c"]
    assert len(chain) == 4


def test_cap_never_exceeds_itself():
    for cap in (1, 2, 3, 6):
        chain = chain_mod.build_chain(
            "nvidia/nvidia/x", ["opencode/a"], list(chain_mod.DEFAULT_CATALOG), cap
        )
        assert len(chain) <= cap


def test_empty_configured_ladder_falls_back_to_the_catalog():
    # Divergence from ladder.ts, documented: the shell harness has no admission
    # layer, so "tackle unreachable" must still yield something runnable.
    chain = chain_mod.build_chain("", [], list(chain_mod.DEFAULT_CATALOG))
    assert chain == list(chain_mod.DEFAULT_CATALOG)


def test_primary_only_when_the_catalog_is_disabled():
    assert chain_mod.build_chain("opencode/big-pickle", [], [], 6) == ["opencode/big-pickle"]
    assert chain_mod.build_chain("", [], [], 6) == []


# ── payload handling and overrides ──────────────────────────────────────

def test_payload_is_mapped_and_deduplicated_end_to_end():
    data = payload(
        entry("nvidia/nemotron-3-super-120b-a12b"),
        [
            entry("nvidia/nemotron-3-super-120b-a12b"),  # duplicate primary
            entry("big-pickle", OPENCODE_ID, "OpenCode", "opencode"),
        ],
    )
    chain = chain_mod.chain_from_payload(data, catalog=[], max_rungs=6)
    assert chain == [
        "nvidia/nvidia/nemotron-3-super-120b-a12b",
        "opencode/big-pickle",
    ]


def test_sysmodel_override_replaces_the_primary_and_its_duplicate():
    data = payload(entry("nvidia/nemotron-3-super-120b-a12b"), [entry("nvidia/nemotron-3-super-120b-a12b")])
    chain = chain_mod.chain_from_payload(
        data, sysmodel="nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b", catalog=[]
    )
    assert chain == ["nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b"]


def test_fallback_model_override_is_appended_before_the_catalog():
    data = payload(entry("nemotron-x"), [])
    chain = chain_mod.chain_from_payload(
        data,
        fallback_model="opencode-go/glm-5.3-flash",
        catalog=["opencode/big-pickle"],
    )
    assert chain == [
        "nvidia/nemotron-x",
        "opencode/big-pickle",
        "opencode-go/glm-5.3-flash",
    ]


def test_empty_payload_yields_the_catalog():
    chain = chain_mod.chain_from_payload({}, catalog=chain_mod.DEFAULT_CATALOG)
    assert chain == list(chain_mod.DEFAULT_CATALOG)


def test_catalog_entries_are_provider_ranked_against_configured_rungs():
    # A configured OpenCode fallback must not be tried before a catalog Nvidia
    # rung: Nvidia is the preferred provider.
    data = payload(entry("nemotron-x"), [entry("big-pickle", OPENCODE_ID, "OpenCode", "opencode")])
    chain = chain_mod.chain_from_payload(data, catalog=["nvidia/nvidia/second-nvidia"])
    assert chain == [
        "nvidia/nemotron-x",
        "nvidia/nvidia/second-nvidia",
        "opencode/big-pickle",
    ]
