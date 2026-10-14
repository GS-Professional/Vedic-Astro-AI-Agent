# PyJHora → AI Vedic Astrologer — 2‑Step Pipeline

Turn the [PyJHora](https://github.com/naturalstupid/PyJHora) astronomical engine into
an expert‑grade Vedic reading using an AI agent (like **Arena.ai Agent Mode**).

The trick is a **clean split**, plus a **plan-first reading protocol**:

1. **Step 0 — `query_playbooks.md`** is the **planning brain**. For every question the
   reader gathers Vedic procedures (Parashari / Jaimini / Tajaka / labelled KP — never
   Western), maps them onto this engine's settings (default: **Lahiri**, Drik, true nodes,
   whole-sign houses), and names the CLI + JSON paths that must be read **end to end**.
2. **Step 1 — `compute_chart.py`** does *all* the heavy Vedic math with the **full**
   PyJHora capability and dumps a **flat, self‑consistent JSON fact sheet**. Real
   questions use `--query-profile deep|marriage|…` (always-deep). FAST is a smoke sheet.
3. **Step 2 — `astrologer_prompt.md`** is the **reading prompt**. PROTOCOL 0 forces
   plan → deep compute → end-to-end read → three-layer cross-reference (promise /
   confirm / timing), then the structured Section 7 reading — **without the AI ever
   needing to do astrology math or invent positions**.

This separation is what guarantees **accuracy without confusion**: the machine never
interprets, the interpreter never computes, and the reader is not allowed to glance
at D9 and stop.

---

## Why this works (the "robustness" reasoning)

- **One source of truth.** All numbers (sign, degree, house, dignity, lordships, yogas,
  doshas, dasha, transits, ashtakavarga) come from one JSON. The prompt *forbids* the AI
  from inventing or "correcting" anything and tells it to cite JSON paths.
- **Decoded, not raw.** Raw PyJHora index arrays (tithi/yoga/karana/nakshatra, dasha
  lord IDs, chara‑karakas) are remapped to *names and dates* so the model reads meaning,
  not `[23, 1.19, 25.3]`.
- **Always deep for a real question.** FAST is a smoke/default fact sheet. `--full`
  and `--query-profile deep|marriage|career|timing|…` add bhava‑bala/shadbala,
  ashtakavarga, raja‑yogas, 25 divisionals, Narayana, yogas_all, running dashas.
  A marriage reading is D1 7th + Venus + DK + UL/A7 + D9 + manglik three-frames +
  dasha + Narayana + gochara — **not D9 alone**. Settings lock: do not mix Lahiri
  whole-sign with KP cusps or Raman ayanamsa unless the JSON was computed that way. Further tiers are opt‑in: `--prediction` (the library's bundled
  interpretive prose + longevity bracket — `--full` includes it; default runs stay a pure
  fact sheet), `--alt-engines` (Surya‑Siddhanta crosschecks) and `--experiments`
  (research‑grade marriage‑window scorers, labelled EXPERIMENTAL everywhere they appear —
  on the 5.0 dev line the upstream package is gone, so each key carries an honest typed
  not‑available note; see the section table).
- **Everything wrapped in try/except.** A single module/failure never kills the file;
  it becomes an `"__error__": "..."` field the prompt is trained to acknowledge and skip.
- **Running dasha + transits.** The engine computes the *actual* running vimsottari chain
  and the transits at a chosen date, so the AI can answer **timing** questions.

---

## Install (once, in your own environment)

```bash
pip install -r requirements.txt      # pyswisseph + geocoding helpers
bash install_pyjhora_dev.sh          # PyJHora DEV line from GitHub master (version 5.0)
python setup_ephemeris.py            # download the Swiss-Ephemeris files PyJHora needs
```

> **Release-line note:** this wrapper targets the PyJHora **dev releases** — GitHub
> `master`, currently version **5.0**. There have been **no PyPI releases since V4.8.7**,
> so PyJHora installs from source; `install_pyjhora_dev.sh` applies the two packaging
> workarounds master needs (readme case, PEP 639 fields) and verifies the import.
> Every run of `compute_chart.py` performs a small **update check** (offline-safe) and
> records the installed-vs-latest status in `meta.update_check`, so you always know when
> a new dev release lands and the wrapper can be re-verified against it
> (`--no-update-check` skips it).

> **Key gotcha:** the PyJHora wheel ships **without** the Swiss Ephemeris binary files,
> so `sunrise/sunset` (and therefore panchanga/samvatsara) throw
> `SwissEph file 'seplm48.se1' not found`. `setup_ephemeris.py` fixes this and is idempotent.

> **Key gotcha (5.0):** PyJHora 5.0 changed its **import-time default ayanamsa to
> TRUE_PUSHYA**. This engine and the test suite explicitly pin **LAHIRI** (our verified
> default), so readings are unaffected — but any ad-hoc script you write against 5.0 must
> call `config.set_ayanamsa_mode("LAHIRI")` or it will silently use a different zodiac.

💡 This workspace already has everything installed and verified; you can run the
commands below directly here.

---

## Step 1 — Compute

