"""Deterministic model registration: given a provider + harness, register
all models from that provider's opencode config as pipeline model entries.

Usage:
    python -m conduit.register_models               # register all ollama models with opencode harness
    python -m conduit.register_models --dry-run      # preview without DB writes
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional


OPencodeCONFIG_PATH = os.path.expanduser("~/.config/opencode/opencode.json")
PG_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"


def _sanitize(name: str) -> str:
    """Sanitize an ollama model name for use in a conduit model ID."""
    s = name.lower().replace(":", "-").replace("/", "-").replace(".", "-")
    s = re.sub(r"[^a-z0-9_-]", "", s)
    return s.strip("-")


def _build_model_id(sanitized: str, harness: str, provider: str) -> str:
    """Build a deterministic model ID encoding harness and provider."""
    h = harness.replace("harn-", "").replace("-", "")
    p = provider.replace("prov-", "").replace("-", "")
    return f"mod-{sanitized}-{h}-{p}"


def _build_role_model_id(model_id: str, role: str) -> str:
    return f"rm-{role}-{model_id}"


def get_ollama_models() -> list[dict]:
    """Return list of ollama models from `ollama list`."""
    result = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"ollama list failed: {result.stderr}")

    models = []
    for line in result.stdout.strip().split("\n")[1:]:  # skip header
        parts = line.split()
        if parts:
            models.append({"name": parts[0], "raw": line})
    return models


def get_opencode_provider_models(provider_type: str = "ollama") -> dict[str, str]:
    """Read opencode config and return {opencode_key: ollama_model_name} for a provider."""
    if not os.path.exists(OPencodeCONFIG_PATH):
        print(f"  [warn] opencode config not found at {OPencodeCONFIG_PATH}", file=sys.stderr)
        return {}

    with open(OPencodeCONFIG_PATH) as f:
        config = json.load(f)

    provider_config = config.get("provider", {}).get(provider_type, {})
    models_config = provider_config.get("models", {})
    return {key: entry.get("name", key) for key, entry in models_config.items()}


def register_models(
    provider: str = "prov-ollama",
    harness: str = "harn-opencode",
    provider_type: str = "ollama",
    role: str = "builder",
    dry_run: bool = False,
    start_priority: Optional[int] = None,
):
    """Deterministic function: register all models from an opencode provider
    as conduit pipeline model + role_models rows.

    Args:
        provider: conduit provider ID
        harness: conduit harness ID
        provider_type: key in opencode config's provider map (e.g. 'ollama')
        role: role to assign role_models for
        dry_run: if True, print instead of writing to DB
        start_priority: starting priority for role_models (auto if None)
    """
    opencode_models = get_opencode_provider_models(provider_type)
    if not opencode_models:
        print(f"  [warn] no models found in opencode config for provider '{provider_type}'")
        return

    print(f"opencode models for provider '{provider_type}':")
    for key, model_name in opencode_models.items():
        print(f"  {key:30s} -> {model_name}")

    ollama_models_list = get_ollama_models()
    ollama_names = {m["name"] for m in ollama_models_list}
    print(f"\nollama local models ({len(ollama_names)}):")
    for m in sorted(ollama_names):
        print(f"  {m}")

    # Build reverse map: ollama model name -> opencode key
    name_to_key = {}
    for key, model_name in opencode_models.items():
        name_to_key[model_name] = key

    models_to_register = []
    for ollama_name in sorted(ollama_names):
        opencode_key = name_to_key.get(ollama_name)
        if not opencode_key:
            print(f"  [skip] {ollama_name}: no matching opencode config entry (add it to opencode.json first)")
            continue
        sanitized = _sanitize(ollama_name)
        model_id = _build_model_id(sanitized, harness, provider)
        models_to_register.append({
            "model_id": model_id,
            "sanitized": sanitized,
            "opencode_key": opencode_key,
            "ollama_name": ollama_name,
        })

    if not models_to_register:
        print("\nNo models to register.")
        return

    print(f"\nModels to register ({len(models_to_register)}):")
    for m in models_to_register:
        print(f"  {m['model_id']:45s} opencode_key={m['opencode_key']:25s} ollama={m['ollama_name']}")

    if dry_run:
        print("\n[dry-run] skipping DB writes")
        return

    import psycopg2

    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("SET search_path TO conduit,tackle")
    now = datetime.now(timezone.utc).isoformat()

    # Determine starting priority
    if start_priority is None:
        cur.execute(
            "SELECT COALESCE(MAX(priority), -1) + 1 FROM role_models WHERE role = %s",
            (role,)
        )
        start_priority = cur.fetchone()[0]

    inserted = 0
    for i, m in enumerate(models_to_register):
        model_id = m["model_id"]
        rm_id = _build_role_model_id(model_id, role)
        priority = start_priority + i
        display_name = m["ollama_name"].replace(":latest", "").replace(":", " ").title()

        # UPSERT model
        cur.execute("""
            INSERT INTO models (id, name, model_identifier, provider_id, harness_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                model_identifier = EXCLUDED.model_identifier,
                updated_at = EXCLUDED.updated_at
        """, (
            model_id,
            f"{display_name} ({harness.replace('harn-', '')} + {provider.replace('prov-', '')})",
            m["opencode_key"],
            provider,
            harness,
            now, now,
        ))

        # UPSERT role_models
        cur.execute("""
            INSERT INTO role_models (id, role, model_id, priority, harness_id, provider_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                priority = EXCLUDED.priority,
                harness_id = EXCLUDED.harness_id,
                provider_id = EXCLUDED.provider_id
        """, (
            rm_id,
            role,
            model_id,
            priority,
            harness,
            provider,
        ))

        inserted += 1

    conn.commit()

    # Show final chain
    cur.execute("""
        SELECT arm.priority, m.model_identifier, m.id, arm.harness_id, arm.provider_id
        FROM role_models arm
        JOIN models m ON arm.model_id = m.id
        WHERE arm.role = %s
        ORDER BY arm.priority
    """, (role,))
    print(f"\nUpdated {role} chain ({inserted} new):")
    for r in cur.fetchall():
        print(f"  p{r[0]}: {r[1]:30s} model={r[2][:35]:35s} harness={r[3]:20s} provider={r[4]}")

    conn.close()
    print(f"\nDone — {inserted} model(s) registered.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register ollama models with opencode harness")
    parser.add_argument("--dry-run", action="store_true", help="Preview without DB writes")
    parser.add_argument("--provider", default="prov-ollama", help="Conduit provider ID")
    parser.add_argument("--harness", default="harn-opencode", help="Conduit harness ID")
    parser.add_argument("--provider-type", default="ollama", help="Opencode config provider key")
    parser.add_argument("--role", default="builder", help="Role for role_models")
    parser.add_argument("--start-priority", type=int, default=None, help="Starting priority")
    args = parser.parse_args()
    register_models(
        provider=args.provider,
        harness=args.harness,
        provider_type=args.provider_type,
        role=args.role,
        dry_run=args.dry_run,
        start_priority=args.start_priority,
    )
