"""Marriage compatibility (Ashtakoota / Dasa Porutham).

Wraps ``jhora.horoscope.match.compatibility``: an ``Ashtakoota`` is built from each
partner's birth nakshatra and pada and scored under either the North Indian (36-point)
or South Indian (Dasa Porutham) convention.
"""
from __future__ import annotations

from typing import Any

from vedic_astro_agent.computation.models import BirthData
from vedic_astro_agent.computation.naming import nakshatra_name, rasi_name
from vedic_astro_agent.computation.runtime import initialize
from vedic_astro_agent.computation.transforms import (
    set_ayanamsa,
    to_local_jd,
    to_place,
)

# The eight Ashta Koota factors in the order compatibility_score() returns them.
_KOOTA_FACTORS = [
    ("varna", 1, "Varna — spiritual and temperamental compatibility"),
    ("vashya", 2, "Vashya — mutual attraction and influence"),
    ("gana", 6, "Gana — nature and temperament (deva, manushya, rakshasa)"),
    ("tara", 3, "Tara/Dina — birth star compatibility and wellbeing"),
    ("yoni", 4, "Yoni — physical and sexual compatibility"),
    ("graha_maitri", 5, "Graha Maitri — friendship between the Moon sign lords"),
    ("bhakoot", 7, "Bhakoot — emotional and financial harmony"),
    ("nadi", 8, "Nadi — health and genetic compatibility, the heaviest factor"),
]

_EXTRA_PORUTHAMS = ["mahendra", "vedha", "rajju", "sthree_dheerga"]

METHOD_NORTH = "North"
METHOD_SOUTH = "South"


def _birth_star(birth: BirthData) -> dict[str, Any]:
    """One partner's Moon nakshatra, pada and Moon sign."""
    from jhora.horoscope.chart import charts
    from jhora.panchanga import drik

    set_ayanamsa(birth.ayanamsa)
    jd = to_local_jd(birth.date_time)
    place = to_place(birth.place)

    nak_index, pada = drik.nakshatra(jd, place)[:2]
    moon_rasi = dict(charts.rasi_chart(jd, place))[1][0]  # Moon is index 1; row 0 is 'L'
    return {
        "date": f"{birth.date_time.year:04d}-{birth.date_time.month:02d}-{birth.date_time.day:02d}",
        "place": birth.place.name,
        "nakshatra": nakshatra_name(nak_index),
        "nakshatra_index": int(nak_index),
        "pada": int(pada),
        "moon_rasi": rasi_name(moon_rasi),
    }


def compatibility(first: BirthData, second: BirthData,
                  method: str = METHOD_NORTH) -> dict[str, Any]:
    """Score a couple. North = 8 kootas out of 36; South = poruthams met/not met."""
    initialize()
    from jhora import const
    from jhora.horoscope.match.compatibility import Ashtakoota

    if method not in (METHOD_NORTH, METHOD_SOUTH):
        raise ValueError(f"method must be 'North' or 'South', got {method!r}")

    boy = _birth_star(first)
    girl = _birth_star(second)

    koota = Ashtakoota(boy["nakshatra_index"], boy["pada"],
                       girl["nakshatra_index"], girl["pada"], method=method)
    is_south = method == METHOD_SOUTH
    scores = koota.compatibility_score()

    def render(value: Any, maximum: int) -> dict[str, Any]:
        if is_south:
            return {"met": bool(value)}
        return {"points": float(value), "maximum_points": maximum}

    factors = {
        key: {"description": description, **render(scores[i], maximum)}
        for i, (key, maximum, description) in enumerate(_KOOTA_FACTORS)
    }
    total = scores[len(_KOOTA_FACTORS)]
    extras = {
        name: bool(scores[len(_KOOTA_FACTORS) + 1 + i])
        for i, name in enumerate(_EXTRA_PORUTHAMS)
        if len(scores) > len(_KOOTA_FACTORS) + 1 + i
    }
    minimum = (const.compatibility_minimum_score_south if is_south
               else const.compatibility_minimum_score_north)

    result: dict[str, Any] = {
        "method": "South Indian (Dasa Porutham)" if is_south else "North Indian (Ashta Koota)",
        "first_person": boy,
        "second_person": girl,
        "factors": factors,
        "supplementary_poruthams": extras,
        "minimum_score": minimum,
        "total_score": float(total),
    }
    if is_south:
        result["maximum_score"] = 10
        result["compatible"] = bool(scores[-1])
    else:
        result["maximum_score"] = sum(m for _, m, _ in _KOOTA_FACTORS)
        result["compatible"] = float(total) >= float(minimum)
        result["interpretation"] = (
            "Not recommended" if total < 18
            else "Average" if total < 25
            else "Good" if total <= 32
            else "Excellent"
        )
    return result
