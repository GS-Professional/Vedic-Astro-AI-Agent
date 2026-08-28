"""Dasha (planetary period) computations.

Since PyJHora 4.8.x every dasha system returns the same flat row shape regardless of
depth::

    [ (lord, lord, ...), (year, month, day, fractional_hour), duration_in_years ]

A depth-d request yields only d-length rows, so ``_nest`` groups them back into a tree
(ancestors created on demand, inheriting their first child's start and the sum of their
children's durations). Period ends are taken from the next sibling's start rather than
re-deriving year lengths, which are configurable inside PyJHora and would drift.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vedic_astro_agent.computation.models import BirthData, DateTimeSpec
from vedic_astro_agent.computation.naming import planet_name
from vedic_astro_agent.computation.runtime import initialize
from vedic_astro_agent.computation.transforms import (
    set_ayanamsa,
    to_date,
    to_local_jd,
    to_place,
)

# Yogini dasha runs 8 lords over 36 years; PyJHora returns the associated planet id and
# these are the Yogini names in the library's own order.
_YOGINI_NAMES = {
    1: "Mangala", 0: "Pingala", 4: "Dhanya", 2: "Bhramari",
    3: "Bhadrika", 6: "Ulka", 5: "Siddha", 7: "Sankata",
}


def _yogini_name(pid: Any) -> str:
    return f"{_YOGINI_NAMES.get(int(pid), '?')} ({planet_name(pid)})"


def _date_tuple_to_str(t: Any) -> str:
    if t is None:
        return ""
    y, m, d = int(t[0]), int(t[1]), int(t[2])
    if len(t) > 3:
        return f"{y:04d}-{m:02d}-{d:02d} {_time_str(t[3])}"
    return f"{y:04d}-{m:02d}-{d:02d}"


def _time_str(fractional_hours: float) -> str:
    total = int(round(float(fractional_hours) * 3600))
    sign = "-" if total < 0 else ""
    total = abs(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"


def _plus_years(start: Any, years: float) -> str:
    """Add a duration to a PyJHora date tuple (for the final period of a group)."""
    from jhora import utils
    from jhora.panchanga import drik

    y, m, d, fh = start
    jd = utils.julian_day_number(drik.Date(y, m, d), (fh, 0, 0))
    return _date_tuple_to_str(utils.jd_to_gregorian(jd + years * 365.25))


def _nest(rows: list, name_of: Callable[[Any], str]) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    open_nodes: list[dict[str, Any]] = []
    open_lords: tuple = ()

    def new_node(lord: Any, start: Any) -> dict[str, Any]:
        return {"lord": name_of(lord), "lord_index": int(lord),
                "start": _date_tuple_to_str(start), "end": None,
                "duration_years": 0.0, "_start_raw": start}

    for lords, start, duration in rows:
        lords = tuple(lords) if isinstance(lords, (list, tuple)) else (lords,)

        # Rows arrive chronologically; a level starts a new period exactly when its lord
        # differs from the previous row's at that level. Comparing against the previous
        # row (rather than a lord lookup) keeps repeated cycles distinct — Yogini runs
        # its eight lords three times over.
        shared = 0
        while (shared < len(lords) and shared < len(open_lords)
               and lords[shared] == open_lords[shared]):
            shared += 1

        del open_nodes[shared:]
        for depth in range(shared, len(lords)):
            node = new_node(lords[depth], start)
            if depth == 0:
                roots.append(node)
            else:
                open_nodes[depth - 1].setdefault("sub_periods", []).append(node)
            open_nodes.append(node)

        open_lords = lords
        for node in open_nodes:  # leaf durations roll up through all ancestors
            node["duration_years"] += float(duration)

    def close(siblings: list[dict[str, Any]], parent_end: str | None) -> None:
        for index, node in enumerate(siblings):
            if index + 1 < len(siblings):
                node["end"] = siblings[index + 1]["start"]
            else:
                node["end"] = parent_end or _plus_years(node["_start_raw"],
                                                        node["duration_years"])
            close(node.get("sub_periods", []), node["end"])
            node["duration_years"] = round(node["duration_years"], 4)
            del node["_start_raw"]

    close(roots, None)
    return roots


def _summarise(rows: list, name_of: Callable, depth: int, **extra: Any) -> dict[str, Any]:
    return {"periods": _nest(rows, name_of), "depth": depth,
            "period_count": len(rows), **extra}


def _validate_depth(depth: int, maximum: int = 4) -> None:
    if not 1 <= depth <= maximum:
        raise ValueError(f"depth must be 1..{maximum}, got {depth}")


def vimsottari_dasha(birth_data: BirthData, depth: int = 2) -> dict[str, Any]:
    """Standard 120-year Vimsottari dasha, seeded from the Moon's birth nakshatra."""
    initialize()
    from jhora.horoscope.dhasa.graha import vimsottari

    _validate_depth(depth)
    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    balance, rows = vimsottari.get_vimsottari_dhasa_bhukthi(jd, place, dhasa_level_index=depth)
    years, months, days = balance
    return _summarise(
        rows, planet_name, depth,
        dasha_system="Vimsottari", total_years=120, ayanamsa=ayanamsa,
        balance_at_birth={
            "years": int(years), "months": int(months), "days": int(days),
            "description": f"{years}y {months}m {days}d of the first Maha Dasha remained at birth",
        },
    )


