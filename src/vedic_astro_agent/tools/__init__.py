"""Tool layer: the only way the agent touches astrology data.

Two interchangeable backends implement the same interface:

* ``LocalToolBackend`` — runs PyJHora in-process (default; no server needed).
* ``McpToolBackend``  — routes every call to a pyjhora-mcp server
  (https://github.com/chinmay-sh/pyjhora-mcp) over stdio or HTTP.

The planner, executor and interpreter never know which one is underneath.
"""

from vedic_astro_agent.tools.registry import (
    ToolBackend,
    ToolCallError,
    ToolCatalog,
    ToolSpec,
    build_default_catalog,
)

__all__ = ["ToolBackend", "ToolCallError", "ToolCatalog", "ToolSpec", "build_default_catalog"]
