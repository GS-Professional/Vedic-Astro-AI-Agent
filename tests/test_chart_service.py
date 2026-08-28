"""Golden-value tests for the chart computation layer.

The reference chart (Chennai 1996-12-07 10:30 IST) was independently verified with a
raw swisseph calculation (Lahiri ayanamsa): ascendant 291.35 absolute = Capricorn 21.3,
Sun ~Scorpio 21, Moon ~Libra 6-7.
"""
from tests.conftest import requires_ephe


@requires_ephe
def test_rasi_chart_golden(chennai_birth):
    from vedic_astro_agent.computation import chart_service

    result = chart_service.rasi_chart(chennai_birth)
    asc = result["ascendant"]
    assert asc["rasi"] == "Capricorn"
    assert abs(asc["longitude_in_rasi"] - 21.35) < 0.75  # Lahiri value tolerance

    planets = {p["planet"]: p for p in result["planets"]}
    assert planets["Sun"]["rasi"] == "Scorpio"
    assert planets["Moon"]["rasi"] == "Libra"
    assert planets["Saturn"]["rasi"] == "Pisces"   # sidereal ~336° = Pisces 6°
    assert planets["Rahu"]["rasi"] == "Virgo"      # sidereal ~161° = Virgo 11°
    assert planets["Ketu"]["rasi"] == "Pisces"     # always opposite Rahu
    # Houses are counted from the computed lagna.
    assert planets["Sun"]["house"] == 11
    assert planets["Moon"]["house"] == 10
    # House-wise occupancy covers all 12 houses.
    assert len(result["houses"]) == 12
    assert result["ayanamsa"] == "LAHIRI"


@requires_ephe
def test_navamsa_chart(chennai_birth):
    from vedic_astro_agent.computation import chart_service

    nav = chart_service.divisional_chart(chennai_birth, divisor=9)
    assert nav["chart_type"].startswith("D9")
    assert nav["ascendant"]["rasi"] == "Cancer"
    assert len(nav["planets"]) == 9


@requires_ephe
def test_divisor_validation(chennai_birth):
    import pytest

    from vedic_astro_agent.computation import chart_service

    with pytest.raises(ValueError):
        chart_service.divisional_chart(chennai_birth, divisor=0)
    with pytest.raises(ValueError):
        chart_service.divisional_chart(chennai_birth, divisor=61)


@requires_ephe
def test_special_lagnas(chennai_birth):
    from vedic_astro_agent.computation import chart_service

    result = chart_service.special_lagnas(chennai_birth)
    for key in ("bhava_lagna", "hora_lagna", "indu_lagna", "bhrigu_bindhu_lagna"):
        assert key in result
        assert result[key]["rasi_index"] in range(12)
        assert 0 <= result[key]["longitude_in_rasi"] < 30
        assert result[key]["nakshatra"]


@requires_ephe
def test_ashtakavarga(chennai_birth):
    from vedic_astro_agent.computation import chart_service

    result = chart_service.ashtakavarga(chennai_birth)
    samudaya = result["samudaya_ashtakavarga"]
    assert len(samudaya) == 12
    total = sum(s["bindus"] for s in samudaya)
    assert 300 <= total <= 337  # conventional total is near 337
    assert result["total_bindus"] == total
    assert len(result["bhinnashtakavarga"]) == 8  # 7 planets + lagna
