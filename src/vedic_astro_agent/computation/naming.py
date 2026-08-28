"""Canonical name tables for every index PyJHora returns.

Rule: indices come from PyJHora, names come from here. PyJHora's bundled "English"
resource file mixes Tamil transliterations (Karthigai, Prathamai) and zodiac glyphs
(Sun☉, ♈Aries); an agent needs stable Sanskrit/English names instead, so the tables are
hardcoded and guarded by length assertions in the tests.

Index conventions (as PyJHora 4.8.7 returns them):
    planet id      0=Sun .. 6=Saturn, 7=Rahu, 8=Ketu, 'L' = ascendant row in charts
    rasi           0=Aries .. 11=Pisces
    nakshatra      1=Ashwini .. 27=Revati
    tithi          1..15 Shukla, 16..30 Krishna
    masa           1=Chaitra .. 12=Phalguna
    yoga           1=Vishkambha .. 27=Vaidhrithi
    karana         1..60
    vaara          0=Sunday .. 6=Saturday
    samvatsara     0..59
    ritu           0=Vasanta .. 5=Shishira
"""
from __future__ import annotations

PLANET_NAMES = ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu")

RASI_NAMES = (
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
)

NAKSHATRA_NAMES = (
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni",
    "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha",
    "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishtha",
    "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada", "Revati",
)

# Tithi 1..15 within a paksha; slot 15 is Purnima in Shukla and Amavasya in Krishna.
_TITHI_NAMES = (
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Purnima",
)

MASA_NAMES = (
    "Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada",
    "Ashwina", "Kartika", "Margashirsha", "Pausha", "Magha", "Phalguna",
)

YOGA_NAMES = (
    "Vishkambha", "Priti", "Ayushman", "Saubhagya", "Shobhana", "Atiganda",
    "Sukarman", "Dhriti", "Shula", "Ganda", "Vriddhi", "Dhruva", "Vyaghata",
    "Harshana", "Vajra", "Siddhi", "Vyatipata", "Variyan", "Parigha", "Shiva",
    "Siddha", "Sadhya", "Shubha", "Shukla", "Brahma", "Indra", "Vaidhrithi",
)

KARANA_NAMES = (
    "Kimstughna", "Bava", "Balava", "Kaulava", "Taitila", "Garaja", "Vanija",
    "Vishti", "Shakuni", "Chatushpada", "Naga",
)

VAARA_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")

RITU_NAMES = ("Vasanta (Spring)", "Greeshma (Summer)", "Varsha (Monsoon)",
              "Sharad (Autumn)", "Hemanta (Pre-winter)", "Shishira (Winter)")

AYANAMSA_CHOICES_HINT = (
    "LAHIRI is the Indian standard; RAMAN, KP and SURYASIDDHANTA are other common choices."
)


def planet_name(pid: object) -> str:
    if pid == "L":
        return "Ascendant (Lagna)"
    i = int(pid)  # type: ignore[arg-type]
    return PLANET_NAMES[i] if 0 <= i < len(PLANET_NAMES) else f"Planet-{i}"


def rasi_name(rid: int) -> str:
    return RASI_NAMES[int(rid) % 12]


def nakshatra_name(nid: int) -> str:
    return NAKSHATRA_NAMES[(int(nid) - 1) % 27]


def tithi_name(tid: int) -> str:
    tid = int(tid)
    within = (tid - 1) % 15
    if tid <= 15:
        return f"Shukla {_TITHI_NAMES[within]}"
    return f"Krishna {'Amavasya' if within == 14 else _TITHI_NAMES[within]}"


def tithi_paksha(tid: int) -> str:
    return "Shukla" if int(tid) <= 15 else "Krishna"


def yoga_name(yid: int) -> str:
    return YOGA_NAMES[(int(yid) - 1) % 27]


def karana_name(kid: int) -> str:
    # Fixed karanas repeat a 7-fold cycle after the first; PyJHora returns 1..60.
    i = int(kid)
    if i <= 1:
        return KARANA_NAMES[0]
    return KARANA_NAMES[1 + ((i - 2) % 7)]


def masa_name(mid: int) -> str:
    return MASA_NAMES[(int(mid) - 1) % 12]


def vaara_name(vid: int) -> str:
    return VAARA_NAMES[int(vid) % 7]


def ritu_name(rid: int) -> str:
    return RITU_NAMES[int(rid) % 6]


def samvatsara_name(sid: int) -> str:
    try:
        from jhora import utils

        names = ["".join(ch for ch in n if ch.isascii()).strip() for n in utils.YEAR_LIST]
        if len(names) == 60 and names[0]:
            return names[int(sid) % 60]
    except Exception:  # resource lists unavailable — fall back to the index
        pass
    return f"Samvatsara-{int(sid) % 60}"
