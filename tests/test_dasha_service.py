"""Dasha computation tests — structure, totals, seeding and running-period logic."""

from tests.conftest import requires_ephe
from vedic_astro_agent.computation.models import DateTimeSpec

VIMSOTTARI_YEARS = {"Sun": 6, "Moon": 10, "Mars": 7, "Rahu": 18, "Jupiter": 16,
                    "Saturn": 19, "Mercury": 17, "Ketu": 7, "Venus": 20}


@requires_ephe
def test_vimsottari_structure_and_total(chennai_birth):
    from vedic_astro_agent.computation import dasha_service

    result = dasha_service.vimsottari_dasha(chennai_birth, depth=1)
    periods = result["periods"]
    assert len(periods) == 9
    total = round(sum(p["duration_years"] for p in periods))
    assert total == 120
    # Each Mahadasha length matches the classical table.
    for p in periods:
        assert round(p["duration_years"]) == VIMSOTTARI_YEARS[p["lord"]]
    # Balance at birth is a sensible remainder of the first dasha.
    balance = result["balance_at_birth"]
    assert 0 <= balance["years"] <= VIMSOTTARI_YEARS[periods[0]["lord"]]


@requires_ephe
def test_vimsottari_seed_matches_moon_nakshatra(chennai_birth):
    """Moon in Swati (Rahu's nakshatra) => the dasha sequence starts with Rahu."""
    from vedic_astro_agent.computation import chart_service, dasha_service

    rasi = chart_service.rasi_chart(chennai_birth)
    moon = next(p for p in rasi["planets"] if p["planet"] == "Moon")
    assert moon["nakshatra"] == "Swati"

    result = dasha_service.vimsottari_dasha(chennai_birth, depth=1)
    assert result["periods"][0]["lord"] == "Rahu"


@requires_ephe
def test_nested_depth_two(chennai_birth):
    from vedic_astro_agent.computation import dasha_service

    result = dasha_service.vimsottari_dasha(chennai_birth, depth=2)
    assert result["period_count"] == 81
    first_md = result["periods"][0]
    assert len(first_md["sub_periods"]) == 9
    # Antara durations roll up to their Mahadasha length.
    assert abs(sum(s["duration_years"] for s in first_md["sub_periods"])
               - first_md["duration_years"]) < 0.01
    # Chronology: each period starts when the previous one ends.
    for prev, nxt in zip(result["periods"], result["periods"][1:]):
        assert prev["end"] == nxt["start"]


@requires_ephe
def test_running_dasha_brackets_target_date(chennai_birth):
    from vedic_astro_agent.computation import dasha_service

    # Birth Dec-1996, Rahu MD first (~balance of a few years) then Jupiter's 16 years:
    # by 2026 the native must be inside Jupiter Mahadasha.
    running = dasha_service.running_dasha(chennai_birth, DateTimeSpec(2026, 8, 27))
    assert running["running"] is not None
    assert running["running"]["Maha Dasha"]["lord"] == "Jupiter"
    assert "Antar Dasha" in running["running"]
    assert "Pratyantar Dasha" in running["running"]


@requires_ephe
def test_running_dasha_at_birth_is_first_lord(chennai_birth):
    from vedic_astro_agent.computation import dasha_service

    running = dasha_service.running_dasha(
        chennai_birth,
        DateTimeSpec(chennai_birth.date_time.year, chennai_birth.date_time.month,
                     chennai_birth.date_time.day))
    assert running["running"]["Maha Dasha"]["lord"] == "Rahu"


@requires_ephe
def test_yogini_and_ashtottari(chennai_birth):
    from vedic_astro_agent.computation import dasha_service

    yog = dasha_service.yogini_dasha(chennai_birth, depth=1)
    # PyJHora emits three full 36-year cycles (8 lords x 3 = 24 periods).
    assert yog["period_count"] == 24
    assert abs(sum(p["duration_years"] for p in yog["periods"]) - 108) < 0.5
    # Each 8-lord cycle sums to 36 years.
    assert abs(sum(p["duration_years"] for p in yog["periods"][:8]) - 36) < 0.5

    ash = dasha_service.ashtottari_dasha(chennai_birth, depth=1)
    assert ash["period_count"] == 8
    assert abs(sum(p["duration_years"] for p in ash["periods"]) - 108) < 0.5
    assert isinstance(ash["applicable"], bool)
