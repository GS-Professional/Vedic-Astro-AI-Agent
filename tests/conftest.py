"""Shared fixtures. Computation tests skip cleanly when the ephemeris is absent."""
from __future__ import annotations

import pytest

from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place


def ephe_available() -> bool:
    try:
        from vedic_astro_agent.computation.runtime import check_ephemeris

        check_ephemeris()
        return True
    except Exception:
        return False


requires_ephe = pytest.mark.skipif(not ephe_available(),
                                     reason="Swiss Ephemeris files not installed "
                                            "(run ./setup_ephe.sh)")


@pytest.fixture(scope="session")
def chennai_birth() -> BirthData:
    """Reference chart: 1996-12-07 10:30 IST, Chennai. Independently cross-checked
    against raw swisseph (Lahiri): ascendant Capricorn ~21.3, Sun Scorpio, Moon Libra."""
    return BirthData(
        Place("Chennai, IN", 13.0827, 80.2707, 5.5),
        DateTimeSpec(1996, 12, 7, 10, 30, 0),
    )


@pytest.fixture(scope="session")
def mumbai_birth() -> BirthData:
    return BirthData(
        Place("Mumbai, IN", 19.0760, 72.8777, 5.5),
        DateTimeSpec(1997, 3, 15, 8, 0, 0),
    )
