# Sample session

Real, unedited output from the CLI. The reference native is the test chart used across
the test suite: **1996-12-07 10:30 IST, Chennai, IN** (Capricorn lagna, Swati Moon).

## 1. The plan, before any computation

`--plan-only` shows the reasoning: which computations the planner schedules and why.

```console
$ veda ask "What does my chart say about my career and when will I get promoted?" \
      --dob 1996-12-07 --tob 10:30 --place "Chennai, IN" \
      --reference-date 2026-08-27 --plan-only

Intents: ['timing', 'career', 'natal_overview']   Topics: ['career']
 1. get_rasi_chart  <- D1 chart: ascendant, planetary signs/houses — the anchor for every other step
 2. get_shadbala  <- Shadbala quantifies which planets can actually deliver their promises
 3. get_vimsottari_dasha  <- Vimsottari periods frame when promises activate
 4. get_bhava_bala  <- House strengths show which life areas are supported
 5. get_yogas  <- Yogas are the classical promise-modifiers of a chart
 6. get_doshas  <- Doshas flag afflictions the reading must address
 7. get_special_lagnas  <- Special lagnas (Indu, Hora...) refine wealth and strength readings
 8. get_running_dasha  <- Running periods as of 2026-08-27 — the active timing lens
 9. get_divisional_chart  <- D10 Dasamsa is the career divisional chart
10. get_raja_yogas  <- Raja yogas indicate rise, authority and status in career
```

## 2. The grounded reading

Same question, executed. Every number below was computed by PyJHora during this run.

```console
$ veda ask "What does my chart say about my career and when will I get promoted?" \
      --dob 1996-12-07 --tob 10:30 --place "Chennai, IN" \
      --reference-date 2026-08-27

Query: What does my chart say about my career and when will I get promoted?
Computed for: Born 1996-12-07 10:30:00 at Chennai, IN (lat 13.0827, lon 80.2707, UTC+5.5) [LAHIRI]
Focus: career

Your ascendant (lagna) is Capricorn. Your Moon sign is Libra (birth nakshatra Swati).
Computed placements: Sun in Scorpio (house 11); Moon in Libra (house 10); Mars in Leo
(house 8); Mercury in Sagittarius (house 12); Jupiter in Sagittarius (house 12), in own
sign; Venus in Libra (house 10), in own sign; Saturn in Pisces (house 3); Rahu in Virgo
(house 9); Ketu in Pisces (house 3).

Regarding career: the relevant houses are the 10th (career, status, actions, authority,
public life), the 6th (work, service, debts, disease, enemies, daily routine), the 2nd
(wealth, family, speech, food, savings), the 11th (gains, income, networks, elder
siblings, fulfilment of desires). House 10 holds Moon, Venus — their nature directly
colours career matters. The 10th house lord Venus sits in house 10, linking career to
that life area. The 6th house lord Mercury sits in house 12, linking career to that life
area.

Timing: you are currently running Jupiter Mahadasha with Moon Antardasha / Saturn
Pratyantardasha. Jupiter periods typically bring growth, wealth, children, wisdom,
teaching and dharma; generally auspicious (classically a generally favourable phase).
The Moon sub-period colours it toward mental focus, travel, public dealings, mother and
family matters; mood swings possible.

Strength (Shadbala ratio vs. minimum required): strongest are Sun (1.41), Venus (1.28).
Below their minimum requirement: Mercury (0.90), Jupiter (0.90) — their promises need
support from favourable dashas.

Yogas found: Amala Yoga, Anaphaa Yoga, Annadana Yoga, Anthya Vayasi Dhana Yoga, Bandhu
Bhisthyaktha Yoga, Bandhu Pujya Yoga (+16 more). Each carries its classical condition and
effect in the evidence. Doshas present: manglik, pitru.

Remedial pointers (traditional): For Manglik dosha, tradition recommends Mangal-related
observances (Tuesday discipline, Hanuman worship) and matching with another Manglik or
Kumbh-vivah where applicable. Mercury is below its strength requirement; strengthening it
(its weekday, colours and charity associated with Mercury) is the classical approach.
Jupiter is below its strength requirement; strengthening it (its weekday, colours and
charity associated with Jupiter) is the classical approach.

This reading is for reflection and entertainment. It is generated from astronomical
computations and traditional interpretive rules; it is not medical, legal or financial
advice.

[Evidence: 10 computations recorded; every figure above comes from them.]

--- execution ---
10/10 tool calls succeeded
grounding: all 10 factual claims verified against computed evidence
```

## 3. Marriage compatibility (two birth charts)

```console
$ veda ask "Am I compatible with someone born on 15 March 1997 at 8:00 in Mumbai?" \
      --dob 1996-12-07 --tob 10:30 --place "Chennai, IN" \
      --dob2 1997-03-15 --tob2 08:00 --place2 "Mumbai, IN"

Compatibility: birth stars Swati and Rohini score 14.0/36 — Not recommended.
Zero-scoring factors: bhakoot, nadi.
...
grounding: all 10 factual claims verified against computed evidence
```

## 4. Daily panchanga and muhurta windows

```console
$ veda ask "today's panchanga" --dob 2026-08-27 --tob 06:00 --place "Chennai, IN"

Query: today's panchanga
Computed for: Born 2026-08-27 06:00:00 at Chennai, IN (lat 13.0827, lon 80.2707, UTC+5.5) [LAHIRI]

Panchanga limbs: tithi Shukla Chaturdashi, nakshatra Dhanishtha, yoga Shobhana,
karana Vanija, weekday Wednesday. Lunar month Shravana, samvatsara Parabhava, ritu
Varsha (Monsoon). Sunrise 06:01:05 / sunset 18:19:47 (day length 12.3116h).
Inauspicious windows: Rahu Kalam 12:10:26–13:42:46; Yamaganda 07:33:25–09:05:46;
Gulika 10:38:06–12:10:26.
...
--- execution ---
4/4 tool calls succeeded
```

## 5. Missing data → a clarifying question, never a guess

```console
$ veda ask "When will I get married?"

I need a few details before computing anything — the computation layer requires exact
inputs and I do not guess. Missing: date of birth, birth time, birth place. Please
provide date of birth (YYYY-MM-DD), birth time (HH:MM), and birth place with country
(e.g. 'Chennai, IN'). Accuracy of the birth time matters: 4 minutes of clock error
shifts the ascendant by about 1 degree.
```