```bash
# DEEP (recommended for any real question — plan-first profile)
python compute_chart.py --name "Asha" \
    --lat 12.9141 --lon 74.8560 --tz 5.5 \
    --date 1990-05-15 --time 10:30:00 \
    --query-profile deep --now 2026-09-11 --transit 2026-09-11 \
    --out chart.json

# Topic profiles (same deep sheet; reading checklist is in query_playbooks.md)
python compute_chart.py ... --query-profile marriage --match-date ... --out chart.json
python compute_chart.py ... --query-profile timing --fine-dasha "2026-01-01,2027-12-31" --max-depth 4

# FAST (smoke / sample_chart.json only — not a professional reading)
python compute_chart.py --name "Asha" \
    --lat 12.9141 --lon 74.8560 --tz 5.5 \
    --date 1990-05-15 --time 10:30:00 \
    --now 2026-09-03 --transit 2026-09-03 \
    --out chart.json

# FULL (every section including prediction; experiments still need --experiments)
python compute_chart.py ... --full --out chart_full.json

# Place name instead of coordinates (uses Nominatim geocoder)
python compute_chart.py --place "Mangaluru, India" ...

# Other options
#   --query-profile deep|marriage|career|timing|health|children|money|muhurta|prashna|compatibility
#   --ayanamsa LAHIRI|KP|RAMAN|SURYA_SIDDHANTA|...
#   --mean-nodes        (default uses TRUE Rahu/Ketu nodes)
#   --fine-dasha "START,END" --max-depth 4   # fine L3/L4 sub-period calendar for a window
#   --match-date/--match-time/--match-lat/--match-lon/--match-tz/--match-place  # Ashtakoota compatibility
#   --gender male|female    # the NATIVE's gender (default male) — steers match directionality
#                           # and gendered yogas (Mahabhagya); mirrored into __meta__
#   --match-gender male|female  # the PARTNER's gender (default female)
#   --now YYYY-MM-DD        # the "now" for running dashas/tajaka/transits (ROUND-8: defaults to TODAY;
#                           # __meta__.now_defaulted_to_today records it)
#   --transit-conjunctions  # also compute all 36 planet-pair next-conjunction dates (default off)
#   --transit-no-events     # skip per-planet retrograde/stationary/entry event dates (default: computed)
#   --running-depth 3       # dhasa_level_index passed to each running-dasha system (ROUND-8: default 3
#                           # = maha→antar→pratyantar; 2 = old behaviour)
#   --annual-year N         # explicit Tajaka annual year (>=1); else derived from --now
#   --tajaka-month N        # Tajaka month within the annual year (1..12); recomputes the
#                           # monthly chart/Lord of the Month for that month (once considered)
#   --fast-full             # like --full but skips the heaviest experimental sections
#                           # (yogas_all, varga_transits, running_dashas) for faster runs
#   --sixty-hour-count N    # sixty-hour subdivision count for the Tajaka sixty-hour chart (default 1)
#   --varga-division N      # divisional chart factor for the varga_transits section (default 9)
#   --experiments           # EXPERIMENTAL research predictors (marriage-window scorers,
#                           # double-transit + sensitive-point scans; adds ~10s)
#   --experiments-deep      # also the heavy degree-locked double-transit scan (~15s+)
#   --marriage-window Y1,Y2 # calendar-year range for the marriage experiments
#   --alt-engines           # Surya-Siddhanta alternative-engine crosscheck section
#                           # (also included automatically by --full)
#   --prediction            # prediction section (bundled interpretive prose + computed
#                           # life-span bracket); --full always includes it
#   --dasha-year-duration N # year-length mode 1..8: 1=mean sidereal, 2=true sidereal
#                           # (library default), 3=mean tropical, 4=true tropical,
#                           # 5=savana, 6=mean lunar, 7=true lunar, 8=Gregorian
#   --prashna               # ROUND-15 wrapper horary fact-sheet (question moment = datetime)
#   --prashna-number N      # KP-249 (1..249) / nadi-108 (1..108) / nadi-150 (1..1800)
#   --prashna-system kp249|nadi108|nadi150
#   --query-house N         # Prashna query house 1..12 (default 7). Implies --prashna
```

Examples of the JSON sections: `__meta__`, `core`, `panchanga`, `planet_flags`,
`house_vars`, `karakas`, `vargas`, `doshas`, `dasha`, `applicability`, `special`,
`yogas` (ROUND‑9: now a FAST default), `transits` (+ `ashtakavarga`, `strengths`, `yogas_all`,
`raja_yogas`, `vargas_all`, `fine_dasha`, `narayana`, `rasi_dasha`, `muhurta`, `vratha`,
`house_systems`, `strength_full`, `ashtakavarga_deep`, `tajaka_deep`, `varga_transits`,
`alt_engines`, `experiments`, `prediction`, `prashna`, `verdicts`, `match` when the corresponding flags are given).

**Two dasha families, decoded.** `dasha` (and `fine_dasha`) are **planet** dashas
(Vimsottari) that give the **TONE** (who/what is active, and the emotion). `narayana`
and `rasi_dasha` are **sign/rasi** dashas (Jaimini) that give the **EVENT** (the house the
sign occupies from the lagna) and corroborate windows. Both are fully decoded to planet/
sign NAMES; the interpretation prompt uses **Vimsottari for tone + Narayana for events**.

---

## Step 2 — Plan, then read

Open **`query_playbooks.md`** (what a Jyotishi actually checks) and
**`astrologer_prompt.md`** (PROTOCOL 0 + the Section 7 reading). Optionally
prepend:

```
FIRST: read query_playbooks.md and write a plan (Vedic procedures → CLI → JSON paths).
THEN:  read chart.json END TO END (long tables included).
THEN:  apply astrologer_prompt.md PROTOCOL 0 + sections 1-8, citing JSON paths.
       Always deep. Vedic only. Lock to __meta__ settings. Marriage ≠ D9 only.
```

---

## Doing this in Arena.ai Agent Mode

Yes — this is fully possible in agent mode, because agent mode has a real shell.

**Recommended one‑shot instruction (paste into a new agent):**

```
You are a Vedic astrologer assistant. Workspace files:
  query_playbooks.md     (Step 0: Vedic procedure checklists + settings lock)
  compute_chart.py       (Step 1: the PyJHora computation engine)
  astrologer_prompt.md   (Step 2: PROTOCOL 0 + the expert reading methodology)

Do this, in order:
0) Read query_playbooks.md. Write a PLAN for the user's question: every Vedic
   procedure a human Jyotishi would use, mapped to this engine (Lahiri / Drik /
   true nodes / whole-sign unless __meta__ says otherwise). No Western astrology.
1) Run DEEP compute (replace birth details as needed). FAST is not enough:
     python compute_chart.py --name "Asha" \
         --lat 12.9141 --lon 74.8560 --tz 5.5 \
         --date 1990-05-15 --time 10:30:00 \
         --query-profile deep --now 2026-09-11 --transit 2026-09-11 \
         --out chart.json
   Marriage → --query-profile marriage (+ --match-* if a partner).
   Timing   → --query-profile timing --fine-dasha "START,END" --max-depth 4.
   If the birth place is a city, use --place "City, Country".
   If "SwissEph file not found", run: python setup_ephemeris.py and retry.
2) Read chart.json END TO END. Do NOT skip long tables — they are the evidence.
3) Apply astrologer_prompt.md PROTOCOL 0 + Sections 1-8. Cross-reference three
   layers (D1 promise, varga/Jaimini/strength confirm, dasha+Narayana+gochara
   timing) before any strong claim. Cite JSON paths. Do not invent. Marriage is
   not D9-only. If a field is "__error__", say it could not be computed.
4) Answer the user's question from that deep read — windows, not fake precision.
```

Then just chat: *"Analyse this chart — what are the big career and marriage themes,
and what does 2027 hold?"* The agent plans, computes deep, reads end-to-end, and
returns the reading.

### Notes on life with Agent Mode
- **Reuse:** for a *new* chart, the agent just re-runs Step 1 with new `--date/--time/
  --lat/--lon` and re-reads. The prompt stays the same.
- **Persistence:** PyJHora + ephemeris are installed in the sandbox. If you ever start a
  fresh workspace, re-run step 1 (install + setup_ephemeris) once.
- **Known environment caveat:** the in‑app file preview loads in a sandboxed iframe, so
  it renders the `.md`/`.json` text fine; the heavy compute runs in the shell, not the
  browser, so nothing visual depends on external resources.

---

## What `compute_chart.py` actually covers (computed & verified)

