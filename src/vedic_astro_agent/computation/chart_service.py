"""Birth chart computations: Rasi, divisional vargas, special lagnas, Ashtakavarga.

Wraps ``jhora.horoscope.chart.charts`` / ``ashtakavarga`` and the special ascendant
functions in ``jhora.panchanga.drik``. All functions take ``BirthData`` and return
JSON-ready dicts — this is the exact data the agent grounds its readings in.
"""
from __future__ import annotations

from typing import Any

from vedic_astro_agent.computation.models import BirthData
from vedic_astro_agent.computation.naming import planet_name, rasi_name
from vedic_astro_agent.computation.runtime import initialize
from vedic_astro_agent.computation.transforms import (
    add_houses,
    parse_chart_positions,
    set_ayanamsa,
    to_local_jd,
    to_place,
)

# Divisor -> conventional name, for labelling output.
VARGA_NAMES = {
    1: "Rasi", 2: "Hora", 3: "Drekkana", 4: "Chaturthamsa", 5: "Panchamsa",
    6: "Shashthamsa", 7: "Saptamsa", 8: "Ashtamsa", 9: "Navamsa", 10: "Dasamsa",
    11: "Rudramsa", 12: "Dwadasamsa", 16: "Shodasamsa", 20: "Vimsamsa",
    24: "Chaturvimsamsa", 27: "Nakshatramsa", 30: "Trimsamsa", 40: "Khavedamsa",
    45: "Akshavedamsa", 60: "Shashtyamsa",
}


def rasi_chart(birth_data: BirthData) -> dict[str, Any]:
    """D1 birth chart: ascendant, every planet's sign/degree/nakshatra/pada and house."""
    initialize()
    from jhora import utils
    from jhora.horoscope.chart import charts

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    planet_positions = charts.rasi_chart(jd, place)
    chart = add_houses(parse_chart_positions(planet_positions))
    asc_rasi = chart["ascendant"]["rasi_index"]

    # 12-slot sign-wise occupancy view of the same data. The occupancy strings hold
    # planet ids ("0/1/L"); 'L' marks the ascendant row.
    house_occupancy = utils.get_house_planet_list_from_planet_positions(planet_positions)
    houses = []
    for rasi, occupants in enumerate(house_occupancy):
        names = []
        for token in _split(occupants):
            names.append("Ascendant (Lagna)" if token == "L" else planet_name(int(token)))
        houses.append({"house": (rasi - asc_rasi) % 12 + 1, "rasi": rasi_name(rasi),
                       "planets": names})

    return {"chart_type": "D1 (Rasi / Birth Chart)", "ayanamsa": ayanamsa, **chart,
            "houses": houses}


def _split(occupants: str) -> list[str]:
    return [o for o in occupants.split("/") if o != ""]


def divisional_chart(birth_data: BirthData, divisor: int = 9,
                     chart_method: int = 1) -> dict[str, Any]:
    """One divisional (varga) chart. The divisional ascendant is its own computation —
    never rescaled from D1."""
    initialize()
    from jhora.horoscope.chart import charts

    if not 1 <= divisor <= 60:
        raise ValueError(f"divisor must be between 1 and 60, got {divisor}")

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    planet_positions = charts.divisional_chart(
        jd, place, divisional_chart_factor=divisor, chart_method=chart_method
    )
    chart = add_houses(parse_chart_positions(planet_positions))
    name = VARGA_NAMES.get(divisor)
    return {
        "chart_type": f"D{divisor}" + (f" ({name})" if name else ""),
        "divisor": divisor,
        "chart_method": chart_method,
        "ayanamsa": ayanamsa,
        **chart,
    }


def special_lagnas(birth_data: BirthData) -> dict[str, Any]:
    """Auxiliary ascendants used in interpretation (all computed, none inferred)."""
    initialize()
    from jhora.panchanga import drik

    from vedic_astro_agent.computation.transforms import describe_position

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    lagnas = {
        "bhava_lagna": drik.bhava_lagna,
        "hora_lagna": drik.hora_lagna,
        "ghati_lagna": drik.ghati_lagna,
        "vighati_lagna": drik.vighati_lagna,
        "pranapada_lagna": drik.pranapada_lagna,
        "indu_lagna": drik.indu_lagna,
        "sree_lagna": drik.sree_lagna,
        "bhrigu_bindhu_lagna": drik.bhrigu_bindhu_lagna,
        "kunda_lagna": drik.kunda_lagna,
    }
    result: dict[str, Any] = {"ayanamsa": ayanamsa}
    for name, fn in lagnas.items():
        rasi_index, longitude = fn(jd, place)
        result[name] = describe_position(rasi_index, longitude)
    return result


def ashtakavarga(birth_data: BirthData) -> dict[str, Any]:
    """Bindu scores: per-planet (Bhinnashtakavarga) and combined (Samudaya)."""
    initialize()
    from jhora import utils
    from jhora.horoscope.chart import ashtakavarga as av
    from jhora.horoscope.chart import charts

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    planet_positions = charts.rasi_chart(jd, place)
    asc_rasi = planet_positions[0][1][0]
    house_occupancy = utils.get_house_planet_list_from_planet_positions(planet_positions)
    binna, samudaya, _prastara = av.get_ashtaka_varga(house_occupancy)

    def by_sign(scores: list) -> list[dict[str, Any]]:
        return [
            {"house": (rasi - asc_rasi) % 12 + 1, "rasi": rasi_name(rasi),
             "bindus": int(score)}
            for rasi, score in enumerate(scores)
        ]

    labels = [planet_name(p) for p in range(7)] + ["Ascendant (Lagna)"]
    return {
        "ayanamsa": ayanamsa,
        "bhinnashtakavarga": {label: by_sign(scores) for label, scores in zip(labels, binna)},
        "samudaya_ashtakavarga": by_sign(samudaya),
        "total_bindus": int(sum(int(s) for s in samudaya)),
    }
