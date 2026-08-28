"""Executes a plan against a tool backend, appending every result to the ledger.

The executor never inspects or rewrites results — what the computation layer returns
is exactly what lands in the evidence ledger, so downstream interpretation stays
traceable to the computation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from vedic_astro_agent.agent.evidence import EvidenceLedger
from vedic_astro_agent.agent.planner import AgentPlan
from vedic_astro_agent.tools.registry import ToolBackend, ToolCallError


@dataclass
class StepTrace:
    tool: str
    reason: str
    ok: bool
    duration_ms: float
    error: str | None = None
    evidence_id: str = ""


@dataclass
class ExecutionTrace:
    steps: list[StepTrace] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.steps if s.ok)

    @property
    def failures(self) -> list[StepTrace]:
        return [s for s in self.steps if not s.ok]

    def summary(self) -> str:
        return (f"{self.ok_count}/{len(self.steps)} tool calls succeeded"
                + ("" if not self.failures else
                   "; failed: " + ", ".join(f"{s.tool} ({s.error})" for s in self.failures)))


class PlanExecutor:
    """Runs plan steps in order; a failing step is recorded and skipped, not fatal."""

    def __init__(self, backend: ToolBackend):
        self._backend = backend

    def execute(self, plan: AgentPlan, ledger: EvidenceLedger) -> ExecutionTrace:
        trace = ExecutionTrace()
        for step in plan.steps:
            started = time.perf_counter()
            try:
                result = self._backend.call(step.tool, step.arguments)
            except ToolCallError as exc:
                rec = ledger.record(step.tool, step.arguments, None,
                                    reason=step.reason, error=str(exc))
                trace.steps.append(StepTrace(
                    tool=step.tool, reason=step.reason, ok=False, error=str(exc),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    evidence_id=rec.evidence_id))
                continue
            rec = ledger.record(step.tool, step.arguments, result, reason=step.reason)
            trace.steps.append(StepTrace(
                tool=step.tool, reason=step.reason, ok=True,
                duration_ms=(time.perf_counter() - started) * 1000,
                evidence_id=rec.evidence_id))
        return trace


def build_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Identity helper kept for symmetry with future backends that need argument shaping."""
    return arguments
