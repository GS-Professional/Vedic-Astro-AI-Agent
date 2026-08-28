"""Grounding validator tests — the anti-hallucination gate."""
from vedic_astro_agent.agent.evidence import (
    ChartFacts,
    EvidenceLedger,
    GroundingValidator,
    extract_facts,
)


def _facts() -> ChartFacts:
    ledger = EvidenceLedger()
    ledger.record("get_rasi_chart", {}, {
        "ascendant": {"rasi": "Capricorn", "rasi_index": 9},
        "planets": [
            {"planet": "Sun", "rasi": "Scorpio", "house": 11},
            {"planet": "Moon", "rasi": "Libra", "house": 10, "nakshatra": "Swati"},
            {"planet": "Saturn", "rasi": "Aquarius", "house": 2},
        ],
    })
    ledger.record("get_running_dasha", {}, {
        "running": {"Maha Dasha": {"lord": "Jupiter"}, "Antar Dasha": {"lord": "Moon"}}})
    ledger.record("get_compatibility", {}, {"total_score": 24.0, "maximum_score": 36})
    ledger.record("get_doshas", {}, {"present": ["manglik"]})
    return extract_facts(ledger)


def test_fact_extraction():
    facts = _facts()
    assert facts.ascendant_sign == "Capricorn"
    assert facts.planet_signs["Sun"] == "Scorpio"
    assert facts.moon_sign == "Libra"
    assert facts.moon_nakshatra == "Swati"
    assert facts.running_periods["Maha Dasha"] == "Jupiter"
    assert facts.compatibility_total == 24.0
    assert facts.doshas_present == ["manglik"]


def test_correct_claims_pass():
    v = GroundingValidator()
    text = ("Your Capricorn ascendant rises with Sun in Scorpio and the Moon in Libra. "
            "You are currently running Jupiter Mahadasha. The pair scored 24.0/36.")
    report = v.validate(text, _facts())
    assert report.ok, report.violations
    assert report.claims_checked >= 4


def test_wrong_planet_sign_caught():
    v = GroundingValidator()
    text = "With Sun in Aries and Moon in Libra you have an intense chart."
    report = v.validate(text, _facts())
    assert not report.ok
    assert any("Sun in Aries" in violation for violation in report.violations)
    assert any("Scorpio" in violation for violation in report.violations)


def test_wrong_ascendant_caught():
    v = GroundingValidator()
    report = v.validate("As a Scorpio ascendant native, you are driven.", _facts())
    assert not report.ok
    assert "Capricorn" in report.violations[0]


def test_wrong_running_dasha_caught():
    v = GroundingValidator()
    report = v.validate("You are now in Saturn Mahadasha, a testing phase.", _facts())
    assert not report.ok


def test_wrong_score_caught():
    v = GroundingValidator()
    report = v.validate("Your compatibility stands at 32/36.", _facts())
    assert not report.ok


def test_unverifiable_claims_are_not_violations():
    """Claims about facts never computed must not pass silently as violations either —
    they are simply not checkable (the planner ensures the computation happens first)."""
    v = GroundingValidator()
    facts = ChartFacts()  # empty ledger
    report = v.validate("Sun in Aries, Leo ascendant, running Venus Mahadasha, score 12/36.",
                        facts)
    assert report.ok  # nothing computed => nothing contradicted
    assert report.claims_checked >= 4


def test_error_records_are_ignored():
    ledger = EvidenceLedger()
    ledger.record("get_rasi_chart", {}, None, error="ephe missing")
    facts = extract_facts(ledger)
    assert facts.ascendant_sign is None
