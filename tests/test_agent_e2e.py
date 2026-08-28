"""End-to-end agent tests: plan → compute → interpret → validate."""
from datetime import date

from tests.conftest import requires_ephe
from vedic_astro_agent.agent.astrologer import VedicAstrologerAgent
from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place
from vedic_astro_agent.llm.base import StubLLM

REF = date(2026, 8, 27)


def _birth() -> BirthData:
    return BirthData(Place("Chennai, IN", 13.0827, 80.2707, 5.5),
                     DateTimeSpec(1996, 12, 7, 10, 30))


@requires_ephe
def test_career_question_end_to_end():
    agent = VedicAstrologerAgent(reference_date=REF)
    response = agent.ask("What does my chart say about my career?", birth_data=_birth())

    assert response.ok
    assert response.clarifying_question is None
    # Plan executed fully.
    assert len(response.trace.failures) == 0, response.trace.summary()
    assert "get_rasi_chart" in response.plan.tool_names()
    assert "get_divisional_chart" in response.plan.tool_names()  # D10 for career
    # Reading is grounded and mentions computed facts.
    assert response.grounding_ok, response.grounding_summary
    assert "Capricorn" in response.reading  # computed ascendant
    assert "Jupiter Mahadasha" in response.reading  # running MD as of REF
    # Evidence trail is attached.
    assert len(response.ledger) >= 5
    assert "Evidence" in response.reading


@requires_ephe
def test_missing_birth_data_asks_clarifying_question():
    agent = VedicAstrologerAgent(reference_date=REF)
    response = agent.ask("When will I get married?")
    assert not response.ok
    assert response.clarifying_question
    assert "date of birth" in response.clarifying_question
    assert len(response.ledger) == 0  # nothing computed without exact inputs


@requires_ephe
def test_overview_question_mentions_yogas_and_doshas():
    agent = VedicAstrologerAgent(reference_date=REF)
    response = agent.ask("Give me an overview of my life", birth_data=_birth())
    assert response.grounding_ok
    # Reference chart has Manglik + Pitru doshas computed — the reading must reflect it.
    assert "manglik" in response.reading.lower()
    assert "Yogas found" in response.reading


@requires_ephe
def test_compatibility_end_to_end():
    agent = VedicAstrologerAgent(reference_date=REF)
    second = BirthData(Place("Mumbai, IN", 19.076, 72.8777, 5.5),
                       DateTimeSpec(1997, 3, 15, 8, 0))
    response = agent.ask("Are we compatible for marriage?",
                         birth_data=_birth(), second_birth_data=second)
    assert response.ok
    assert response.grounding_ok
    assert "/36" in response.reading
    assert "Compatibility" in response.reading


@requires_ephe
def test_llm_polish_gate_accepts_grounded_draft():
    """A faithful LLM polish passes the grounding gate."""
    agent = VedicAstrologerAgent(llm=StubLLM(), reference_date=REF)
    response = agent.ask("Tell me about my career", birth_data=_birth())
    assert response.llm_used
    assert response.grounding_ok


@requires_ephe
def test_llm_polish_gate_rejects_hallucination():
    """An LLM that invents facts is overruled by the validator."""
    lying_llm = StubLLM(reply=("You have Leo ascendant with Sun in Aries, and you are "
                               "currently running Saturn Mahadasha. Score 35/36."))
    agent = VedicAstrologerAgent(llm=lying_llm, reference_date=REF)
    response = agent.ask("Tell me about my career", birth_data=_birth())
    assert not response.llm_used  # hallucinated draft rejected
    assert response.grounding_ok  # deterministic fallback released instead
    assert any("grounding" in w.lower() for w in response.warnings)
    assert "Capricorn" in response.reading


def test_to_dict_shape():
    """Smoke test for the JSON trace used by `veda ask --json` (no ephe needed)."""
    agent = VedicAstrologerAgent(reference_date=REF)
    response = agent.ask("career?", birth_data=None)
    payload = response.to_dict()
    assert payload["clarifying_question"]
    assert payload["plan"]["steps"] == []
