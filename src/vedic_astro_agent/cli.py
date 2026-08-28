"""Command line interface.

Examples:
    veda ask "What does my chart say about my career?" \
        --dob 1996-12-07 --tob 10:30 --place "Chennai, IN" \
        --lat 13.0827 --lon 80.2707 --tz 5.5

    veda ask "Am I compatible with someone born 1997-03-15 08:00 in Mumbai?" \
        --dob 1996-12-07 --tob 10:30 --place "Chennai, IN" \
        --dob2 1997-03-15 --tob2 08:00 --place2 "Mumbai, IN" \
        --lat2 19.076 --lon2 72.8777 --tz2 5.5

    veda ask "What is today's panchanga?" --dob 2026-08-27 --tob 06:00 \
        --place "Chennai, IN" --lat 13.0827 --lon 80.2707 --tz 5.5

    veda tools          # list the computation tool catalog
    veda ask ... --json # machine-readable full agent trace
"""
from __future__ import annotations

import argparse
import json
import sys

from vedic_astro_agent.agent.astrologer import VedicAstrologerAgent
from vedic_astro_agent.computation.models import BirthData, DateTimeSpec, Place
from vedic_astro_agent.config import AgentConfig, build_backend
from vedic_astro_agent.llm.openai_compat import llm_from_env


def _parse_date(text: str | None) -> DateTimeSpec | None:
    return DateTimeSpec.parse(text) if text else None


def _build_birth(args, suffix: str) -> BirthData | None:
    dob = getattr(args, f"dob{suffix}", None)
    tob = getattr(args, f"tob{suffix}", None)
    place_name = getattr(args, f"place{suffix}", None)
    lat = getattr(args, f"lat{suffix}", None)
    lon = getattr(args, f"lon{suffix}", None)
    tz = getattr(args, f"tz{suffix}", None)
    if not dob or not place_name:
        return None
    if lat is None or lon is None or tz is None:
        # Try the planner's gazetteer so common cities work without coordinates.
        from vedic_astro_agent.agent.planner import CITY_GAZETTEER

        hit = CITY_GAZETTEER.get(place_name.split(",")[0].strip().lower())
        if hit and (lat is None):
            lat, lon, tz = hit
        else:
            raise SystemExit(
                f"--lat{suffix}/--lon{suffix}/--tz{suffix} are required when the place "
                "is not in the built-in gazetteer")
    date_time = _parse_date(dob)
    if tob:
        t = _parse_date(f"2000-01-01 {tob}")
        assert t is not None
        date_time = DateTimeSpec(date_time.year, date_time.month, date_time.day,
                                 t.hour, t.minute, t.second)
    assert date_time is not None
    return BirthData(Place(place_name, float(lat), float(lon), float(tz)), date_time)


def _add_birth_arguments(parser: argparse.ArgumentParser, suffix: str = "") -> None:
    parser.add_argument(f"--dob{suffix}", help="Date of birth YYYY-MM-DD")
    parser.add_argument(f"--tob{suffix}", help="Time of birth HH:MM[:SS] (local)")
    parser.add_argument(f"--place{suffix}", help="Birth place, e.g. 'Chennai, IN'")
    parser.add_argument(f"--lat{suffix}", type=float, help="Latitude (decimal degrees)")
    parser.add_argument(f"--lon{suffix}", type=float, help="Longitude (decimal degrees)")
    parser.add_argument(f"--tz{suffix}", type=float, help="UTC offset in hours, e.g. 5.5")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="veda",
                                     description="Agentic Vedic astrologer grounded in "
                                                 "PyJHora computations")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask the agent a question")
    ask.add_argument("query", help="Your question in plain language")
    _add_birth_arguments(ask)
    _add_birth_arguments(ask, "2")
    ask.add_argument("--ayanamsa", default=None, help="Ayanamsa mode (default LAHIRI)")
    ask.add_argument("--backend", choices=["local", "mcp"], default=None,
                     help="Tool backend override (default from VEDIC_TOOL_BACKEND)")
    ask.add_argument("--json", action="store_true", help="Emit the full agent trace as JSON")
    ask.add_argument("--plan-only", action="store_true",
                     help="Show the computation plan without executing it")
    ask.add_argument("--no-llm", action="store_true",
                     help="Force deterministic mode even if OPENAI_API_KEY is set")
    ask.add_argument("--reference-date", default=None,
                     help="Pretend today is YYYY-MM-DD (timing queries)")

    tools = sub.add_parser("tools", help="List the tool catalog")
    tools.add_argument("--backend", choices=["local", "mcp"], default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AgentConfig.from_env()
    if getattr(args, "backend", None):
        config = AgentConfig(ayanamsa=config.ayanamsa, tool_backend=args.backend,
                             mcp_command=config.mcp_command, mcp_url=config.mcp_url,
                             mcp_api_key=config.mcp_api_key,
                             max_tool_rounds=config.max_tool_rounds)
    backend = build_backend(config)

    if args.command == "tools":
        catalog = backend.list_tools()
        for spec in catalog.manifest():
            fn = spec["function"]
            print(f"{fn['name']}\n    {fn['description']}\n")
        print(f"{len(catalog)} tools available on backend '{backend.name}'")
        backend.close()
        return 0

    try:
        birth = _build_birth(args, "")
        birth2 = _build_birth(args, "2")
    except ValueError as exc:
        print(f"Invalid birth input: {exc}", file=sys.stderr)
        return 2
    if birth and args.ayanamsa:
        birth = BirthData(birth.place, birth.date_time, args.ayanamsa)
    if birth2 and args.ayanamsa:
        birth2 = BirthData(birth2.place, birth2.date_time, args.ayanamsa)

    llm = None if args.no_llm else llm_from_env()
    reference_date = (DateTimeSpec.parse(args.reference_date).as_date_tuple()
                      if args.reference_date else None)
    from datetime import date as _date

    ref = _date(*reference_date) if reference_date else None

    agent = VedicAstrologerAgent(backend=backend, llm=llm, reference_date=ref)

    if args.plan_only and birth:
        from vedic_astro_agent.agent.planner import Planner

        understanding = Planner(reference_date=ref).understand(
            args.query, birth_data=birth, second_birth_data=birth2)
        plan = Planner(reference_date=ref).plan(understanding)
        print(f"Intents: {understanding.intents}   Topics: {understanding.topics}")
        for i, step in enumerate(plan.steps, 1):
            print(f"{i:2d}. {step.tool}  <- {step.reason}")
        backend.close()
        return 0

    response = agent.ask(args.query, birth_data=birth, second_birth_data=birth2)

    if args.json:
        print(json.dumps(response.to_dict(), indent=2, default=str))
    elif response.clarifying_question:
        print(response.clarifying_question)
    else:
        print(response.reading)
        print("\n--- execution ---")
        print(response.trace.summary())
        print(f"grounding: {response.grounding_summary}")
        for w in response.warnings:
            print(f"warning: {w}", file=sys.stderr)

    backend.close()
    return 0 if response.grounding_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
