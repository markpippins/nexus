#!/usr/bin/env python3
"""sysadmin_model_chain.py — build the sysadmin harness's model ladder.

Input:  the JSON body of ``GET /config/ai/resolve/<role>`` on stdin (or
        ``--resolve-file``). Empty/unparseable input is treated as "tackle did
        not answer", which yields the catalog-only tail.
Output: opencode ``--model`` ids, one per line, primary first.

Why this exists
---------------
``bin/sysadmin-harness.sh`` used to assemble its chain inline: tackle's
primary, tackle's ``fallback_models``, ``$FALLBACK_MODEL``, then a single
hardcoded ``opencode/big-pickle``. On 2026-11-06 that produced a two-rung
ladder for the sysadmin role -- ``nvidia/nvidia/nemotron-3-super-120b-a12b``
followed by the pickle -- where the first rung was a model the NVIDIA account
cannot serve (upstream HTTP 404) and the second hung until the 15-minute
per-attempt cap. Five hourly cycles passed with no heartbeat.

``typescript/harness-srv/src/ladder.ts`` fixed the same shape for harness-srv:
primary pinned, remaining rungs ordered by provider preference, duplicates
collapsed, catalog appended under a rung cap. This module mirrors those rules
for the shell harness, which cannot import the TypeScript.

Provider preference: nvidia -> opencode -> opencode-go -> openrouter -> ollama
-> everything else. The configured primary is never reordered: whoever
configured the role chose it.

The wire id is ALWAYS ``<opencode-provider>/<model_identifier>`` (see
``typescript/harness-srv/src/model.ts``). The opencode provider is resolved
from the tackle provider id (or provider name), never from the identifier's
first segment: ``z-ai/glm-5.2`` is an Nvidia-hosted model and runs as
``nvidia/z-ai/glm-5.2``, not ``z-ai/z-ai/glm-5.2``.

Divergence from ladder.ts: an empty configured ladder returns the catalog
rather than nothing. harness-srv rejects NO_CONFIG upstream; the shell harness
has no admission layer, and "tackle is unreachable" is exactly when the
sysadmin loop still needs to run.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Catalog appended after the role's configured ladder. Every entry was measured
# through this host's opencode CLI on 2026-11-06 (trivial "reply OK" run, exit
# 0). A catalog rung that cannot resolve burns the budget, which is the failure
# this module exists to remove -- so only measured-good ids belong here.
DEFAULT_CATALOG: tuple[str, ...] = (
    "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
    "opencode/big-pickle",
    "opencode/glm-5.3-flash-free",
    "opencode-go/glm-5.3-flash",
)

# Rungs a ladder may reach, primary included. The run budget is shared across
# the whole ladder (CHAIN_BUDGET_MINUTES in the harness), so the cap keeps the
# last rung a real slice of it.
DEFAULT_MAX_RUNGS = 6

# tackle provider id -> opencode provider key. Mirrors OPENCODE_PROVIDER_BY_TACKLE
# in typescript/harness-srv/src/model.ts; the canonical source is
# tackle.providers.config_json.opencodeProvider, which the resolve payload does
# not carry, so the harness keeps the same map.
OPENCODE_PROVIDER_BY_TACKLE: Dict[str, str] = {
    "prov-1783906359513": "nvidia",
    "prov-1782144397043": "openrouter",
    "prov-opencode-go": "opencode-go",
    "prov-opencode": "opencode",
    "prov-ollama": "ollama",
    "prov-deepseek": "deepseek",
}

# Fallback when only the display name is available (resolve payload always has
# provider_name). Lower-case, trimmed.
OPENCODE_PROVIDER_BY_NAME: Dict[str, str] = {
    "nvidia": "nvidia",
    "opencode": "opencode",
    "opencode go": "opencode-go",
    "openrouter": "openrouter",
    "ollama": "ollama",
    "deepseek": "deepseek",
}

# Last resort only. The tackle provider `type` is the API dialect, not the
# provider identity — Nvidia and OpenRouter are both type "openai" — so using it
# for those would fabricate `openai/nvidia/...` and burn a rung. Only types whose
# models.dev provider key is the type itself are safe.
OPENCODE_PROVIDER_TYPES = ("ollama", "opencode", "codex")

# opencode provider prefix -> preference rank (lower is tried earlier).
PROVIDER_MODEL_RANK: Dict[str, int] = {
    "nvidia": 0,
    "opencode": 1,
    "opencode-go": 2,
    "openrouter": 3,
    "ollama": 4,
    "deepseek": 5,
    "deepseek-ai": 5,
}

UNKNOWN_PROVIDER_RANK = 6


def provider_rank_of_model(model: Optional[str]) -> int:
    """Preference rank of an opencode wire model id (read from its prefix)."""
    if not model:
        return UNKNOWN_PROVIDER_RANK
    text = model.strip()
    slash = text.find("/")
    provider = (text[:slash] if slash > 0 else text).strip().lower()
    return PROVIDER_MODEL_RANK.get(provider, UNKNOWN_PROVIDER_RANK)


def harness_provider_for(entry: Dict[str, Any]) -> Optional[str]:
    """opencode provider key for a tackle resolve entry, or None.

    Provider id first (canonical), then display name, then the narrow type
    fallback. `None` means "unmapped": the caller falls back to the identifier's
    first segment, matching model.ts.
    """
    provider_id = (entry.get("provider_id") or "").strip()
    if provider_id in OPENCODE_PROVIDER_BY_TACKLE:
        return OPENCODE_PROVIDER_BY_TACKLE[provider_id]

    name = (entry.get("provider_name") or "").strip().lower()
    if name in OPENCODE_PROVIDER_BY_NAME:
        return OPENCODE_PROVIDER_BY_NAME[name]

    provider_type = (entry.get("provider_type") or "").strip().lower()
    if provider_type in OPENCODE_PROVIDER_TYPES:
        return provider_type

    return None


def wire_id_for(entry: Dict[str, Any]) -> str:
    """``<opencode-provider>/<model_identifier>`` for one resolve entry."""
    identifier = (entry.get("model_identifier") or "").strip()
    if not identifier:
        return ""

    provider = harness_provider_for(entry)
    if provider:
        return f"{provider}/{identifier}"

    # Unmapped provider + already-namespaced identifier -> legacy first-segment
    # heuristic, exactly as model.ts does.
    slash = identifier.find("/")
    if slash > 0:
        return f"{identifier[:slash]}/{identifier}"
    return identifier


def parse_catalog(value: Optional[str]) -> List[str]:
    """Catalog from a CLI/env value.

    None -> DEFAULT_CATALOG; "" -> empty (explicit opt-out); "a, b" -> [a, b].
    Blank entries are dropped rather than becoming a rung with no model name.
    """
    if value is None:
        return list(DEFAULT_CATALOG)
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_max_rungs(value: Optional[Any]) -> int:
    """Rung cap; a non-positive or unparseable value uses the default."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_MAX_RUNGS
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_RUNGS
    if parsed < 1:
        return DEFAULT_MAX_RUNGS
    return parsed


