# REBUILD GUIDE — PyJHora → AI Vedic Astrologer (2-Step Pipeline)

This folder is the complete, verified deliverable. Follow these steps in **any clean
environment** (local Python 3.9+ or an arena agent workspace) and it works.

> **Target release line:** the PyJHora **dev** line — GitHub `master`, currently version
> **5.0**. Since V4.8.7 there have been **no PyPI releases**, so PyJHora is installed
> from source, not `pip install PyJHora`. See §1.

---

## 1. Install

```bash
# Python dependencies (SwissEph + geocoding + numpy) — PyJHora itself is NOT here:
pip install -r requirements.txt

# PyJHora dev release from GitHub master (applies the two packaging workarounds,
# installs source-only, then verifies the import):
bash install_pyjhora_dev.sh

# The Swiss-Ephemeris data files ship in the source tree but NOT in the pip install:
python setup_ephemeris.py        # ~50 MB, idempotent (safe to re-run)
```

> **Critical gotcha (ephemeris):** without the Swiss Ephemeris binary files,
> `panchanga`/`sunrise`/`samvatsara` throw `SwissEph file 'seplm48.se1' not found`.
> `setup_ephemeris.py` fetches them into PyJHora's `data/ephe/` folder.

> **Critical gotcha (dev-line packaging):** `pip install git+…` fails on master.
> `setup.py` expects `Readme.md` (the file is `readme.md`) and `setup.cfg` carries
> PEP 639 fields (`license-expression`/`license-files`) that pip/setuptools reject.
> `install_pyjhora_dev.sh` works around both, then installs with `--no-deps
> --force-reinstall`. Manual equivalent is in the script comments.

> **Critical gotcha (5.0 default ayanamsa):** PyJHora 5.0 changed its **import-time
> default ayanamsa to TRUE_PUSHYA** (4.8.7 defaulted to Lahiri). Both the engine and
> the test suite explicitly pin **LAHIRI**, so readings are unaffected — but any ad-hoc
> script you write against 5.0 must call `config.set_ayanamsa_mode("LAHIRI")` or it
> will silently compute on a different zodiac.

## 2. Verify (optional but recommended)

```bash
python test_coverage.py          # 485 self-contained assertions, engine self-audit
```
Expected: **all PASS / 0 FAIL** against the 5.0 dev line. (Clean setup kits ship
WITHOUT `outputs/boy_full.json`/`girl_full.json`: AUDIT 4 then prints `[SKIP]` for the
two artifact scans and the artifact-meta check records itself as skipped — 0 FAIL is
still expected; every computational audit runs on the fly from built-in birth data.) This confirms every section
computes and every decode is correct (positions, panchanga, karakas, vargas incl.
standard D150, special lagnas, upagrahas, sahams, karakamsa, muntha, arudhas, strengths,
yogas capability report, dasha catalog incl. per-system schema checks, fine_dasha, match,
muhurta, vratha, prediction texts + life-span bracket, alt engines, compat-patch
registry + `--stock` mode, update-check envelope, and the 5.0 experiments-absent
graceful path...). Golden longitudes are re-verified identical to the 4.8.7 baseline
under Lahiri (D1 lagna = 44.001, D72 = 288.092, D9 = 36.012).

## 3. Compute a chart

```bash
# FAST — clean prioritized summary for most readings
python compute_chart.py --name "Asha" \
    --lat 12.9141 --lon 74.8560 --tz 5.5 \
    --date 1990-05-15 --time 10:30:00 \
    --now 2026-09-03 --transit 2026-09-03 \
    --out chart.json

# FULL — everything (deep tables + fine dasha + marriage compat)
python compute_chart.py ... --full \
    --fine-dasha "2026-08-10,2028-07-05" --max-depth 4 \
    --match-date ... --match-time ... --match-lat ... --match-lon ... --match-tz ... \
    --out chart_full.json
```

Key flags: `--full`, `--fast-full`, `--now` (ROUND-8: defaults to TODAY; pass it
explicitly for reproducible runs), `--transit`, `--sections`, `--list-sections`,
`--place "City, Country"`, `--ayanamsa`, `--mean-nodes`,
`--fine-dasha "START,END" --max-depth N` (ROUND-10: date-only bounds normalize to
"YYYY-MM-DD 00:00" / "YYYY-MM-DD 23:59" so every calendar row is the canonical
"YYYY-MM-DD HH:MM" format), `--match-*` (ROUND-10: `--match-tz` omitted resolves the
partner's timezone from the PARTNER'S OWN COORDINATES — DST-aware, via the library's
`utils.get_place_timezone_offset` — with the native tz only as last resort, disclosed in
`match.timezone_note`; the Round-9 default-to-native behaviour silently forced IST
because `--tz` has an argparse default of 5.5. Upstream `Horoscope(timezone_offset=None)`
calls `exit()`, which the engine catches into a typed `__error__`),
`--gender {male,female}` / `--match-gender {male,female}` (ROUND-8: match directionality —
the native's data always fills the engine's `boy_*` slots; `--gender female` swaps the
inputs and labels them via `genders`/`whose`),
`--running-depth N` (ROUND-8: default **3** = maha→antar→pratyantar).
ROUND-9 FAST defaults: the `yogas` section now runs by default and `vargas` carries
D2+D3 alongside D7/D9/D10/D12; ROUND-14 added `chakras` — **12 content sections
without flags** (deep `--full` runs carry 40).
`--experiments [--experiments-deep] [--marriage-window Y1,Y2]`, `--alt-engines`,
`--prediction` (bundled library prose + life-span bracket; `--full` includes it),
`--dasha-year-duration 1..8` (year-length mode; restored after the run),
`--house-system {KN_RAO | 'P' Placidus | …}` (5.0 `const.BHAVA_METHODS`),
`--stock` (disable the compat patches — for reproducing raw upstream behaviour),
`--no-update-check` (skip the release check; see below).

