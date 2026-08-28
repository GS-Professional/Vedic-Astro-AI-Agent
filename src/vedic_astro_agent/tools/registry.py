"""Tool registry and the backend interface the whole agent is built on.

A *tool* is a named computation with a JSON-schema argument description. The planner
sees the catalog (names + descriptions + schemas) to decide what to compute; the
executor calls tools through a :class:`ToolBackend`. Keeping catalog and backend
separate is what lets the same agent run against the bundled in-process PyJHora backend
or a remote ``pyjhora-mcp`` server without any change upstream of this file.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ToolCallError(RuntimeError):
    """A tool call failed. The message is surfaced to the planner's reflection step."""


@dataclass(frozen=True)
class ToolSpec:
    """Metadata for one tool, in the shape LLM tool-calling APIs expect."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    handler: Callable[..., Any] = field(repr=False, default=None)  # type: ignore[assignment]

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolCatalog:
    """An ordered collection of ToolSpecs."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def add(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name!r}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise ToolCallError(
                f"Unknown tool {name!r}. Available: {', '.join(sorted(self._tools))}"
            )
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def manifest(self) -> list[dict[str, Any]]:
        """Name + description + schema for every tool — what the planner reads."""
        return [spec.as_llm_tool() for spec in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


class ToolBackend:
    """Interface every backend implements: list tools, call a tool by name."""

    name: str = "backend"

    def list_tools(self) -> ToolCatalog:
        raise NotImplementedError

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


def build_default_catalog() -> ToolCatalog:
    """The local PyJHora-backed catalog (the default backend)."""
    from vedic_astro_agent.tools.local_backend import build_local_catalog

    return build_local_catalog()
