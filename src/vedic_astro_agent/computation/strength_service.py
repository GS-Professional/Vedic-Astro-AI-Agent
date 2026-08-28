"""Planetary and house strength (Bala) computations.

``strength.shad_bala(jd, place)`` returns nine parallel lists of seven values
(Sun..Saturn): the six bala components, total virupas, total rupas, and the ratio of
rupas to each planet's minimum requirement (``const.shad_bala_factors``).
"""
from __future__ import annotations

from typing import Any

from vedic_astro_agent.computation.models import BirthData
from vedic_astro_agent.computation.naming import planet_name, rasi_name
from vedic_astro_agent.computation.runtime import initialize
from vedic_astro_agent.computation.transforms import (
    set_ayanamsa,
    to_local_jd,
    to_place,
)

_SHADBALA_COMPONENTS = [
    ("sthana_bala", "Positional strength — exaltation, own sign, varga placement"),
    ("kala_bala", "Temporal strength — time of day, paksha, year/month/day/hora lordship"),
    ("dig_bala", "Directional strength — the direction each planet is strongest in"),
    ("cheshta_bala", "Motional strength — retrograde planets score higher"),
    ("naisargika_bala", "Natural strength — fixed ranking, Sun strongest to Saturn weakest"),
    ("drik_bala", "Aspectual strength — benefic aspects add, malefic subtract"),
]

_SUN_TO_SATURN = range(7)


def shadbala(birth_data: BirthData) -> dict[str, Any]:
    """Six-fold strength of the seven classical planets (Rahu/Ketu excluded)."""
    initialize()
    from jhora import const
    from jhora.horoscope.chart import strength

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    sb = strength.shad_bala(jd, place)
    *components, total_virupas, total_rupas, strength_ratio = sb
    required = const.shad_bala_factors

    planets: dict[str, Any] = {}
    for pid in _SUN_TO_SATURN:
        planets[planet_name(pid)] = {
            **{key: round(float(components[i][pid]), 2)
               for i, (key, _desc) in enumerate(_SHADBALA_COMPONENTS)},
            "total_virupas": round(float(total_virupas[pid]), 2),
            "total_rupas": round(float(total_rupas[pid]), 2),
            "minimum_required_rupas": float(required[pid]),
            "strength_ratio": round(float(strength_ratio[pid]), 2),
            "sufficient": float(strength_ratio[pid]) >= 1.0,
        }
    ranking = sorted(planets, key=lambda n: float(planets[n]["strength_ratio"]), reverse=True)
    return {"ayanamsa": ayanamsa, "shadbala": planets,
            "strongest_to_weakest": ranking,
            "component_meanings": dict(_SHADBALA_COMPONENTS)}


def bhava_bala(birth_data: BirthData) -> dict[str, Any]:
    """Strength of each of the 12 houses."""
    initialize()
    from jhora.horoscope.chart import charts, strength

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    virupas, rupas, ratios = strength.bhava_bala(jd, place)
    asc_rasi = charts.rasi_chart(jd, place)[0][1][0]

    houses = []
    for index in range(12):
        houses.append({
            "house": index + 1,
            "rasi": rasi_name(asc_rasi + index),
            "bala_virupas": round(float(virupas[index]), 2),
            "bala_rupas": round(float(rupas[index]), 2),
            "strength_ratio": round(float(ratios[index]), 2),
            "sufficient": float(ratios[index]) >= 1.0,
        })
    ranking = [h["house"] for h in
               sorted(houses, key=lambda h: float(h["strength_ratio"]), reverse=True)]
    return {"ayanamsa": ayanamsa, "bhava_bala": houses, "strongest_to_weakest": ranking}


def vimsopaka_bala(birth_data: BirthData) -> dict[str, Any]:
    """How well each planet is placed across divisional charts (four varga groups)."""
    initialize()
    from jhora.horoscope.chart import charts

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    groups = {
        "shad_varga": ("6 charts: D1, D2, D3, D9, D12, D30",
                       charts.vimsopaka_shadvarga_of_planets),
        "sapta_varga": ("7 charts: shad varga + D7",
                        charts.vimsopaka_sapthavarga_of_planets),
        "dasa_varga": ("10 charts: D1, D2, D3, D7, D9, D10, D12, D16, D30, D60",
                       charts.vimsopaka_dhasavarga_of_planets),
        "shodasa_varga": ("16 charts: D1 through D60",
                          charts.vimsopaka_shodhasavarga_of_planets),
    }
    result: dict[str, Any] = {"ayanamsa": ayanamsa, "vimsopaka_bala": {}}
    for group, (description, fn) in groups.items():
        scores = fn(jd, place)
        result["vimsopaka_bala"][group] = {
            "charts_considered": description,
            "planets": {
                planet_name(pid): {"favourable_varga_count": int(count),
                                   "favourable_charts": charts_str,
                                   "score_out_of_20": round(float(score), 3)}
                for pid, (count, charts_str, score) in scores.items()
            },
        }
    return result
