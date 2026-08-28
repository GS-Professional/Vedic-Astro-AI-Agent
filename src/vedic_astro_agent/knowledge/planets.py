"""Planetary significations, dignities and period effects (traditional consensus)."""
from __future__ import annotations

SIGNIFICATIONS: dict[str, str] = {
    "Sun": "soul, father, authority, government, vitality, fame, leadership",
    "Moon": "mind, mother, emotions, public appeal, comfort, adaptability",
    "Mars": "energy, courage, siblings, property, engineering, initiative, competition",
    "Mercury": "intellect, speech, commerce, learning, adaptability, humour",
    "Jupiter": "wisdom, wealth, children, dharma, expansion, counsel, law",
    "Venus": "love, marriage, arts, luxury, diplomacy, vehicles, comforts",
    "Saturn": "discipline, labour, longevity, karma, delays, structure, service",
    "Rahu": "ambition, obsession, foreign matters, technology, sudden events",
    "Ketu": "detachment, past-life karma, research, liberation, sudden breaks",
}

NATURE: dict[str, str] = {
    "Sun": "malefic (mild)", "Moon": "benefic (waxing) / neutral (waning)",
    "Mars": "malefic", "Mercury": "benefic (unless with malefics)",
    "Jupiter": "benefic", "Venus": "benefic", "Saturn": "malefic",
    "Rahu": "malefic", "Ketu": "malefic",
}

# Classical dignity table: sign index (0=Aries) for exaltation/debilitation/own signs.
EXALTATION: dict[str, int] = {"Sun": 0, "Moon": 1, "Mars": 9, "Mercury": 5,
                              "Jupiter": 3, "Venus": 11, "Saturn": 6, "Rahu": 1, "Ketu": 7}
DEBILITATION: dict[str, int] = {"Sun": 6, "Moon": 7, "Mars": 3, "Mercury": 11,
                                "Jupiter": 9, "Venus": 5, "Saturn": 0, "Rahu": 7, "Ketu": 1}
OWN_SIGNS: dict[str, tuple[int, ...]] = {
    "Sun": (4,), "Moon": (3,), "Mars": (0, 7), "Mercury": (2, 5),
    "Jupiter": (8, 11), "Venus": (1, 6), "Saturn": (9, 10),
    "Rahu": (), "Ketu": (),
}

# Conventional dasha-period themes per lord (Vimsottari).
DASHA_EFFECTS: dict[str, str] = {
    "Sun": "authority, career visibility, father and government matters; health and ego need care",
    "Moon": ("mental focus, travel, public dealings, mother and family matters; "
             "mood swings possible"),
    "Mars": "property, land, siblings, enterprise and competition; temper and accidents need care",
    "Mercury": "education, writing, business, networking and skill-building; generally smooth",
    "Jupiter": "growth, wealth, children, wisdom, teaching and dharma; generally auspicious",
    "Venus": "relationships, marriage, arts, comforts and vehicles; pleasures and spending rise",
    "Saturn": "hard work, restructuring, delays that test patience, service and longevity work",
    "Rahu": "sudden gains and losses, foreign connections, ambition surges; verify before trusting",
    "Ketu": "introspection, detachment, research, spiritual work; abrupt endings clear the path",
}

DASHA_QUALITY: dict[str, str] = {
    "Sun": "mixed", "Moon": "mixed", "Mars": "active-demanding", "Mercury": "generally favourable",
    "Jupiter": "generally favourable", "Venus": "generally favourable",
    "Saturn": "demanding", "Rahu": "volatile", "Ketu": "renouncing",
}


def dignity_of(planet: str, rasi_index: int) -> str:
    """Classical dignity of a planet in a sign (sign index 0=Aries)."""
    if EXALTATION.get(planet) == rasi_index:
        return "exalted"
    if DEBILITATION.get(planet) == rasi_index:
        return "debilitated"
    if rasi_index in OWN_SIGNS.get(planet, ()):
        return "in own sign"
    return ""


def planet_blurb(planet: str) -> str:
    return f"{planet} signifies {SIGNIFICATIONS.get(planet, 'general matters')}"