def build_chain(
    primary_wire: str,
    fallback_wires: Sequence[str],
    catalog: Sequence[str],
    max_rungs: int = DEFAULT_MAX_RUNGS,
) -> List[str]:
    """Merge configured rungs and the catalog into one ordered ladder.

    Rules (same as ladder.ts):
      - the primary stays first;
      - every other rung is ordered by provider rank, then by original
        position, so configured order survives inside one provider;
      - a model appears once;
      - at most ``max_rungs`` rungs, counting the primary. When the cap binds,
        CATALOG rungs are dropped first: a configured rung is an explicit
        choice by whoever configured the role.
    """
    primary = (primary_wire or "").strip()
    seen = set()
    if primary:
        seen.add(primary.lower())

    configured: List[tuple[str, int, int, bool]] = []
    for index, model in enumerate(fallback_wires):
        wire = (model or "").strip()
        key = wire.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        configured.append((wire, provider_rank_of_model(wire), index, False))

    catalog_rungs: List[tuple[str, int, int, bool]] = []
    offset = len(fallback_wires)
    for index, model in enumerate(catalog):
        wire = (model or "").strip()
        key = wire.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        catalog_rungs.append((wire, provider_rank_of_model(wire), offset + index, True))

    candidates = configured + catalog_rungs
    candidates.sort(key=lambda candidate: (candidate[1], candidate[2]))

    cap = parse_max_rungs(max_rungs)
    room = max(0, cap - 1)
    if len(candidates) > room:
        kept = [c for c in candidates if not c[3]][:room]
        kept += [c for c in candidates if c[3]][: max(0, room - len(kept))]
        keep_indexes = {c[2] for c in kept}
        candidates = [c for c in candidates if c[2] in keep_indexes]

    chain = [primary] if primary else []
    chain.extend(c[0] for c in candidates)
    return chain


def chain_from_payload(
    payload: Optional[Dict[str, Any]],
    sysmodel: str = "",
    fallback_model: str = "",
    catalog: Optional[Sequence[str]] = None,
    max_rungs: int = DEFAULT_MAX_RUNGS,
) -> List[str]:
    """Ladder from a tackle resolve payload, with the harness overrides."""
    data = payload or {}

    if sysmodel.strip():
        # SYSMODEL replaces the tackle primary outright; the resolve payload is
        # not consulted (the harness does not even fetch it in that case).
        primary = sysmodel.strip()
        configured: List[str] = []
    else:
        primary = wire_id_for(data) if data.get("model_identifier") else ""
        configured = [
            wire_id_for(entry)
            for entry in (data.get("fallback_models") or [])
            if isinstance(entry, dict) and entry.get("model_identifier")
        ]

    if fallback_model.strip():
        configured.append(fallback_model.strip())

    return build_chain(
        primary,
        configured,
        list(catalog) if catalog is not None else list(DEFAULT_CATALOG),
        max_rungs,
    )


def _read_payload(path: Optional[str]) -> Dict[str, Any]:
    try:
        if path:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        elif sys.stdin.isatty():
            return {}
        else:
            text = sys.stdin.read()
    except OSError:
        return {}

    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the sysadmin harness model ladder from a tackle resolve payload.",
    )
    parser.add_argument("--role", default="sysadmin", help="role name, for diagnostics only")
    parser.add_argument("--resolve-file", default=None, help="read the resolve payload from this file")
    parser.add_argument(
        "--sysmodel",
        default="",
        help="opencode --model id used as the primary, verbatim (overrides tackle)",
    )
    parser.add_argument(
        "--fallback-model",
        default="",
        help="extra opencode --model id inserted after the configured fallbacks",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help='comma-separated catalog tail (default: built-in; "" disables it)',
    )
    parser.add_argument("--max-rungs", default=None, help="rung cap, primary included (default: 6)")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="write the assembled rungs and their source to stderr",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    payload = _read_payload(args.resolve_file)
    chain = chain_from_payload(
        payload,
        sysmodel=args.sysmodel,
        fallback_model=args.fallback_model,
        catalog=parse_catalog(args.catalog),
        max_rungs=parse_max_rungs(args.max_rungs),
    )

    if args.explain:
        source = "tackle" if payload.get("model_identifier") else "catalog-only"
        print(
            f"role={args.role} source={source} rungs={len(chain)}: " + " > ".join(chain),
            file=sys.stderr,
        )

    for model in chain:
        print(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