def yogini_dasha(birth_data: BirthData, depth: int = 2) -> dict[str, Any]:
    """8-lord, 36-year Yogini dasha cycle."""
    initialize()
    from jhora.horoscope.dhasa.graha import yogini

    _validate_depth(depth)
    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    rows = yogini.get_dhasa_bhukthi(
        to_date(birth_data.date_time), birth_data.date_time.as_time_tuple(),
        to_place(birth_data.place), dhasa_level_index=depth,
    )
    return _summarise(rows, _yogini_name, depth,
                      dasha_system="Yogini", total_years=36, ayanamsa=ayanamsa)


def ashtottari_dasha(birth_data: BirthData, depth: int = 2) -> dict[str, Any]:
    """Conditional 108-year, 8-lord Ashtottari dasha with its applicability check."""
    initialize()
    from jhora.horoscope.chart import charts
    from jhora.horoscope.dhasa.graha import ashtottari

    _validate_depth(depth)
    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    applicable = bool(ashtottari.applicability_check(charts.rasi_chart(jd, place)))
    rows = ashtottari.get_ashtottari_dhasa_bhukthi(jd, place, dhasa_level_index=depth)
    return _summarise(
        rows, planet_name, depth,
        dasha_system="Ashtottari", total_years=108, ayanamsa=ayanamsa,
        applicable=applicable,
        applicability_note=(
            "Condition met: Ashtottari applies (Rahu in kendra/trikona from lagna lord "
            "or born in Krishna paksha per the classical rule)."
            if applicable else
            "Condition not met for this chart; periods shown for inspection only."
        ),
    )


def running_dasha(birth_data: BirthData, on_date: DateTimeSpec,
                  depth: int = 3) -> dict[str, Any]:
    """The Vimsottari periods in force on a given date (MD / AD / PD).

    Walks the flat depth-``depth`` rows, converts each start tuple to a Julian day and
    brackets the target date. Period length uses PyJHora's own duration years and the
    365.25-day year convention the library itself applies for these conversions.
    """
    initialize()
    from jhora import utils
    from jhora.horoscope.dhasa.graha import vimsottari
    from jhora.panchanga import drik

    ayanamsa = set_ayanamsa(birth_data.ayanamsa)
    jd = to_local_jd(birth_data.date_time)
    place = to_place(birth_data.place)

    _balance, rows = vimsottari.get_vimsottari_dhasa_bhukthi(jd, place, dhasa_level_index=depth)

    target_jd = to_local_jd(on_date)
    levels = ["Maha Dasha", "Antar Dasha", "Pratyantar Dasha", "Sookshma Dasha"][:depth]
    active: dict[str, Any] = {}
    for lords, start, duration in rows:
        lords = tuple(lords) if isinstance(lords, (list, tuple)) else (lords,)
        y, m, d, fh = start
        start_jd = utils.julian_day_number(drik.Date(y, m, d), (fh, 0, 0))
        end_jd = start_jd + float(duration) * 365.25
        if start_jd <= target_jd < end_jd:
            for level, lord in zip(levels, lords):
                slot = active.setdefault(level, {"lord": planet_name(lord),
                                                 "lord_index": int(lord),
                                                 "start": _date_tuple_to_str(start)})
                slot.setdefault("end", _plus_years(start, duration))
            break

    return {
        "dasha_system": "Vimsottari", "ayanamsa": ayanamsa,
        "on_date": on_date.iso(), "depth": depth,
        "running": active or None,
        "note": "No active period bracketed the target date (outside the 120-year cycle?)"
        if not active else None,
    }
