QUERY PLAYBOOKS — Plan → Compute deep → Read end-to-end → Cross-reference

This file is the planning brain for the two-step pipeline. The JSON from
`compute_chart.py` is the only source of numbers. This file tells the
reader what a professional Jyotishi actually checks for a question, which
CLI to run, which JSON paths to read in full (not skim), and how to
cross-reference so the answer is not a shallow “D9 looks fine”.

Always deep. Even if the user asks a one-line question, run the deep
compute for that topic and read every listed path. Shallow readings are a
failure mode, not a speed optimisation.

Vedic only. Parashara, Jaimini, Tajaka, KP as a labelled frame, Nadi
number-prasna as library tables. No tropical signs, no outer-planet psychology,
no Western houses/orbs, no social-media “Mercury retrograde ruined your week”.

0. SETTINGS LOCK (read `__meta__` before any doctrine)

This engine’s default chart is:

| Setting | Engine default | Do not mix with |
| --- | --- | --- |
| Ayanamsa | Lahiri (pinned; PyJHora 5.0 import-default is TRUE_PUSHYA) | KP ayanamsa, Raman, True Chitrapaksha, tropical |
| Positions | Drik (Swiss Ephemeris) | Surya-Siddhanta (`alt_engines` is crosscheck only) |
| Nodes | True Rahu/Ketu | Mean nodes (`--mean-nodes`) |
| Houses | Whole-sign from Lagna (house 1 = Lagna sign) | Placidus/KP cuspal occupancy unless you label `house_systems.kp_cuspal_*` |
| Bhava madhya default | KN Rao (method 1) | BV Raman / Sripathi / KP cusps — different numbers |
| Dasha year length | true sidereal (library mode 2) unless `--dasha-year-duration` | Gregorian/savana year lengths |
| Match | North Ashtakoota /36 unless `--match-method South` | quoting /36 on a South run |
| Now | `--now` defaults to today | a stale JSON’s running dasha |

Rule. A technique found in a book, article, or r/nakshatra post is usable
only if (a) it is Vedic, and (b) you can execute it on this JSON’s settings.
If the source assumes KP ayanamsa, Raman ayanamsa, Sripati houses, or mean
nodes, do not use those rulesets with this computation. Never silently
convert. If you have no proper technique/ruleset for a query under the
current settings, but rulesets exist for other settings, re-run the engine
with the modified settings and read inside THAT frame.

If `__meta__.ayanamsa` is not Lahiri, the entire reading must name the ayanamsa
in the header and must not cite Lahiri-only memory.

1. THE FIVE PHASES (do these in order, every query — mirrors PROTOCOL 0 in astrologer_prompt.md)

Phase A — Understand the question
Write (silently or in a short “plan” preface you may keep internal):
- Exact question (timing vs nature vs compatibility vs election vs horary)
- Native vs partner vs event-date
- What would make the answer wrong if missed (the usual shallow trap)

Phase B — Gather Vedic procedures
From training and (when the environment allows) classical manuals, serious
Jyotish articles, and practitioner notes (e.g. nakshatra-centred Parashari
threads — not pop astrology):
- List every Vedic procedure that competent humans use for this question.
- Discard Western / New Age / “sun-sign” items.
- For each surviving procedure, note the logic (why it is checked) and the
  frame (Parashari D1, varga, Jaimini, Tajaka, KP-labelled, muhurta).

Phase C — Map procedures → this engine
For each procedure, name:
- CLI (`--query-profile …` / extra flags)
- JSON paths that must be read completely
- What would count as confirmation vs conflict
If a procedure has no JSON path, you may not fake it. Say it is not in this
build (`omitted_checks`, `__error__`, or absent section).

Phase D — Compute deep, then read end-to-end
Run the chart deep for the topic (see profiles below). Do not use FAST
defaults as the final evidence for a life question.
Read every listed section end to end. Long tables (Narayana timeline,
yogas_absent, ashtakavarga rows, fine_dasha calendar) are evidence, not
optional flavour.
Cite JSON paths on every factual claim.

