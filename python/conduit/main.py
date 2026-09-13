import argparse
import atexit
import fcntl
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from db_adapter import DBAdapter, provider_prefix_slug, qualify_opencode_model_id, fallback_provider_prefix_slug
from env_config import load_env  # shared .env loader; load_env() fires at import time
from executor_registry import ModelConfig, RegistryConfig, load_registry, resolve_executor
from token_estimator import load_pricing, estimate_tokens, estimate_cost
from work_request_factory import WorkRequestFactory


# ── Structured logging ────────────────────────────────────────────

_log: logging.Logger | None = None


def _default_conduit_data_dir() -> str:
    """Default conduit runtime data dir.

    ``.conduit-data/`` was removed 2026-08-09 and mirrored to
    ``nexus/audit/CONDUIT_DATA`` for posterity; the emitter must not
    recreate the deleted directory. ``CONDUIT_DATA_DIR`` overrides.
    """
    env = os.environ.get("CONDUIT_DATA_DIR")
    if env:
        return env
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "audit", "CONDUIT_DATA",
    )


_LOG_PATH = os.environ.get("CONDUIT_LOG_PATH", os.path.join(
    _default_conduit_data_dir(),
    "conduit.log"
))


def _setup_logging() -> logging.Logger:
    global _log
    if _log is not None:
        return _log
    level_name = os.environ.get("CONDUIT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    _log = logging.getLogger("conduit")
    _log.setLevel(level)
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=10,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _log.addHandler(handler)
    _log.info(f"Conduit PID={os.getpid()} started (log_level={level_name})")
    return _log


def _get_log() -> logging.Logger:
    if _log is None:
        _setup_logging()
    return _log  # type: ignore[return-value]


# ── Path constants ──────────────────────────────────────────────────

DEFAULT_DB_PATH = _default_conduit_data_dir()
LOCK_PATH = os.environ.get("PIPELINE_LOCK_PATH", "/tmp/pipeline-manager.lock")
DCO_DIR = os.environ.get("PIPELINE_DCO_DIR", os.path.join(_default_conduit_data_dir(), "WORK_REQUESTS"))
PROJECT_ROOT = os.environ.get("PIPELINE_ROOT", "/home/codex/dev")

# ── WR emission mode (legacy emitter gate) ──────────────────────────
# Architect finding 89d7fbe3 (2026-08-09): the legacy DCO-file emission
# + file-based executor dispatch (``executor_cloud.py <dco_path>``) is
# gated behind an explicit opt-in. Default is "runtime" — WRs are
# recorded in the runtime store (vision.work_requests / execution.requests
# + cascade admission) and NO DCO file is written and NO executor is
# launched from main.py. Set CONDUIT_WR_EMIT_MODE=file (or pass
# ``--emit-mode file``) to restore the legacy behavior; PIPELINE_DCO_DIR
# then controls the target dir and must point somewhere writable (NOT a
# removed / read-only directory).
_DEFAULT_WR_EMIT_MODE = "runtime"
WR_EMIT_MODES = ("file", "runtime")


def _resolve_default_emit_mode() -> str:
    mode = os.environ.get("CONDUIT_WR_EMIT_MODE", _DEFAULT_WR_EMIT_MODE).strip().lower()
    if mode not in WR_EMIT_MODES:
        raise ValueError(f"Invalid CONDUIT_WR_EMIT_MODE {mode!r}; expected one of {WR_EMIT_MODES}")
    return mode


WR_EMIT_MODE = _resolve_default_emit_mode()

EXECUTOR_TIMEOUT_SECONDS = int(os.environ.get("PIPELINE_EXECUTOR_TIMEOUT", "1800"))
WATCHDOG_STALE_SECONDS = int(os.environ.get("PIPELINE_WATCHDOG_STALE", "1500"))
LOCK_STALE_SECONDS = int(os.environ.get("PIPELINE_LOCK_STALE", "3600"))

# ── Rate-limit retry (v090) ───────────────────────────────────────
API_LIMIT_RETRY_DELAY = int(os.environ.get("API_LIMIT_RETRY_DELAY", "300"))
API_LIMIT_MAX_RETRIES = int(os.environ.get("API_LIMIT_MAX_RETRIES", "5"))
_retry_delay_seconds = API_LIMIT_RETRY_DELAY
_retry_max_attempts = API_LIMIT_MAX_RETRIES

_lock_fd = None


def _kill_process_tree(pid: int, sig: int = signal.SIGKILL) -> None:
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
    except (ProcessLookupError, OSError):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            pass


def _is_descendant_of(pid: int, ancestor_pid: int) -> bool:
    """Check if *pid* is a descendant of *ancestor_pid* by walking /proc PPID."""
    if pid <= 0 or ancestor_pid <= 0:
        return False
    visited: set[int] = set()
    current = pid
    while current > 0 and current not in visited:
        visited.add(current)
        try:
            with open(f"/proc/{current}/status") as f:
                ppid = None
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
                if ppid is None:
                    return False
                if ppid == ancestor_pid:
                    return True
                current = ppid
        except (FileNotFoundError, OSError, ValueError):
            return False
    return False


def _cleanup_orphaned_processes() -> None:
    orphans_killed = 0
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,etime,cmd", "--no-headers"],
            capture_output=True, text=True, timeout=5
        )
        my_pid = os.getpid()
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid_str, elapsed, cmd = parts
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            if pid == my_pid:
                continue
            if "executor_cloud.py" in cmd:
                total_seconds = _parse_elapsed(elapsed)
                if total_seconds is not None and total_seconds > WATCHDOG_STALE_SECONDS:
                    print(f"Orphan cleanup: Killing stale executor PID {pid} (elapsed {elapsed}): {cmd[:80]}...")
                    _kill_process_tree(pid)
                    orphans_killed += 1
            elif "opencode" in cmd:
                total_seconds = _parse_elapsed(elapsed)
                if total_seconds is not None and total_seconds > WATCHDOG_STALE_SECONDS:
                    if _is_descendant_of(pid, my_pid):
                        print(f"Orphan cleanup: Killing stale conduit-spawned opencode PID {pid} (elapsed {elapsed}): {cmd[:80]}...")
                        _kill_process_tree(pid)
                        orphans_killed += 1
    except Exception as e:
        print(f"Orphan cleanup: scan failed ({e}), continuing.")
    if orphans_killed:
        print(f"Orphan cleanup: killed {orphans_killed} stale process(es).")


