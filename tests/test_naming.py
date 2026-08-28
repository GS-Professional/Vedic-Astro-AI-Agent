"""Name tables must stay aligned with PyJHora's index spaces."""
from vedic_astro_agent.computation.naming import (
    KARANA_NAMES,
    MASA_NAMES,
    NAKSHATRA_NAMES,
    PLANET_NAMES,
    RASI_NAMES,
    RITU_NAMES,
    VAARA_NAMES,
    YOGA_NAMES,
    karana_name,
    nakshatra_name,
    planet_name,
    rasi_name,
    tithi_name,
)


def test_table_lengths():
    assert len(PLANET_NAMES) == 9
    assert len(RASI_NAMES) == 12
    assert len(NAKSHATRA_NAMES) == 27
    assert len(YOGA_NAMES) == 27
    assert len(MASA_NAMES) == 12
    assert len(VAARA_NAMES) == 7
    assert len(RITU_NAMES) == 6
    assert len(KARANA_NAMES) == 11


def test_index_wrapping():
    assert rasi_name(0) == "Aries"
    assert rasi_name(12) == "Aries"
    assert nakshatra_name(1) == "Ashwini"
    assert nakshatra_name(27) == "Revati"
    assert nakshatra_name(28) == "Ashwini"
    assert planet_name("L") == "Ascendant (Lagna)"
    assert planet_name(7) == "Rahu"


def test_tithi_names():
    assert tithi_name(1).startswith("Shukla")
    assert tithi_name(15) == "Shukla Purnima"
    assert tithi_name(16).startswith("Krishna")
    assert tithi_name(30) == "Krishna Amavasya"


def test_karana_wrapping():
    assert karana_name(1) == "Kimstughna"
    assert karana_name(2) == "Bava"
    # Fixed-karana cycle repeats every 7 after the movable start.
    assert karana_name(9) == karana_name(2)
