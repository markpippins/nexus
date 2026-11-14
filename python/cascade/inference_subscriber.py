"""inference_subscriber.py — POC NATS subscriber bridging Cascade events to Tackle inference.

Subscribes to ``nexus.cascade.v1.workflow.idea_captured`` via NATS,
resolves model/harness config through Tackle, invokes inference via
subprocess, and publishes results back as InferenceCompleted events.

Design:
  - Single-file POC, zero new packages
  - NATS input only (no file polling)
  - Tackle for model/harness resolution + CLI command building
  - Subprocess invocation (fire-and-forget, no session management)
  - Dual-write output: events/ directory + NATS publish
  - Temporary bridge during Cascade LLM strip (Plan #1021 → #1022)

Usage::

    NATS_URL=nats://localhost:4222 python3 inference_subscriber.py

Architecture::

    Cascade (pure event bus)
      │ IdeaCaptured on nexus.cascade.v1.workflow.idea_captured
      ▼
    inference_subscriber.py
      ├─ NATS subscribe
      ├─ Tackle → resolve model/harness
      ├─ HarnessLauncher → build CLI command
      ├─ subprocess.run → invoke inference
      └─ publish InferenceCompleted (events/ + NATS)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import subprocess
import sys
import uuid
from typing import Any

# ── Path setup (same pattern as nats_publisher.py) ──────────────────
# cascade subprocesses run with cascade/ as cwd; shared packages are
# one level above at nexus/python/.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [inference-sub] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
_log = logging.getLogger("inference-subscriber")


# ═══════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT = "nexus.cascade.v1.workflow.idea_captured"
OUTPUT_SUBJECT_PREFIX = "nexus.cascade.v1.inference"
EVENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events")
INFERENCE_TIMEOUT_SECONDS = int(os.environ.get("INFERENCE_TIMEOUT", "300"))

# Event type → Tackle role mapping (hardcoded for POC; configurable later)
EVENT_TYPE_TO_ROLE: dict[str, str] = {
    "IdeaCaptured": "architect",
}


# ═══════════════════════════════════════════════════════════════════════
#  Tackle inference resolution
# ═══════════════════════════════════════════════════════════════════════

def resolve_inference_config(role: str) -> dict[str, Any] | None:
    """Resolve model/harness config for a role via Tackle.

    Calls ``tackle.db.get_role_config()`` which queries tackle-mcp
    on port 3400 (TTL-cached, 60s).
    """
    try:
        from tackle.db import get_role_config
        cfg = get_role_config(role)
        if cfg:
            _log.info("Resolved config for role=%s: model=%s harness=%s provider=%s",
                      role, cfg.get("model_identifier"), cfg.get("harness_name"), cfg.get("provider_type"))
            return cfg
        _log.warning("No config found for role=%s (tackle-mcp may be down or role not configured)", role)
        return None
    except Exception as e:
        _log.error("Failed to resolve config for role=%s: %s", role, e)
        return None


def build_prompt(event: dict[str, Any]) -> str:
    """Build a minimal prompt from the event payload.

    Extracts the idea text and wraps it in a simple instruction.
    This is intentionally minimal — the POC just proves the plumbing.
    """
    payload = event.get("payload", {})
    idea = payload.get("idea", "") or payload.get("title", "") or json.dumps(payload)

    return (
        f"You are a domain analysis architect. Analyze this idea and produce "
        f"a structured analysis with entities, actions, states, and constraints.\n\n"
        f"Idea:\n{idea}\n\n"
        f"Return your analysis as JSON with keys: entities, actions, states, constraints."
    )


def invoke_inference(cfg: dict[str, Any], prompt: str) -> tuple[str | None, str | None]:
    """Invoke inference via Tackle's HarnessLauncher + subprocess.

    Uses Tackle's ``HarnessLauncher`` to build the correct CLI command
    for the resolved harness, then runs it as a subprocess.

    This is synchronous (blocks the caller). Callers should wrap in
    ``asyncio.to_thread()`` or ``loop.run_in_executor()`` to avoid
    blocking the event loop.

    Returns (stdout, error_string). On success, error is None.
    """
    try:
        from tackle.harness_launcher import HarnessLauncher

        launcher = HarnessLauncher.from_harness_row({
            "name": cfg.get("harness_name", "opencode"),
            "invocation_semantics": cfg.get("invocation_semantics", {}),
        })

        model_id = cfg.get("model_identifier", "")
        if model_id:
            launcher.set_model(model_id)

        launcher.set_prompt(prompt)

        # Working directory for the harness
        project_root = os.environ.get("PIPELINE_ROOT", "/home/codex/dev")
        launcher.set_working_directory(project_root)

        cmd = launcher.build()
        _log.info("Invoking: %s", " ".join(cmd))

    except Exception as e:
        _log.error("Failed to build harness command: %s", e)
        # Fall back to direct ollama call if ollama is configured
        if cfg.get("provider_type") == "ollama" and cfg.get("model_identifier"):
            return _invoke_ollama_direct(cfg, prompt)
        return None, f"HarnessLauncher build failed: {e}"

    # Merge API key into environment if configured
    proc_env = os.environ.copy()
    api_key = cfg.get("api_key") or ""
    provider_type = cfg.get("provider_type") or ""
    if api_key and provider_type:
        env_name = _PROVIDER_ENV_MAP.get(provider_type, f"{provider_type.upper()}_API_KEY")
        if env_name:
            proc_env[env_name] = api_key

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=INFERENCE_TIMEOUT_SECONDS,
            cwd=os.environ.get("PIPELINE_ROOT", "/home/codex/dev"),
            env=proc_env,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0:
            _log.warning("Inference exited %d: %s", proc.returncode, stderr[:300])
            if stdout:
                return stdout, None  # sometimes stdout has output despite non-zero exit
            return None, f"Inference exited {proc.returncode}: {stderr[:500]}"

        _log.info("Inference completed: %d chars stdout", len(stdout))
        return stdout, None

    except subprocess.TimeoutExpired:
        return None, f"Inference timed out after {INFERENCE_TIMEOUT_SECONDS}s"
    except FileNotFoundError:
        return None, f"Binary not found: {cmd[0]}"
    except Exception as e:
        return None, f"Inference invocation failed: {e}"


# Map provider types to their API key env vars (mirrors tackle/agent_chat.py)
_PROVIDER_ENV_MAP = {
    "opencode": "OPENCODE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "",
    "codex": "CODEX_API_KEY",
}


def _invoke_ollama_direct(cfg: dict[str, Any], prompt: str) -> tuple[str | None, str | None]:
    """Direct Ollama HTTP call fallback (no HarnessLauncher needed)."""
    import urllib.request
    import urllib.error

    model = cfg.get("model_identifier", "deepseek-coder:latest")
    endpoint = cfg.get("endpoint_url", "").rstrip("/") or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    url = f"{endpoint}/api/generate"

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=INFERENCE_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            result = body.get("response", "").strip()
            _log.info("Ollama direct: %d chars", len(result))
            return result, None
    except urllib.error.URLError as e:
        return None, f"Ollama connection refused: {e}"
    except Exception as e:
        return None, f"Ollama error: {e}"


# ═══════════════════════════════════════════════════════════════════════
#  Result publishing
# ═══════════════════════════════════════════════════════════════════════

def _is_canonical_envelope(data: dict[str, Any]) -> bool:
    """Detect whether a dict is a CanonicalEnvelope (vs a flat cascade event).

    Uses ``CanonicalEnvelope.from_dict()`` if available, otherwise falls
    back to key-based detection.
    """
    # Prefer type-based detection via the shared envelope module
    try:
        from nats_envelope.envelope import CanonicalEnvelope
        CanonicalEnvelope.from_dict(data)
        return True
    except (ImportError, KeyError, TypeError):
        pass

    # Fallback: key-based detection
    return (
        "payload" in data
        and "event_type" in data
        and ("origin_component" in data or "classification" in data)
        and "correlation_id" in data
    )


def publish_result(
    event: dict[str, Any],
    output: str | None,
    error: str | None,
    role: str,
) -> None:
    """Publish an InferenceCompleted event via dual-write.

    Writes to events/ directory (filesystem) AND publishes via NATS
    (using the existing nats_publisher infrastructure).
    """
    now = datetime.datetime.now(datetime.UTC).isoformat()
    event_id = str(uuid.uuid4())
    source_event_id = event.get("id", "")

    result_event: dict[str, Any] = {
        "id": event_id,
        "type": "InferenceCompleted",
        "timestamp": now,
        "source": "inference-subscriber",
        "payload": {
            "source_event_id": source_event_id,
            "source_event_type": event.get("type", "unknown"),
            "role": role,
            "status": "success" if output else "error",
            "output": output,
            "error": error,
        },
    }

    # Write to events/ directory
    os.makedirs(EVENTS_DIR, exist_ok=True)
    event_path = os.path.join(EVENTS_DIR, f"{event_id}.json")
    with open(event_path, "w") as f:
        json.dump(result_event, f, indent=2)
    _log.info("Event written: %s", event_path)

    # Enqueue for NATS publish via sidecar (fire-and-forget)
    try:
        from nats_publisher import try_enqueue_event
        try_enqueue_event(
            result_event,
            causation_id=source_event_id,
            source_event_ids=[source_event_id],
        )
        _log.info("Event enqueued for NATS: %s", event_id)
    except ImportError:
        _log.debug("nats_publisher not available — NATS publish skipped")
    except Exception as e:
        _log.warning("NATS enqueue failed: %s", e)


# ═══════════════════════════════════════════════════════════════════════
#  NATS subscriber
# ═══════════════════════════════════════════════════════════════════════

async def handle_event(event: dict[str, Any]) -> None:
    """Process a single Cascade event: resolve → invoke → publish.

    Inference invocation is offloaded to a thread via ``asyncio.to_thread()``
    to avoid blocking the NATS event loop during subprocess calls.
    """
    event_type = event.get("type", "")
    event_id = event.get("id", "?")

    role = EVENT_TYPE_TO_ROLE.get(event_type)
    if not role:
        _log.debug("No role mapping for event_type=%s — skipping", event_type)
        return

    _log.info("Processing event %s type=%s role=%s", event_id, event_type, role)

    # Resolve inference config via Tackle
    cfg = resolve_inference_config(role)
    if not cfg:
        publish_result(event, None, f"No Tackle config for role={role}", role)
        return

    # Build prompt
    prompt = build_prompt(event)
    _log.info("Prompt built: %d chars", len(prompt))

    # Invoke inference in a thread to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    output, error = await loop.run_in_executor(None, invoke_inference, cfg, prompt)

    # Publish result
    publish_result(event, output, error, role)


async def run_subscriber() -> None:
    """Main loop: connect to NATS, subscribe, process events."""
    try:
        import nats
    except ImportError:
        _log.error("nats-py not installed. Install with: pip install nats-py>=0.8.0")
        return

    # Start NATS publish sidecar so try_enqueue_event() works
    _start_publish_sidecar()

    _log.info("Connecting to NATS at %s", NATS_URL)
    try:
        nc = await nats.connect(NATS_URL)
    except Exception as e:
        _log.error("Cannot connect to NATS: %s", e)
        _log.error("Is NATS running? Start with: nats-server -js")
        return

    try:
        _log.info("Subscribing to %s", SUBJECT)
        sub = await nc.subscribe(SUBJECT)

        _log.info("Ready — waiting for events on %s", SUBJECT)
        async for msg in sub.messages:
            try:
                payload = msg.data.decode()
                event = json.loads(payload)

                # If the message is a CanonicalEnvelope, extract the inner payload
                if _is_canonical_envelope(event):
                    inner = event.get("payload", {})
                    if isinstance(inner, dict) and inner.get("type"):
                        event = inner

                await handle_event(event)
            except json.JSONDecodeError:
                _log.warning("Invalid JSON on %s: %s", msg.subject, msg.data[:200])
            except Exception as e:
                _log.error("Error handling event: %s", e, exc_info=True)

    finally:
        _log.info("Closing NATS connection")
        await nc.close()
        _stop_publish_sidecar()


def _start_publish_sidecar() -> None:
    """Start the NATS publish sidecar thread from nats_publisher.

    Required so that ``try_enqueue_event()`` has a worker draining
    the publish queue. Without this, events pile up silently.
    """
    try:
        from nats_publisher import start_nats_sidecar
        start_nats_sidecar(NATS_URL)
        _log.info("NATS publish sidecar started")
    except ImportError:
        _log.warning("nats_publisher not available — NATS publish disabled")
    except Exception as e:
        _log.warning("NATS publish sidecar failed to start: %s", e)


def _stop_publish_sidecar() -> None:
    """Stop the NATS publish sidecar thread gracefully."""
    try:
        from nats_publisher import stop_nats_sidecar
        stop_nats_sidecar()
        _log.info("NATS publish sidecar stopped")
    except ImportError:
        pass
    except Exception as e:
        _log.debug("NATS publish sidecar stop: %s", e)


# ═══════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    """Start the inference subscriber."""
    _log.info("Starting Cascade Inference Subscriber (POC)")
    _log.info("NATS URL: %s", NATS_URL)
    _log.info("Subject: %s", SUBJECT)
    _log.info("Events dir: %s", EVENTS_DIR)
    _log.info("Role mapping: %s", EVENT_TYPE_TO_ROLE)

    try:
        asyncio.run(run_subscriber())
    except KeyboardInterrupt:
        _log.info("Shutting down")
    except Exception as e:
        _log.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
