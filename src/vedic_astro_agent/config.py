"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    ayanamsa: str = "LAHIRI"
    tool_backend: str = "local"          # "local" | "mcp"
    mcp_command: str | None = None       # stdio server command line
    mcp_url: str | None = None           # HTTP endpoint of a pyjhora-mcp server
    mcp_api_key: str | None = None
    max_tool_rounds: int = 3

    @classmethod
    def from_env(cls) -> AgentConfig:
        return cls(
            ayanamsa=os.environ.get("VEDIC_AYANAMSA", "LAHIRI").upper(),
            tool_backend=os.environ.get("VEDIC_TOOL_BACKEND", "local").lower(),
            mcp_command=os.environ.get("VEDIC_MCP_COMMAND") or None,
            mcp_url=os.environ.get("VEDIC_MCP_URL") or None,
            mcp_api_key=os.environ.get("VEDIC_MCP_API_KEY") or None,
            max_tool_rounds=int(os.environ.get("VEDIC_MAX_TOOL_ROUNDS", "3")),
        )


def build_backend(config: AgentConfig):
    """Instantiate the tool backend the configuration asks for."""
    if config.tool_backend == "mcp":
        from vedic_astro_agent.tools.mcp_backend import McpToolBackend

        return McpToolBackend(command=config.mcp_command, url=config.mcp_url,
                              api_key=config.mcp_api_key)
    from vedic_astro_agent.agent.astrologer import _LocalBackend

    return _LocalBackend()
