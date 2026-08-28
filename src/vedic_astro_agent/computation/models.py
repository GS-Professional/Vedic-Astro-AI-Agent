"""Typed, validated inputs for every computation.

Plain dataclasses on purpose: the computation layer has no dependency on pydantic so it
can run anywhere PyJHora runs. Validation happens at construction time, so a bad input
fails loudly before any Swiss Ephemeris call is made.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from vedic_astro_agent.computation.naming import AYANAMSA_CHOICES_HINT

_VALID_AYANAMSAS = {
    "LAHIRI",
    "RAMAN",
    "KP",
    "SURYASIDDHANTA",
    "SURYASIDDHANTA_MSUN",
    "ARYABHATA",
    "ARYABHATA_MSUN",
    "SS_REVATI",
    "SS_CITRA",
    "TRUE_CITRA",
    "TRUE_REVATI",
    "TRUE_PUSHYA",
    "GALCENT_0SAG",
    "GALCENT_RGILBRAND",
    "GALACTIC_CENTER",
    "DELUCE",
    "JN_BHASIN",
    "BABYL_HUBER",
    "BABYL_KUGLER1",
    "BABYL_KUGLER2",
    "BABYL_KUGLER3",
    "FAGAN_BRADLEY",
    "J2000",
    "J1900",
    "B1950",
}


class BirthDataError(ValueError):
    """Raised when birth input is invalid or incomplete."""


@dataclass(frozen=True)
class Place:
    """Geographic location with an explicit UTC offset — nothing is geocoded."""

    name: str
    latitude: float
    longitude: float
    timezone_offset: float
    elevation: float = 0.0

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise BirthDataError("Place name must not be empty")
        if not -90.0 <= self.latitude <= 90.0:
            raise BirthDataError(f"latitude must be in [-90, 90], got {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise BirthDataError(f"longitude must be in [-180, 180], got {self.longitude}")
        if not -14.0 <= self.timezone_offset <= 14.0:
            raise BirthDataError(
                f"timezone_offset must be in [-14, 14] hours, got {self.timezone_offset}"
            )

    def describe(self) -> str:
        return (
            f"{self.name} (lat {self.latitude:.4f}, lon {self.longitude:.4f}, "
            f"UTC{'+' if self.timezone_offset >= 0 else ''}{self.timezone_offset:g})"
        )


@dataclass(frozen=True)
class DateTimeSpec:
    """Local wall-clock date and time."""

    year: int
    month: int
    day: int
    hour: int = 0
    minute: int = 0
    second: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise BirthDataError(f"month must be 1..12, got {self.month}")
        if not 1 <= self.day <= 31:
            raise BirthDataError(f"day must be 1..31, got {self.day}")
        if not 0 <= self.hour <= 23:
            raise BirthDataError(f"hour must be 0..23, got {self.hour}")
        if not 0 <= self.minute <= 59:
            raise BirthDataError(f"minute must be 0..59, got {self.minute}")
        if not 0 <= self.second <= 59:
            raise BirthDataError(f"second must be 0..59, got {self.second}")

    def as_date_tuple(self) -> tuple[int, int, int]:
        return (self.year, self.month, self.day)

    def as_time_tuple(self) -> tuple[int, int, int]:
        return (self.hour, self.minute, self.second)

    def iso(self) -> str:
        return (
            f"{self.year:04d}-{self.month:02d}-{self.day:02d} "
            f"{self.hour:02d}:{self.minute:02d}:{self.second:02d}"
        )

    @classmethod
    def parse(cls, text: str) -> DateTimeSpec:
        """Parse 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]'."""
        text = text.strip()
        date_part, _, time_part = text.partition(" ")
        y, m, d = (int(x) for x in date_part.split("-"))
        hour = minute = second = 0
        if time_part:
            parts = [int(x) for x in time_part.split(":")]
            hour = parts[0]
            minute = parts[1] if len(parts) > 1 else 0
            second = parts[2] if len(parts) > 2 else 0
        return cls(y, m, d, hour, minute, second)


@dataclass(frozen=True)
class BirthData:
    """Place + local date/time + ayanamsa: the input to nearly every tool."""

    place: Place
    date_time: DateTimeSpec
    ayanamsa: str = field(default="LAHIRI")

    def __post_init__(self) -> None:
        mode = str(self.ayanamsa).upper().strip()
        object.__setattr__(self, "ayanamsa", mode)
        if mode not in _VALID_AYANAMSAS:
            raise BirthDataError(
                f"Unknown ayanamsa mode {mode!r}. {AYANAMSA_CHOICES_HINT}"
            )

    def summary(self) -> str:
        return f"Born {self.date_time.iso()} at {self.place.describe()} [{self.ayanamsa}]"
