"""Sign basics: lords and elements."""
from __future__ import annotations

from vedic_astro_agent.computation.naming import RASI_NAMES

# Sign index (0=Aries) -> lord planet.
LORDS: dict[int, str] = {0: "Mars", 1: "Venus", 2: "Mercury", 3: "Moon", 4: "Sun",
                         5: "Mercury", 6: "Venus", 7: "Mars", 8: "Jupiter",
                         9: "Saturn", 10: "Saturn", 11: "Jupiter"}

ELEMENTS: dict[int, str] = {0: "Fire", 1: "Earth", 2: "Air", 3: "Water", 4: "Fire",
                            5: "Earth", 6: "Air", 7: "Water", 8: "Fire", 9: "Earth",
                            10: "Air", 11: "Water"}


def lord_of_sign(rasi_index: int) -> str:
    return LORDS[int(rasi_index) % 12]


def sign_name(rasi_index: int) -> str:
    return RASI_NAMES[int(rasi_index) % 12]
