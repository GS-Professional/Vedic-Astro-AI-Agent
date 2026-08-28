"""Marriage compatibility computation tests."""
import pytest

from tests.conftest import requires_ephe


@requires_ephe
def test_north_method_bounds(chennai_birth, mumbai_birth):
    from vedic_astro_agent.computation import compatibility_service

    result = compatibility_service.compatibility(chennai_birth, mumbai_birth, "North")
    assert result["maximum_score"] == 36
    assert 0 <= result["total_score"] <= 36
    assert len(result["factors"]) == 8
    factor_sum = sum(f["points"] for f in result["factors"].values())
    assert abs(factor_sum - result["total_score"]) < 0.01
    for key, factor in result["factors"].items():
        assert factor["points"] <= factor["maximum_points"], key
    assert result["compatible"] == (result["total_score"] >= result["minimum_score"])
    assert result["first_person"]["nakshatra"]
    assert result["second_person"]["moon_rasi"]


@requires_ephe
def test_south_method(chennai_birth, mumbai_birth):
    from vedic_astro_agent.computation import compatibility_service

    result = compatibility_service.compatibility(chennai_birth, mumbai_birth, "South")
    assert result["maximum_score"] == 10
    assert all(set(v) == {"met", "description"} or "met" in v
               for v in result["factors"].values())
    assert isinstance(result["compatible"], bool)


@requires_ephe
def test_method_validation(chennai_birth, mumbai_birth):
    from vedic_astro_agent.computation import compatibility_service

    with pytest.raises(ValueError):
        compatibility_service.compatibility(chennai_birth, mumbai_birth, "East")
