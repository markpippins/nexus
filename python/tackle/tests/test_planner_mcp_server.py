"""Tests for planner_mcp_server's env-driven transport configuration.

Two callers share this module:

- ``planner.py`` imports it in-process and calls its ``@mcp.tool`` functions
  directly — the transport is never started, so the stdio default must stay.
- the systemd unit runs it as a standalone daemon. A stdio server under
  systemd reads stdin EOF and exits in under a second, so the unit must be
  able to select SSE (host + port) without patching code.

These tests pin the default and the override; the daemon itself is verified
operationally against the running unit.
"""

import importlib

import pytest

import tackle.planner_mcp_server as server

ENV_KEYS = ("PLANNER_MCP_TRANSPORT", "PLANNER_MCP_HOST", "PLANNER_MCP_PORT")


@pytest.fixture(autouse=True)
def _restore_module_defaults(monkeypatch):
    """Leave the module in its import-time default state for other tests."""
    yield
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(server)


def _reload(monkeypatch, **env):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(server)


def test_defaults_stay_stdio_for_in_process_import(monkeypatch):
    mod = _reload(monkeypatch)
    assert mod.TRANSPORT == "stdio"
    assert mod.HOST == "127.0.0.1"
    assert mod.PORT == 8000


def test_sse_transport_is_env_selectable(monkeypatch):
    mod = _reload(
        monkeypatch,
        PLANNER_MCP_TRANSPORT="sse",
        PLANNER_MCP_HOST="127.0.0.1",
        PLANNER_MCP_PORT="3119",
    )
    assert mod.TRANSPORT == "sse"
    assert mod.HOST == "127.0.0.1"
    assert mod.PORT == 3119


def test_server_keeps_its_registered_name(monkeypatch):
    mod = _reload(monkeypatch, PLANNER_MCP_TRANSPORT="sse", PLANNER_MCP_PORT="3119")
    assert mod.mcp.name == "planner-mcp"
