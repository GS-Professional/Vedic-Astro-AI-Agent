"""Yoga (planetary combination) and Dosha (affliction) detection.

Uses PyJHora's high-level evaluators — ``yoga.get_yoga_details``,
``dosha.get_dosha_details`` and ``raja_yoga.get_raja_yoga_details`` — which run every
implemented rule and report the classical condition and effect for each hit.
"""
from __future__ import annotations

import re
from typing import Any

from vedic_astro_agent.computation.models import BirthData
from vedic_astro_agent.computation.naming import planet_name
from vedic_astro_agent.computation.runtime import initialize
from vedic_astro_agent.computation.transforms import (
    set_ayanamsa,
    to_local_jd,
    to_place,
)

_TAG = re.compile(r"<[^>]+>")


def _plain(text: Any) -> str:
    """Strip the HTML PyJHora wraps its yoga and dosha prose in."""
    if not isinstance(text, str):
        return str(text)
    return " ".join(_TAG.sub(" ", text).split())


def _presence(value: Any) -> tuple[bool, Any]:
    """Normalise a dosha predicate result to (present, details).

    The predicates are inconsistent: some return a bare bool, others
    ``[present, houses]`` / ``[present, flag, houses]`` or ``(bool, bool)``.
    """
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (list, tuple)) and value:
        details = [v for v in value[1:] if not isinstance(v, bool)]
        return bool(value[0]), (details[0] if len(details) == 1 else details or None)
    return bool(value), None


def yogas(birth_data: BirthData, divisional_chart: int = 1) -> dict[str, Any]:
    """All yogas present in a chart (284 rules checked in PyJHora 4.8.7)."""
    initialize()
    from jhora.horoscope.chart import yoga

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    results, found, checked = yoga.get_yoga_details(
        jd, place, divisional_chart_factor=divisional_chart
    )
    yogas_out = []
    for key, details in results.items():
        chart_id, name, condition, effect = (list(details) + [""] * 4)[:4]
        yogas_out.append({"key": key, "name": _plain(name), "chart": chart_id,
                          "condition": _plain(condition), "effect": _plain(effect)})
    return {"ayanamsa": ayanamsa, "chart": f"D{divisional_chart}",
            "count": found, "checked": checked,
            "yogas": sorted(yogas_out, key=lambda y: y["name"])}


def doshas(birth_data: BirthData) -> dict[str, Any]:
    """The eight classical doshas with presence flags and details."""
    initialize()
    from jhora import utils
    from jhora.horoscope.chart import charts, dosha
    from jhora.panchanga import drik

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    planet_positions = charts.rasi_chart(jd, place)
    house_occupancy = utils.get_house_planet_list_from_planet_positions(planet_positions)
    moon_star = drik.nakshatra(jd, place)[0]

    predicates = {
        "kala_sarpa": dosha.kala_sarpa(house_occupancy),
        "manglik": dosha.manglik(planet_positions),
        "pitru": dosha.pitru_dosha(planet_positions),
        "guru_chandala": dosha.guru_chandala_dosha(planet_positions),
        "ganda_moola": dosha.ganda_moola(moon_star),
        "kalathra": dosha.kalathra(planet_positions),
        "ghata": dosha.ghata(planet_positions),
        "shrapit": dosha.shrapit(planet_positions),
    }
    descriptions = list(dosha.get_dosha_details(jd, place).items())

    out: dict[str, Any] = {}
    for index, (key, raw) in enumerate(predicates.items()):
        present, details = _presence(raw)
        label, prose = descriptions[index] if index < len(descriptions) else (key, "")
        out[key] = {"name": label, "present": present, "details": details,
                    "description": _plain(prose)}
    return {"ayanamsa": ayanamsa, "doshas": out,
            "present": [k for k, v in out.items() if v["present"]]}


def raja_yogas(birth_data: BirthData, divisional_chart: int = 1) -> dict[str, Any]:
    """Raja yogas plus the kendra/trikona lord pairs that form them."""
    initialize()
    from jhora.horoscope.chart import charts, raja_yoga

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    results, found, checked = raja_yoga.get_raja_yoga_details(
        jd, place, divisional_chart_factor=divisional_chart
    )
    pairs = raja_yoga.get_raja_yoga_pairs_from_planet_positions(charts.rasi_chart(jd, place))
    return {
        "ayanamsa": ayanamsa, "chart": f"D{divisional_chart}",
        "count": found, "checked": checked,
        "raja_yogas": [{"key": key, "details": [_plain(d) for d in details]}
                       for key, details in results.items()],
        "raja_yoga_planet_pairs": [[planet_name(p) for p in pair] for pair in pairs],
    }
