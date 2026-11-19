#!/usr/bin/env python3
"""harness_enums.py — Architectural concepts for the harness adapter layer.

These are the concepts the orchestrator reasons about when building CLI commands.
Instances (codex, opencode, ollama, etc.) are stored in the database as harness rows
with enums as their attribute values.

Rule of thumb:
  - Concepts the orchestrator reasons about → enum/code.
  - Instances of those concepts           → database.
"""

import logging
from enum import Enum

_log = logging.getLogger("conduit.harness_enums")


class ExecutionMode(Enum):
    """How the harness binary is launched and managed."""

    ONESHOT = "oneshot"
    """Single invocation: binary runs once with task as argument, exits when done.
       Examples: codex exec <prompt>, aider --message <task>"""

    INTERACTIVE = "interactive"
    """Persistent session: binary opens an interactive subprocess with stdin/stdout.
       Examples: opencode run --agent planner <prompt>"""

    DAEMON = "daemon"
    """Long-running server: binary is started as a background service, then queried
       via API/HTTP. Examples: ollama serve, vllm serve"""


class RoleMappingStrategy(Enum):
    """How the agent role is communicated to the harness."""

    AGENT = "agent"
    """Role is passed as a named CLI flag like --agent <role>.
       Examples: opencode --agent planner, aider --architect"""

    PROMPT_FILE = "prompt_file"
    """Role is injected by writing a role-specific prompt file and passing it.
       Examples: codex exec <prompt_file>"""

    SYSTEM_FLAG = "system_flag"
    """Role is injected as a system prompt via a CLI flag.
       Examples: some CLI with --system-prompt <role_prompt>"""

    NONE = "none"
    """Harness does not support role isolation. The calling code handles it."""


class ArgumentType(Enum):
    """How a semantic argument (model, working_directory, etc.) maps to CLI syntax."""

    FLAG = "flag"
    """Named option: --<name> <value>. Example: --model gpt-4o"""

    POSITIONAL_AFTER_SUBCOMMAND = "positional_after_subcommand"
    """Positional argument that follows a subcommand.
       Example: ollama run <model> — model is positional after "run" """

    ENV_VAR = "env_var"
    """Argument is passed via environment variable.
       Example: OPENAI_API_KEY=sk-..."""

    CONFIG_FILE = "config_file"
    """Argument is written to a config file and the harness reads it.
       Example: .claude/settings.json"""


class HarnessCapability(Enum):
    """Capabilities a harness may support."""

    MODEL = "model"
    AGENT = "agent"
    WORKING_DIRECTORY = "working_directory"
    SYSTEM_PROMPT = "system_prompt"


# ── Lookup helpers (for the DB → enum bridge) ───────────────────────


def parse_execution_mode(value: str | None) -> ExecutionMode:
    """Parse an execution mode string from the database into an enum."""
    if not value:
        _log.debug("parse_execution_mode: None/empty, returning INTERACTIVE")
        return ExecutionMode.INTERACTIVE  # safe default
    try:
        mode = ExecutionMode(value.lower())
        _log.debug("parse_execution_mode: %s → %s", value, mode)
        return mode
    except ValueError:
        _log.warning("parse_execution_mode: unknown value '%s', returning INTERACTIVE", value)
        return ExecutionMode.INTERACTIVE


def parse_role_mapping_strategy(value: str | None) -> RoleMappingStrategy:
    """Parse a role mapping strategy string from the database into an enum."""
    if not value:
        _log.debug("parse_role_mapping_strategy: None/empty, returning AGENT")
        return RoleMappingStrategy.AGENT  # safe default
    try:
        strategy = RoleMappingStrategy(value.lower())
        _log.debug("parse_role_mapping_strategy: %s → %s", value, strategy)
        return strategy
    except ValueError:
        _log.warning("parse_role_mapping_strategy: unknown value '%s', returning AGENT", value)
        return RoleMappingStrategy.AGENT


def parse_argument_type(value: str | None) -> ArgumentType:
    """Parse an argument type string from the database into an enum."""
    if not value:
        _log.debug("parse_argument_type: None/empty, returning FLAG")
        return ArgumentType.FLAG  # safe default
    try:
        arg_type = ArgumentType(value.lower())
        _log.debug("parse_argument_type: %s → %s", value, arg_type)
        return arg_type
    except ValueError:
        _log.warning("parse_argument_type: unknown value '%s', returning FLAG", value)
        return ArgumentType.FLAG
