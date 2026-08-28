"""LLM client interface."""
from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Anything that can turn a (system, user) prompt pair into text."""

    def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        ...


class StubLLM:
    """Deterministic stand-in used in tests: returns the draft unchanged."""

    def __init__(self, reply: str | None = None):
        self._reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls.append((system, user))
        if self._reply is not None:
            return self._reply
        # Echo the draft back — a perfectly grounded "polish".
        return user.split("Draft reading:\n", 1)[-1].split("\n\nEvidence trail:", 1)[0]
