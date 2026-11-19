#!/usr/bin/env python3
"""harness_launcher.py — Generic CLI command builder driven by harness schema.

Instead of hardcoded ``if harness_name == "opencode"`` branches, this module
reads semantic metadata from the harness row (stored in ``invocation_semantics``
JSON) and constructs CLI arguments based on typed enums.

The calling code never asks "what flag is model?" — it calls ``set_model("gpt-4o")``
and the adapter translates via the schema::

    launcher = HarnessLauncher.from_harness_row(row)
    launcher.set_model("gpt-4o")
    launcher.set_agent("planner")
    cmd = launcher.build()

Domain model
────────────
- **Concepts** (enums in ``harness_enums``): ExecutionMode, RoleMappingStrategy,
  ArgumentType — these are architectural primitives the orchestrator reasons about.
- **Instances** (rows in ``ai_harnesses``): opencode, codex, ollama, aider, etc.
  — these are concrete harness definitions stored as data in the DB.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from harness_enums import (
    ArgumentType,
    ExecutionMode,
    RoleMappingStrategy,
    parse_argument_type,
    parse_execution_mode,
    parse_role_mapping_strategy,
)


# ── Module-level logger ─────────────────────────────────────────────
_log = logging.getLogger("conduit.harness_launcher")


# ── Schema keys (match the JSON shape stored in invocation_semantics) ──

CAPABILITIES_KEY = "capabilities"
EXECUTION_KEY = "execution"
SEMANTICS_KEY = "semantics"
ROLE_MAPPING_KEY = "role_mapping"
BINARY_KEY = "binary"

# ── Default binary paths ─────────────────────────────────────────────

DEFAULT_BINARIES = {
    "opencode": os.environ.get("OPENCODE_BIN", "/home/codex/.opencode/bin/opencode"),
    "codex": "codex",
    "ollama": "ollama",
}

# ── HarnessLauncher ──────────────────────────────────────────────────


class HarnessLauncher:
    """Build a CLI command from semantic operations.

    Usage::

        launcher = HarnessLauncher.from_harness_row(row)
        launcher.set_model("gpt-4o")
        launcher.set_agent("planner")
        launcher.set_working_directory("/home/codex/dev")
        launcher.set_prompt("Implement feature X")
        cmd = launcher.build()
        # → ["opencode", "run", "--agent", "planner", "--dir", "/home/codex/dev",
        #     "--model", "gpt-4o", "Implement feature X"]
    """

    def __init__(
        self,
        *,
        binary: str,
        capabilities: dict[str, bool] | None = None,
        execution_mode: ExecutionMode = ExecutionMode.INTERACTIVE,
        semantics: dict[str, Any] | None = None,
        role_mapping_strategy: RoleMappingStrategy = RoleMappingStrategy.AGENT,
        execution_data: dict[str, Any] | None = None,
    ) -> None:
        self._binary = binary
        self._capabilities = capabilities or {}
        self._execution_mode = execution_mode
        self._semantics = semantics or {}
        self._role_mapping_strategy = role_mapping_strategy
        self._execution_data = execution_data or {}

        # Accumulated semantic arguments (setters append here)
        self._model: str | None = None
        self._agent: str | None = None
        self._working_directory: str | None = None
        self._system_prompt: str | None = None
        self._prompt: str | None = None
        self._prompt_file_path: str | None = None

        _log.debug("HarnessLauncher.__init__: binary=%s mode=%s role_mapping=%s",
                    binary, execution_mode.value, role_mapping_strategy.value)

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_harness_row(cls, row: dict[str, Any]) -> HarnessLauncher:
        """Construct a launcher from a DB harness row (or dict with same shape).

        Expects keys: ``name`` (or ``id``), ``invocation_semantics`` (JSON str or dict).

        **Backward compatibility:** If the ``invocation_semantics`` contains the old
        flat-flag format (``{"executable": ..., "flags": {...}}``) instead of the new
        semantic schema, this falls back to a hardcoded opencode default.
        """
        semantics_raw = row.get("invocation_semantics") or "{}"
        if isinstance(semantics_raw, str):
            try:
                semantics_data = json.loads(semantics_raw)
            except (json.JSONDecodeError, TypeError):
                semantics_data = {}
        else:
            semantics_data = semantics_raw

        name = row.get("name") or row.get("id", "opencode")
        _log.debug("HarnessLauncher.from_harness_row: name=%s", name)

        # □ Backward compatibility: detect old flat-flag format (has "flags" key, missing "semantics")
        if CAPABILITIES_KEY not in semantics_data and "flags" in semantics_data:
            _log.warning("HarnessLauncher.from_harness_row: old flat-flag format detected name=%s, falling back to defaults", name)
            # Fall back to a fresh opencode default so old DB data doesn't silently break.
            # Uses a factory (not a singleton) so concurrent callers get separate instances.
            return _default_opencode_launcher()

        # Resolve binary path: prefer DEFAULT_BINARIES (full paths) over
        # the semantics-provided bare name (e.g., "opencode" may not be in PATH).
        binary = DEFAULT_BINARIES.get(name) or semantics_data.get(BINARY_KEY, name)

        # Capabilities
        capabilities = semantics_data.get(CAPABILITIES_KEY) or {}

        # Execution mode + subcommand
        exec_section = semantics_data.get(EXECUTION_KEY) or {}
        execution_mode = parse_execution_mode(exec_section.get("mode"))

        # Semantic argument mappings
        semantics_mapping = semantics_data.get(SEMANTICS_KEY) or {}

        # Role mapping strategy
        role_section = semantics_data.get(ROLE_MAPPING_KEY) or {}
        role_strategy = parse_role_mapping_strategy(role_section.get("strategy"))

        _log.debug("HarnessLauncher.from_harness_row: constructed name=%s binary=%s capabilities=%s",
                    name, binary, capabilities)
        return cls(
            binary=binary,
            capabilities=capabilities,
            execution_mode=execution_mode,
            semantics=semantics_mapping,
            role_mapping_strategy=role_strategy,
            execution_data=exec_section,
        )

    # ── Semantic argument setters ────────────────────────────────────

    def set_model(self, model_id: str) -> None:
        """Set the model identifier to pass to the harness."""
        if self._capabilities.get("model", False):
            self._model = model_id
            _log.debug("HarnessLauncher.set_model: model=%s", model_id)

    def set_agent(self, role: str) -> None:
        """Set the agent role for the harness.

        The role is always stored (for prompt file naming, role metadata, etc.)
        even if the harness does not support ``--agent`` as a CLI flag.
        The CLI flag is only emitted in ``_build_role_args()`` when the
        ``agent`` capability is enabled.
        """
        self._agent = role
        _log.debug("HarnessLauncher.set_agent: role=%s", role)

    def set_working_directory(self, path: str) -> None:
        """Set the working directory for the harness."""
        if self._capabilities.get("working_directory", False):
            self._working_directory = path
            _log.debug("HarnessLauncher.set_working_directory: path=%s", path)

    def set_system_prompt(self, prompt: str) -> None:
        """Set the system prompt for the harness.

        The system prompt is always stored (for prompt file writing in
        ``PROMPT_FILE`` strategy) even if the harness does not support
        ``--system-prompt`` as a CLI flag.
        """
        self._system_prompt = prompt
        _log.debug("HarnessLauncher.set_system_prompt: len=%d", len(prompt) if prompt else 0)

    def set_prompt(self, prompt: str) -> None:
        """Set the main task prompt (always stored, no capability check)."""
        self._prompt = prompt
        _log.debug("HarnessLauncher.set_prompt: len=%d", len(prompt) if prompt else 0)

    # ── Private: argument construction ───────────────────────────────

    def _build_arg(self, key: str, value: str) -> list[str]:
        """Translate a semantic key + value into CLI args using the semantics mapping."""
        mapping = self._semantics.get(key)
        if not mapping:
            return []

        arg_type = parse_argument_type(mapping.get("type"))

        if arg_type == ArgumentType.FLAG:
            flag_name = mapping.get("flag", f"--{key}")
            return [flag_name, value]

        if arg_type == ArgumentType.POSITIONAL_AFTER_SUBCOMMAND:
            subcommand = mapping.get("subcommand", "")
            if subcommand:
                return [subcommand, value]
            return [value]

        if arg_type == ArgumentType.ENV_VAR:
            # Environment variables are handled externally (not added to cmd)
            return []

        if arg_type == ArgumentType.CONFIG_FILE:
            # Config file injection is handled externally
            return []

        return []

    def _build_role_args(self) -> list[str]:
        """Build CLI args for role injection based on role_mapping_strategy."""
        if not self._agent:
            return []

        if self._role_mapping_strategy == RoleMappingStrategy.AGENT:
            return self._build_arg("agent", self._agent)

        if self._role_mapping_strategy in (
            RoleMappingStrategy.SYSTEM_FLAG,
            RoleMappingStrategy.PROMPT_FILE,
        ):
            # These are handled differently (system_prompt injection or file writing)
            # The agent role itself might not be a CLI flag — caller handles it.
            return []

        return []

    def _build_execution_prefix(self) -> list[str]:
        """Build the command prefix before semantic arguments.

        Some harnesses use a subcommand for execution::

            opencode run ...
            ollama run ...
            codex exec ...

        The ``subcommand`` is specified in the ``execution`` section of the
        invocation_semantics JSON.
        """
        subcommand = self._execution_data.get("subcommand")
        if subcommand:
            return [subcommand]
        return []

    # ── Build final command ──────────────────────────────────────────

    def build(self) -> list[str]:
        """Assemble the full CLI command from all set semantic arguments.

        Order::

            <binary> [execution_prefix] [agent_args] [dir_args] [model_args] <prompt>
        """
        cmd: list[str] = [self._binary]
        cmd.extend(self._build_execution_prefix())
        cmd.extend(self._build_role_args())

        if self._working_directory:
            cmd.extend(self._build_arg("working_directory", self._working_directory))

        if self._model:
            cmd.extend(self._build_arg("model", self._model))

        if self._system_prompt:
            if self._role_mapping_strategy == RoleMappingStrategy.SYSTEM_FLAG:
                cmd.extend(self._build_arg("system_prompt", self._system_prompt))
                # For PROMPT_FILE, system prompt is written to a file instead

        # ── Final argument: prompt text or prompt file path ──
        if self._role_mapping_strategy == RoleMappingStrategy.PROMPT_FILE:
            if self._prompt_file_path:
                cmd.append(self._prompt_file_path)
            elif self._prompt:
                # Fallback: file wasn't written yet, use inline (unusual but safe)
                cmd.append(self._prompt)
        elif self._prompt:
            cmd.append(self._prompt)

        _log.debug("HarnessLauncher.build: cmd=%s", ' '.join(cmd))
        return cmd

    # ── Prompt file writer (for PROMPT_FILE strategy) ────────────────

    def prepare_role_prompt_file(self, prompts_dir: str | None = None) -> str:
        """Write the role prompt to a file for ``PROMPT_FILE`` strategy harnesses.

        This is used by harnesses like Codex that expect a prompt file path
        on the command line instead of inline prompt text.

        The file path is stored internally so that ``build()`` appends the path
        instead of the raw prompt text.

        Args:
            prompts_dir: Directory to write the prompt file into.
                         Defaults to ``/tmp/.codex-prompts``.

        Returns:
            The absolute path to the written prompt file, or empty string if
            the strategy is not PROMPT_FILE or no prompt was set.
        """
        if self._role_mapping_strategy != RoleMappingStrategy.PROMPT_FILE:
            return ""

        if not self._prompt:
            return ""

        prompts_dir = prompts_dir or "/tmp/.codex-prompts"
        os.makedirs(prompts_dir, exist_ok=True)

        role = self._agent or "default"
        safe_role = re.sub(r"[^a-zA-Z0-9_-]+", "_", role)
        file_path = os.path.join(prompts_dir, f"role_{safe_role}.md")

        with open(file_path, "w", encoding="utf-8") as f:
            if self._system_prompt:
                f.write(self._system_prompt.rstrip())
                f.write("\n\n")
            f.write(self._prompt)

        self._prompt_file_path = file_path
        _log.info("HarnessLauncher.prepare_role_prompt_file: role=%s path=%s", role, file_path)
        print(f"[harness-launcher] Wrote prompt file for role={role}: {file_path}", flush=True)
        return file_path

    # ── Accessors ────────────────────────────────────────────────────

    @property
    def binary(self) -> str:
        return self._binary

    @property
    def execution_mode(self) -> ExecutionMode:
        return self._execution_mode

    @property
    def role_mapping_strategy(self) -> RoleMappingStrategy:
        return self._role_mapping_strategy

    @property
    def capabilities(self) -> dict[str, bool]:
        return dict(self._capabilities)

    def __repr__(self) -> str:
        return (
            f"HarnessLauncher(binary={self._binary!r}, "
            f"execution_mode={self._execution_mode.value!r}, "
            f"role_mapping={self._role_mapping_strategy.value!r})"
        )


# ── Factory: default opencode launcher (backward compatibility) ───


def _default_opencode_launcher() -> HarnessLauncher:
    """Return a fresh HarnessLauncher with opencode defaults.

    Used when old-format harness rows (flat flags) are detected in the DB.
    Always returns a new instance — callers mutate it via ``set_model()`` etc.
    Matches the seed defaults in ``nexus/typescript/conduit-mcp/src/db.ts``.
    """
    _log.debug("_default_opencode_launcher: constructing default opencode launcher")
    return HarnessLauncher(
        binary=DEFAULT_BINARIES["opencode"],
        capabilities={"model": True, "agent": True, "working_directory": True},
        semantics={
            "model": {"type": "flag", "flag": "--model"},
            "agent": {"type": "flag", "flag": "--agent"},
            "working_directory": {"type": "flag", "flag": "--dir"},
        },
        execution_data={"mode": "interactive", "subcommand": "run"},
        # role_mapping_strategy defaults to RoleMappingStrategy.AGENT
    )


# ── Convenience: build launcher from DB config lookup ──────────────


def build_launcher_for_role(role_cfg: dict[str, Any] | None) -> HarnessLauncher:
    """Build a HarnessLauncher from a role config dict.

    The ``role_cfg`` dict should have keys::
        model_identifier, harness_name, invocation_semantics

    Returns a launcher with ``model`` already set if the model ID is available.
    Falls back to a default opencode launcher if no config is found.
    """
    if not role_cfg:
        # No DB config — build a default opencode launcher
        _log.debug("build_launcher_for_role: no role_cfg, using default opencode launcher")
        return HarnessLauncher(
            binary=DEFAULT_BINARIES.get("opencode", "opencode"),
            capabilities={"model": True, "agent": True},
            execution_mode=ExecutionMode.INTERACTIVE,
            semantics={
                "model": {"type": "flag", "flag": "--model"},
                "agent": {"type": "flag", "flag": "--agent"},
            },
            role_mapping_strategy=RoleMappingStrategy.AGENT,
        )

    harness_name = role_cfg.get("harness_name", "opencode")
    _log.debug("build_launcher_for_role: harness=%s", harness_name)
    semantics_raw = role_cfg.get("invocation_semantics") or {}
    launcher = HarnessLauncher.from_harness_row({
        "name": harness_name,
        "invocation_semantics": semantics_raw,
    })

    model_id = role_cfg.get("model_identifier", "")
    if model_id:
        launcher.set_model(model_id)
        _log.debug("build_launcher_for_role: model set to %s", model_id)

    return launcher



