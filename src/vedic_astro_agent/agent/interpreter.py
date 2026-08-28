"""Deterministic, evidence-grounded interpretation.

The interpreter never looks at the user's chart directly — it reads the
:class:`EvidenceLedger`, extracts :class:`ChartFacts`, and composes prose from those
facts plus the fixed traditional vocabulary in :mod:`vedic_astro_agent.knowledge`.
Every sentence therefore traces back to a computation; that is by construction what
the :class:`GroundingValidator` later confirms.
"""
from __future__ import annotations

from vedic_astro_agent.agent.evidence import ChartFacts, EvidenceLedger, extract_facts
from vedic_astro_agent.agent.planner import AgentPlan, QueryUnderstanding
from vedic_astro_agent.knowledge import houses as houses_kb
from vedic_astro_agent.knowledge import planets as planets_kb

_DISCLAIMER = (
    "This reading is for reflection and entertainment. It is generated from astronomical "
    "computations and traditional interpretive rules; it is not medical, legal or financial "
    "advice."
)


class Interpreter:
    """Composes the final reading strictly from ledger evidence + knowledge tables."""

    def interpret(self, plan: AgentPlan, ledger: EvidenceLedger) -> str:
        u = plan.understanding
        facts = extract_facts(ledger)
        sections: list[str] = []

        sections.append(self._header(u, facts))
        if facts.panchanga_full:
            sections.append(self._panchanga_section(facts))
        if facts.ascendant_sign:
            sections.append(self._identity(facts))
        sections.extend(self._topic_sections(u, facts, ledger))
        sections.append(self._timing_section(facts))
        sections.append(self._strength_section(facts))
        sections.append(self._yoga_dosha_section(facts))
        if "compatibility" in u.intents:
            sections.append(self._compatibility_section(facts, ledger))
        sections.append(self._remedies_section(facts))
        sections.append(_DISCLAIMER)

        body = "\n\n".join(s for s in sections if s)
        return f"{body}\n\n[Evidence: {len(ledger)} computations recorded; " \
               f"every figure above comes from them.]"

    # -- sections -------------------------------------------------------------

    @staticmethod
    def _header(u: QueryUnderstanding, facts: ChartFacts) -> str:
        bd = u.birth_data
        lines = [f"Query: {u.raw_query}",
                 f"Computed for: {bd.summary() if bd else '(birth data not provided)'}"]
        if u.topics:
            lines.append("Focus: " + ", ".join(u.topics))
        return "\n".join(lines)

    @staticmethod
    def _panchanga_section(facts: ChartFacts) -> str:
        """Render the computed daily almanac (used by panchanga/muhurta queries)."""
        parts = []
        p = facts.panchanga
        if p.get("tithi"):
            limbs = [f"tithi {p['tithi']}", f"nakshatra {p['nakshatra']}",
                     f"yoga {p.get('yoga', '')}", f"karana {p.get('karana', '')}",
                     f"weekday {p.get('vaara', '')}"]
            parts.append("Panchanga limbs: " + ", ".join(x for x in limbs if x) + ".")
        full = facts.panchanga_full or {}
        masa = (full.get("masa") or {}).get("name")
        if masa:
            samvatsara = (full.get("samvatsara") or {}).get("name", "")
            extra = f" Lunar month {masa}"
            if (full.get("masa") or {}).get("is_adhika_maasa"):
                extra += " (adhika maasa)"
            if samvatsara:
                extra += f", samvatsara {samvatsara}"
            parts.append(extra + f", ritu {full.get('ritu', '')}." if full.get("ritu")
                         else extra + ".")
        if facts.sunrise_sunset:
            ss = facts.sunrise_sunset
            parts.append(f"Sunrise {ss.get('sunrise')} / sunset {ss.get('sunset')} "
                         f"(day length {ss.get('day_length_hours')}h).")
        if facts.rahu_kala:
            rk = facts.rahu_kala
            periods = []
            for key, label in (("rahu_kalam", "Rahu Kalam"), ("yamaganda_kalam", "Yamaganda"),
                               ("gulika_kalam", "Gulika")):
                window = rk.get(key) or {}
                if window.get("start"):
                    periods.append(f"{label} {window['start']}\u2013{window['end']}")
            if periods:
                parts.append("Inauspicious windows: " + "; ".join(periods) + ".")
        return " ".join(parts)

    @staticmethod
    def _identity(facts: ChartFacts) -> str:
        parts = [f"Your ascendant (lagna) is {facts.ascendant_sign}."]
        if facts.moon_sign:
            moon_line = f"Your Moon sign is {facts.moon_sign}"
            if facts.moon_nakshatra:
                moon_line += f" (birth nakshatra {facts.moon_nakshatra})"
            parts.append(moon_line + ".")
        placements = []
        for planet in ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn",
                       "Rahu", "Ketu"):
            sign = facts.planet_signs.get(planet)
            house = facts.planet_houses.get(planet)
            if sign:
                bit = f"{planet} in {sign}"
                if house:
                    bit += f" (house {house})"
                dignity = planets_kb.dignity_of(planet, _sign_index(sign))
                if dignity:
                    bit += f", {dignity}"
                placements.append(bit)
        if placements:
            parts.append("Computed placements: " + "; ".join(placements) + ".")
        return " ".join(parts)

    def _topic_sections(self, u: QueryUnderstanding, facts: ChartFacts,
                        ledger: EvidenceLedger) -> list[str]:
        sections = []
        rasi = ledger.first_result("get_rasi_chart")
        if not rasi or not facts.ascendant_sign:
            return sections

        asc_rasi_index = (rasi.get("ascendant") or {}).get("rasi_index")
        if asc_rasi_index is None:
            return sections
        lords = houses_kb.house_lords(asc_rasi_index)

        for topic in u.topics or ["natal_overview"]:
            topic_houses = houses_kb.TOPIC_HOUSES.get(topic)
            if not topic_houses:
                continue
            lines = [f"Regarding {topic}: the relevant houses are "
                     + ", ".join(f"the {self._ord(h)} ({houses_kb.TOPICS[h]})"
                                 for h in topic_houses) + "."]
            # What sits in those houses (computed), and where their lords go (computed).
            occupancy = {h["house"]: h for h in rasi.get("houses", [])}
            for h in topic_houses[:2]:
                occupants = occupancy.get(h, {}).get("planets", [])
                lord = lords[h]
                lord_house = facts.planet_houses.get(lord)
                if occupants:
                    lines.append(f"House {h} holds {', '.join(occupants)} — "
                                 f"their nature directly colours {topic} matters.")
                if lord and lord_house:
                    lines.append(f"The {self._ord(h)} house lord {lord} sits in house "
                                 f"{lord_house}, linking {topic} to that life area.")
            sections.append(" ".join(lines))
        return sections

    @staticmethod
    def _timing_section(facts: ChartFacts) -> str:
        if not facts.running_periods:
            return ""
        md = facts.running_periods.get("Maha Dasha")
        ad = facts.running_periods.get("Antar Dasha")
        pd = facts.running_periods.get("Pratyantar Dasha")
        if not md:
            return ""
        text = f"Timing: you are currently running {md} Mahadasha"
        if ad:
            text += f" with {ad} Antardasha"
        if pd:
            text += f" / {pd} Pratyantardasha"
        text += "."
        effect = planets_kb.DASHA_EFFECTS.get(md)
        if effect:
            text += (f" {md} periods typically bring {effect} "
                     f"(classically a {planets_kb.DASHA_QUALITY.get(md, 'mixed')} phase).")
        if ad and ad != md:
            ad_effect = planets_kb.DASHA_EFFECTS.get(ad)
            if ad_effect:
                text += f" The {ad} sub-period colours it toward {ad_effect}."
        return text

    @staticmethod
    def _strength_section(facts: ChartFacts) -> str:
        if not facts.shadbala_ratio:
            return ""
        ranked = sorted(facts.shadbala_ratio.items(), key=lambda kv: kv[1], reverse=True)
        strong = [f"{n} ({r:.2f})" for n, r in ranked[:2]]
        weak = [f"{n} ({r:.2f})" for n, r in ranked[-2:] if r < 1.0]
        text = "Strength (Shadbala ratio vs. minimum required): strongest are " \
               + ", ".join(strong) + "."
        if weak:
            text += " Below their minimum requirement: " + ", ".join(weak) + \
                    " — their promises need support from favourable dashas."
        return text

    @staticmethod
    def _yoga_dosha_section(facts: ChartFacts) -> str:
        parts = []
        if facts.yogas_computed and facts.yoga_names:
            shown = facts.yoga_names[:6]
            parts.append("Yogas found: " + ", ".join(shown)
                         + (f" (+{len(facts.yoga_names) - len(shown)} more)"
                            if len(facts.yoga_names) > len(shown) else "")
                         + ". Each carries its classical condition and effect in the evidence.")
        if facts.doshas_computed:
            if facts.doshas_present:
                parts.append("Doshas present: " + ", ".join(facts.doshas_present) + ".")
            else:
                parts.append("No classical dosha was detected in this chart.")
        return " ".join(parts)

    @staticmethod
    def _compatibility_section(facts: ChartFacts, ledger: EvidenceLedger) -> str:
        result = ledger.first_result("get_compatibility")
        if not result:
            return ""
        total = result.get("total_score")
        maximum = result.get("maximum_score")
        interp = result.get("interpretation", "")
        first = (result.get("first_person") or {}).get("nakshatra", "?")
        second = (result.get("second_person") or {}).get("nakshatra", "?")
        text = (f"Compatibility: birth stars {first} and {second} score "
                f"{total}/{maximum} — {interp}.")
        low = [k for k, v in (result.get("factors") or {}).items()
               if isinstance(v, dict) and v.get("points", v.get("maximum_points", 0)) == 0
               and v.get("maximum_points")]
        if low:
            text += " Zero-scoring factors: " + ", ".join(low) + "."
        return text

    @staticmethod
    def _remedies_section(facts: ChartFacts) -> str:
        if not facts.doshas_present and not facts.shadbala_ratio:
            return ""
        advice = []
        if "manglik" in facts.doshas_present:
            advice.append("For Manglik dosha, tradition recommends Mangal-related observances "
                          "(Tuesday discipline, Hanuman worship) and matching with another "
                          "Manglik or Kumbh-vivah where applicable.")
        if "kala_sarpa" in facts.doshas_present:
            advice.append("For Kala Sarpa dosha, tradition suggests Rahu-Ketu shanti observances.")
        weak = [n for n, r in facts.shadbala_ratio.items() if r < 1.0]
        for planet in weak[:2]:
            advice.append(f"{planet} is below its strength requirement; strengthening it "
                          f"(its weekday, colours and charity associated with {planet}) is the "
                          "classical approach.")
        if not advice:
            return ""
        return "Remedial pointers (traditional): " + " ".join(advice)

    @staticmethod
    def _ord(h: int) -> str:
        return {1: "1st", 2: "2nd", 3: "3rd"}.get(h, f"{h}th")


def _sign_index(name: str) -> int:
    from vedic_astro_agent.computation.naming import RASI_NAMES

    try:
        return RASI_NAMES.index(name)
    except ValueError:
        return -1