def _parse_elapsed(elapsed: str) -> Optional[int]:
    try:
        parts = elapsed.split("-")
        days = 0
        time_part = elapsed
        if len(parts) == 2:
            days = int(parts[0])
            time_part = parts[1]
        time_parts = [int(x) for x in time_part.split(":")]
        if len(time_parts) == 3:
            h, m, s = time_parts
        elif len(time_parts) == 2:
            h, m, s = 0, time_parts[0], time_parts[1]
        elif len(time_parts) == 1:
            h, m, s = 0, 0, time_parts[0]
        else:
            return None
        return days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return None


def _is_lock_stale() -> bool:
    try:
        with open(LOCK_PATH, "r") as f:
            pid_str = f.read().strip()
        if not pid_str.isdigit():
            return True
        pid = int(pid_str)
        os.kill(pid, 0)
        mtime = os.path.getmtime(LOCK_PATH)
        age = datetime.now().timestamp() - mtime
        if age > LOCK_STALE_SECONDS:
            print(f"Lock file is {int(age)}s old (holder PID {pid} alive). Force-breaking stale lock.")
            _kill_process_tree(pid)
            return True
        return False
    except (FileNotFoundError, ProcessLookupError, OSError):
        return True


def acquire_lock() -> bool:
    global _lock_fd
    try:
        _lock_fd = open(LOCK_PATH, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(f"{os.getpid()}\n")
        _lock_fd.flush()
        atexit.register(release_lock)
        return True
    except (IOError, BlockingIOError):
        if _lock_fd:
            _lock_fd.close()
            _lock_fd = None
        if _is_lock_stale():
            print("Stale lock detected. Force-acquiring.")
            try:
                os.remove(LOCK_PATH)
            except OSError:
                pass
            try:
                _lock_fd = open(LOCK_PATH, "w")
                fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _lock_fd.write(f"{os.getpid()}\n")
                _lock_fd.flush()
                atexit.register(release_lock)
                return True
            except (IOError, BlockingIOError):
                if _lock_fd:
                    _lock_fd.close()
                    _lock_fd = None
                return False
        return False


def release_lock():
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass
        _lock_fd = None


def get_model(db: DBAdapter, registry: RegistryConfig, role: str = "builder") -> ModelConfig:
    """Resolve the harness + model for *role* via tackle-mcp.

    Falls back to ``registry.default_model`` when:
    - The circuit breaker is tripped (returns fallback_model instead)
    - The tackle lookup fails or returns no config
    """
    log = _get_log()
    if db.is_circuit_breaker_tripped():
        log.warning("get_model: circuit breaker tripped role=%s returning fallback", role)
        return registry.fallback_model

    try:
        cfg = db.get_role_model_config(role)
        if cfg and cfg.get("harness") and cfg.get("model"):
            qualified = qualify_opencode_model_id(
                cfg["model"],
                provider_prefix_slug(
                    cfg.get("provider_name", ""),
                    cfg.get("provider_type", ""),
                    cfg.get("provider_id", ""),
                ),
            )
            log.debug("get_model: DB config role=%s harness=%s model=%s", role, cfg["harness"], qualified)
            return ModelConfig(harness=cfg["harness"], model=qualified)
    except Exception:
        pass

    log.debug("get_model: using default role=%s harness=%s model=%s", role,
              registry.default_model.harness, registry.default_model.model)
    return registry.default_model


def _is_role_circuit_breaker_tripped(db: DBAdapter, role: str) -> bool:
    """Per-role circuit breaker check (Temporal-era hardening).

    Falls back to the global breaker if the adapter doesn't expose a
    per-role method yet — keeps the global blocking semantics in that
    case. The adapter may grow is_role_circuit_breaker_tripped(role);
    until then this is a forward-compat shim that does no harm.
    """
    fn = getattr(db, "is_role_circuit_breaker_tripped", None)
    if callable(fn):
        try:
            return bool(fn(role))
        except Exception:
            pass
    return db.is_circuit_breaker_tripped()


# ── API usage limit detection patterns (v075) ──────────────────────
_API_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
    "usage exceeded",
    "api usage",
    "insufficient_quota",
    "quota exceeded",
    "billing",
    "credit",
    "429",
    "402",
    "exceeded your current quota",
    "your account must be",
    "payment required",
    "freeusagelimiterror",
]


def _detect_api_limit_error(exit_code: int, output: str) -> bool:
    output_lower = output.lower()
    if exit_code == 0:
        return False
    for pattern in _API_LIMIT_PATTERNS:
        if pattern in output_lower:
            return True
    return False


