"""Planner tests — no ephemeris needed (planning happens before computation)."""
from datetime import date

import pytest

from vedic_astro_agent.agent.planner import Planner, _find_date, _find_time
from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place

REF = date(2026, 8, 27)


@pytest.fixture()
def planner():
    return Planner(reference_date=REF)


@pytest.fixture()
def birth():
    return BirthData(Place("Chennai, IN", 13.0827, 80.2707, 5.5),
                     DateTimeSpec(1996, 12, 7, 10, 30))


def test_date_extraction_variants():
    assert _find_date("born on 1996-12-07") == (1996, 12, 7)
    assert _find_date("born on 7 Dec 1996") == (1996, 12, 7)
    assert _find_date("born on December 7, 1996") == (1996, 12, 7)
    assert _find_date("DOB 07-12-1996") == (1996, 12, 7)
    assert _find_date("no date here") is None


def test_time_extraction_variants():
    assert _find_time("at 10:30 in Chennai") == (10, 30)
    assert _find_time("born 10:30:45 am") == (10, 30)
    assert _find_time("at 2:15 pm") == (14, 15)
    assert _find_time("12:05 am sharp") == (0, 5)
    assert _find_time("no time") is None


def test_birth_data_extracted_from_query(planner):
    u = planner.understand("What does my career look like? Born 7 Dec 1996 at 10:30 in Chennai")
    assert u.birth_data is not None
    assert u.birth_data.place.name == "Chennai"
    assert u.birth_data.date_time == DateTimeSpec(1996, 12, 7, 10, 30)
    assert "career" in u.topics
    assert not u.missing


def test_missing_birth_data_triggers_clarification(planner):
    u = planner.understand("When will I get promoted?")
    assert u.birth_data is None
    assert u.missing
    plan = planner.plan(u)
    assert plan.needs_clarification
    question = plan.clarification_question()
    assert "date of birth" in question
    assert "birth time" in question


def test_career_plan_recipe(planner, birth):
    u = planner.understand("Tell me about my career prospects", birth_data=birth)
    plan = planner.plan(u)
    tools = plan.tool_names()
    assert tools[0] == "get_rasi_chart"  # D1 anchors everything
    assert "get_divisional_chart" in tools  # D10
    assert "get_shadbala" in tools
    assert "get_vimsottari_dasha" in tools
    assert "get_running_dasha" in tools
    d10 = [s for s in plan.steps if s.tool == "get_divisional_chart"]
    assert d10[0].arguments["divisor"] == 10
    # Every step carries a written reason (the audit trail).
    assert all(s.reason for s in plan.steps)


def test_marriage_plan_includes_navamsa_and_doshas(planner, birth):
    u = planner.understand("Will my marriage happen soon?", birth_data=birth)
    plan = planner.plan(u)
    tools = plan.tool_names()
    assert "get_divisional_chart" in tools
    assert any(s.arguments.get("divisor") == 9 for s in plan.steps)
    assert "get_doshas" in tools


def test_compatibility_plan_needs_second_person(planner, birth):
    u = planner.understand("Are we compatible?", birth_data=birth)
    plan = planner.plan(u)
    assert plan.needs_clarification
    assert "second person" in plan.clarification_question()


def test_compatibility_plan_with_both(planner, birth):
    second = BirthData(Place("Mumbai, IN", 19.076, 72.8777, 5.5),
                       DateTimeSpec(1997, 3, 15, 8, 0))
    u = planner.understand("Check our compatibility", birth_data=birth,
                           second_birth_data=second)
    plan = planner.plan(u)
    assert "get_compatibility" in plan.tool_names()
    assert not plan.needs_clarification


def test_panchanga_plan_today(planner, birth):
    u = planner.understand("What is today's panchanga? Rahu kalam?", birth_data=birth)
    plan = planner.plan(u)
    tools = plan.tool_names()
    assert "get_panchanga" in tools
    assert "get_rahu_kala" in tools


def test_running_dasha_targets_reference_date(planner, birth):
    u = planner.understand("When will I get a promotion this year?", birth_data=birth)
    plan = planner.plan(u)
    running = [s for s in plan.steps if s.tool == "get_running_dasha"]
    assert running, "timing intent must add get_running_dasha"
    assert running[0].arguments["on_date"] == {"year": 2026, "month": 8, "day": 27}


def test_plan_deduplicates(planner, birth):
    u = planner.understand("career and money overview", birth_data=birth)
    plan = planner.plan(u)
    import json

    keys = [(s.tool, json.dumps(s.arguments, sort_keys=True)) for s in plan.steps]
    assert len(keys) == len(set(keys))
