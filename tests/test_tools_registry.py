"""Tool catalog / backend contract tests."""
import pytest

from vedic_astro_agent.tools.mcp_backend import McpToolBackend
from vedic_astro_agent.tools.registry import ToolCallError, build_default_catalog


def test_local_catalog_covers_pyjhora_mcp_surface():
    """The local catalog mirrors the pyjhora-mcp tool surface."""
    catalog = build_default_catalog()
    expected = {
        "get_rasi_chart", "get_divisional_chart", "get_special_lagnas", "get_ashtakavarga",
        "get_panchanga", "get_sunrise_sunset", "get_rahu_kala", "get_muhurtha",
        "get_planet_positions", "get_retrograde_planets",
        "get_vimsottari_dasha", "get_yogini_dasha", "get_ashtottari_dasha",
        "get_running_dasha", "get_compatibility",
        "get_yogas", "get_doshas", "get_raja_yogas",
        "get_shadbala", "get_bhava_bala", "get_vimsopaka_bala",
    }
    assert expected <= set(catalog.names())


def test_manifest_shape_for_llm_tool_calling():
    catalog = build_default_catalog()
    for tool in catalog.manifest():
        assert tool["type"] == "function"
        fn = tool["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"


def test_unknown_tool_raises():
    catalog = build_default_catalog()
    with pytest.raises(ToolCallError):
        catalog.get("get_karma")


def test_invalid_birth_arguments_raise_tool_error():
    catalog = build_default_catalog()
    spec = catalog.get("get_rasi_chart")
    with pytest.raises(ToolCallError):
        spec.handler({"birth_data": {"place": {"name": "X"}}})  # missing coordinates


def test_mcp_backend_requires_endpoint():
    with pytest.raises(ToolCallError):
        McpToolBackend()
