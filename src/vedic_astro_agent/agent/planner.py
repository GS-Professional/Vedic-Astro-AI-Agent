"""Query understanding and tool-call planning.

The planner turns a free-form question into:

1. a *query understanding* — intents, life topics, and any birth data it can extract
   (dates, times, and known cities);
2. an :class:`AgentPlan` — an ordered, de-duplicated list of computation-layer tool
   calls with a written *reason* for each, so the audit trail explains why every number
   in the reading was computed;
3. or a *clarification request* when birth data is missing — the agent never guesses.

Planning is deterministic (keyword + regex + recipes), so it runs offline and is fully
testable. An optional LLM can refine the understanding, but it cannot add facts: the
recipe still decides which computations happen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place

# ---------------------------------------------------------------------------
# Built-in gazetteer — explicit coordinates, never geocoded at runtime
# ---------------------------------------------------------------------------

CITY_GAZETTEER: dict[str, tuple[float, float, float]] = {
    # name (lowercase) -> (latitude, longitude, utc_offset_hours)
    "chennai": (13.0827, 80.2707, 5.5), "madras": (13.0827, 80.2707, 5.5),
    "mumbai": (19.0760, 72.8777, 5.5), "bombay": (19.0760, 72.8777, 5.5),
    "delhi": (28.6139, 77.2090, 5.5), "new delhi": (28.6139, 77.2090, 5.5),
    "bengaluru": (12.9716, 77.5946, 5.5), "bangalore": (12.9716, 77.5946, 5.5),
    "kolkata": (22.5726, 88.3639, 5.5), "hyderabad": (17.3850, 78.4867, 5.5),
    "pune": (18.5204, 73.8567, 5.5), "ahmedabad": (23.0225, 72.5714, 5.5),
    "jaipur": (26.9124, 75.7873, 5.5), "lucknow": (26.8467, 80.9462, 5.5),
    "kochi": (9.9312, 76.2673, 5.5), "cochin": (9.9312, 76.2673, 5.5),
    "patna": (25.5941, 85.1376, 5.5), "varanasi": (25.3176, 82.9739, 5.5),
    "coimbatore": (11.0168, 76.9558, 5.5), "madurai": (9.9252, 78.1198, 5.5),
    "visakhapatnam": (17.6868, 83.2185, 5.5), "indore": (22.7196, 75.8577, 5.5),
    "bhopal": (23.2599, 77.4126, 5.5), "nagpur": (21.1458, 79.0882, 5.5),
    "surat": (21.1702, 72.8311, 5.5), "vadodara": (22.3072, 73.1812, 5.5),
    "kanpur": (26.4499, 80.3319, 5.5), "chandigarh": (30.7333, 76.7794, 5.5),
    "guwahati": (26.1445, 91.7362, 5.5), "thiruvananthapuram": (8.5241, 76.9366, 5.5),
    "new york": (40.7128, -74.0060, -5.0), "los angeles": (34.0522, -118.2437, -8.0),
    "chicago": (41.8781, -87.6298, -6.0), "san francisco": (37.7749, -122.4194, -8.0),
    "london": (51.5074, -0.1278, 0.0), "paris": (48.8566, 2.3522, 1.0),
    "berlin": (52.5200, 13.4050, 1.0), "dubai": (25.2048, 55.2708, 4.0),
    "singapore": (1.3521, 103.8198, 8.0), "tokyo": (35.6762, 139.6503, 9.0),
    "sydney": (-33.8688, 151.2093, 10.0), "toronto": (43.6532, -79.3832, -5.0),
    "kathmandu": (27.7172, 85.3240, 5.75), "colombo": (6.9271, 79.8612, 5.5),
    "kuala lumpur": (3.1390, 101.6869, 8.0), "johannesburg": (-26.2041, 28.0473, 2.0),
}

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
           "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ToolCallPlan:
    tool: str
    arguments: dict[str, Any]
    reason: str


@dataclass
class QueryUnderstanding:
    raw_query: str
    intents: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    birth_data: BirthData | None = None
    second_birth_data: BirthData | None = None
    reference_date: date | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.birth_data is not None


@dataclass
class AgentPlan:
    understanding: QueryUnderstanding
    steps: list[ToolCallPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.understanding.missing)

    def clarification_question(self) -> str:
        missing = ", ".join(self.understanding.missing)
        return (
            "I need a few details before computing anything — the computation layer "
            f"requires exact inputs and I do not guess. Missing: {missing}. "
            "Please provide date of birth (YYYY-MM-DD), birth time (HH:MM), and birth "
            "place with country (e.g. 'Chennai, IN'). Accuracy of the birth time matters: "
            "4 minutes of clock error shifts the ascendant by about 1 degree."
        )

    def tool_names(self) -> list[str]:
        return [s.tool for s in self.steps]


# ---------------------------------------------------------------------------
# Intent & topic tables
# ---------------------------------------------------------------------------

_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("compatibility", ("compatibility", "match making", "matching", "porutham", "koota",
                       "marriage match", "kundali milan", "compatible")),
    ("panchanga", ("panchanga", "panchang", "muhurta", "muhurtha", "rahu kaal", "rahu kalam",
                   "sunrise", "sunset", "auspicious time", "today's", "todays", "almanac",
                   "tithi today", "what is the tithi")),
    ("timing", ("when will", "when can", "when is", "timing", "period", "dasha", "dasa",
                "this year", "next year", "in 20", "month", "forecast", "transit")),
    ("career", ("career", "job", "profession", "promotion", "business", "employment",
                "work", "salary", "boss", "startup")),
    ("wealth", ("money", "wealth", "finance", "income", "rich", "savings", "gains",
                "investment", "debt", "loan")),
    ("marriage", ("marriage", "marry", "spouse", "husband", "wife", "wedding", "love",
                  "relationship", "partner", "divorce")),
    ("health", ("health", "illness", "disease", "recovery", "surgery", "wellbeing",
                "well-being", "medical")),
    ("children", ("children", "child", "kids", "pregnancy", "conception", "progeny",
                  "baby", "son", "daughter")),
    ("education", ("education", "studies", "exam", "college", "university", "degree",
                   "learning", "school")),
    ("remedies", ("remedy", "remedies", "gemstone", "gem stone", "gem", "upay", "upaya",
                  "parihar", "mantra", "donation")),
    ("natal_overview", ("overview", "about me", "my chart", "my kundali", "kundli",
                        "horoscope", "birth chart", "tell me about myself", "life reading",
                        "who am i", "personality", "my life")),
]


class Planner:
    """Deterministic query → computation-plan converter."""

    def __init__(self, reference_date: date | None = None) -> None:
        # Injectable clock keeps tests deterministic; production uses today.
        self._reference_date = reference_date

    # -- understanding -------------------------------------------------------

    def understand(self, query: str, birth_data: BirthData | None = None,
                   second_birth_data: BirthData | None = None,
                   reference_date: date | None = None) -> QueryUnderstanding:
        text = query.strip()
        lower = text.lower()
        u = QueryUnderstanding(raw_query=text)
        u.intents = [intent for intent, keys in _INTENT_KEYWORDS
                     if any(k in lower for k in keys)]
        if not u.intents:
            u.intents = ["natal_overview"]
        u.topics = self._topics_for(u.intents)

        ref = reference_date or self._reference_date or datetime.now().date()
        u.reference_date = ref

        # Explicit birth data wins; otherwise try to extract it from the query text.
        u.birth_data = birth_data or self._extract_birth(text)
        if u.birth_data is None:
            u.missing = self._missing_fields(text)

        if "compatibility" in u.intents:
            u.second_birth_data = second_birth_data
            if second_birth_data is None:
                u.missing.append("second person's birth details (date, time, place)")
        return u

    @staticmethod
    def _topics_for(intents: list[str]) -> list[str]:
        topics = []
        for intent in intents:
            if intent in ("career", "wealth", "marriage", "health", "children",
                          "education"):
                topics.append(intent)
        return topics

    def _missing_fields(self, text: str) -> list[str]:
        missing = []
        if not _find_date(text):
            missing.append("date of birth")
        if not _find_time(text):
            missing.append("birth time")
        if not self._find_city(text):
            missing.append("birth place")
        return missing or ["complete birth details (date, time, place)"]

    # -- birth-data extraction -----------------------------------------------

    @staticmethod
    def _find_city(text: str) -> str | None:
        lower = text.lower()
        best: str | None = None
        for name in CITY_GAZETTEER:
            if re.search(rf"\b{re.escape(name)}\b", lower):
                if best is None or len(name) > len(best):
                    best = name
        return best

    def _extract_birth(self, text: str) -> BirthData | None:
        d = _find_date(text)
        t = _find_time(text)
        city = self._find_city(text)
        if not (d and t and city):
            return None
        lat, lon, tz = CITY_GAZETTEER[city]
        display = city.title()
        year, month, day = d
        hour, minute = t
        return BirthData(Place(display, lat, lon, tz),
                         DateTimeSpec(year, month, day, hour, minute))

    # -- planning -------------------------------------------------------------

    def plan(self, understanding: QueryUnderstanding) -> AgentPlan:
        plan = AgentPlan(understanding=understanding)
        if understanding.missing:
            plan.notes.append("missing birth inputs — clarification required before computing")
            return plan

        bd_args = self._birth_args(understanding.birth_data)
        seen: set[tuple[str, str]] = set()

        def add(tool: str, arguments: dict[str, Any], reason: str) -> None:
            import json
            key = (tool, json.dumps(arguments, sort_keys=True, default=str))
            if key in seen:
                return
            seen.add(key)
            plan.steps.append(ToolCallPlan(tool=tool, arguments=arguments, reason=reason))

        intents = understanding.intents
        topics = understanding.topics
        ref = understanding.reference_date or datetime.now().date()

        if "panchanga" in intents and not topics and "timing" not in intents:
            add("get_panchanga", {"birth_data": bd_args},
                "Panchanga limbs define the day's quality")
            add("get_rahu_kala", self._place_dt_args(understanding.birth_data),
                "Inauspicious windows to avoid")
            add("get_muhurtha", self._place_dt_args(understanding.birth_data),
                "Auspicious windows available today")
            add("get_sunrise_sunset", self._place_dt_args(understanding.birth_data),
                "Day/night boundaries anchor all panchanga times")
            return plan

        # Everything natal starts with the D1 chart — the anchor for all other evidence.
        add("get_rasi_chart", {"birth_data": bd_args},
            "D1 chart: ascendant, planetary signs/houses — the anchor for every other step")
        add("get_shadbala", {"birth_data": bd_args},
            "Shadbala quantifies which planets can actually deliver their promises")
        add("get_vimsottari_dasha", {"birth_data": bd_args, "depth": 2},
            "Vimsottari periods frame when promises activate")

        if "natal_overview" in intents or not topics:
            add("get_bhava_bala", {"birth_data": bd_args},
                "House strengths show which life areas are supported")
            add("get_yogas", {"birth_data": bd_args},
                "Yogas are the classical promise-modifiers of a chart")
            add("get_doshas", {"birth_data": bd_args},
                "Doshas flag afflictions the reading must address")
            add("get_special_lagnas", {"birth_data": bd_args},
                "Special lagnas (Indu, Hora...) refine wealth and strength readings")

        if "timing" in intents or topics:
            add("get_running_dasha",
                {"birth_data": bd_args,
                 "on_date": {"year": ref.year, "month": ref.month, "day": ref.day}},
                f"Running periods as of {ref.isoformat()} — the active timing lens")

        if "career" in topics:
            add("get_divisional_chart", {"birth_data": bd_args, "divisor": 10},
                "D10 Dasamsa is the career divisional chart")
            add("get_raja_yogas", {"birth_data": bd_args},
                "Raja yogas indicate rise, authority and status in career")

        if "wealth" in topics:
            add("get_ashtakavarga", {"birth_data": bd_args},
                "Ashtakavarga bindus quantify house-wise strength for gains")
            add("get_special_lagnas", {"birth_data": bd_args},
                "Indu lagna is the classical wealth pointer")

        if "marriage" in topics:
            add("get_divisional_chart", {"birth_data": bd_args, "divisor": 9},
                "D9 Navamsa governs marriage quality and spouse")
            add("get_doshas", {"birth_data": bd_args},
                "Manglik/Kalathra doshas must be checked for marriage questions")

        if "health" in topics:
            add("get_bhava_bala", {"birth_data": bd_args},
                "House strengths for 1/6/8 show vitality and disease resistance")

        if "children" in topics:
            add("get_divisional_chart", {"birth_data": bd_args, "divisor": 7},
                "D7 Saptamsa is the children divisional chart")

        if "education" in topics:
            add("get_divisional_chart", {"birth_data": bd_args, "divisor": 24},
                "D24 Chaturvimsamsa is the education divisional chart")

        if "remedies" in intents:
            add("get_doshas", {"birth_data": bd_args},
                "Remedies target the doshas actually present")
            add("get_shadbala", {"birth_data": bd_args},
                "Weak planets (low strength ratio) are remedy candidates")

        if "compatibility" in intents and understanding.second_birth_data is not None:
            add("get_compatibility",
                {"first_birth": bd_args,
                 "second_birth": self._birth_args(understanding.second_birth_data),
                 "method": "North"},
                "Ashta Koota score (36 points) is the North Indian matching standard")

        plan.notes.append(
            f"{len(plan.steps)} computations planned across "
            f"{len({s.tool for s in plan.steps})} tools"
        )
        return plan

    # -- argument builders ------------------------------------------------------

    @staticmethod
    def _birth_args(bd: BirthData) -> dict[str, Any]:
        return {
            "place": {"name": bd.place.name, "latitude": bd.place.latitude,
                      "longitude": bd.place.longitude,
                      "timezone_offset": bd.place.timezone_offset},
            "date_time": {"year": bd.date_time.year, "month": bd.date_time.month,
                          "day": bd.date_time.day, "hour": bd.date_time.hour,
                          "minute": bd.date_time.minute, "second": bd.date_time.second},
            "ayanamsa": bd.ayanamsa,
        }

    def _place_dt_args(self, bd: BirthData) -> dict[str, Any]:
        args = self._birth_args(bd)
        return {"place": args["place"], "date_time": args["date_time"]}


# ---------------------------------------------------------------------------
# Date/time extraction helpers (module level so they are individually testable)
# ---------------------------------------------------------------------------


def _find_date(text: str) -> tuple[int, int, int] | None:
    # ISO: 1996-12-07 or 1996/12/7
    m = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return (y, mo, d)
    # 7 Dec 1996 / 7 December 1996 / Dec 7 1996
    m = re.search(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
                  r"[,\s]+(\d{4})\b", text, re.IGNORECASE)
    if m:
        return (int(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
                  r"[,\s]+(\d{1,2})[,\s]+(\d{4})\b", text, re.IGNORECASE)
    if m:
        return (int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
    # 07-12-1996 / 7/12/1996 (DD-MM-YYYY)
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return (y, mo, d)
    return None


def _find_time(text: str) -> tuple[int, int] | None:
    m = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        ampm = (m.group(4) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)
    return None