def _extract_tokens_from_output(output: str) -> int:
    patterns = [
        r'"total"\s*:\s*(\d+)',
        r'"total_tokens"\s*:\s*(\d+)',
        r'Total tokens?\s*[:=]\s*([\d,]+)',
        r'total_tokens\s*=\s*(\d+)',
        r'tokens?_?used\s*[:=]\s*([\d,]+)',
        r'Tokens:\s*([\d,]+)',
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return 0


def _extract_token_split(output: str) -> tuple[int, int]:
    """Extract input and output token counts from model output.
    
    Returns (input_tokens, output_tokens). Falls back to total if split unavailable.
    """
    input_pat = r'"(?:prompt|input)_tokens"\s*:\s*(\d+)'
    output_pat = r'"(?:completion|output)_tokens"\s*:\s*(\d+)'
    input_m = re.search(input_pat, output, re.IGNORECASE)
    output_m = re.search(output_pat, output, re.IGNORECASE)
    if input_m and output_m:
        return int(input_m.group(1)), int(output_m.group(1))
    total = _extract_tokens_from_output(output)
    if total > 0:
        return total // 2, total - total // 2
    return 0, 0


# ── v078: Receipt-type mappings (used after work completes) ────────
_SUCCESS_RECEIPTS = {
    "builder": "IMPLEMENTATION",
    "reviewer": "REVIEW_PASS",
    "planner": "PLAN_CREATE",
    "critic": "CRITIQUE_PASS",
}
_FAIL_RECEIPTS = {
    "builder": "BLOCK",
    "reviewer": "REVIEW_REJECT",
    "planner": "PLAN_BLOCK",
    "critic": "CRITIQUE_REJECT",
}

BUDGET_EXIT_CODE = 4


def _record_cost(
    db: DBAdapter, output_text: str, ticket_id: str,
    session_id: str, role: str, model: str,
    tokens_override: int = 0,
) -> None:
    if tokens_override > 0:
        total_tokens = tokens_override
    else:
        total_tokens = _extract_tokens_from_output(output_text)
    if total_tokens <= 0:
        return
    input_tokens, output_tokens = _extract_token_split(output_text)
    if input_tokens == 0 and output_tokens == 0:
        input_tokens = total_tokens // 2
        output_tokens = total_tokens - input_tokens
    pricing = load_pricing(db)
    cost_usd = estimate_cost(input_tokens, output_tokens, model, pricing)
    db.insert_cost_log(session_id, ticket_id, model, input_tokens, output_tokens, None, cost_usd)
    db.update_ticket_costs(ticket_id, cost_usd)
    db.update_agent_budget_usage(role, cost_usd, total_tokens)


def _insert_empty_chain_block(db: DBAdapter, plan_id: str, role: str,
                              session_id: str, ticket_id: str) -> None:
    """Temporal-era hardening: when the resolved model chain is empty,
    emit a BLOCK receipt with closure_reason='no_model_config' and do
    NOT trip the circuit breaker or requeue (prevents the infinite
    requeue loop documented in conduit-hang-remediation.md).
    """
    log = _get_log()
    log.warning("_insert_empty_chain_block: role=%s plan=%s no model config", role, plan_id)
    db.insert_receipt(
        plan_id=plan_id,
        receipt_type="BLOCK",
        agent_role=role,
        session_id=session_id,
        ticket_id=ticket_id,
        summary=f"No model configuration found for role={role}. "
                f"Configure a model in AI Settings (tackle-mcp :3400).",
        metadata={"error": "no_model_config", "role": role},
        tokens_used=0,
    )
    db.close_ticket(plan_id, role, session_id, "failed")


def _resolve_model_chain(db: DBAdapter, role: str) -> list:
    """Build the primary + fallback model chain via tackle.

    Returns a list of dicts: primary first (from get_role_model_config),
    then fallbacks (from get_fallback_models).  Each entry's ``model`` is
    the fully-qualified opencode ID for that model's OWN provider (e.g.
    'opencode/big-pickle', 'nvidia/nvidia/nemotron-3-ultra-550b-a55b'),
    so the executor never has to guess a provider prefix for fallback
    models.  (v120 fix: every model used to be prefixed with the ROLE's
    provider, producing 'nvidia/big-pickle' etc. →
    ProviderModelNotFoundError.)

    Entries whose harness binary is empty are dropped with a warning log
    (Temporal-era hardening — prevents silent infinite requeue on
    misconfigured harnesses).
    """
    log = _get_log()
    chain: list = []
    primary = None
    try:
        primary = db.get_role_model_config(role)
    except Exception as exc:
        log.warning("_resolve_model_chain: primary lookup failed role=%s: %s", role, exc)

    # provider_id → slug map for the primary provider.  Fallbacks that
    # share it (duplicates of the primary model) reuse its name slug
    # instead of their own numeric provider_id.
    primary_slug = ""
    provider_map: dict = {}
    if primary:
        primary_slug = provider_prefix_slug(
            primary.get("provider_name", ""),
            primary.get("provider_type", ""),
            primary.get("provider_id", ""),
        )
        pid = primary.get("provider_id", "")
        if pid and primary_slug:
            provider_map[pid] = primary_slug

    if primary and primary.get("harness") and primary.get("model"):
        chain.append({
            "harness": primary["harness"],
            "model": qualify_opencode_model_id(primary["model"], primary_slug),
            "priority": -1,
        })

    try:
        fallbacks = db.get_fallback_models(role)
    except Exception as exc:
        log.warning("_resolve_model_chain: fallback lookup failed role=%s: %s", role, exc)
        fallbacks = []

    primary_model = (primary or {}).get("model", "")
    for fb in fallbacks:
        semantics = fb.get("invocation_semantics") or {}
        binary = semantics.get("binary", "")
        if not binary:
            log.warning(
                "_resolve_model_chain: skipping fallback role=%s model=%s "
                "harness '%s' has no 'binary' in invocation_semantics",
                role, fb.get("model_identifier", "?"), fb.get("harness_name", "?"),
            )
            continue
        model_id = fb.get("model_identifier", "")
        # Skip fallbacks that merely duplicate the primary (same model).
        if model_id and model_id == primary_model:
            log.debug("_resolve_model_chain: skipping duplicate fallback role=%s model=%s", role, model_id)
            continue
        # Resolve this fallback's OWN provider slug: shared primary
        # provider → type (opencode/ollama ARE the slug) → provider_name
        # for generic APIs (OpenRouter) → provider_id → primary slug.
        fbid = fb.get("provider_id", "")
        slug = provider_map.get(fbid, "") or fallback_provider_prefix_slug(
            fb.get("provider_name", ""), fb.get("provider_type", ""),
            fbid, primary_slug,
        )
        chain.append({
            "harness": binary,
            "model": qualify_opencode_model_id(model_id, slug),
            "priority": fb.get("priority", 0),
        })

    # Fallback: if chain is still empty, try PIPELINE_MODEL env var
    # (allows local execution without tackle-mcp / DB role config)
    if not chain:
        env_model = os.environ.get("PIPELINE_MODEL", "")
        if env_model:
            log.info("_resolve_model_chain: PIPELINE_MODEL fallback role=%s model=%s", role, env_model)
            chain.append({
                "harness": "opencode",
                "model": env_model,
                "priority": -1,
            })

    return chain


def _reject_budget_exceeded(
    db: DBAdapter, plan_id: str, role: str,
    session_id: str, ticket_id: str, reason: str,
) -> None:
    log = _get_log()
    log.warning("_reject_budget_exceeded: role=%s plan=%s reason=%s", role, plan_id, reason)
    db.insert_receipt(
        plan_id=plan_id,
        receipt_type="BLOCK",
        agent_role=role,
        session_id=session_id,
        ticket_id=ticket_id,
        summary=f"Budget exceeded for {role}: {reason}",
        metadata={"reason": reason, "exit_code": BUDGET_EXIT_CODE},
        tokens_used=0,
    )
    db.close_ticket(plan_id, role, session_id, "failed")
    db.close_session(session_id, BUDGET_EXIT_CODE)


def _check_budget(
    db: DBAdapter, plan: dict, role: str,
    session_id: str, ticket_id: str, model: str,
) -> bool:
    """Check aggregate agent budget before dispatch.
    
    Returns True if within budget (OK to proceed), False if budget exceeded (rejected).
    """
    agent_budget = db.get_agent_budget(role)
    if agent_budget:
        ceiling = agent_budget.get("ceiling_usd")
        current = agent_budget.get("current_usd", 0)
        if ceiling is not None and current >= ceiling:
            _reject_budget_exceeded(
                db, plan["id"], role, session_id, ticket_id,
                f"agent budget exhausted: ${current:.2f} >= ${ceiling:.2f}",
            )
            return False

    ticket_budget = db.get_ticket_budget(ticket_id)
    cost_budget = ticket_budget.get("cost_budget_usd", 0)
    if cost_budget > 0:
        plan_text = json.dumps(plan)
        rough_tokens = estimate_tokens(plan_text, model)
        pricing = load_pricing(db)
        estimated = estimate_cost(rough_tokens, 0, model, pricing)
        if estimated >= cost_budget:
            _reject_budget_exceeded(
                db, plan["id"], role, session_id, ticket_id,
                f"estimated cost ${estimated:.6f} >= ticket budget ${cost_budget:.2f}",
            )
            return False

    return True


def _dispatch_one(
    plan: dict,
    role: str,
    db: DBAdapter,
    registry: RegistryConfig,
    model_cfg,
) -> None:
    """Normalize a single plan into a DCO and dispatch to the executor.

    v078: Tickets own authority.  A Ticket is claimed before any work.
    Receipts are linked to their Ticket (Invariant 2).  On terminal
    state, next Tickets are created deterministically (Invariant 5).
    """
    log = _get_log()
    plan_id = plan["id"]
    cursor_before = db.get_cursor(role)
    print(f"Processing plan: {plan_id} - {plan.get('title', '')} for role {role}")
    print(f"  cursor before: {cursor_before or '(none)'}")

    # ── Legacy emitter gate (architect finding 89d7fbe3, 2026-08-09) ──
    # DCO-file emission + executor dispatch is opt-in only ("file"). In
    # the default "runtime" mode main.py performs NO emission and NO
    # dispatch: the plan is left eligible for the runtime store
    # (execution.requests + cascade admission) and completion receipts
    # come from the harness. No ticket claimed, no session opened, no
    # cursor moved — a clean no-op.
    if WR_EMIT_MODE != "file":
        print(f"  [gated] Legacy WR file-emission disabled (CONDUIT_WR_EMIT_MODE={WR_EMIT_MODE!r}). "
              f"Plan {plan_id} role={role} left for runtime dispatch — "
              f"no DCO file written, no executor launched.")
        log.warning("wr-emission gated: plan=%s role=%s mode=%s (runtime dispatch)",
                    plan_id, role, WR_EMIT_MODE)
        return

    session_id = f"{role}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"
    db.create_session(session_id, role, [plan_id])

    # ── Claim the Ticket (Invariant 1: no work without a Ticket) ──
    ticket_id = db.claim_ticket(plan_id, role, session_id)
    if ticket_id is None:
        print(f"  Ticket for {role} on plan {plan_id} was already claimed. Skipping.")
        db.close_session(session_id, 1)
        return
    print(f"  Ticket {ticket_id} claimed for {role} on plan {plan_id}.")

    # ── Execution Authority (ADR-006): acquire lease ───────────
    exec_request = db.get_or_create_execution_request(
        plan_id=plan_id,
        title=plan.get("title", ""),
        objective=plan.get("goal", ""),
    )
    request_id = exec_request["id"]
    lease = db.acquire_lease(request_id=request_id, executor_id=role, ttl_seconds=3600)
    if not lease:
        print(f"  Could not acquire lease for {role} on plan {plan_id}. Skipping.")
        db.close_ticket(plan_id, role, session_id, "failed")
        db.close_session(session_id, 1)
        return
    lease_id = lease["id"]
    lease_released = False
    print(f"  Lease {lease_id} acquired.")

    # ── Resolve model chain (primary + fallbacks) ─────────────────
    chain = _resolve_model_chain(db, role)
    if not chain:
        _insert_empty_chain_block(db, plan_id, role, session_id, ticket_id)
        db.release_lease(lease_id)
        db.advance_cursor(role, plan_id, "")
        db.close_session(session_id, 1)
        return

    print(f"  Model chain: {[e['model'] for e in chain]}")

    # ── Outer loop: try each model in the chain ───────────────────
    last_exit_code = -1
    last_output_text = ""
    last_tokens_used = 0
    last_wr_id = ""
    overall_success = False

    for chain_idx, entry in enumerate(chain):
        harness = entry.get("harness", "opencode")
        model = entry.get("model", "")
        is_primary = (chain_idx == 0)
        model_label = f"primary={model}" if is_primary else f"fallback#{chain_idx}={model}"
        print(f"  [{model_label}] trying harness={harness}")

        # ── Create a fresh DCO for this model ─────────────────
        current_model_cfg = ModelConfig(harness=harness, model=model)
        try:
            dco = WorkRequestFactory.create_from_plan(
                plan, role=role, model_cfg=current_model_cfg,
                working_path=PROJECT_ROOT, session_id=session_id,
            )
        except Exception as e:
            print(f"  [{model_label}] DCO creation failed: {e}. Skipping to next fallback.")
            log.warning("_dispatch_one: DCO creation failed role=%s plan=%s model=%s: %s",
                        role, plan_id, model, e)
            last_exit_code = -1
            continue

        wr_id = dco.id
        last_wr_id = wr_id

        os.makedirs(DCO_DIR, exist_ok=True)
        dco_path = os.path.join(DCO_DIR, f"{wr_id}.json")
        with open(dco_path, "w") as f:
            json.dump(dco.model_dump(by_alias=True), f, indent=2)

        db.add_work_request(wr_id, plan_id, json.dumps(dco.model_dump(by_alias=True)),
                             title=plan.get('title', '') or plan.get('goal', '')[:100])

        # ── Budget check ───────────────────────────────────────
        if not _check_budget(db, plan, role, session_id, ticket_id, model):
            db.advance_cursor(role, plan_id, wr_id)
            print(f"  [{model_label}] Budget check failed. Trying next model.")
            continue

        # ── Resolve executor ───────────────────────────────────
        try:
            executor = resolve_executor(registry, harness)
            executor_cmd = executor.invocation_contract.command
            if not executor_cmd:
                log.warning("_dispatch_one: executor has no command role=%s harness=%s model=%s",
                            role, harness, model)
                print(f"  [{model_label}] No executor command for harness '{harness}'. Trying next.")
                continue
        except Exception as e:
            log.warning("_dispatch_one: executor resolve failed role=%s harness=%s: %s",
                        role, harness, e)
            print(f"  [{model_label}] Executor resolution failed: {e}. Trying next.")
            last_exit_code = -1
            continue

        print(f"  [{model_label}] Executor '{executor.executor_id}' → {wr_id}")

        # ── Inner retry loop for this model (v090) ─────────────
        model_failed = False
        tokens_used = 0
        exit_code = -1
        try:
            for attempt_num in range(1, _retry_max_attempts + 1):
                # ── Execution Authority: create fresh attempt for each retry ──
                attempt_rec = db.create_attempt(lease_id=lease_id, request_id=request_id, executor_id=role)
                attempt_id = attempt_rec["id"]
                print(f"  [{model_label}] attempt {attempt_num}/{_retry_max_attempts} (attempt={attempt_id})")
                work_start = time.time()
                proc = subprocess.Popen(
                    [sys.executable, executor_cmd, dco_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                db.update_session_activity(session_id, pid=proc.pid)
                db.start_attempt(attempt_id)
                try:
                    stdout, _ = proc.communicate(timeout=EXECUTOR_TIMEOUT_SECONDS)
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    print(f"  [{model_label}] TIMEOUT: exceeded {EXECUTOR_TIMEOUT_SECONDS}s. Killing PID {proc.pid}.")
                    _kill_process_tree(proc.pid)
                    try:
                        stdout, _ = proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        stdout, _ = proc.communicate(timeout=5)
                    exit_code = 124

                work_elapsed = time.time() - work_start
                db.add_session_work_time(session_id, work_elapsed)

                output_text = stdout or ""
                tokens_used = _extract_tokens_from_output(output_text)

                _sync_plan_files_to_db(db, PROJECT_ROOT)

                # ── Success → close everything, return ──────────
                if exit_code == 0:
                    overall_success = True
                    db.update_work_request_status(wr_id, "completed")
                    db.close_ticket(plan_id, role, session_id, "completed")
                    db.insert_receipt(
                        plan_id=plan_id,
                        receipt_type=_SUCCESS_RECEIPTS.get(role, "IMPLEMENTATION"),
                        agent_role=role,
                        session_id=session_id,
                        ticket_id=ticket_id,
                        summary=f"{role} completed via {wr_id} (model={model})",
                        metadata={
                            "work_request_id": wr_id,
                            "role": role,
                            "harness": harness,
                            "model": model,
                            "exit_code": 0,
                            "chain_index": chain_idx,
                        },
                        tokens_used=tokens_used,
                    )
                    # ── Execution Authority (ADR-006): complete attempt + receipt ──
                    db.complete_attempt(attempt_id, "SUCCEEDED", exit_code=0,
                                       result={"work_request_id": wr_id, "model": model})
                    db.issue_execution_receipt(
                        attempt_id=attempt_id, request_id=request_id,
                        receipt_type=_SUCCESS_RECEIPTS.get(role, "IMPLEMENTATION"),
                        agent_role=role,
                        summary=f"{role} completed via {wr_id} (model={model})",
                        metadata={"work_request_id": wr_id, "model": model, "harness": harness},
                    )
                    db.release_lease(lease_id)
                    lease_released = True
                    if tokens_used > 0:
                        db.increment_ticket_tokens(ticket_id, tokens_used)
                    _record_cost(db, output_text, ticket_id, session_id, role, model)

                    created = db.create_next_tickets(
                        plan_id, role, "completed",
                        parent_ticket_id=ticket_id,
                        objective=plan.get("title") or plan.get("goal", ""),
                        completion_criteria=plan.get("acceptance_criteria", ""),
                        owner=role,
                    )
                    if created:
                        print(f"  Created {created} next Ticket(s) after {role} completed.")
                    break  # inner retry loop

                # ── Rate limit → sleep & retry (or try next model) ──
                if _detect_api_limit_error(exit_code, output_text):
                    print(f"  [{model_label}] Rate limit hit (attempt {attempt_num}/{_retry_max_attempts}).")
                    error_summary = "API usage limit reached"
                    for line in output_text.splitlines():
                        line_lower = line.lower()
                        if any(p in line_lower for p in ["limit", "quota", "usage", "credit", "429"]):
                            error_summary = line.strip()[:200]
                            break

                    db.update_work_request_status(wr_id, "rate_limited")
                    db.insert_receipt(
                        plan_id=plan_id,
                        receipt_type="API_LIMIT",
                        agent_role=role,
                        session_id=session_id,
                        ticket_id=ticket_id,
                        summary=f"Rate limit retry {attempt_num}/{_retry_max_attempts}: {error_summary}",
                        metadata={
                            "work_request_id": wr_id,
                            "role": role,
                            "harness": harness,
                            "model": model,
                            "exit_code": exit_code,
                            "attempt": attempt_num,
                            "chain_index": chain_idx,
                        },
                        tokens_used=tokens_used,
                    )
                    # ── Execution Authority (ADR-006): complete attempt + receipt ──
                    # 4-path status consistency (ruling ac38fa9b Q4): the attempts
                    # CHECK constraint allows only CREATED/RUNNING/SUCCEEDED/FAILED/
                    # TIMED_OUT — 'FATAL_ERROR' violated it and would crash this
                    # terminal path on revival.
                    db.complete_attempt(attempt_id, "FAILED", exit_code=exit_code,
                                       error=error_summary)
                    db.issue_execution_receipt(
                        attempt_id=attempt_id, request_id=request_id,
                        receipt_type="API_LIMIT", agent_role=role,
                        summary=f"Rate limit {attempt_num}/{_retry_max_attempts}: {error_summary}",
                        metadata={"model": model, "harness": harness, "exit_code": exit_code},
                    )
                    if tokens_used > 0:
                        db.increment_ticket_tokens(ticket_id, tokens_used)
                    _record_cost(db, output_text, ticket_id, session_id, role, model)

                    if attempt_num < _retry_max_attempts:
                        print(f"  [{model_label}] Waiting {_retry_delay_seconds}s before retry...")
                        time.sleep(_retry_delay_seconds)
                        continue  # retry same model
                    else:
                        # Retries exhausted for this model → mark WR and try next fallback
                        print(f"  [{model_label}] Retries exhausted. Falling back to next model.")
                        db.update_work_request_status(wr_id, "failed")
                        last_exit_code = exit_code
                        last_output_text = output_text
                        last_tokens_used = tokens_used
                        model_failed = True
                        break  # inner retry loop → outer chain loop

                # ── Non-rate-limit failure → try next fallback ──
                print(f"  [{model_label}] Failed with exit code {exit_code} (not a rate limit). Trying next fallback.")
                db.update_work_request_status(wr_id, "failed")
                db.insert_receipt(
                    plan_id=plan_id,
                    receipt_type=_FAIL_RECEIPTS.get(role, "BLOCK"),
                    agent_role=role,
                    session_id=session_id,
                    ticket_id=ticket_id,
                    summary=f"{role} failed exit={exit_code} model={model}",
                    metadata={
                        "work_request_id": wr_id,
                        "role": role,
                        "harness": harness,
                        "model": model,
                        "exit_code": exit_code,
                        "chain_index": chain_idx,
                    },
                    tokens_used=tokens_used,
                )
                # ── Execution Authority (ADR-006): complete attempt + receipt ──
                db.complete_attempt(attempt_id, "FAILED", exit_code=exit_code,
                                   error=f"exit code {exit_code}")
                db.issue_execution_receipt(
                    attempt_id=attempt_id, request_id=request_id,
                    receipt_type=_FAIL_RECEIPTS.get(role, "BLOCK"), agent_role=role,
                    summary=f"{role} failed exit={exit_code} model={model}",
                    metadata={"model": model, "harness": harness, "exit_code": exit_code},
                )
                if tokens_used > 0:
                    db.increment_ticket_tokens(ticket_id, tokens_used)
                _record_cost(db, output_text, ticket_id, session_id, role, model)
                last_exit_code = exit_code
                last_output_text = output_text
                last_tokens_used = tokens_used
                model_failed = True
                break  # inner retry loop → outer chain loop

        except Exception as e:
            # Exception during this model's attempt → log and try next fallback
            print(f"  [{model_label}] Exception during execution: {e}. Trying next fallback.")
            log.warning("_dispatch_one: exception role=%s plan=%s model=%s: %s",
                        role, plan_id, model, e)
            db.update_work_request_status(wr_id, "failed")
            # ── Execution Authority (ADR-006): complete attempt + receipt ──
            try:
                db.complete_attempt(attempt_id, "FAILED", exit_code=-1, error=str(e))
                db.issue_execution_receipt(
                    attempt_id=attempt_id, request_id=request_id,
                    receipt_type=_FAIL_RECEIPTS.get(role, "BLOCK"), agent_role=role,
                    summary=f"{role} exception: {e}",
                    metadata={"model": model, "harness": harness},
                )
            except Exception as inner_e:
                log.warning("_dispatch_one: failed to record execution authority for exception: %s", inner_e)
            last_exit_code = -1
            model_failed = True
            # Continue to next model in chain

        # ── If the success path already closed the ticket, return ──
        if overall_success:
            db.advance_cursor(role, plan_id, wr_id)
            cursor_after = db.get_cursor(role)
            print(f"  cursor after: {cursor_after}")
            db.close_session(session_id, 0)
            return

        # model_failed → continue to next entry in chain

    # ── All models in chain exhausted ──────────────────────────
    if not overall_success:
        print(f"  All {len(chain)} model(s) failed for {role} on plan {plan_id}. Closing ticket.")
        db.close_ticket(plan_id, role, session_id, "failed")

        db.insert_receipt(
            plan_id=plan_id,
            receipt_type=_FAIL_RECEIPTS.get(role, "BLOCK"),
            agent_role=role,
            session_id=session_id,
            ticket_id=ticket_id,
            summary=f"All fallbacks exhausted for {role} on plan {plan_id}",
            metadata={
                "role": role,
                "plan_id": plan_id,
                "chain_attempted": [e.get("model", "?") for e in chain],
                "last_exit_code": last_exit_code,
            },
            tokens_used=last_tokens_used,
        )
        # ── Execution Authority (ADR-006): release lease (all attempts completed) ──
        if not lease_released:
            try:
                db.release_lease(lease_id)
                lease_released = True
                print(f"  Lease {lease_id} released after all models exhausted.")
            except Exception as e:
                log.warning("_dispatch_one: failed to release lease %s: %s", lease_id, e)
        if last_tokens_used > 0:
            db.increment_ticket_tokens(ticket_id, last_tokens_used)
        created = db.create_next_tickets(
            plan_id, role, "failed",
            parent_ticket_id=ticket_id,
            objective=plan.get("title") or plan.get("goal", ""),
            owner=role,
        )
        if created:
            print(f"  Created {created} retry Ticket(s) after all models failed.")

        db.advance_cursor(role, plan_id, last_wr_id)
        cursor_after = db.get_cursor(role)
        print(f"  cursor after (all failed): {cursor_after}")
        db.close_session(session_id, last_exit_code)


def dispatch_single_plan(
    plan_id: str,
    db: DBAdapter,
    registry: RegistryConfig,
    force: bool = False,
) -> None:
    """Dispatch a builder for a single plan, bypassing cursor/pause/breaker checks."""
    if not force and db.is_circuit_breaker_tripped():
        print(f"Restart blocked: circuit breaker is tripped. Use --force to override.")
        return

    active = db.get_active_session("builder")
    if active:
        pid = active.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
                print(f"Builder session {active['id']} (PID {pid}) is still active. Cannot restart.")
                return
            except ProcessLookupError:
                print(f"Stale builder session {active['id']} (PID {pid}). Closing and releasing Tickets.")
                db.release_session_tickets(active["id"])
                db.close_session(active["id"], 137)
        else:
            db.release_session_tickets(active["id"])
            db.close_session(active["id"], 1)

    plan_row = db.get_plan_by_id(plan_id)
    if not plan_row:
        print(f"Plan {plan_id} not found.")
        return

    plan = dict(plan_row)
    print(f"Restarting builder for plan: {plan_id} - {plan.get('title', '')}")

    now = datetime.now(timezone.utc).isoformat() + "Z"
    db.create_ticket_if_missing(plan_id, "builder", "restart-v078", now)

    model_cfg = get_model(db, registry, "builder")
    _dispatch_one(plan, "builder", db, registry, model_cfg)


def run_role(db_path: str, role: str, registry: RegistryConfig):
    db = DBAdapter(db_path)

    if db.is_conduit_paused():
        print(f"Pipeline is paused. Skipping role {role}.")
        return

    # ── Per-role circuit breaker check (Temporal-era hardening) ──
    if _is_role_circuit_breaker_tripped(db, role):
        print(f"Circuit breaker tripped for role {role}. Skipping.")
        return

    active_sessions = db.get_all_active_sessions()
    for active_session in active_sessions:
        session_role = active_session.get("agent_role", "")
        pid = active_session.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
                total_work = active_session.get("total_work_seconds", 0) or 0
                if total_work > WATCHDOG_STALE_SECONDS:
                    print(f"Watchdog: Killing stale session {active_session['id']} (role={session_role}, PID {pid}, total_work={int(total_work)}s)")
                    _kill_process_tree(pid)
                    db.release_session_tickets(active_session["id"])
                    db.close_session(active_session["id"], 137)
                else:
                    print(f"Session {active_session['id']} (role={session_role}) still active ({int(total_work)}s work so far).")
                    if session_role == role:
                        return
            except ProcessLookupError:
                print(f"Watchdog: Session {active_session['id']} (role={session_role}) PID {pid} is dead. Closing.")
                db.release_session_tickets(active_session["id"])
                db.close_session(active_session["id"], 1)
        else:
            print(f"Session {active_session['id']} (role={session_role}) has no PID. Closing.")
            db.release_session_tickets(active_session["id"])
            db.close_session(active_session["id"], 1)

    stale = db.detect_stale_tickets()
    expired = db.detect_expired_tickets()
    if stale or expired:
        print(f"Ticket lifecycle: {stale} stale, {expired} expired.")

    # ── Execution Authority (ADR-006): expire stale leases ──
    try:
        expired_leases = db.expire_stale_leases()
        if expired_leases:
            print(f"Execution Authority: expired {expired_leases} stale lease(s).")
    except Exception as e:
        log.warning("run_role: failed to expire stale leases: %s", e)

    # ── Execution Authority (ADR-006): cascade admission ──
    try:
        ready_count = db.cascade_admission()
        if ready_count:
            print(f"Execution Authority: {ready_count} request(s) transitioned to READY.")
    except Exception as e:
        log.warning("run_role: failed to cascade admission: %s", e)

    eligible_plans = db.get_eligible_plans(role)

    if role == "planner":
        blocked = db.get_blocked_plans()
        if blocked:
            print(f"Found {len(blocked)} blocked plans.")

    if not eligible_plans:
        print(f"No eligible plans for role {role}.")
        return

    print(f"Found {len(eligible_plans)} eligible plan(s) for role {role}.")
    model_cfg = get_model(db, registry, role)

    for plan in eligible_plans:
        _dispatch_one(plan, role, db, registry, model_cfg)


def clean_test_artifacts(db_path: str) -> None:
    db = DBAdapter(db_path)
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT id, plans_processed FROM sessions WHERE exit_code = 3"
        ).fetchall()
        if not rows:
            print("No test artifacts found.")
            return
        count = 0
        for session_id, plans_json in rows:
            try:
                plan_ids = json.loads(plans_json)
            except Exception:
                continue
            for plan_id in plan_ids:
                conn.execute(
                    "DELETE FROM vision.receipts WHERE plan_id = %s AND session_id = %s AND type = 'BLOCK'",
                    (plan_id, session_id),
                )
                count += conn.total_changes
        conn.commit()
        print(f"Cleaned {count} test-artifact receipt(s) from {len(rows)} session(s).")


def print_status(db_path: str, registry: RegistryConfig) -> None:
    log = _get_log()
    log.info("print_status: entry db_path=%s", db_path)
    db = DBAdapter(db_path)
    roles = ["builder", "reviewer", "planner", "critic"]

    print("=== Pipeline Status ===")
    print(f"  Timestamp: {datetime.now(timezone.utc).isoformat()}")

    tripped = db.is_circuit_breaker_tripped()
    print(f"  Circuit breaker: {'TRIPPED' if tripped else 'ok'}")
    log.info("print_status: circuit_breaker=%s", 'TRIPPED' if tripped else 'ok')

    model_cfg = get_model(db, registry, "builder")
    print(f"  Active model: {model_cfg.harness}/{model_cfg.model}")
    log.info("print_status: active_model=%s/%s", model_cfg.harness, model_cfg.model)

    blocked_count = len(db.get_blocked_plans())
    print("\n  Role          Cursor         Eligible  Blocked  Tokens")
    print("  ────          ──────         ────────  ───────  ──────")
    for role in roles:
        cursor = db.get_cursor(role)
        eligible = len(db.get_eligible_plans(role))
        blocked = blocked_count if role == "planner" else 0
        tok = db.get_token_usage_by_role(role)
        tokens = tok.get("total_tokens", 0)
        print(f"  {role:12}  {cursor or '(none)':14}  {eligible:8}  {blocked:7}  {tokens:6}")
    log.info("print_status: status printed roles=%s", roles)


def _sync_plan_files_to_db(db: DBAdapter, conduit_data_dir: str) -> None:
    """Tell the MCP server to scan the filesystem and upsert missing plan rows."""
    try:
        from urllib.request import Request, urlopen
        mcp_url = os.environ.get("MCP_BASE_URL", "http://localhost:3100")
        req = Request(f"{mcp_url}/plans/sync", method="POST", data=b"{}")
        req.add_header("Content-Type", "application/json")
        resp = urlopen(req, timeout=5)
        body = resp.read().decode("utf-8")
        result = json.loads(body)
        synced = result.get("synced", 0)
        if synced > 0:
            print(f"  Synced {synced} new plan file(s) to DB.")
    except Exception as e:
        print(f"  Note: /plans/sync call failed ({e}). PlanWatcher will catch up.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Manager (cron-driven)")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Conduit data directory (DB connection is via CONDUIT_PG_DSN env)")
    parser.add_argument("--registry", help="Path to registry.json (deprecated — tackle-mcp owns AI config)")
    parser.add_argument("--status", action="store_true", help="Print pipeline status and exit (no lock)")
    parser.add_argument("--clean-test-artifacts", action="store_true", help="Remove test receipts (exit code 3 BLOCKs) and exit")
    parser.add_argument("--run", choices=["builder", "reviewer", "planner", "critic"], help="Run the conduit for a specific role")
    parser.add_argument("--plan", help="Dispatch a builder for a single plan ID (bypasses cursor/pause)")
    parser.add_argument("--force", action="store_true", help="Override circuit breaker block when using --plan")
    parser.add_argument("--emit-mode", choices=list(WR_EMIT_MODES), default=None,
                        help="WR emission mode: 'file' (legacy DCO-file dispatch, opt-in) or "
                             "'runtime' (default — no file emission; the runtime store is the "
                             "dispatch authority). Overrides CONDUIT_WR_EMIT_MODE.")
    parser.add_argument("--all", action="store_true", help="Run for all roles sequentially")
    parser.add_argument("--supersede", help="Supersede a ticket by ID (mark terminal, optionally create replacement)")
    parser.add_argument("--supersede-reason", default="", help="Reason for superseding (used with --supersede)")
    parser.add_argument("--supersede-replace", action="store_true", help="Also create a replacement ticket (used with --supersede)")
    parser.add_argument("--cancel", help="Cancel a ticket by ID (mark terminal, deny authorization)")
    parser.add_argument("--cancel-reason", default="", help="Reason for cancelling (used with --cancel)")
    parser.add_argument("--kernel-sync", action="store_true",
                        help="Sync conduit receipts to the WRP Kernel Runtime (one-shot)")
    parser.add_argument("--kernel-sync-daemon", action="store_true",
                        help="Run kernel sync in continuous poll loop")

    args = parser.parse_args()
    if args.emit_mode is not None:
        WR_EMIT_MODE = args.emit_mode  # module-level; overrides env default
    registry = load_registry(args.registry)

    # ── Kernel bridge flags (no lock needed) ─────────────────────
    if args.kernel_sync_daemon:
        from bridge.sync import syncer as _bridge
        print("Kernel bridge daemon starting (Ctrl+C to stop)...")
        _bridge.run_daemon()
        sys.exit(0)
    elif args.kernel_sync:
        from bridge.sync import syncer as _bridge
        count = _bridge.sync_once()
        if count > 0:
            print(f"Kernel bridge: synced {count} receipt(s)")
        elif count == 0:
            print("Kernel bridge: nothing new")
        else:
            print(f"Kernel bridge: sync failed (kernel API rejected)")
            sys.exit(1)
        _bridge.close()
        sys.exit(0)

    if args.clean_test_artifacts:
        clean_test_artifacts(args.db)
        sys.exit(0)

    if args.status:
        print_status(args.db, registry)
        sys.exit(0)

    if not acquire_lock():
        print("Another conduit instance is running. Exiting.")
        sys.exit(0)

    _cleanup_orphaned_processes()

    if args.cancel:
        ticket_id = args.cancel
        if not ticket_id.startswith("ticket-"):
            print(f"Error: {ticket_id} doesn't look like a ticket ID (expected 'ticket-...')")
            sys.exit(1)
        db = DBAdapter(args.db)
        count = db.cancel_ticket(
            ticket_id,
            reason=args.cancel_reason or "cancelled from CLI",
        )
        if count > 0:
            print(f"Ticket {ticket_id} cancelled.")
        else:
            print(f"Ticket {ticket_id} not found or not cancellable.")
            sys.exit(1)
    elif args.supersede:
        ticket_id = args.supersede
        if not ticket_id.startswith("ticket-"):
            print(f"Error: {ticket_id} doesn't look like a ticket ID (expected 'ticket-...')")
            sys.exit(1)
        db = DBAdapter(args.db)
        result = db.supersede_ticket(
            ticket_id,
            reason=args.supersede_reason or "superseded from CLI",
        )
        if result.get("superseded"):
            print(f"Ticket {ticket_id} superseded.")
            if args.supersede_replace:
                old = result.get("old_ticket", {})
                if old:
                    now = datetime.now(timezone.utc).isoformat() + "Z"
                    ts = int(datetime.now(timezone.utc).timestamp())
                    repl = db.create_ticket_if_missing(
                        old["plan_id"], old["role"],
                        f"supersede-replace-{ts}", now,
                        objective=old.get("objective") or "",
                        owner=old.get("owner") or old.get("role", ""),
                        parent_ticket_id=ticket_id,
                        spawn_reason="replacement after supersede",
                        replacement_of=ticket_id,
                    )
                    if repl:
                        print(f"  Replacement ticket: {repl}")
                    else:
                        print(f"  Replacement ticket already exists (idempotent).")
        else:
            print(f"Ticket {ticket_id} not found or not supersedeable.")
            sys.exit(1)
    elif args.plan:
        db = DBAdapter(args.db)
        dispatch_single_plan(args.plan, db, registry, force=args.force)
    elif args.run:
        run_role(args.db, args.run, registry)
    elif args.all:
        for role in ["reviewer", "planner", "builder", "critic"]:
            run_role(args.db, role, registry)
    else:
        parser.print_help()

    if _log is not None:
        for h in _log.handlers:
            h.flush()