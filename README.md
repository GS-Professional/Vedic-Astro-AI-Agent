# Vedic-Astro-AI-Agent

An **agentic AI Vedic astrologer** that treats astrology as a computation problem.
It never recites memorised horoscopes and never invents placements: for every question
it **plans** which exact calculations are needed, runs them through the
[PyJHora](https://github.com/naturalstupid/PyJHora) engine (locally or through the
[pyjhora-mcp](https://github.com/chinmay-sh/pyjhora-mcp) MCP server), records each
result in an **evidence ledger**, and only then composes a reading — with a grounding
validator that rejects anything not supported by the computed evidence.

```
                 ┌───────────────────────────────────────────────────────────┐
                 │                     Agentic loop                          │
 user question ─▶│ understand ─▶ plan ─▶ compute ─▶ reflect ─▶ interpret ─▶ │─▶ grounded
                 │                │          │                    ▲  validate │   reading
                 └────────────────┼──────────┼────────────────────┼──────────┘
                                  │          ▼                    │
                        topic recipes   ┌──────────┐      every claim must
                        (career→D10,    │   tool   │      trace back to the
                        marriage→D9,…)  │  backend │      evidence ledger
                                        └────┬─────┘
                          ┌──────────────────┴───────────────────┐
                          ▼                                      ▼
                   LocalToolBackend                       McpToolBackend
                   PyJHora in-process          pyjhora-mcp server (stdio/HTTP)
                   Swiss Ephemeris             FastMCP · Swiss Ephemeris
```

## Why this design

Most "AI astrologer" demos ask a language model to *generate* the chart and the reading
in one step — which is exactly where hallucinated lagnas, wrong dasha dates and invented
yogas come from. This project inverts that:

1. **Computation first.** All astronomy/astrology (planetary longitudes, divisional
   charts, dashas, yogas, strengths, matching scores) comes from PyJHora, a library
   verified against ~6,800 test cases from P.V.R. Narasimha Rao's *Vedic Astrology — An
   Integrated Approach* and the Jagannatha Hora software.
2. **Planning as reasoning.** The agent's intelligence is spent deciding *what to
   compute* for the specific query (a career question gets D10 + raja yogas + running
   dashas; a marriage question gets D9 + doshas; a timing question gets the running
   Vimsottari periods), and each planned step carries a written reason.
3. **Grounding as a hard gate.** Readings are assembled from the ledger (or drafted by
   an LLM *from* the ledger), then a validator re-checks every factual claim — planet
   signs, ascendant, running dasha, scores — against the computed facts. Claims that
   contradict the evidence are discarded; an LLM that hallucinates gets overruled.

## How the two upstream repositories fit

| Repository | Role here |
|---|---|
| [naturalstupid/PyJHora](https://github.com/naturalstupid/PyJHora) | The **computation layer**. Pinned to `PyJHora==4.8.7`; the services in `src/vedic_astro_agent/computation/` wrap its low-level API (`charts.rasi_chart`, `vimsottari`, `strength.shad_bala`, `Ashtakoota`, …) and normalise its irregular return shapes into JSON-ready dicts. |
| [chinmay-sh/pyjhora-mcp](https://github.com/chinmay-sh/pyjhora-mcp) | The **tool-server deployment path**. `McpToolBackend` speaks MCP (stdio and HTTP) to a running `pyjhora-mcp` server and adopts its tool catalog, so the same agent can run against the remote server with zero code changes. Local tool names deliberately mirror that server's (`get_rasi_chart`, `get_vimsottari_dasha`, …). |

Use the local backend for a self-contained install; use the MCP backend when you want
the computation isolated in the pyjhora-mcp Docker service (no C toolchain needed on the
agent host).

## Feature overview

**21 computation tools**, grouped like the MCP server's:

| Category | Tools |
|---|---|
| Horoscope | `get_rasi_chart`, `get_divisional_chart` (D2–D60), `get_special_lagnas`, `get_ashtakavarga` |
| Panchanga | `get_panchanga`, `get_sunrise_sunset`, `get_rahu_kala`, `get_muhurtha`, `get_planet_positions`, `get_retrograde_planets` |
| Dasha | `get_vimsottari_dasha`, `get_yogini_dasha`, `get_ashtottari_dasha`, `get_running_dasha` |
| Yoga & Dosha | `get_yogas` (284 rules), `get_raja_yogas`, `get_doshas` (8 classical) |
| Strength | `get_shadbala`, `get_bhava_bala`, `get_vimsopaka_bala` |
| Compatibility | `get_compatibility` (Ashta Koota 36-point / Dasa Porutham) |

**Agent behaviours**

- Intent + topic classification (career, wealth, marriage, health, children, education,
  timing, panchanga/muhurta, compatibility, remedies, overview) with per-topic
  computation recipes and house-lord analysis.
- Birth-data extraction from free text (`7 Dec 1996 at 10:30 in Chennai`) plus a built-in
  gazetteer of ~45 cities; explicit coordinates always win.
- Clarifying questions instead of guesses when birth inputs are missing.
- Reflection: if the anchor D1 computation fails the agent retries with defaults
  relaxed, and refuses to produce a reading without ground truth.
- Optional LLM polish (any OpenAI-compatible endpoint) gated by the grounding validator.
- Full audit trail: `--json` emits the plan, per-step timing, evidence count and the
  grounding report.

## Quick start

Requires Python 3.10+ and a C toolchain for `pyswisseph` (`build-essential` + `swig` on
Debian/Ubuntu) unless you only use the MCP backend.

```bash
git clone https://github.com/GS-Professional/Vedic-Astro-AI-Agent.git
cd Vedic-Astro-AI-Agent
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# PyJHora does not ship the ~105MB Swiss Ephemeris — fetch it once:
./setup_ephe.sh              # puts *.se1 files in ./ephe
export VEDIC_EPHE_DIR=$PWD/ephe

veda ask "What does my chart say about my career?" \
    --dob 1996-12-07 --tob 10:30 --place "Chennai, IN" \
    --lat 13.0827 --lon 80.2707 --tz 5.5

pytest                       # 59 tests; computation tests skip if ephe is missing
```

More examples in [`examples/sample_session.md`](examples/sample_session.md).

### Talking to a pyjhora-mcp server instead

```bash
cp .env.example .env         # set VEDIC_TOOL_BACKEND=mcp and one of:
# VEDIC_MCP_COMMAND=fastmcp run /path/to/pyjhora-mcp/src/pyjhora_mcp/server.py
# VEDIC_MCP_URL=http://localhost:8000/mcp   (+ VEDIC_MCP_API_KEY if the server has one)

veda tools --backend mcp     # shows the remote server's tool catalog
veda ask "overview of my chart" --dob ... --backend mcp
```

### Optional LLM polish

```bash
export OPENAI_API_KEY=...            # or any OpenAI-compatible endpoint:
export VEDIC_LLM_BASE_URL=http://localhost:8080/v1
export VEDIC_LLM_MODEL=qwen2.5-7b-instruct
```

The LLM may rephrase the evidence-grounded draft but cannot change facts; any draft that
fails the grounding validator is discarded in favour of the deterministic reading (this
is covered by `tests/test_agent_e2e.py::test_llm_polish_gate_rejects_hallucination`).

## Accuracy notes

- Default ayanamsa is **LAHIRI**; any PyJHora mode works (`--ayanamsa RAMAN`, `KP`, …).
- Julian days follow PyJHora's convention (**local** wall-clock jd; UTC conversion is
  internal) — `transforms.py` documents this, and the test chart was independently
  cross-checked against raw Swiss Ephemeris calls.
- Birth time sensitivity is real: ~4 minutes ≈ 1° of ascendant. The agent asks for the
  time rather than assuming noon.
- Interpretations are traditional-consensus texts for reflection/entertainment; the CLI
  prints a disclaimer with every reading.

## Repository layout

```
src/vedic_astro_agent/
├── computation/     # PyJHora wrappers: models, runtime, naming, six services
├── tools/           # ToolSpec registry + local backend + MCP client backend
├── agent/           # planner, executor, evidence ledger, interpreter, orchestrator
├── knowledge/       # traditional significations, dignities, house topics
├── llm/             # optional OpenAI-compatible client (+ stub for tests)
├── config.py        # env-driven configuration
└── cli.py           # `veda` command
tests/               # 59 tests: golden chart values, dasha math, planner, grounding,
                     # MCP protocol (mock stdio server), end-to-end agent runs
```

## Roadmap

- Multi-turn conversation memory (chart stays computed once, follow-ups re-plan cheaply).
- Transit engine: current-transit-to-natal aspects as first-class tools.
- Muhurta *search* (find dates matching panchanga constraints) rather than single-day lookup.
- More dasha systems on demand (Narayana, Chara, Kalachakra) via the same tool contract.
- Web UI + HTTP API wrapper around the agent.

## License & credits

AGPL-3.0 (matching PyJHora's license). Chart mathematics by PyJHora, implementing
P.V.R. Narasimha Rao's *Vedic Astrology — An Integrated Approach*; ephemeris by the
Swiss Ephemeris. Tool surface and MCP integration patterns informed by
[chinmay-sh/pyjhora-mcp](https://github.com/chinmay-sh/pyjhora-mcp).
