"""House topics and classifications, plus house-lord resolution from computed charts."""
from __future__ import annotations

from vedic_astro_agent.knowledge.signs import lord_of_sign

TOPICS: dict[int, str] = {
    1: "self, body, personality, health, initiative",
    2: "wealth, family, speech, food, savings",
    3: "courage, siblings, communication, short travel, skills",
    4: "home, mother, property, vehicles, inner peace",
    5: "intelligence, children, education, creativity, speculation",
    6: "work, service, debts, disease, enemies, daily routine",
    7: "marriage, partnerships, public dealings, contracts",
    8: "longevity, transformation, occult, other's wealth, research",
    9: "dharma, fortune, father, higher learning, long travel",
    10: "career, status, actions, authority, public life",
    11: "gains, income, networks, elder siblings, fulfilment of desires",
    12: "expenses, losses, foreign lands, isolation, liberation",
}

KENDRA = (1, 4, 7, 10)
TRIKONA = (1, 5, 9)
UPACHAYA = (3, 6, 10, 11)
DUSTHANA = (6, 8, 12)

# Topic keywords that route a user's question to houses.
TOPIC_HOUSES: dict[str, tuple[int, ...]] = {
    "career": (10, 6, 2, 11),
    "wealth": (2, 11, 9, 5),
    "marriage": (7, 2, 4, 11),
    "love": (7, 5, 11, 2),
    "health": (1, 6, 8, 12),
    "children": (5, 9, 2, 11),
    "education": (4, 5, 9, 3),
    "parents": (4, 9, 2, 10),
    "property": (4, 2, 11, 3),
    "travel": (3, 9, 12, 7),
    "spirituality": (9, 12, 5, 8),
}


def house_lords(ascendant_rasi_index: int) -> dict[int, str]:
    """House number -> lord planet, derived from the computed ascendant sign."""
    return {h: lord_of_sign((int(ascendant_rasi_index) + h - 1) % 12) for h in range(1, 13)}


def classification(house: int) -> str:
    tags = []
    if house in KENDRA:
        tags.append("kendra")
    if house in TRIKONA:
        tags.append("trikona")
    if house in UPACHAYA:
        tags.append("upachaya")
    if house in DUSTHANA:
        tags.append("dusthana")
    return "/".join(tags) if tags else "neutral"