The engine surfaces **essentially the whole PyJHora surface**, each section wrapped in
`try/except` so a single failure becomes an `"__error__"` field, never a crash.
Verified end-to-end by `test_coverage.py` (**485 self-contained assertions**, all on real
data: the Narayana/rasi-dasha decode audits, the Parashari `applicability` rule, the full
**56-system** dasha catalog + safe decode, extended panchanga, transit events + latta
stars, the annual dashas, the Tajaka **current-year** anchor + Tajaka aspect/ithasala
tables, the dasha compute cache, decoded transit retrograde/combustion/motion, the
dynamic 240-yoga scan, raja-yoga pair checks, the prediction/alt-engines/experiments
sections, the marriage-experiments dasha-segment shim, the life-span py3 patch, the
Round-6 review fixes: raasi-domain house categories with house-12 parity, bhava
house-number decode + raw parity, true-UT `julian_day_ut`, resolved place metadata,
Gulika/Maandi dedicated-function parity + the 11-point drik-vs-charts agreement
crosscheck, typed doshas with manglik cancellation, the tagged serializer placeholder,
opt-in prediction, and the dasha-year-duration restore — and the **Round-7 + 5.0-dev
audit**: fractional-hour JD unit tests, the vimsottari resolver, Gregorian-vs-sidereal
dasha-year differ fixtures + effective-duration records, running-depth 2/4,
tribhagi differ, `__status__` map (zero silent `unverified_fallback`), empirical lord
ranges for kalachakra/saptharishi/karana/panchasvara/sudarshana/karaka dashas, the
compat-patch registry + `--stock` empty check, patched calls for the 5.0-dev bugs
(`previous_ascendant_entry_date`, duhphali-kutta), update-check version parsing +
offline envelope, 5.0 signature re-verifications (`special_thaara`, `full/new_moon`,
purnimanta, Ashtakoota `method='North'`, bhava/house systems incl. Placidus +
`available_house_systems`), fine_dasha non-midnight boundaries, and artifact-meta
disclosure checks — and the **Round-8 review audit** (`audit_round8`, 21 records):
chara-karaka role tables + Darakaraka-last with the Rahu mirror rule, JSON coercion of
numpy/date/set, `_tob` string parsing without silent-noon defaults, tajaka_deep
degradation without `tajaka_yoga` (19 blocks still returned), match gender
directionality, fine-dasha seeds keeping fractional hours, tightened error-string
markers, classical Muntha from the natal lagna, unknown planet-id labelling, the
generic dasha-entry resolver fallback, the getsource-free life-span reimplementation
(proven equal to the source-patched result), the `deprecated_upstream` experiments
status, the 28-yoga capability audit + wrapper-computed yogas, avastha re-derivation,
vara/hora/ayana recomputation, ashtakavarga label consistency, gochara classification,
nakshatra lords, and the CLI defaults (`--now` = today, running depth 3, `--gender`) — and
the **Round-9 review audit** (`audit_round9`, 11 records): the Muntha completed-years
anchor (at birth Muntha = natal lagna, house 1), a runtime LOCAL-domain proof for the
janma-hora sunrise math (rejecting the claimed timezone corruption at ±tz margins), the
curated-yoga `_from_planet_positions` variant census (rejecting the claimed signature
mismatch), `_call_dasha` failure-vs-fallback tagging, ardhashtama=4th/panchama=5th gochara
keys, the match tz default + SystemExit guard, build_vargas D1 reuse parity, decoded
varga-transit entry dates, structured sphutas/sahams, the expanded FAST defaults, and the
bhaava_madhya_swe kwarg fallback — and the **Round-10 review audit** (`audit_round10`,
12 records): the morning-birth janma-hora counter-proof (third rejection of the
timezone claim: 10:30 birth → 4.35 h since sunrise, not 9.75), curated-yoga
named-parameter binding stubs, the partner-tz coordinate resolution with DST proof
(NYC −4.0 EDT vs −5.0 EST), the defensive sphuta shapes, the fine-dasha window
normalization, KP cuspal micro-lord hand-checks (12/12 star lords + the 249-number
formula), D60 deity recompute-exactness, the Jaimini dual-sign co-lord resolution, the
yoga lord/avayogi table + honest tattva omission, partner-Manglik parity with doshas,
core inline-flag parity with planet_flags + minimal-arc sun distances, and the
pushkara-bhaga dual-table recompute, and the **Round-11 review audit** (`audit_round11`, 26 records 11a–11i): the janma-hora triple proof (fourth rejection of the timezone claim — the sunrise JD element decodes to LOCAL 6.15 h and the engine's 4.35 h equals pure local-HMS arithmetic), build_core source forensics vs the alleged syntax splice, the is_night_birth pool complements + zero-consumer scan, standard-orb combustion + cazimi + mismatch flags (the retro-Mercury 16.07° case), gandanta recompute + a synthetic boundary chart, dagdha table/mapping/variants, kakshya recompute + natal-prastara bindu cross-checks, Dasa-Koota South mode with the North 17.0 golden untouched, and the yogi/duplicate/avayogi derivations with the +186°40′ library crosscheck), and the **Round-12 review audit** (`audit_round12`, 17 records 12a–12i): the hora-tz claim's fifth rejection (the review's own `julian_day_utc` fix executed to a −1.15 h / k=22 absurdity vs the engine's exact 4.35 h), `drik.is_night_birth`-sourced day/night pool keys with the 17:30-IST counter-proof, the BAV row-7 Lagna relabel in both builders, the `jd_local`-kwarg signature + DST proof, the 9-row shad_bala decode with BPHS-minimum ratios and the Sun-table dispute disclosed, the 60−ishta kashta complement, navamsa parity against `divisional_chart(9)` on two charts, the `<key>_by_planet` twin pass, decoded sodhya pindas (shodhya = raasi + graha), and the decoded yogini dasha (names web-verified, equal-antar split disclosed), and the **Round-13 review audit** (`audit_round13`, 15 records 13a–13f): the navamsa mod-360 no-op sweep (7,204 longitudes; the review's "360° = Pisces" claim refuted by its own formula), signature-adaptive partner-tz binding across three simulated library builds plus the NYC DST engine regression, FAST↔deep `shad_bala_summary` whole-dict identity with per-row index verification, D10 arudha parity against a direct library call, the girl-chart Moon-frame Manglik proof (8th from Moon, exceptions [7,12,16,17]) with byte-identical legacy keys, and the KP four-fold significators recomputed independently in both frames, and the
**Round-14 review audit** (`audit_round14`, 15 records 14a–14e): four REJECT proofs
against library-wide scans (no `jhora.chart_renderer` module + PyQt6-locked UI; zero
prashna/ashtamangala/tambula/horary matches across all 160 library .py files; all
eight drik muhurta functions single-jd `(jd, place)`; no deity/gemstone remedy
constants — `tithi_deities` is the 16 Nitya Devis) and the ADOPTED `aayu` harana
tables with per-system catalog-duration parity (deltas ≤ 0.005 y), the Santhanam
min-rule recomposition for all 7 planets, the Kota/Sarvatobhadra data ports (incl.
the Abhijit boundary edge at birth star 21) and the Surya-kundali from-sun frame, and the
**Round-15 audit** (`audit_round15`, 21 records 15a–15d): `_hours_to_hhmm` now emits
`(-1d)` for PyJHora's negative tithi/yoga/karana start hours (a 25.4 h Krishna Panchami
no longer looks empty); leftover panchanga (`special_tithis`, `karaka_tithi`,
`lunar_month`, `samvatsara`, `vedic_date`, `chandrabalam`, `thaaraabalam`) decoded to
names on both golden charts; FAST `verdicts` ledger (`by_name[X].present` true|false|null
— the only source for is-combination-present; `yogas_all.yogas_absent` listed so a missing
key is not treated as unevaluated); wrapper `prashna` using the library's real `prasna`
spelling (KP-249 + nadi-108/150, with the nadi `number//N` off-by-one disclosed as
`corrected_sign`). Round-14's REJECT of an Ashtamangala *oracle module* still holds
(0 English `prashna` matches library-wide), and the
**Round-16 audit** (`audit_round16`, 21 records 16a–16q): remaining drik/charts/vratha
surface — `vedic_weekday` (sunrise-to-sunrise = `vaara`; civil differs at 02:30),
equal day+night 60-ghati / 30-muhurta, `days_in_tamil_month`, `daily_planet_speed` for
all 9 grahas (Moon matches `moon_speed_daily`), next/previous lunar year + solar year
(Mesha sankranti, **not** the natal solar return) + solar month, experimental
`next_same_tithi` (library TODO), `nava_thaara_for_all_planets` /
`special_thaara_for_all_planets`, `next_planet_nakshatra_pravesha` (retrograde past-JD
flagged), `karana.lord` + `moon_nakshatra.library_lord` (library-backed — the Round-10
"karana lord absent" note is amended), `sphutas.library_longitudes_by_varga` D1/D9/D10
(D1.tri longitude == named `tri_sphuta`; `Horoscope.get_ava_saha_yoga_*` skipped as
HTML of S13/S14), `sahams.__divisional__` D9 36 lots with the D1 named-key contract
unchanged, `special.special_lagna_longitudes_by_varga` D9 9 keys, `vratha.next_special_vratha`
19 underscore-keyed next dates (spaces return None):
**Round-17 audit** (`audit_round17`, 17 records 17a–17n): remaining non-UI APIs —
per-planet KP-249 sub/star/sub lords, `drik.navamsa_from_long` == inline navamsa,
tamil month from JD, Sree lagna from Moon+Asc, D9 nava-thaara, 12 Varnada lagnas,
mixed D9xD12/D1xD10, Bhava Arudhas A1–A12 as signs, strongest-among-7, chathusras,
Jaimini sign-pair longevity classes, previous conjunctions + D9 sign-entry, mixed
D1xD9 sphuta/saham. Yoga-700 aliases and named varga aliases remain covered by
`yogas_all` / `vargas_all` (not duplicated); Horoscope HTML getters are UI.
**Round-19 audit** (`audit_round19`, 19 records 19a–19m): plan-first `--query-profile` (deep natal sheet vs focused muhurta/prashna), prompt PROTOCOL 0 (always-deep, settings lock, three-layer cross-reference), `query_playbooks.md` (marriage ≠ D9 only), and the garbled prompt-EOF fragment removed.

**Round-18 audit** (`audit_round18`, 8 records 18a–18h): D9 special-thaara, mixed D1xD9 arudha/special-lagna/upagrahas, previous D9 sign-entry, strongest kendra sign, and the Rahu–Ketu next-conjunction NoneType crash replaced with an explicit skip; remaining next_conjunctions decoded to dates:

**37 registry sections + 3 flag-driven ones (`transits`, `fine_dasha`, `match`), 56 dasha
systems (48 decoded to planet/sign names, 8 kept raw + tagged), 25 divisional charts,
dynamic 240-yoga discovery**. On the two reference charts the full run finishes in ~2
minutes; every remaining error field is a documented, disclosed upstream issue
(2 yoga predicates + `raja_yogas.other_raja_yoga_2` with internal bugs, marriage5
absent because PyJHora 5.0 removed the `jhora.experiments` package) — guarded, labelled
in `meta`, never silent.

| Section | What it gives |
|---------|---------------|
| `core` | Lagna, all 9 grahas (sign/degree/**house**/**dignity**/nakshatra+pada/**nakshatra_lord** — ROUND‑8), full 12‑house table (sign, lordship, occupants) + **ROUND‑10 inline per planet**: `is_retrograde`, `is_combust` (identical sources to `planet_flags`), `angular_distance_from_sun` (true minimal arc) and `pushkara_bhaga` (both library degree tables + signed distances + ±1° orb flags + the library's own membership band; also on the lagna) with `planet_inline_flags_note` disclosing sources |
| `panchanga` | tithi, yoga, karana, weekday, Moon nakshatra (+ pada, end‑time, next nakshatra), sunrise/sunset, moon speed, raahu‑kaala, ayanamsa, chandrabalam — **tithi/yoga/karana/nakshatra carry `start_time`/`end_time` (+`duration_hours`)** — PLUS the ROUND‑5 extras: ritu, elapsed era years (kali/vikrama/saka), triguna, vedic_time, vivaha_chakra_palan, tamil_yogam, karaka_yogam, maandi longitude, next/prev sankranti, year durations, sahasra_chandrodayam, kali ahargana, graha_drekkana lords, **nava_thaara** (from lagna & moon), **special_thaara** (Abhijit‑aware, standard nakshatra vocabulary), planet declinations — PLUS ROUND‑8: **vara_lord** (weekday lord), **janma_hora_lord** (birth hora from sunrise, Chaldaean order), **ayana** (Uttarayana/Dakshinayana, sidereal sankranti convention), **omitted_checks** (dagdha‑tithi / tithi‑shunya‑rasi deliberately not computed — disclosed, never guessed) — PLUS ROUND‑10: **yoga.lord + yoga.avayogi** (nitya‑yoga lords from `const.yogam_lords_and_avayogis`), **omitted_checks.panchanga_tattva_lords** (the review's element‑mapping rejected as unverifiable — disclosed), **chandrabalam_thaaraabalam_note** (those two keys are muhurta helpers in 5.0 — no natal kwargs exist; the note points to the natal‑relative equivalents) — PLUS ROUND‑11: **dagdha_rasis** (the birth tithi's burnt signs — krishna n−15 mapping, Purnima/Amavasya none — with `planets_in_dagdha_signs` + `houses_in_dagdha` decoded and BOTH disputed source variants disclosed; the dagdha‑YOGA muhurta combos stay in omitted_checks) |
| `planet_flags` | retrograde, combustion, marana‑karaka‑sthana, mrityu‑bhaga, pushkara‑navamsa, **graha_yudh** (planetary war pairs + Bhed/Ullekh/Apsavya/Anshumard category), **speed_sign_at_birth** (per‑planet direct/retrograde/stationary, glyph‑normalized) + **ROUND‑8 avasthas** (per graha: **baaladi** five states with sign‑parity bands + conventional result %, **jagradadi** wake‑states from dignity + compound relation to the sign lord, with `jagradadi_basis`) + **ROUND‑11**: **combustion_detail** (standard BPHS orbs — Moon 12°, Mars 17°, Mercury 14°/12°R, Jupiter 11°, Venus 10°/8°R, Saturn 15° — + cazimi ≤0°17′ + per‑body `library_vs_standard_mismatch` flags; the library's own orb table is mis‑indexed at planet_id−2 with a stale retrograde check — disclosed, not refixed) and **gandanta** (water→fire junction zones — last 3°20′ of the water signs, first 3°20′ of the fire signs — for lagna + all 9 grahas, with junction name + degrees to the exact knot) |
| `house_vars` | quadrant lords, trine lords, marakas, **yogakaraka**, baadhakas, argala/virodhargala + **ROUND‑10 `dual_sign_rulers`** — Jaimini dual‑sign co‑lord resolution (Scorpio Mars‑vs‑Ketu, Aquarius Saturn‑vs‑Rahu) via the library's `house.stronger_planet_from_planet_positions`, with `rules_sign_for_jaimini_purposes` and a note that Parashari lordship elsewhere is unchanged |
| `karakas` | **Full Jaimini chara‑karaka scheme (ROUND‑8 corrected)**: 8‑karaka roles Atmakaraka → Amatyakaraka → Bhratrikaraka → Matrikaraka → **Pitrikaraka** → Putrakaraka → Gnatikaraka → **Darakaraka (LAST)** — or the 7‑karaka scheme (no Pitrikaraka) when the library returns 7; `chara_karaka_scheme` + `chara_karaka_roles` + `nak_karaka_definition` disclose which is in force; Rahu ranked by (30°−degree) mirror convention; Ketu excluded |
| `vargas` | **D2, D3**, D7, D9, D10, D12 (whole‑sign, per varga lagna + planets) — ROUND‑9 adds D2 (Hora, wealth) + D3 (Drekkana, siblings/vitality) to the FAST default, completing the classical Shadvarga core |
| `special` | special lagnas (hora/ghati/vighati/pranapada/indu/sree/varnada/bhava/bhrigu‑bindhu/kunda), **11 upagrahas with full names** (Round 6: Gulika/Maandi via the library's dedicated functions — an earlier part‑based mapping had them swapped), **special_points_agreement** (drik‑vs‑charts independent‑path crosscheck, 11/11 points, tolerance 0.01°), **special_planet_longitudes_D9**, special_ascendant, varnada mixed chart, **mixed_chart D1×D9**, nakshatra‑dhasa progression longitudes, chart element longitude, **divisional_positions_from_rasi** (D2–D12 derived from D1 longitudes — self‑consistency crosscheck of the ephemeris vargas) |
| `dasha` | **decoded** vimsottari timeline + running maha/antar/pratyantar chain + yogini |
| `narayana` | **decoded** Jaimini rasi dasha: `antardasha_timeline` (sign→lord, 288 rows), `running` sign-chain with `event_tone` (house-from-lagna + theme + lord + functional status) at `--now`, and **`divisional_narayana_D9`** — the same maha→antar machinery on the navamsa frame (second event layer for marriage/inner life) |
| `rasi_dasha` | **decoded** chara / sudasa / shoola / drig / kendradhi‑rasi: each antardasha row decoded to sign‑lord + a house-based `event_tone` (corroboration) |
| `transits` | each graha transit sign/house from Lagna & Moon + sarva‑bindu count + **motion_at_transit_date** (direct/retrograde/stationary on the exact date) + **latta_stars** (per‑planet obstruction‑star pairs, decoded into the standard 27+Abhijit vocabulary) + transit_events/next_conjunctions (flags) + **ROUND‑8 gochara**: `saturn_gochara_from_natal_moon` (sade_sati + phase rising/peak/setting, ashtama, kantaka, **ROUND‑9: ardhashtama = 4th from natal Moon**, panchama = 5th — the Round‑8 5th‑house "ardhashtama" key was a mislabel and is gone) and `jupiter_gochara_from_natal_moon` (auspicious 2/5/7/9/11), each with conventions note — **ROUND‑11 kakshya**: per Sun..Saturn row the 3°45′ sub‑sign block {kakshya_number, span_in_sign_deg, kakshya_lord (Saturn→Jupiter→Mars→Sun→Venus→Mercury→Moon→Lagna from 0°), natal_bindu_from_kakshya_lord_at_transit_sign, favorable} — favorable iff the kakshya lord contributed a bindu in the transiting planet's OWN natal BAV row; nodes excluded + kakshya_transits_note |
| `sphutas` | tri, chatur, pancha, prana, deha, mrityu, sookshma‑tri, beeja, kshetra, tithi, yoga, rahu‑tithi, yogi, avayogi — ROUND‑9: every sphuta decoded to {sign, sign_index, sankrit, degree_in_sign, longitude} (was a raw [sign_index, degree] pair) — ROUND‑11: **yogi_avayogi_planets** decodes the yogi/avayogi POINTS into the three planets — yogi = nakshatra lord of the point, duplicate yogi = SIGN lord of the POINT's sign, avayogi = lord of the 6th nakshatra counting inclusively (idx+5) — with the library's +186°40′ avayogi point as an independent crosscheck |
| `sahams` | the 36 Sahams — ROUND‑9: each position string ("♎︎Libra 5° 20' 13\"") parsed into {sign, sign_index, sankrit, degree_in_sign, longitude, raw}; unparseable strings keep raw + a note |
| `arudhas` | arudha lagna longitudes, KP lords, 22nd drekkana, 64th navamsa, **karakamsa** |
| `strengths` | shadbala, pancha‑vargeeya, dhasavarga, bhava‑bala, bhava‑drishti‑bala, harsha‑bala, vimsopaka, aspect tables + **planet_aspect_relationship_table_pvr** (9×9 PVR aspect‑strength percentages), **vimsopaka_dhasavarga** (per planet: good varga count + score), **vimsamsavarga** |
| `ashtakavarga` | binna (per‑planet), samudhaya/sarva (per‑sign), prastara + **ROUND‑8 labelled views**: `samudhaya_by_sign`, `samudhaya_by_house` (H1..H12 from lagna), `binna_by_sign` (all 7 planets) + `bindu_labels_note` |
| `yogas` | **28 curated library yogas (ROUND‑8: 16→28 — adds all five Pancha Mahapurusha, kemadruma, sunaphaa, duradhara, vesi/vosi/ubhayachara, saraswathi)** with `__capability__` audit (listed/discovered/executed/errored/absent_in_this_build) + `computed_yogas` (wrapper‑computed, labelled): budhaditya + subha/paapa kartari from lagna AND moon, with occupant lists. **ROUND‑9: now a FAST DEFAULT section** (runs in milliseconds — Mahapurusha/Kemadruma/Gajakesari belong in every base fact sheet) |
| `yogas_all` | **dynamic discovery of ~240 PyJHora `*_from_planet_positions` yoga predicates** — `yogas_present` + ROUND‑15 **`yogas_absent`** (executed-but-false) + checked/present/absent/error counts. Never treat a missing present-key as unevaluated. |
| `verdicts` | **ROUND‑15 FAST ledger (always emitted):** `by_name[X].present` is true\|false\|null. THE only source the reading prompt may cite for "is combination X present?". Missing from `by_name` = not evaluated (not absent). |
| `prashna` *(--prashna)* | **ROUND‑15 wrapper horary.** Question moment = the supplied datetime. Ashtamangala *limbs as facts* (tithi/nakshatra/yoga/karana/vara/lagna/hora/chandra-rasi — no yes/no oracle), number-prasna via library `prasna` KP-249 / nadi-108 / nadi-150 (nadi off-by-one disclosed), tri/chatur/pancha sphuta, query-house occupants/lord (default 7). |
| `raja_yogas` | raja‑yoga details + **raja_yoga_pair_checks** (per kendra×trikona pair: neecha‑bhanga / vipareetha / dharma‑karmadhipati present‑flag + detail), other_raja_yoga_1/2/3 (#2 guarded — known PyJHora‑internal bug), **raja_yogas_all_charts_pairs** + scan_info (which of D1..D12 carry raja‑yoga pairs) |
| `tajaka` | annual chart, Lord of the Year, **classical Muntha (ROUND‑8 frame fix + ROUND‑9 completed‑years fix)** — the `muntha` block computes Muntha = natal lagna at birth, advancing one sign per COMPLETED solar year (varsha N carries `completed_years` = N−1): sign, house‑from‑natal (= legacy `muntha_house`), house‑from‑annual, both lagnas, `years_elapsed` + `completed_years` + the rule text; for the **current** annual year via `--now` (defaults to today) or explicit `--annual-year N` |
| `vargas_all` | 25 divisional charts: D1,2,3,4,5,6,7,8,9,10,11,12,16,20,24,27,30,40,45,60,**72**,81,108,144,**150**. **D150 (Nadiamsa) is a STANDARD PyJHora 5.0 divisional factor** (`factor_source: "standard"`; golden lagna re-verified). **D72 is EXPERIMENTAL**: upstream has no standard D72 algorithm, so it runs PyJHora's generic equal-division `custom_divisional_chart` and every D72 block carries `method_status` (`classical_scheme_implemented: false`, maturity warning) + a `custom_divisional_check` self-crosscheck — never quote as classical D72. **ROUND‑10: `D60.shashtiamsa_deities`** — per body (Lagna + 9 grahas) the Shashtiamsa deity + index + kroora/soumya nature, computed with the library's own resolver (`utils.get_amsa_ruler_from_planet_longitude`, `const.amsa_rulers[60]`) and a parity-convention disclosure note. |
| `all_dashas` | **56 dasha systems** (raw arrays, corroboration): every graha + rasi dasha (incl. both kendradhi rasi‑bhukthi variants), the 8 conditional sama dashas, ashtaka‑varga, rashmi, buddhi‑gathi, moola, panchasvara, sudarsana‑chakra, etc. |
| `dasha_decoded` | The **safe, AI-readable** companion to `all_dashas`: each of the 56 systems carries an explicit per‑system `lord_type`; the 48 planet/sign/karaka‑pair systems are decoded to NAMES, the 8 with non‑planet encodings (karana / element / nakshatra / sudarsana / unknown) are kept raw + tagged so they are never misread as a planet. Each row preserves the nested mahadasha→antardasha chain (`chain` + `depth`). |
| `running_dashas` | **Operative dasha for 53 systems** (any that expose `get_running_dhasa_for_given_date`): the decoded running chain at `--now` (deepest level = currently operating sub‑period, with start/end), per system's `lord_type`. Also reports `systems_attempted` / `systems_skipped` / `skip_reasons`; `--running-depth` sets the `dhasa_level_index` (ROUND‑8: default **3** — maha→antar→pratyantar). |
| `applicability` | **Parashari conditional‑dasha rule**: `applicable_conditional_dashas` = which conditional dashas are MATHEMATICALLY active for this birth (e.g. ashtottari/dwadasottari/satabdika). Check this FIRST alongside `dasha`. |
| `muhurta` | **27 election keys**: abhijit, amrit‑kaalam, raahu/yamaganda/gulikai‑kaalam, durmuhurtam, varjyam, gauri choghadiya (16 slots), shubha hora, panchaka_rahitha + next_panchaka_days, vidaal/aadal/anandhaadhi yogas, **brahma/godhuli/vijaya/nishita muhurthas**, sandhya periods, tamil jaamam, **pushkara_yoga** (none/dwi/tri), **udhaya_lagna_windows** (12 rising‑sign windows), **muhurthas_of_day** (30 named rows: 15 day + 15 night), trikalam (all three), drik1 extras |
| `vratha` | shiva/agni/yogini vaasa, disha shool, panchaka, daily `festival_query`, festivals_of_day + festivals_year_after_birth (pyIslam‑guarded, degrade to a note), pancha-paksha, **`vratha_dates` — 24 pyIslam-free date finders**: sankranti (with sign names)/pournami/amavasya/ekadashi/pradosham/shivarathri/sashti/sankatahara/vinayaka chathurthi/chandra dharshan/moondraam pirai/manvaadhi/yugadhi/srartha…, PLUS the personal anchors **janma_tithi / janma_nakshatra / janma_yoga dates** and **tithi_pravesha_current_year** (the birth‑tithi anniversary, `{dates:[…]}` contract) |
| `eclipses` | solar/lunar eclipse flags + next/prev solar & lunar, global max-location + type |
| `house_systems` | **Beyond whole-sign**: bhava chart + bhava houses (cusps decoded), KP lords, **ROUND‑10 `kp_cuspal_lords`** (all 12 KP cusps × KP star number 1–249 + sign/star/sub/pratyantara/sookshma/praana/deha lords — composed from `drik.bhaava_madhya_kp` × `utils.kp_lords_for_longitude`), stronger planet/rasi, order of planets & rasis by strength, natural/temporary/compound relationships, graha & rasi drishti, aspected houses, kendras/panaparas/apoklimas, vargottama, rudra/brahma/maheshwara/trishoola/longevity, planet classification by house category (ROUND‑6: computed in the library's canonical **raasi domain** — house‑12 occupants now correctly appear in dushthanas/apoklimas; **bhava_houses** = {body → equal‑house number 1..12} and **bhava_cusps_decoded** = position‑ordered houses with decoded raasi names, both raw‑parity tested) + ROUND‑5: **planet_aspect_details** (both directions, per planet), **raasi_aspect_details** (12 signs), **lagna_sign_aspect_groups** (13 structural families), planet_functional_extras (yogakaraka + strong signs), functional benefics/malefics, **bhava_madhya_variants** (5 house doctrines incl. BV Raman rows), **pachakadi_sambhandha** (paachaka/bodhaka/kaaraka/vedhaka decoded), **planets_in_each_raasi**, **relative_houses** (from lagna AND moon), kendra ordering |
| `strength_full` | **Every Shadbala component** (sthana/dig/kaala/cheshta/naisargika/drik/uchcha/kendra/sapthavargaja/drekkana/navamsa/divaratri/nathonnata/paksha/tribhaga/abda/masa/vaara/hora/ayana/yuddha/ishta/subha) + full shad_bala, vargeeya balas, vimsopaka & vaiseshikamsa balas |
| `ashtakavarga_deep` | Binna + samudhaya + prastara (sign-based; binna Sun-row 48, samudhaya 337 verified), **trikona & ekadhipatya sodhana** (+ sodhita binna), sodhya pindas (JSON-safe), vimsopaka shadvarga/shodasavarga + the **ROUND‑8 labelled views** (samudhaya_by_sign/_by_house, binna_by_sign — same block as the FAST section) |
| `chakras` *(FAST)* | **ROUND-14 data ports** of the two content-bearing chakras: `kota_chakra` (28-star circle with Abhijit, four rings Baahya/Praakaara/Durgantara/Sthambha, per-body zone placement, kota lord + kota paala) and `sarvatobhadra_chakra` (full 9×9 grid decoded into nakshatra/rasi/syllable/tithi-group cells, body cells, birth tithi-group cell). PyJHora computes NO SBC vedha penetrations — vedha readings are the interpreter's doctrine applied to the grid data.|
| `aayu` *(--full / --query-profile deep)* | **ROUND-14:** the harana reduction TABLES behind the catalog aayu dashas — base-year tables, the four harana factor tables (combustion / enemy-sign / above-horizon / malefic-rising) + bharana increases, Santhanam (method 1) and Varahamihira (method 2) finals, aayur-type, lagna longevity, the public longevity() total, catalog-parity note. Classical strength indices — NEVER death predictions.|
| `tajaka_deep` | Monthly chart, sixty-hour chart, Lord of the Month, varsha/maasa pravesh, Tajaka aspects, **all Tajaka yogas** (ithasala, eesarpha, kamboola, gairi-kamboola, radda, manahoo, khallasara, duhphali-kutta, nakta, yamaya, ishkavala, induvara) + ROUND‑5: **tajaka_raasi_aspect_details** (12 signs × 9 typed aspects, decoded; empty‑sign conjunction lookups labelled, never crash), tajaka_lagna_sign_aspect_breakdown, **ithasala_deeptamsa_details** (aspects + approaching + deeptamsa with Varthamaana/Bhavishya/Poorna type), **planet_aspect_on_houses_tajaka** (5 planets × 7 houses), annual_chart_approximate (Table‑71 crosscheck; explanatory `__note__` when the age has no table entry). **All Tajaka-specific values computed on the annual (varsha) chart** — `annual_ascendant_sign` feeds every yoga/aspect. `months_elapsed` derived from `--now` (or `--tajaka-month N`). |
| `varga_transits` | Transits measured in a **divisional** (default D9) chart: divisional positions + next/prev divisional-chart entry dates (ROUND‑9: decoded to {jd, date "YYYY‑MM‑DD HH:MM", longitude/sign in the divisional frame, frame_note} — were raw JD floats) (`--varga-division`, `--transit`) + **next_conjunctions_divisional** (Jupiter‑Saturn / Jupiter‑Venus / Sun‑Moon conjunctions in the divisional frame, ISO dates + longitudes). **Divisional houses measured from the divisional chart's own lagna** (`transit_house_from_D{n}_lagna`, `natal_D{n}_house`). |
| `fine_dasha` *(flag)* | a **decoded** L3/L4 sub‑period calendar for a window — exact pratyantar/sukshma boundaries, seeded from **every** mahadasha overlapping the window so a window that crosses a mahadasha boundary keeps both (see `--fine-dasha`) |
| `match` *(flag)* | **Ashtakoota marriage compatibility** vs a partner: 8 poruthams `{score,max}`, total /36, + 4 special poruthams + **ROUND‑8 directionality**: `--gender`/`--match-gender` decide whose chart fills the engine's fixed `boy_*`/`girl_*` slots; `genders` block + per‑input `whose` labels + same‑gender note. **ROUND‑9**: an omitted/None partner tz can no longer kill the run — upstream `Horoscope(timezone_offset=None)` calls `exit()` (SystemExit), caught into a typed `__error__`. **ROUND‑10 (fixes the Round‑9 default)**: an omitted `--match-tz` is now resolved FROM THE PARTNER'S COORDINATES (DST‑aware, library `utils.get_place_timezone_offset`) instead of silently inheriting the native's `--tz` default 5.5; native tz is only a last resort; every case disclosed via `timezone_note`. PLUS **`manglik_compatibility`**: both sides evaluated with the identical `dosha.manglik` calls (exceptions on/off, indices listed) + the traditional mutual‑neutralization verdict — **ROUND‑11**: `--match-method South` now actually runs the Tamil **Dasa‑Koota** — `dasa_koota_breakdown` (10 boolean poruthams: dina, gana, mahendra, sthree_dheerga, yoni, raasi, raasi_adhipathi, vasiya, rajju, vedha) + `dasa_koota_total` out of 10 (varna/naadi excluded by design); previously a silent no‑op — the 5.0 wrappers branch on CHART_STYLE enums and the library's `compatibility_score_south` is buried inside a docstring, so the wrapper sums the ten `*_south` predicates directly |
| `prediction` *(--prediction / --full)* | PyJHora's bundled reference prose (BV Raman / ChinmayaAstrolger janma‑rasi texts + planet‑in‑house & house‑lord compendiums, HTML‑stripped, labelled as LIBRARY TEXT — not computed claims) + the computed Parashari **life_span_range** bracket (Alpayu/Madhyayu/Poornayu — never a date) + longevity reference constants. **Opt‑in (Round 6):** the DEFAULT run is a pure fact sheet with no bundled prose. |
| `alt_engines` *(--alt-engines / --full)* | Surya‑Siddhanta crosschecks from the module its author labels work‑in‑progress: SS sunrise/sunset, SS kali ahargana (vaara‑shimmed), khanda‑khaadyaka ahargana, SS ascendant_new (EXPERIMENTAL) + `unavailable_in_this_build` documenting the SS entry points broken in 4.8.7 and still broken in the 5.0 dev line. Drik values elsewhere remain authoritative. |
| `experiments` *(--experiments)* | EXPERIMENTAL research predictors (`jhora.experiments`) — **the package was REMOVED upstream in PyJHora 5.0**, so on the dev line every key (`marriage5_windows`, `marriage2_windows_variant`, `double_transit_windows`, sensitive‑point scans) emits a typed, honest not‑available note instead of silently vanishing. The wiring is retained: if upstream restores the package, the section (incl. the dasha‑segment shim and degree‑locked scan) works again unchanged. Every consumer is instructed: corroborate, never sole basis. |

**Not exposed (deliberately):** the `ui/*` Qt widgets; `experiments.conjunction`
(needs mplcursors/GUI; the whole `jhora.experiments` package was removed in 5.0 anyway);
`hijri` + the pyIslam‑based festival listings (pyIslam is not
installable here — the vratha finders are the pyIslam‑free equivalent, and the two
pyIslam‑dependent fields degrade to explicit notes); ~~deep KP sub‑lord tables~~ (**SUPERSEDED by Round 10**: the cuspal star→sub→pratyantara→sookshma→praana→deha chain now ships in `house_systems.bhava_madhya_variants.kp_cuspal_lords`; only a ready‑made KP significator/house‑group table remains unexposed — the cuspal lords are its inputs); `ss.planet_positions`/`tithi`/`nakshatra`/
`bhujantara_correction`/`solar_month_and_date` (internally broken in 4.8.7 and still
broken in the 5.0 dev line — documented
in `alt_engines.unavailable_in_this_build`); the empty `pancha_pakshi` DB matcher
(the birth‑bird pancha‑paksha itself IS exposed under `vratha`). Everything else in the
`jhora` package is surfaced by a section, a flag, or an explicit unavailability note —
the engine is built to be extended.

## How the reading prompt handles the heavy data (the "no-confusion" design)

`astrologer_prompt.md` is written to prevent the model from drowning or contradicting
itself when given high‑dimensional data:

1. **Directed computation first.** Section 1 maps a question to a *small* `--sections`
   set (e.g. career → `core,karakas,dasha,transits,vargas`). The model is told to
   compute the smallest set that answers the question and to add sections only if it
   needs more — not to dump 23 vargas for a simple question.
2. **Priority stack.** It always forms a 5‑level priority (Lagna/lagna‑lord → Moon +
   Atmakaraka + running dasha → strongest house → weakest house → timing), so no matter
   how much data is present, the interpretation is anchored the same way.
3. **Deep tables are confirmatory, not primary.** The rules say ashtakavarga, strengths,
   vargas_all and the raw dasha arrays (`all_dashas`, 56 systems) only quantify/support a claim already derived from
   `core`/`dasha`/`transits`, never contradict it, and that the base chart wins on conflict.
4. **Raw dasha warning (`__notes__`).** The engine writes `__notes__.dasha_lords` telling
   the model that raw `all_dashas.*` lords are integer ids, not planet names — so it won't
   misquote them. The decoded `dasha` section is marked the only authoritative timing source.
5. **Cap rule.** If it does have `--full`, it interprets the 3–5 high‑value pieces the
   question needs and explicitly says what it did not use and why.
6. **`__error__` handling.** Missing/uncomputable fields become `"__error__"` and the model
   is instructed to acknowledge and skip, never guess.
7. **Self‑check.** A pre‑finalise checklist re‑validates that every claim traces to the JSON.

## Files

| File | Purpose |
|------|---------|
| `compute_chart.py` | Step 1 — PyJHora engine → JSON factsheet (near‑full capability, CLI + section filters). |
| `query_playbooks.md` | Step 0 — plan-first Vedic checklists (always-deep, settings-locked, topic-by-topic). Marriage ≠ D9 only. |
| `astrologer_prompt.md` | Step 2 — PROTOCOL 0 + the expert reading system prompt (heavy‑data aware). Enforces a strict professional-Jyotishi VOICE (zero validity disclaimers, zero Western-astrology mixing). |
| `install_pyjhora_dev.sh` | Installs PyJHora from GitHub master (the dev line, version 5.0) with the two packaging workarounds. |
| `setup_ephemeris.py` | Downloads Swiss-Ephemeris files PyJHora requires. |
| `requirements.txt` | Python dependencies (PyJHora itself installs from source — see above). |
| `test_coverage.py` | Full‑coverage audit (**485 self-contained assertions**, all on real data, incl. the Round-7 + 5.0-dev audit and the Round-8/9/10/11/12/13/14/15/16/17/18/19 review audits) — run after install to verify the engine. |
| `sample_chart.json` | A ready-to-read FAST sample (default 1990 chart, FAST sections + ROUND-15 `verdicts`, 0 errors — the opt‑in `prediction`/`prashna` flags are excluded) so you can test the prompt instantly. |
| `outputs/boy_full.json`, `outputs/girl_full.json` | OPTIONAL — verified FULL reference runs on the 5.0 dev line, NOT bundled in the clean setup zip (the suite self-verifies without them — artifact scans then print `[SKIP]` and 0 FAIL is still expected; regenerate anytime with the documented commands) (`--full --now --transit --experiments --marriage-window`): 40 sections each (ROUND-14 adds `chakras` FAST + `aayu` deep); the only error fields are documented, disclosed upstream issues (2 yoga predicates + `raja_yogas.other_raja_yoga_2` internal bugs, marriage5 absent because 5.0 removed `jhora.experiments`); 0 serialization‑masked values; full disclosure in `meta`. |

> `test_coverage.py` regenerates both test charts on the fly from built‑in birth data,
> so the package self‑verifies even without the `outputs/` files.

---

## Integrity notes
- Positions use **Lahiri ayanamsa** and **Drik** (modern ephemeris) by default —
  matching PyJHora's verified book examples, and **explicitly pinned** because the 5.0
  dev line changed PyJHora's import-time default to TRUE_PUSHYA. Change with `--ayanamsa`.
- Default uses **TRUE** Rahu/Ketu nodes; use `--mean-nodes` for Mean-node systems.
- **Update check on every run** (unless `--no-update-check`): PyPI + GitHub are queried
  once, offline-safe, and the result lands in `meta.update_check` (installed vs latest
  vs update recommended) so new dev releases never go unnoticed.
- **Full disclosure in `meta`:** the status of the five tracked upstream issues
  (#52, #57, #59, #61, #62), the wrapper-discovered 5.0-dev bugs, and every compat
  patch applied during the run (`meta.compatibility_patches`; `--stock` installs none
  and says so).
- `compute_chart.py` writes its own `sign_lord`/`dignity` fields (classical lordship +
  exaltation/debilitation) and the houseless "whole-sign" house numbering relative to
  the lagna — all reproducible from the JSON, so nothing is hidden.
- It also **always** decodes raw PyJHora indices to names/dates (tithi/yoga/karana/
  nakshatra, dasha lords, chara‑karakas, planet ids in `planet_flags`, `fine_dasha`
  lords, `match` poruthams, upagrahas, sahams, karakamsa, muntha) — so the AI never has
  to decipher `[23, 1.19]`. The `__legend__`/`__notes__` blocks document every convention.
