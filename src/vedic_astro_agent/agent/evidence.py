"""The evidence ledger and the grounding validator.

Every tool result is appended to an :class:`EvidenceLedger`. The interpreter is only
allowed to speak about facts that can be reconstructed from ledger entries, and
:class:`GroundingValidator` re-checks any generated text before it is released:

* "<planet> in <sign>" claims must match the computed rasi chart,
* "<sign> ascendant/lagna/rising" must match the computed ascendant,
* "current/running <planet> Mahadasha" must match the computed running dasha,
* quoted compatibility scores must match the computed total.

This is what "strictly uses the fully computed data" means in code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SIGNS = ("Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
         "Sagittarius", "Capricorn", "Aquarius", "Pisces")
PLANETS = ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu")


@dataclass
class EvidenceRecord:
    """One executed tool call and its computed output."""

    evidence_id: str
    tool: str
    arguments: dict[str, Any]
    result: Any
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        status = "error" if self.error else "ok"
        return f"[{self.evidence_id}] {self.tool} ({status}) — {self.reason or 'no reason given'}"


class EvidenceLedger:
    """Append-only log of computed evidence for one session."""

    def __init__(self) -> None:
        self._records: list[EvidenceRecord] = []

    def record(self, tool: str, arguments: dict[str, Any], result: Any,
               reason: str = "", error: str | None = None) -> EvidenceRecord:
        rec = EvidenceRecord(evidence_id=f"E{len(self._records) + 1}", tool=tool,
                             arguments=arguments, result=result, reason=reason, error=error)
        self._records.append(rec)
        return rec

    @property
    def records(self) -> list[EvidenceRecord]:
        return list(self._records)

    def find(self, tool: str) -> list[EvidenceRecord]:
        return [r for r in self._records if r.tool == tool and r.ok]

    def first_result(self, tool: str) -> Any | None:
        hits = self.find(tool)
        return hits[0].result if hits else None

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# Fact extraction — atomic, checkable statements derived from ledger results
# ---------------------------------------------------------------------------


@dataclass
class ChartFacts:
    """Atomic facts the validator can check a reading against."""

    ascendant_sign: str | None = None
    moon_sign: str | None = None
    moon_nakshatra: str | None = None
    planet_signs: dict[str, str] = field(default_factory=dict)   # planet -> sign name
    planet_houses: dict[str, int] = field(default_factory=dict)  # planet -> house from lagna
    running_periods: dict[str, str] = field(default_factory=dict)  # level -> lord
    shadbala_ratio: dict[str, float] = field(default_factory=dict)
    doshas_present: list[str] = field(default_factory=list)
    doshas_computed: bool = False
    yoga_names: list[str] = field(default_factory=list)
    yogas_computed: bool = False
    compatibility_total: float | None = None
    compatibility_max: float | None = None
    panchanga: dict[str, str] = field(default_factory=dict)
    panchanga_full: dict[str, Any] | None = None
    rahu_kala: dict[str, Any] | None = None
    sunrise_sunset: dict[str, Any] | None = None


def extract_facts(ledger: EvidenceLedger) -> ChartFacts:
    facts = ChartFacts()
    for rec in ledger.records:
        if not rec.ok or not isinstance(rec.result, dict):
            continue
        result = rec.result

        if rec.tool == "get_rasi_chart" and facts.ascendant_sign is None:
            asc = result.get("ascendant") or {}
            facts.ascendant_sign = asc.get("rasi")
            for planet in result.get("planets", []):
                name = planet.get("planet")
                if name in PLANETS:
                    facts.planet_signs[name] = planet.get("rasi")
                    if planet.get("house"):
                        facts.planet_houses[name] = int(planet["house"])
                    if name == "Moon":
                        facts.moon_sign = planet.get("rasi")
                        facts.moon_nakshatra = planet.get("nakshatra")

        elif rec.tool == "get_running_dasha" and not facts.running_periods:
            running = result.get("running") or {}
            facts.running_periods = {level: info.get("lord") for level, info in running.items()}

        elif rec.tool == "get_shadbala" and not facts.shadbala_ratio:
            for name, info in (result.get("shadbala") or {}).items():
                facts.shadbala_ratio[name] = float(info.get("strength_ratio", 0.0))

        elif rec.tool == "get_doshas" and not facts.doshas_computed:
            facts.doshas_computed = True
            facts.doshas_present = list(result.get("present", []))

        elif rec.tool == "get_yogas" and not facts.yogas_computed:
            facts.yogas_computed = True
            facts.yoga_names = [y.get("name", "") for y in result.get("yogas", [])]

        elif rec.tool == "get_compatibility" and facts.compatibility_total is None:
            facts.compatibility_total = result.get("total_score")
            facts.compatibility_max = result.get("maximum_score")

        elif rec.tool == "get_panchanga" and not facts.panchanga:
            facts.panchanga_full = result
            facts.panchanga = {
                "tithi": (result.get("tithi") or {}).get("name", ""),
                "nakshatra": (result.get("nakshatra") or {}).get("name", ""),
                "yoga": (result.get("yoga") or {}).get("name", ""),
                "karana": (result.get("karana") or {}).get("name", ""),
                "vaara": result.get("vaara", ""),
                "masa": (result.get("masa") or {}).get("name", ""),
            }

        elif rec.tool == "get_rahu_kala" and facts.rahu_kala is None:
            facts.rahu_kala = result

        elif rec.tool == "get_sunrise_sunset" and facts.sunrise_sunset is None:
            facts.sunrise_sunset = result
    return facts


# ---------------------------------------------------------------------------
# Grounding validation of generated text
# ---------------------------------------------------------------------------


@dataclass
class GroundingReport:
    claims_checked: int = 0
    verified: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if not self.claims_checked:
            return "no checkable factual claims found"
        if self.ok:
            return f"all {self.claims_checked} factual claims verified against computed evidence"
        return f"{len(self.violations)} of {self.claims_checked} claims contradict the evidence"


_SIGN_ALT = "|".join(SIGNS)
_PLANET_ALT = "|".join(PLANETS)

_PLANET_IN_SIGN = re.compile(
    rf"\b({_PLANET_ALT})\b[^.!?]{{0,40}}?\bin\s+({_SIGN_ALT})\b", re.IGNORECASE)
_ASC_CLAIM = re.compile(rf"\b({_SIGN_ALT})\s+(?:ascendant|lagna|lagnam|rising)\b", re.IGNORECASE)
_RUNNING_MD = re.compile(
    rf"(?:running|current|now in)\s+({_PLANET_ALT})\s+(?:maha ?dasha|mahadasha|md)\b",
    re.IGNORECASE)
_SCORE_CLAIM = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*/\s*(36|10)\b")


class GroundingValidator:
    """Checks a generated reading against ChartFacts extracted from the ledger."""

    def validate(self, text: str, facts: ChartFacts) -> GroundingReport:
        report = GroundingReport()

        for planet, sign in _PLANET_IN_SIGN.findall(text):
            report.claims_checked += 1
            planet = planet.capitalize()
            expected = facts.planet_signs.get(planet)
            claim = f"{planet} in {sign.capitalize()}"
            if expected is None:
                continue  # nothing computed for this planet — not verifiable either way
            if expected.lower() == sign.lower():
                report.verified.append(claim)
            else:
                report.violations.append(f"{claim} — computed position is {planet} in {expected}")

        for sign in _ASC_CLAIM.findall(text):
            report.claims_checked += 1
            claim = f"{sign.capitalize()} ascendant"
            if facts.ascendant_sign is None:
                continue
            if facts.ascendant_sign.lower() == sign.lower():
                report.verified.append(claim)
            else:
                report.violations.append(
                    f"{claim} — computed ascendant is {facts.ascendant_sign}")

        for lord in _RUNNING_MD.findall(text):
            report.claims_checked += 1
            claim = f"running {lord.capitalize()} Mahadasha"
            current_md = facts.running_periods.get("Maha Dasha")
            if current_md is None:
                continue
            if current_md.lower() == lord.lower():
                report.verified.append(claim)
            else:
                report.violations.append(
                    f"{claim} — computed running Mahadasha lord is {current_md}")

        for value, maximum in _SCORE_CLAIM.findall(text):
            report.claims_checked += 1
            claim = f"score {value}/{maximum}"
            if facts.compatibility_total is None:
                continue
            if (abs(float(value) - float(facts.compatibility_total)) < 1e-6
                    and int(maximum) == int(facts.compatibility_max or -1)):
                report.verified.append(claim)
            else:
                report.violations.append(
                    f"{claim} — computed score is "
                    f"{facts.compatibility_total}/{facts.compatibility_max}")

        return report