Phase E — Cross-reference like a human
A professional does not stop at one indicator. Require at least three
independent layers before a strong claim:
- Promise   (D1 house/lord/karaka/yoga/dosha)
- Confirm   (relevant varga + Jaimini pada/karaka + strength/AV)
- Timing    (Vimshottari tone + Narayana/rasi event + gochara)
If layers disagree: report both, weight by (a) D1 over varga for outer events,
(b) varga for the inner/specific domain, (c) stronger shadbala / yogakaraka,
(d) running dasha lord’s involvement. Never average them into mush.

2. CLI PROFILES (`--query-profile`)

Always pass `--now` (reproducible date) and `--transit` (same date unless the
question is about another day). Every `--query-profile` run implies
`--transit` at `--now` when `--transit` is omitted, and `verdicts` is always
emitted. Add `--fine-dasha START,END --max-depth 4` for any timing window.

| Profile | Use for | Compute |
| --- | --- | --- |
| deep | any serious natal question | every section except experiments (includes prediction + alt_engines; quote prediction.bundled_texts as library prose, never as computed fact) |
| marriage | marriage, spouse, divorce, delay | deep sheet + `--match-*` if a partner is given |
| career | job, status, business, promotion | deep sheet (read: D10 + A10/A11 + AK/AmK + 10th) |
| timing | “when / this year / 2027” | deep sheet + `--fine-dasha` (+ `--tajaka-month` if intra-year) |
| health | vitality, chronic tendency (not diagnosis) | deep sheet (read: 6/8/12, maraka; aayu tables confirmatory only) |
| children | progeny, pregnancy windows | deep sheet (read: 5th, D7, PK, harsha-bala, Jupiter) |
| money | wealth, gains, losses | deep sheet (read: 2/11, D2, Dhana yogas, AV of 2/11) |
| muhurta | election of a day/hour | focused set: core, panchanga, muhurta, vratha, planet_flags, house_vars, special, yogas, applicability (+ transits at --now, + always-on verdicts) |
| prashna | horary | focused set: core, panchanga, prashna, planet_flags, house_vars, doshas, yogas, karakas, special (+ transits, + always-on verdicts) |
| compatibility | two-chart match | native deep sheet + `--match-*` (`--match-method South` only if requested) |

Default for Agent Mode on a real question: `--query-profile deep --now DATE --transit DATE`.

3. TOPIC PLAYBOOKS (what “deep” means)

Each playbook is minimum coverage. If you skip a row, the reading is shallow.

3.1 Marriage / spouse / delay / divorce
Shallow trap: glance at D9 lagna and Venus, stop.
Procedures (Vedic):
- Parashari 7th: house, occupants, lord (placement, dignity, aspects, combustion, retro, avastha, nakshatra).
- 7th from Moon and from Venus (Kalatra / Chandra-Kalatra).
- Venus as kalatra-karaka: sign, house, Navamsa, combust/cazimi, with Saturn/Rahu/Ketu.
- Darakaraka (last chara karaka): house, sign, aspects, its Vimshottari.
- Upapada (UL / A12) and A7 from `arudhas` — Jaimini spouse pada.
- D9: navamsa lagna, 7th from it, Venus, DK, vargottama of 7th-lord / Venus.
- D7 only for children-from-marriage, not the marriage itself.
- Manglik three frames (`doshas.manglik_multi_reference`: lagna / Moon / Venus) + exception indices. Do not collapse to one boolean. Library-exact semantics: exceptions are reported ALONGSIDE the verdict (`exceptions_applied`/`exception_indices`) and do NOT flip the boolean — interpret cancellations explicitly.
- Other doshas: kalathra, kala-sarpa, shrapit — only if `verdicts` / `doshas_present` say so.
- Yogas via `verdicts.by_name[…].present` only (Malavya, Kemadruma, Gaja-Kesari, Kalathra-related). Never infer from “not in yogas_present”.
- Timing: MD/AD of 7th-lord, Venus, DK, Jupiter; Narayana signs that are the 7th from lagna; `narayana.divisional_narayana_D9`; Jupiter/Saturn/Rahu gochara on 7th / Venus / DK; Sade-Sati only as a moderator.
- Strength: Venus + 7th-lord `shad_bala_summary` ratios; AV bindus of the 7th.
- Partner chart: `match` kootas + `manglik_compatibility`. North = /36; South = /10.
JSON to finish, not skim: `core`, `house_vars`, `karakas`, `vargas.D9`, `arudhas` (UL, A7, D9 arudhas), `doshas.manglik_multi_reference`, `verdicts`, `dasha` + `fine_dasha`, `narayana`, `transits` (incl. Jupiter/Saturn gochara + kakshya), `strengths` / `strength_full.shad_bala_summary`, `ashtakavarga*`, `yogas`, `planet_flags` (Venus combust/gandanta).
Cross-check: Promise in D1 7th and D9 7th and a dasha of Venus/7th/DK and a supporting gochara. Two of four = tentative; one of four = do not sell as certainty.