### Update check (every run)
Unless `--no-update-check` is passed, the engine phones PyPI/GitHub once per run
(offline-safe, never blocks) and writes the result into the JSON `meta.update_check`
envelope: installed version, latest available, and whether an update is recommended.
This is how you learn a new dev release dropped so the wrapper can be re-verified and
patched against it.

## 4. Read with the AI

Hand the agent the planning brain + the reading brain + the JSON:
- query_playbooks.md   (Step 0 — settings lock + Vedic procedure checklists)
- astrologer_prompt.md (Step 2 — PROTOCOL 0 + the expert reading methodology)
- the chart JSON you generated

Instruction to paste:
FIRST: read query_playbooks.md and PLAN the question (Vedic procedures → CLI → JSON paths).
THEN:  read the chart JSON in full — long tables are evidence, not flavour.
THEN:  apply astrologer_prompt.md exactly — PROTOCOL 0 + Sections 1–8 — and output
       only the structured Section-7 reading (items 1–8), citing JSON paths for
       every claim. Windows, not fake precision.


> **Voice mandate (ROUND-14c):** the prompt's top `YOUR VOICE` block makes ANY agent
> speak as a practising professional Vedic astrologer — zero validity disclaimers
> ("not a science", "for entertainment only"), zero Western-astrology mixing, no
> mid-reading hedging against the shastra (uncertainty is expressed as chart-strength
> weighting only). Its §7 output rule and §8 self-check enforce this on every run.

---

## File map

| File | Role |
|------|------|
| `compute_chart.py` | **Step 1** — the PyJHora engine → decoded JSON factsheet. |
| `astrologer_prompt.md` | **Step 2** — the expert reading methodology / system prompt. |
| query_playbooks.md|Step 0 — the planning brain: settings lock + per-topic Vedic checklists (marriage / career / timing / money / children / health / muhurta / prashna / compatibility) mapped to CLI profiles + the JSON paths to read end-to-end. Marriage ≠ D9 only.|
| `install_pyjhora_dev.sh` | Installs PyJHora from GitHub master (dev line) with packaging workarounds. |
| `setup_ephemeris.py` | Swiss-Ephemeris downloader (idempotent). |
| `requirements.txt` | Python dependencies (SwissEph + geocoding; **PyJHora installed from source**). |
| `test_coverage.py` | Full-coverage self-audit (485 self-contained assertions incl. Rounds 7–19). |
| `sample_chart.json` | Ready-to-read sample output for an instant prompt test. |
| `outputs/boy_full.json`, `outputs/girl_full.json` | OPTIONAL verified FULL reference runs — NOT part of the clean setup kit (the suite self-verifies without them). |
| `README.md` | Full documentation (steps, options, coverage). |
| `COVERAGE_REPORT.md` | OPTIONAL history — section-by-section coverage + review dispositions (Rounds 8–14). Not in the clean setup kit. |
| `REVIEW_TRIAGE.md` | OPTIONAL history — adjudication of every review finding (Rounds 1–14). Not in the clean setup kit. |
| `REBUILD.md` | This guide. |

Clean setup kit = the 10 non-OPTIONAL files above (`compute_chart.py`,
`query_playbooks.md`, `astrologer_prompt.md`, `install_pyjhora_dev.sh`,
`setup_ephemeris.py`, `requirements.txt`, `test_coverage.py`, `sample_chart.json`,
`README.md`, `REBUILD.md`) — sufficient to install, verify and run the FULLY WORKING
system in a fresh environment (the 485-assertion suite skips only the two optional
artifact scans when `outputs/` is absent).

## Integrity defaults
- **Lahiri ayanamsa** (explicitly pinned — 5.0's import-time default is TRUE_PUSHYA),
  **Drik** (modern ephemeris), **TRUE Rahu/Ketu** (use `--mean-nodes` for Mean).
- **Whole-sign houses** (house 1 = the Ascendant sign).
- Raw PyJHora indices are **always decoded** to names/dates so the AI never guesses.
- Uncomputable fields become `"__error__"` and are acknowledged/skipped, never invented.
- Every wrapper-discovered upstream bug and every compat patch is **disclosed in
  `meta`** (`upstream_issue_status`, `wrapper_discovered_5_0_dev_bugs`,
  `compatibility_patches`), and the machine never silently falls back: fallbacks are
  labelled `"status": "unverified_fallback"` with a reason.

All Python files pass `pyflakes` (0 issues) and compile. `compute_chart.py` is a single
self-contained file with imports lazily inside functions, so a partial dependency won't
crash the whole run.
