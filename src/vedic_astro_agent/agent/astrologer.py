"""The agentic orchestrator.

One ``ask()`` runs the full loop:

    understand → plan → compute → reflect → interpret → validate → answer

* *understand*: intents, topics, birth data (extracted or supplied).
* *plan*: the deterministic planner emits tool calls with written reasons; if inputs are
  missing the agent answers with a clarification question instead of guessing.
* *compute*: every tool call goes through the configured backend (local PyJHora or a
  pyjhora-mcp server) and is appended to the evidence ledger.
* *reflect*: if the anchor computation (D1 chart) failed, the agent replans once with
  corrected inputs where possible rather than producing an ungrounded reading.
* *interpret*: prose is composed from ledger evidence + traditional knowledge tables;
  when an LLM is configured it may polish the draft, but...
* *validate*: the grounding validator re-checks every factual claim against the computed
  facts, and any violation forces a fallback to the deterministic draft. Nothing
  ungrounded is ever released.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from vedic_astro_agent.agent.evidence import (
    EvidenceLedger,
    GroundingValidator,
    extract_facts,
)
from vedic_astro_agent.agent.executor import ExecutionTrace, PlanExecutor
from vedic_astro_agent.agent.interpreter import Interpreter
from vedic_astro_agent.agent.planner import AgentPlan, Planner
from vedic_astro_agent.computation.models import BirthData
from vedic_astro_agent.llm.base import LLMClient
from vedic_astro_agent.tools.registry import ToolBackend, build_default_catalog


@dataclass
class AgentResponse:
    """Everything one question produced."""

    query: str
    reading: str
    plan: AgentPlan
    ledger: EvidenceLedger
    trace: ExecutionTrace
    grounding_ok: bool
    grounding_summary: str
    clarifying_question: str | None = None
    llm_used: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.clarifying_question is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "clarifying_question": self.clarifying_question,
            "reading": self.reading,
            "plan": {
                "intents": self.plan.understanding.intents,
                "topics": self.plan.understanding.topics,
                "steps": [{"tool": s.tool, "reason": s.reason} for s in self.plan.steps],
            },
            "execution": {
                "tool_calls": [
                    {"evidence_id": st.evidence_id, "tool": st.tool, "ok": st.ok,
                     "duration_ms": round(st.duration_ms, 1), "error": st.error}
                    for st in self.trace.steps
                ],
                "summary": self.trace.summary(),
            },
            "evidence_count": len(self.ledger),
            "grounding": {"ok": self.grounding_ok, "summary": self.grounding_summary},
            "llm_used": self.llm_used,
            "warnings": self.warnings,
        }


class VedicAstrologerAgent:
    """The agent. Give it a tool backend; optionally an LLM for planning polish."""

    def __init__(self, backend: ToolBackend | None = None,
                 llm: LLMClient | None = None,
                 max_reflection_rounds: int = 2,
                 reference_date: date | None = None):
        self._backend = backend or _LocalBackend()
        self._planner = Planner(reference_date=reference_date)
        self._executor = PlanExecutor(self._backend)
        self._interpreter = Interpreter()
        self._validator = GroundingValidator()
        self._llm = llm
        self._max_rounds = max(1, max_reflection_rounds)

    @property
    def tool_catalog(self):
        return self._backend.list_tools()

    # ------------------------------------------------------------------

    def ask(self, query: str, birth_data: BirthData | None = None,
            second_birth_data: BirthData | None = None,
            reference_date: date | None = None) -> AgentResponse:
        ledger = EvidenceLedger()
        warnings: list[str] = []

        understanding = self._planner.understand(
            query, birth_data=birth_data, second_birth_data=second_birth_data,
            reference_date=reference_date)

        plan = self._planner.plan(understanding)
        if plan.needs_clarification:
            empty_trace = ExecutionTrace()
            return AgentResponse(query=query, reading="", plan=plan, ledger=ledger,
                                 trace=empty_trace, grounding_ok=True,
                                 grounding_summary="no computation performed yet",
                                 clarifying_question=plan.clarification_question())

        trace = self._executor.execute(plan, ledger)

        # Reflection: when the plan relied on the D1 chart it anchors every other claim.
        # Without it there is no ground truth for the reading — better to report the
        # failure than improvise. (Plans that never ask for D1 skip this entirely.)
        rounds = 1
        while ("get_rasi_chart" in plan.tool_names()
               and not ledger.first_result("get_rasi_chart")
               and rounds < self._max_rounds):
            rounds += 1
            warnings.append("D1 chart computation failed on round "
                            f"{rounds - 1}; retrying with the LAHIRI default ayanamsa")
            if understanding.birth_data is not None:
                understanding.birth_data = BirthData(
                    understanding.birth_data.place, understanding.birth_data.date_time,
                    "LAHIRI")
            plan = self._planner.plan(understanding)
            trace = self._executor.execute(plan, ledger)

        reading = self._interpreter.interpret(plan, ledger)
        llm_used = False

        if self._llm is not None and ledger.first_result("get_rasi_chart"):
            polished = self._llm_polish(query, reading, ledger)
            if polished is not None:
                facts = extract_facts(ledger)
                report = self._validator.validate(polished, facts)
                if report.ok:
                    reading = polished
                    llm_used = True
                else:
                    warnings.append(
                        "LLM draft failed grounding (" + report.summary()
                        + "); deterministic evidence-grounded reading used instead")

        facts = extract_facts(ledger)
        report = self._validator.validate(reading, facts)

        return AgentResponse(query=query, reading=reading, plan=plan, ledger=ledger,
                             trace=trace, grounding_ok=report.ok,
                             grounding_summary=report.summary(), llm_used=llm_used,
                             warnings=warnings)

    # ------------------------------------------------------------------

    def _llm_polish(self, query: str, draft: str, ledger: EvidenceLedger) -> str | None:
        """Ask the LLM to improve the draft without altering facts; None on any error."""
        evidence_lines = [rec.summary() for rec in ledger.records]
        system = (
            "You are a Vedic astrology editor. Rewrite the draft reading below into warm, "
            "fluent prose. HARD RULES: never change any computed fact — planet signs, "
            "houses, dasha lords, scores, dates must stay exactly as written; never add "
            "any astrology claim that is not already in the draft; keep every number "
            "verbatim. Output only the rewritten reading."
        )
        user = ("Draft reading:\n" + draft + "\n\nEvidence trail:\n"
                + "\n".join(evidence_lines) + "\n\nQuery: " + query)
        try:
            return self._llm.complete(system=system, user=user)
        except Exception:  # the loop must survive any LLM hiccup
            return None

    def close(self) -> None:
        self._backend.close()


class _LocalBackend(ToolBackend):
    """Lazy default backend: the local PyJHora catalog."""

    name = "local"

    def __init__(self) -> None:
        self._catalog = None

    def list_tools(self):
        if self._catalog is None:
            self._catalog = build_default_catalog()
        return self._catalog

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        spec = self.list_tools().get(tool_name)
        return spec.handler(arguments)