3.2 Career / status / business
Shallow trap: “10th lord in a kendra, good career.”
Procedures:
- 10th house + 10th lord + planets in 10th; 6th (service) vs 7th (business) vs 11th (gains).
- Amatyakaraka and Atmakaraka linkage (Jaimini career).
- D10 lagna, 10th from D10 lagna, 10th-lord in D10.
- Arudhas A10 Rajyapada and A11 Labhapada (`arudhas.arudhas_by_varga.D10`).
- Yogakaraka; Dharma-Karmadhipati (9th+10th lords); Raja yogas via `verdicts` + `raja_yogas`.
- Sun/Saturn/Mercury as natural significators only after functional status.
- Timing: MD of 10th-lord / yogakaraka / AmK / Sun; Narayana on 10th/11th/6th; Saturn/Jupiter gochara on 10th.
- Shadbala of 10th-lord; AV of 10th and 11th.
JSON: `core`, `house_vars.yogakaraka`, `karakas`, `vargas.D10`, `arudhas.arudhas_by_varga.D10`, `raja_yogas`, `verdicts`, `dasha`, `narayana`, `transits`, `strength_full` / `strengths.shad_bala_summary`, `ashtakavarga`.

3.3 Timing of an event / “what about 2027”
Shallow trap: quote only the current mahadasha.
Procedures:
- Applicability first (`applicability.applicable_conditional_dashas`) — Vimshottari always; add Ashtottari etc. when listed.
- Vimshottari tone: houses owned + occupied by MD and AD lords; dignity; functional status.
- Fine calendar for the window (`fine_dasha.calendar` depth 4) — exact AD/PD boundaries.
- Narayana event: which sign-period is which house-from-lagna in that window.
- At least one more rasi dasha (`rasi_dasha.chara` or `sudasa`) as corroboration.
- Gochara: Saturn, Jupiter, Rahu, dasha-lord; Sade-Sati / kantaka / ashtama; Jupiter auspicious houses from natal Moon; kakshya favorable flag.
- Tajaka of that solar year: Muntha house, Lord of Year, ithasala pairs — intra-year only.
- Double-transit of Jupiter+Saturn on natal lagna/Moon/karaka as classical support (from `transits`, not from removed `experiments`).
JSON: `applicability`, `dasha`, `fine_dasha`, `narayana`, `rasi_dasha`, `running_dashas` (optional extra systems), `transits` (full), `tajaka` + `tajaka_deep` if the question is inside one year.
Phrase windows, not days, unless muhurta/election.

3.4 Money / wealth
2nd + 11th + their lords; D2 hora lagna and 2nd; Dhana yogas via `verdicts`; Venus/Jupiter functional status; A2/A11; AV of 2 and 11; dashas of 2nd/11th/yogakaraka; transits on 2/11.

