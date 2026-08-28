"""Daily almanac (panchanga) computations: limbs, rise/set times, muhurtas, transits.

PyJHora's return shapes are irregular; they are documented once here:

* ``tithi``/``yogam`` -> ``[index, start_hours, end_hours, ...]``
* ``nakshatra``       -> ``[index, pada, start_hours, end_hours, ...]``
* ``sunrise``/``sunset``/``moonrise``/``moonset`` -> ``[local_hours, "hh:mm:ss", jd]``
* ``midday``          -> ``(local_hours, julian_day)``; ``midnight`` -> bare float
* ``trikalam``/``durmuhurtam``/``abhijit_muhurta`` -> already-formatted strings
* ``brahma_muhurtha`` and friends -> ``(start_hours, end_hours)``

Hours count from the day's sunrise, so a limb ending after midnight reads past 24.
"""
from __future__ import annotations

from typing import Any

from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place
from vedic_astro_agent.computation.naming import (
    karana_name,
    masa_name,
    nakshatra_name,
    planet_name,
    ritu_name,
    samvatsara_name,
    tithi_name,
    tithi_paksha,
    vaara_name,
    yoga_name,
)
from vedic_astro_agent.computation.runtime import initialize
from vedic_astro_agent.computation.transforms import (
    set_ayanamsa,
    to_date,
    to_local_jd,
    to_place,
)


def _time(value: Any) -> Any:
    """Render fractional hours as HH:MM:SS; strings already formatted pass through."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_time(v) for v in value]
    total = int(round(float(value) * 3600))
    sign = "-" if total < 0 else ""
    total = abs(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"


def _period(pair: Any) -> dict[str, Any]:
    return {"start": _time(pair[0]), "end": _time(pair[1])}


def panchanga(birth_data: BirthData) -> dict[str, Any]:
    """The five limbs plus masa, samvatsara and ritu for a date/time/place."""
    initialize()
    from jhora.panchanga import drik

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    tithi = drik.tithi(jd, place)
    nakshatra = drik.nakshatra(jd, place)
    yoga = drik.yogam(jd, place)
    karana = drik.karana(jd, place)
    # lunar_month_date (not lunar_month): the latter derives the index as
    # (solar_month + 1) % 12 and returns 0 for Phalguna, outside its documented 1..12.
    masa_index, _day, _year, is_adhika, is_nija = drik.lunar_month_date(jd, place)
    samvatsara_index = drik.samvatsara(to_date(birth_data.date_time), place)

    return {
        "ayanamsa": ayanamsa,
        "tithi": {"index": int(tithi[0]), "name": tithi_name(tithi[0]),
                  "paksha": tithi_paksha(tithi[0]),
                  "start_time": _time(tithi[1]), "end_time": _time(tithi[2])},
        "nakshatra": {"index": int(nakshatra[0]), "name": nakshatra_name(nakshatra[0]),
                      "pada": int(nakshatra[1]),
                      "start_time": _time(nakshatra[2]), "end_time": _time(nakshatra[3])},
        "yoga": {"index": int(yoga[0]), "name": yoga_name(yoga[0]),
                 "start_time": _time(yoga[1]), "end_time": _time(yoga[2])},
        "karana": {"index": int(karana[0]), "name": karana_name(karana[0]),
                   "start_time": _time(karana[1]), "end_time": _time(karana[2])},
        "vaara": vaara_name(drik.vaara(jd, place)),
        "masa": {"index": int(masa_index), "name": masa_name(masa_index),
                 "is_adhika_maasa": bool(is_adhika), "is_nija_maasa": bool(is_nija)},
        "samvatsara": {"index": int(samvatsara_index),
                       "name": samvatsara_name(samvatsara_index)},
        "ritu": ritu_name(drik.ritu(masa_index)),
    }


def sunrise_sunset(place: Place, date_time: DateTimeSpec) -> dict[str, Any]:
    """Rise/set times and day/night lengths, all local."""
    initialize()
    from jhora.panchanga import drik

    jd = to_local_jd(date_time)
    p = to_place(place)
    return {
        "sunrise": _time(drik.sunrise(jd, p)[0]),
        "sunset": _time(drik.sunset(jd, p)[0]),
        "moonrise": _time(drik.moonrise(jd, p)[0]),
        "moonset": _time(drik.moonset(jd, p)[0]),
        "midday": _time(drik.midday(jd, p)[0]),
        "midnight": _time(drik.midnight(jd, p)),
        "day_length_hours": round(drik.day_length(jd, p), 4),
        "night_length_hours": round(drik.night_length(jd, p), 4),
    }


def rahu_kala(place: Place, date_time: DateTimeSpec) -> dict[str, Any]:
    """Inauspicious periods: Rahu Kalam, Yamaganda Kalam, Gulika Kalam."""
    initialize()
    from jhora.panchanga import drik

    jd = to_local_jd(date_time)
    p = to_place(place)
    return {
        "rahu_kalam": _period(drik.trikalam(jd, p, "raahu kaalam")),
        "yamaganda_kalam": _period(drik.trikalam(jd, p, "yamagandam")),
        "gulika_kalam": _period(drik.trikalam(jd, p, "gulikai")),
    }


def muhurtha(place: Place, date_time: DateTimeSpec) -> dict[str, Any]:
    """Auspicious and inauspicious muhurta windows for a day."""
    initialize()
    from jhora.panchanga import drik

    jd = to_local_jd(date_time)
    p = to_place(place)

    flat = drik.durmuhurtam(jd, p)  # flat list of formatted times
    durmuhurtam = [_period(flat[i:i + 2]) for i in range(0, len(flat) - 1, 2)]
    vijaya_day, vijaya_night = drik.vijaya_muhurtha(jd, p)
    pratah, madhyaahna, saayam = drik.sandhya_periods(jd, p)

    return {
        "abhijit_muhurtha": _period(drik.abhijit_muhurta(jd, p)),
        "brahma_muhurtha": _period(drik.brahma_muhurtha(jd, p)),
        "godhuli_muhurtha": _period(drik.godhuli_muhurtha(jd, p)),
        "vijaya_muhurtha": {"day": _period(vijaya_day), "night": _period(vijaya_night)},
        "nishita_muhurtha": _period(drik.nishita_muhurtha(jd, p)),
        "sandhya_periods": {"pratah": _period(pratah), "madhyahna": _period(madhyaahna),
                            "sayam": _period(saayam)},
        "durmuhurtam": durmuhurtam,
    }


def planet_positions(birth_data: BirthData) -> dict[str, Any]:
    """Sidereal positions of all nine grahas at the given date/time (transits)."""
    initialize()
    from jhora.horoscope.chart import charts

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    from vedic_astro_agent.computation.transforms import parse_chart_positions

    chart = parse_chart_positions(charts.rasi_chart(jd, place))
    return {"ayanamsa": ayanamsa, **chart}


def retrograde_planets(birth_data: BirthData) -> dict[str, Any]:
    """Which planets are retrograde at the given date/time (Rahu/Ketu excluded)."""
    initialize()
    from jhora.panchanga import drik

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    retro = drik.planets_in_retrograde(jd, place)
    return {
        "ayanamsa": ayanamsa,
        "retrograde_planets": [planet_name(p) for p in retro],
        "retrograde_indices": [int(p) for p in retro],
    }
