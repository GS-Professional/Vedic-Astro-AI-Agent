"""Yoga / dosha / strength computation tests."""
from tests.conftest import requires_ephe


@requires_ephe
def test_yogas_run_all_rules(chennai_birth):
    from vedic_astro_agent.computation import yoga_service

    result = yoga_service.yogas(chennai_birth)
    assert result["checked"] >= 250  # PyJHora 4.8.7 implements 284 rules
    assert result["count"] <= result["checked"]
    assert len(result["yogas"]) == result["count"]
    for yoga in result["yogas"]:
        assert yoga["name"]
        assert "<" not in yoga["condition"]  # HTML stripped


@requires_ephe
def test_doshas_full_report(chennai_birth):
    from vedic_astro_agent.computation import yoga_service

    result = yoga_service.doshas(chennai_birth)
    expected = {"kala_sarpa", "manglik", "pitru", "guru_chandala", "ganda_moola",
                "kalathra", "ghata", "shrapit"}
    assert set(result["doshas"]) == expected
    for info in result["doshas"].values():
        assert isinstance(info["present"], bool)
        assert info["description"]
    assert all(k in result["doshas"] for k in result["present"])
    # Reference chart: Mars is in the 7th house => Manglik must be flagged.
    assert "manglik" in result["present"]


@requires_ephe
def test_raja_yogas(chennai_birth):
    from vedic_astro_agent.computation import yoga_service

    result = yoga_service.raja_yogas(chennai_birth)
    assert result["checked"] > 0
    assert "raja_yoga_planet_pairs" in result
    for pair in result["raja_yoga_planet_pairs"]:
        assert len(pair) == 2


@requires_ephe
def test_shadbala(chennai_birth):
    from vedic_astro_agent.computation import strength_service

    result = strength_service.shadbala(chennai_birth)
    planets = result["shadbala"]
    assert set(planets) == {"Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"}
    for info in planets.values():
        assert info["total_rupas"] > 0
        assert info["total_virupas"] > 0
        assert 0 < info["minimum_required_rupas"] <= 7
        # ratio = rupas / minimum
        assert abs(info["strength_ratio"]
                   - round(info["total_rupas"] / info["minimum_required_rupas"], 2)) <= 0.02
    assert len(result["strongest_to_weakest"]) == 7


@requires_ephe
def test_bhava_bala(chennai_birth):
    from vedic_astro_agent.computation import strength_service

    result = strength_service.bhava_bala(chennai_birth)
    assert len(result["bhava_bala"]) == 12
    assert sorted(result["strongest_to_weakest"]) == list(range(1, 13))
    # House 1 of the reference chart is Capricorn (ascendant rasi).
    assert result["bhava_bala"][0]["rasi"] == "Capricorn"


@requires_ephe
def test_vimsopaka_bala(chennai_birth):
    from vedic_astro_agent.computation import strength_service

    result = strength_service.vimsopaka_bala(chennai_birth)
    groups = result["vimsopaka_bala"]
    assert set(groups) == {"shad_varga", "sapta_varga", "dasa_varga", "shodasa_varga"}
    for group in groups.values():
        assert len(group["planets"]) >= 7
        for score in group["planets"].values():
            assert 0 <= score["score_out_of_20"] <= 20