3.5 Children
5th + 5th lord; Putrakaraka; Jupiter; D7 lagna and 5th from D7; harsha-bala; Santana/Putra yogas via `verdicts`; 5th-lord dasha; Jupiter gochara on 5th. Never medical promises.

3.6 Health / vitality (tendencies only)
Lagna + lagna-lord; 6th/8th/12th; marakas (`house_vars.marakas`); combust/gandanta of lagna-lord; D30 if in `vargas_all`; `aayu` harana tables confirmatory only; `prediction.life_span_range` as Alpayu/Madhya/Poorna bracket, never a date. No diagnosis, no death date.

3.7 Muhurta / election
Birth chart is context; the day is `panchanga` + `muhurta` + `vratha` + that day’s `transits`. Intersect: abhijit/amrit/shubha hora/pushkara minus raahu/yamaganda/gulikai/durmuhurtam/varjyam. Match rising lagna to the election’s house topic. Check janma-tara (`panchanga.nava_thaara_from_moon`) for the Moon of the day if natal Moon is in the JSON. Do not use tropical weekday pop-rules.

3.8 Prashna / horary
Datetime is the question moment. Read `prashna.ashtamangala_limbs` as facts, never as a yes/no reducer (none exists in PyJHora). Number-prasna only if `--prashna-number` given; for nadi-108/150 cite `corrected_sign`. Query house default 7. Then read that house in the same D1 as a prasna chart (core + verdicts). No Tambula/cowrie oracle.

3.9 Character / “who am I”
Lagna + lagna-lord; Moon + nakshatra + pada + paksha; Atmakaraka; Sun; D9 lagna (inner); avasthas; Gaja-Kesari / Kemadruma / Pancha Mahapurusha via `verdicts`; Panchanga only as birth-time colour, not fate.

4. HOW TO USE OUTSIDE SOURCES (books, articles, practitioner threads)
Allowed as procedure design, never as numbers:
- BPHS, Jaimini Sutras, Phaladeepika, Uttara Kalamrita, Tajaka Neelakanthi, KP readers labelled as KP.
- Serious practitioner write-ups on nakshatra, dasha, varga, arudha that stay inside Jyotisha.
When you adopt a rule from outside:
- Name the frame (Parashari whole-sign Lahiri vs KP cuspal vs Raman ayanamsa).
- Execute it only on matching JSON fields.
- If the JSON cannot execute it, drop it.
- Never import tropical dignity, Placidus houses, or outer planets as lords.
Reddit / blogs are hypothesis generators. The JSON is the court.

5. CROSS-REFERENCE MATRIX (minimum)

| Claim type | Must also check |
| --- | --- |
| “This yoga gives status” | `verdicts.by_name[X].present` + 10th/9th lords + D10 + dasha of those lords |
| “Marriage in year Y” | 7th/Venus/DK dasha AND Narayana 7th-sign AND Jupiter/Saturn gochara — not D9 alone |
| “Planet is weak” | dignity + combust/cazimi + retro + avastha + shadbala ratio + AV bindu |
| “Dosha will ruin X” | `doshas_present` + exception indices + cancelling yoga + dasha involvement |
| “KP says 7th sub-lord …” | `kp_cuspal_lords` + `kp_significators` AND say this is KP-cuspal, not whole-sign house 7 |
| “Saturn is terrible now” | Sade-Sati phase + house from natal Moon + dasha lord + kakshya + 10th/7th involvement |

6. OUTPUT DISCIPLINE
After the plan and the compute:
- Produce the Section 7 reading in `astrologer_prompt.md`.
- Lead with the strongest evidenced point for the question.
- Every paragraph: at least one JSON path.
- Windows, not fake precision.
- No Western mixing, no validity disclaimers (see YOUR VOICE).
- If evidence is thin, say which layers are missing — do not pad.
That is “professional human Jyotishi”, not “generic model glanced at D9”.