"""Conversion between this package's typed inputs and PyJHora's native types.

The single most important convention lives here: **PyJHora Julian days are local**.
``utils.julian_day_number`` encodes the wall-clock time as-is, and every ``drik`` /
``charts`` function converts to UTC internally via the place's timezone offset. Passing
a UTC Julian day instead shifts the ascendant by hours of rotation — a silent,
plausible-looking error. All services in this package obtain their jd from
``to_local_jd`` and nothing else.
"""
from __future__ import annotations

from typing import Any

from vedic_astro_agent.computation.models import DateTimeSpec, Place
from vedic_astro_agent.computation.naming import nakshatra_name, rasi_name
from vedic_astro_agent.computation.runtime import initialize


def to_place(place: Place) -> Any:
    """Build a real ``drik.Place``.

    Elevation is passed explicitly as 0.0 — left as None, PyJHora lazily calls
    ``utils.get_elevation()``, which makes an outbound HTTP request.
    """
    initialize()
    from jhora.panchanga import drik

    return drik.Place(place.name, place.latitude, place.longitude,
                      place.timezone_offset, elevation=place.elevation or 0.0)


def to_date(dt: DateTimeSpec) -> Any:
    initialize()
    from jhora.panchanga import drik

    return drik.Date(dt.year, dt.month, dt.day)


def to_local_jd(dt: DateTimeSpec) -> float:
    """Julian day encoding *local* wall-clock time (PyJHora's convention)."""
    initialize()
    from jhora import utils

    return utils.julian_day_number(dt.as_date_tuple(), dt.as_time_tuple())


def to_jd_utc(dt: DateTimeSpec, place: Place) -> float:
    """UTC Julian day; ``drik.sidereal_longitude`` wants this, not the local jd."""
    initialize()
    from jhora import utils

    return utils.julian_day_utc(to_local_jd(dt), to_place(place))


def set_ayanamsa(mode: str) -> str:
    """Apply an ayanamsa mode for subsequent calculations; returns the mode applied."""
    initialize()
    from jhora.panchanga import drik

    drik.set_ayanamsa_mode(str(mode).upper())
    return str(mode).upper()


def describe_position(rasi_index: int, longitude_in_rasi: float) -> dict[str, Any]:
    """Expand a (rasi, degrees-within-rasi) pair into the full position block.

    Nakshatra and pada come from ``drik.nakshatra_pada`` rather than open-coded
    arithmetic, so they agree with PyJHora's own definitions.
    """
    initialize()
    from jhora.panchanga import drik

    total = float(rasi_index) * 30.0 + float(longitude_in_rasi)
    nak_index, pada, _rest = drik.nakshatra_pada(total)
    return {
        "rasi": rasi_name(rasi_index),
        "rasi_index": int(rasi_index),
        "longitude_in_rasi": round(float(longitude_in_rasi), 4),
        "total_longitude": round(total % 360.0, 4),
        "nakshatra": nakshatra_name(nak_index),
        "nakshatra_index": int(nak_index),
        "pada": int(pada),
    }


def parse_chart_positions(planet_positions: list) -> dict[str, Any]:
    """Split PyJHora's chart output into an ascendant block plus a planets list.

    Charts come back as ``[['L', (rasi, lon)], [0, (rasi, lon)], ...]`` — note the
    first row's id is the *string* ``'L'``.
    """
    from vedic_astro_agent.computation.naming import planet_name

    ascendant = None
    planets = []
    for pid, (rasi, longitude) in planet_positions:
        position = describe_position(rasi, longitude)
        block = {"planet": planet_name(pid), "planet_index": pid, **position}
        if pid == "L":
            block["planet"] = "Ascendant (Lagna)"
            ascendant = block
        else:
            planets.append(block)
    if ascendant is None:
        raise ValueError("chart output has no ascendant row ('L')")
    return {"ascendant": ascendant, "planets": planets}


def add_houses(chart: dict[str, Any]) -> dict[str, Any]:
    """Attach the house (from the chart's own ascendant) to every planet block."""
    asc_rasi = chart["ascendant"]["rasi_index"]
    for planet in chart["planets"]:
        planet["house"] = (planet["rasi_index"] - asc_rasi) % 12 + 1
    return chart
