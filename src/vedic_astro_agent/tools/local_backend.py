"""Local tool backend: every tool runs PyJHora in-process.

Tool names and semantics deliberately mirror the pyjhora-mcp server
(https://github.com/chinmay-sh/pyjhora-mcp) so an agent configured for one backend can
switch to the other without changing its plans.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vedic_astro_agent.computation import (
    chart_service,
    compatibility_service,
    dasha_service,
    panchanga_service,
    strength_service,
    yoga_service,
)
from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place
from vedic_astro_agent.tools.registry import ToolCallError, ToolCatalog, ToolSpec

# ---------------------------------------------------------------------------
# Shared JSON-schema fragments
# ---------------------------------------------------------------------------

_PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Place name, e.g. 'Chennai, IN'"},
        "latitude": {"type": "number", "description": "North positive, south negative"},
        "longitude": {"type": "number", "description": "East positive, west negative"},
        "timezone_offset": {"type": "number", "description": "UTC offset in hours, e.g. 5.5"},
    },
    "required": ["name", "latitude", "longitude", "timezone_offset"],
}

_DATETIME_SCHEMA = {
    "type": "object",
    "properties": {
        "year": {"type": "integer"},
        "month": {"type": "integer", "minimum": 1, "maximum": 12},
        "day": {"type": "integer", "minimum": 1, "maximum": 31},
        "hour": {"type": "integer", "minimum": 0, "maximum": 23, "default": 0},
        "minute": {"type": "integer", "minimum": 0, "maximum": 59, "default": 0},
        "second": {"type": "integer", "minimum": 0, "maximum": 59, "default": 0},
    },
    "required": ["year", "month", "day"],
}

_BIRTH_SCHEMA = {
    "type": "object",
    "properties": {
        "place": _PLACE_SCHEMA,
        "date_time": _DATETIME_SCHEMA,
        "ayanamsa": {"type": "string", "default": "LAHIRI",
                     "description": "Ayanamsa mode; LAHIRI is the Indian standard"},
    },
    "required": ["place", "date_time"],
}


def _args_birth(arguments: dict[str, Any]) -> BirthData:
    try:
        bd = arguments["birth_data"]
        place = bd["place"]
        dt = bd["date_time"]
        return BirthData(
            Place(place["name"], float(place["latitude"]), float(place["longitude"]),
                  float(place["timezone_offset"]), float(place.get("elevation", 0.0))),
            DateTimeSpec(int(dt["year"]), int(dt["month"]), int(dt["day"]),
                         int(dt.get("hour", 0)), int(dt.get("minute", 0)),
                         int(dt.get("second", 0))),
            str(bd.get("ayanamsa", "LAHIRI")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolCallError(f"Invalid birth_data argument: {exc}") from exc


def _args_place_dt(arguments: dict[str, Any]) -> tuple[Place, DateTimeSpec]:
    try:
        place = arguments["place"]
        dt = arguments["date_time"]
        return (
            Place(place["name"], float(place["latitude"]), float(place["longitude"]),
                  float(place["timezone_offset"])),
            DateTimeSpec(int(dt["year"]), int(dt["month"]), int(dt["day"]),
                         int(dt.get("hour", 0)), int(dt.get("minute", 0)),
                         int(dt.get("second", 0))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolCallError(f"Invalid place/date_time argument: {exc}") from exc


def _with_birth(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"birth_data": _BIRTH_SCHEMA, **properties},
        "required": ["birth_data", *required],
    }


# ---------------------------------------------------------------------------
# Tool registrations
# ---------------------------------------------------------------------------

def _register(catalog: ToolCatalog, name: str, description: str, schema: dict[str, Any],
              fn: Callable[[dict[str, Any]], Any]) -> None:
    catalog.add(ToolSpec(name=name, description=description, parameters=schema,
                         handler=lambda arguments, _fn=fn: _fn(arguments)))


def build_local_catalog() -> ToolCatalog:
    catalog = ToolCatalog()

    _register(catalog, "get_rasi_chart",
              "Calculate the Vedic birth chart (Rasi / D1): ascendant, every planet's sign, "
              "degree, nakshatra, pada and house, plus house-wise occupancy.",
              _with_birth({}, []),
              lambda a: chart_service.rasi_chart(_args_birth(a)))

    _register(catalog, "get_divisional_chart",
              "Calculate a divisional (varga) chart. Divisors: 2 Hora, 3 Drekkana, "
              "7 Saptamsa (children), 9 Navamsa (marriage/strength), 10 Dasamsa (career), "
              "12 Dwadasamsa (parents), 24 Chaturvimsamsa (education), 30 Trimsamsa, "
              "60 Shashtyamsa.",
              _with_birth({
                  "divisor": {"type": "integer", "minimum": 1, "maximum": 60, "default": 9},
                  "chart_method": {"type": "integer", "default": 1},
              }, []),
              lambda a: chart_service.divisional_chart(
                  _args_birth(a), int(a.get("divisor", 9)), int(a.get("chart_method", 1))))

    _register(catalog, "get_special_lagnas",
              "Calculate special ascendants: Bhava, Hora, Ghati, Vighati, Pranapada, Indu "
              "(wealth), Sree, Bhrigu Bindu and Kunda lagna.",
              _with_birth({}, []),
              lambda a: chart_service.special_lagnas(_args_birth(a)))

    _register(catalog, "get_ashtakavarga",
              "Calculate Ashtakavarga bindu scores: per-planet Bhinnashtakavarga and combined "
              "Samudaya (signs above ~30 strong, below ~25 weak out of the 337 total).",
              _with_birth({}, []),
              lambda a: chart_service.ashtakavarga(_args_birth(a)))

    _register(catalog, "get_panchanga",
              "Calculate the Panchanga (Hindu almanac): tithi, nakshatra, yoga, karana, vaara, "
              "masa, samvatsara and ritu for a date, time and place.",
              _with_birth({}, []),
              lambda a: panchanga_service.panchanga(_args_birth(a)))

    _register(catalog, "get_sunrise_sunset",
              "Sunrise, sunset, moonrise, moonset, midday, midnight and day/night lengths.",
              {"type": "object", "properties": {"place": _PLACE_SCHEMA,
                                                "date_time": _DATETIME_SCHEMA},
               "required": ["place", "date_time"]},
              lambda a: panchanga_service.sunrise_sunset(*_args_place_dt(a)))

    _register(catalog, "get_rahu_kala",
              "Inauspicious periods for a day: Rahu Kalam, Yamaganda Kalam, Gulika Kalam.",
              {"type": "object", "properties": {"place": _PLACE_SCHEMA,
                                                "date_time": _DATETIME_SCHEMA},
               "required": ["place", "date_time"]},
              lambda a: panchanga_service.rahu_kala(*_args_place_dt(a)))

    _register(catalog, "get_muhurtha",
              "Auspicious and inauspicious muhurta windows: Abhijit, Brahma, Godhuli, Vijaya, "
              "Nishita, Sandhya and Durmuhurtam.",
              {"type": "object", "properties": {"place": _PLACE_SCHEMA,
                                                "date_time": _DATETIME_SCHEMA},
               "required": ["place", "date_time"]},
              lambda a: panchanga_service.muhurtha(*_args_place_dt(a)))

    _register(catalog, "get_planet_positions",
              "Sidereal positions of all nine grahas at a given date/time (use for transits).",
              _with_birth({}, []),
              lambda a: panchanga_service.planet_positions(_args_birth(a)))

    _register(catalog, "get_retrograde_planets",
              "Which planets are retrograde at a given date/time.",
              _with_birth({}, []),
              lambda a: panchanga_service.retrograde_planets(_args_birth(a)))

    _register(catalog, "get_vimsottari_dasha",
              "Calculate Vimsottari Dasha — the standard 120-year planetary period system "
              "seeded from the Moon's birth nakshatra. depth 1 = Maha (9 periods), 2 = +Antara, "
              "3 = +Pratyantara.",
              _with_birth({"depth": {"type": "integer", "minimum": 1, "maximum": 4,
                                     "default": 2}}, []),
              lambda a: dasha_service.vimsottari_dasha(_args_birth(a), int(a.get("depth", 2))))

    _register(catalog, "get_yogini_dasha",
              "Calculate Yogini Dasha — 8-lord, 36-year cycle used as a cross-check on Vimsottari.",
              _with_birth({"depth": {"type": "integer", "minimum": 1, "maximum": 3,
                                     "default": 2}}, []),
              lambda a: dasha_service.yogini_dasha(_args_birth(a), int(a.get("depth", 2))))

    _register(catalog, "get_ashtottari_dasha",
              "Calculate Ashtottari Dasha — conditional 108-year, 8-lord system; reports whether "
              "its applicability condition holds for this chart.",
              _with_birth({"depth": {"type": "integer", "minimum": 1, "maximum": 3,
                                     "default": 2}}, []),
              lambda a: dasha_service.ashtottari_dasha(_args_birth(a), int(a.get("depth", 2))))

    _register(catalog, "get_running_dasha",
              "The Vimsottari periods (Maha/Antar/Pratyantar) in force on a specific date — "
              "the core timing tool for any 'when' question.",
              _with_birth({"on_date": _DATETIME_SCHEMA}, ["on_date"]),
              lambda a: dasha_service.running_dasha(
                  _args_birth(a),
                  DateTimeSpec.parse(a["on_date"]) if isinstance(a["on_date"], str)
                  else DateTimeSpec(int(a["on_date"]["year"]), int(a["on_date"]["month"]),
                                    int(a["on_date"]["day"]))))

    _register(catalog, "get_compatibility",
              "Marriage compatibility between two people from their birth details. "
              "method 'North' = Ashta Koota out of 36, 'South' = Dasa Porutham.",
              {
                  "type": "object",
                  "properties": {
                      "first_birth": _BIRTH_SCHEMA,
                      "second_birth": _BIRTH_SCHEMA,
                      "method": {"type": "string", "enum": ["North", "South"],
                                 "default": "North"},
                  },
                  "required": ["first_birth", "second_birth"],
              },
              lambda a: compatibility_service.compatibility(
                  _args_birth({"birth_data": a["first_birth"]}),
                  _args_birth({"birth_data": a["second_birth"]}),
                  str(a.get("method", "North"))))

    _register(catalog, "get_yogas",
              "Identify planetary Yogas present in a chart (284 rules checked: Mahapurusha, "
              "Chandra, Dhana, Gaja-Kesari, Neecha Bhanga...). Each hit carries its classical "
              "condition and predicted effect.",
              _with_birth({"divisional_chart": {"type": "integer", "default": 1}}, []),
              lambda a: yoga_service.yogas(_args_birth(a), int(a.get("divisional_chart", 1))))

    _register(catalog, "get_doshas",
              "Check the eight classical doshas: Kala Sarpa, Manglik, Pitru, Guru Chandala, "
              "Ganda Moola, Kalathra, Ghata, Shrapit.",
              _with_birth({}, []),
              lambda a: yoga_service.doshas(_args_birth(a)))

    _register(catalog, "get_raja_yogas",
              "Identify Raja Yogas (Dharma-Karmadhipati, Vipareetha, Neecha Bhanga) and the "
              "kendra/trikona lord pairs that form them.",
              _with_birth({"divisional_chart": {"type": "integer", "default": 1}}, []),
              lambda a: yoga_service.raja_yogas(_args_birth(a),
                                                int(a.get("divisional_chart", 1))))

    _register(catalog, "get_shadbala",
              "Shadbala — the six-fold strength of the seven classical planets, with total "
              "virupas/rupas and a strength ratio against each planet's minimum requirement.",
              _with_birth({}, []),
              lambda a: strength_service.shadbala(_args_birth(a)))

    _register(catalog, "get_bhava_bala",
              "Bhava Bala — strength of each of the 12 houses (lord + direction + aspects).",
              _with_birth({}, []),
              lambda a: strength_service.bhava_bala(_args_birth(a)))

    _register(catalog, "get_vimsopaka_bala",
              "Vimsopaka Bala — how well each planet is placed across divisional charts "
              "(shad/sapta/dasa/shodasa varga groups, scored out of 20).",
              _with_birth({}, []),
              lambda a: strength_service.vimsopaka_bala(_args_birth(a)))

    return catalog
