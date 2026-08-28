"""McpToolBackend protocol tests against a mock stdio MCP server.

The mock speaks exactly the MCP dialect the real pyjhora-mcp server (FastMCP) uses, so
these tests prove the client can adopt a remote catalog and unwrap tools/call results.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from vedic_astro_agent.tools.mcp_backend import McpToolBackend
from vedic_astro_agent.tools.registry import ToolCallError

MOCK = pathlib.Path(__file__).parent / "mock_mcp_server.py"


@pytest.fixture()
def backend():
    command = f"{sys.executable} {MOCK}"
    b = McpToolBackend(command=command)
    yield b
    b.close()


def test_catalog_adopted_from_server(backend):
    catalog = backend.list_tools()
    assert "get_rasi_chart" in catalog
    spec = catalog.get("get_rasi_chart")
    assert spec.description == "Mock rasi chart"
    # Manifest is LLM-tool-call shaped, same as the local backend's.
    manifest = catalog.manifest()
    assert manifest[0]["type"] == "function"
    assert manifest[0]["function"]["name"] == "get_rasi_chart"


def test_call_round_trip(backend):
    result = backend.call("get_rasi_chart", {})
    assert result == {"ascendant": {"rasi": "Capricorn"}}


def test_server_side_error_surfaces(backend):
    with pytest.raises(ToolCallError):
        backend.call("get_karma", {})


def test_unknown_tool_on_client_side(backend):
    with pytest.raises(ToolCallError):
        backend.call("not_even_listed", {})
