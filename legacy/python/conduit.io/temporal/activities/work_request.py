"""Temporal Activity for building WorkRequest DCOs from plans."""

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from temporalio import activity

_PARENT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PARENT))

from work_request_factory import WorkRequestFactory
from executor_registry import ModelConfig
from db_adapter import DBAdapter


@activity.defn
async def build_work_request_dco_activity(
    plan: Dict[str, Any],
    role: str = "builder",
    model_cfg: Optional[Dict[str, Any]] = None,
    working_path: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    """Build a WorkRequest DCO from a plan.

    Returns a dict with 'dco' (the WorkRequestDCO as dict), 'wr_id',
    and 'executor_cmd' (the executor invocation command).
    """
    if not working_path:
        working_path = os.environ.get(
            "PIPELINE_ROOT",
            "/home/codex/dev",
        )

    mcfg = None
    if model_cfg:
        mcfg = ModelConfig(harness=model_cfg["harness"], model=model_cfg["model"])

    dco_obj = WorkRequestFactory.create_from_plan(
        plan,
        role=role,
        model_cfg=mcfg,
        working_path=working_path,
        session_id=session_id,
    )

    # Resolve executor from DB
    db = DBAdapter()
    cfg = db.get_role_model_config(role)
    if cfg:
        executor_cmd = cfg.get("harness", "opencode")
        executor_id = f"executor-{cfg.get('harness', 'unknown')}"
    else:
        executor_cmd = "opencode"
        executor_id = "executor-opencode"

    dco_dict = dco_obj.model_dump(by_alias=True)

    return {
        "dco": dco_dict,
        "wr_id": dco_obj.id,
        "executor_cmd": executor_cmd,
        "executor_id": executor_id,
    }


@activity.defn
async def resolve_model_chain_activity(role: str) -> list:
    """Resolve the model chain (primary + fallbacks) for a role.

    Returns a list of dicts with keys: harness, model, retry_delay, max_retries.
    The first entry is the primary model, followed by fallback models.
    """
    from db_adapter import DBAdapter

    db = DBAdapter()

    # Get primary model
    cfg = db.get_role_model_config(role)
    chain = []
    seen_models: set = set()

    if cfg:
        chain.append({
            "harness": cfg["harness"],
            "model": cfg["model"],
            "provider_type": cfg.get("provider_type", ""),
            "endpoint_url": cfg.get("endpoint_url", ""),
            "retry_delay": 120,
            "max_retries": 3,
        })
        seen_models.add(cfg["model"])

    # Get fallback models — skip any whose model_identifier was already added
    # (role_models includes priority 0 which duplicates the primary from role_config).
    fallbacks = db.get_fallback_models(role)
    for fb in fallbacks:
        model_id = fb["model_identifier"]
        if model_id in seen_models:
            continue
        sem = fb.get("invocation_semantics", {})
        harness_binary = sem.get("binary", "") if isinstance(sem, dict) else ""
        if not harness_binary:
            activity.logger.warning(
                f"resolve_model_chain: skipping fallback for role={role} "
                f"model={model_id} — harness '{fb.get('harness_name', '?')}' "
                f"(id={fb.get('harness_id', '?')}) has no 'binary' in invocation_semantics"
            )
            continue
        if model_id:
            chain.append({
                "harness": harness_binary,
                "model": model_id,
                "provider_type": fb.get("provider_type", ""),
                "endpoint_url": fb.get("endpoint_url", ""),
                "retry_delay": 120,
                "max_retries": 3,
            })
            seen_models.add(model_id)

    activity.logger.info(f"resolve_model_chain: role={role} chain_length={len(chain)}")
    return chain


# ── Health check cache ────────────────────────────────────────────

_health_cache: Dict[str, tuple[bool, float]] = {}  # endpoint_url -> (healthy, cached_at)
_HEALTH_TTL = 60  # seconds


@activity.defn
async def check_provider_health_activity(chain: list) -> list:
    """Filter a model chain to only entries whose provider endpoint is healthy.

    Pings each unique provider endpoint with a lightweight HTTP GET.
    Results are cached for ``_HEALTH_TTL`` seconds (default 60).
    Providers with empty endpoint_url are assumed healthy (cannot check).
    """
    if not chain:
        return chain

    # Map unique endpoints -> health result
    unique_endpoints: dict = {}
    for entry in chain:
        ep = entry.get("endpoint_url", "")
        ptype = entry.get("provider_type", "")
        if not ep:
            unique_endpoints[f"__noop__{ptype}"] = {"endpoint": "", "provider_type": ptype}
        else:
            key = f"{ep}|{ptype}"
            if key not in unique_endpoints:
                unique_endpoints[key] = {"endpoint": ep, "provider_type": ptype}

    # Check each unique endpoint (with caching)
    healthy_endpoints: set = set()
    endpoint_to_url: dict = {}
    for key, info in unique_endpoints.items():
        ep = info["endpoint"]
        ptype = info["provider_type"]
        endpoint_to_url[key] = ep

        if not ep:
            healthy_endpoints.add(key)
            continue

        now = time.time()
        cached = _health_cache.get(ep)
        if cached is not None and (now - cached[1]) < _HEALTH_TTL:
            if cached[0]:
                healthy_endpoints.add(key)
            continue

        ok = await _ping_provider(ep, ptype)
        _health_cache[ep] = (ok, now)
        if ok:
            healthy_endpoints.add(key)

    # Filter chain — only entries whose endpoint is healthy
    filtered = []
    for entry in chain:
        ep = entry.get("endpoint_url", "")
        ptype = entry.get("provider_type", "")
        key = f"{ep}|{ptype}"
        if not ep or key in healthy_endpoints:
            filtered.append(entry)

    activity.logger.info(
        f"check_provider_health: {len(chain)} entries -> {len(filtered)} healthy. "
        f"Skipped: {[e['model'] for e in chain if e not in filtered]}"
    )
    return filtered


async def _ping_provider(endpoint_url: str, provider_type: str) -> bool:
    """Ping a provider endpoint with the appropriate health check."""
    import aiohttp

    health_paths = {
        "ollama": "/api/tags",
        "opencode": "/health",
    }
    path = health_paths.get(provider_type, "/health")
    url = endpoint_url.rstrip("/") + path

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status == 200
    except (asyncio.TimeoutError, Exception):
        return False
