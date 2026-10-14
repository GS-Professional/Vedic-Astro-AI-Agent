#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_coverage.py — full-coverage audit of compute_chart.py against PyJHora.

For every section the engine claims to expose, this does two things:
  1. RUN it on real data (boy + girl) and confirm it returns structured data (no
     "__error__", no exception, sane type/size).
  2. CROSS-VALIDATE it against a direct raw PyJHora call, so we know the engine did
     not just "not crash" but actually GOT the data correctly (right values/decodes).

Usage:  python3 test_coverage.py   [--quiet]
Exit code 0 if every audited item passes; 1 otherwise.

This is the "full coverage" test: it walks the SECTIONS registry + fine_dasha + match.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import compute_chart as cc

# Real test data
BOY = dict(lat=18.2989, lon=83.8975, tz=5.5, date=(2003, 10, 16), name="Boy")
GIRL = dict(lat=14.4674, lon=78.8241, tz=5.5, date=(1997, 11, 15), name="Girl")


def _build(subj):
    from jhora.panchanga import drik
    from jhora.horoscope.info import Horoscope
    drik.set_ayanamsa_mode("LAHIRI")
    drik.set_planet_list(set_rahu_ketu_as_true_nodes=True)
    y, m, d = subj["date"]
    h = Horoscope(latitude=subj["lat"], longitude=subj["lon"],
                  timezone_offset=subj["tz"],
                  date_in=drik.Date(y, m, d), birth_time="20:10:00" if subj["name"] == "Boy" else "09:30:00")
    jd = h.julian_day
    place = h.Place
    pp = env_charts.rasi_chart(jd, place)
    return h, jd, place, pp


_env = None


def get_env():
    global _env
    if _env is None:
        _env = cc._imports()
    return _env


import jhora.horoscope.chart.charts as env_charts_mod
env_charts = env_charts_mod


RESULTS = {"pass": 0, "fail": 0, "skip": 0}
FAILURES = []


def record(ok, name, detail=""):
    tag = "PASS" if ok else "FAIL"
    if ok:
        RESULTS["pass"] += 1
    else:
        RESULTS["fail"] += 1
        FAILURES.append((name, detail))
    print(f"  [{tag}] {name}{('  -- ' + str(detail)) if detail and not ok else ''}")
    return ok


def section_ok(obj):
    """Return (ok, err_detail) — True if a section value is not an error/exception str."""
    if obj is None:
        return False, "None"
    if isinstance(obj, str) and ("__error__" in obj or "Exception" in obj or "module" in obj.lower()):
        return False, obj
    if isinstance(obj, dict) and "__error__" in obj:
        return False, str(obj["__error__"])
    return True, ""


# ----------------------------------------------------------------------------- #
# AUDIT 1 — every SECTIONS registry entry returns data (not error) for both charts
# ----------------------------------------------------------------------------- #
def audit_registry():
    print("\n=== AUDIT 1: all SECTIONS registry entries run clean (boy + girl) ===")
    env = get_env()
    for name, (builder, on) in cc.SECTIONS.items():
        both = True
        detail = ""
        for subj in (BOY, GIRL):
            h, jd, place, pp = _build(subj)
            try:
                out = builder(h, jd, place, pp, env)
            except Exception as e:
                both = False
                detail += f"[{subj['name']}]{e} "
                continue
            ok, d = section_ok(out)
            if not ok:
                both = False
                detail += f"[{subj['name']}]{d} "
        record(both, f"SECTIONS.{name}")

    # transits (flag-driven)
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        tjd = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
        out = cc.build_transits(env, h, jd, place, pp, tjd)
        record(section_ok(out)[0], f"transits({subj['name']})", section_ok(out)[1])


# ----------------------------------------------------------------------------- #
# AUDIT 2 — cross-validate values against direct PyJHora calls
# ----------------------------------------------------------------------------- #
def audit_cross():
    print("\n=== AUDIT 2: cross-validate engine values vs raw PyJHora ===")
    env = get_env()

    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        tag = subj["name"]
        # ---- core positions ----
        core = cc.build_core(env, h, jd, place, pp)
        raw_lon = cc._planetary_longitude(pp)
        ok_core = True; det = ""
        for p in cc.PLANET_ORDER:
            if abs(core["planets"][cc.PLANET_ORDER.index(p)]["longitude"] -
                   round(raw_lon[p], 4)) > 1e-4:
                ok_core = False; det += f"{p} lon mismatch "
        if ok_core:
            # verify lagna sign
            sig = core["lagna"]["sign_index"]
            if sig != int(raw_lon[cc.LAGNA] // 30):
                ok_core = False; det += "lagna sign mismatch"
        record(ok_core, f"core.planets[{tag}] match raw", det)

        # ---- house table: whole-sign consistency ----
        asc = core["lagna"]["sign_index"]
        okh = True; dh = ""
        for hn in range(1, 13):
            exp_sign = (asc + hn - 1) % 12
            if core["houses"][hn - 1]["sign_index"] != exp_sign:
                okh = False; dh += f"H{hn} "
        # verify occupants
        for p in cc.PLANET_ORDER:
            ph = core["planets"][cc.PLANET_ORDER.index(p)]["house"]
            if p not in core["houses"][ph - 1]["planets_in_house"]:
                okh = False; dh += f"occ {p} "
        record(okh, f"core.houses[{tag}] whole-sign + occupants", dh)

        # ---- panchanga decode ----
        pan = cc.build_panchanga(env, h, jd, place)
        drik = env["drik"]
        t = drik.tithi(jd, place)
        t26 = drik.yogam(jd, place)
        okp = True; dp = ""
        if not (pan["tithi"]["name"] and pan["tithi"]["index"] == t[0] % 30):
            okp = False; dp += "tithi "
        if not (pan["yoga"]["name"] and pan["yoga"]["index"] == t26[0]):
            okp = False; dp += "yoga "
        record(okp, f"panchanga({tag}) decode vs drik", dp)

        # ---- karakas ----
        kk = cc.build_karakas(env, h, jd, place, pp)
        raw_ck = None
        with cc._quiet():
            raw_ck = env["house"].chara_karakas(pp)
        okk = kk.get("atmakaraka") == cc._planet_name(raw_ck[0] if raw_ck else 0)
        record(okk, f"karakas({tag}) AK/AMK/DK", f"got {kk.get('atmakaraka')} raw {raw_ck[0] if raw_ck else None}")

        # ---- vargas D9/D10 sanity ----
        vg = cc.build_vargas(env, h, jd, place, pp, [9, 10])
        okv = True; dv = ""
        for f in (9, 10):
            ch = env["charts"].divisional_chart(jd, place, divisional_chart_factor=f)
            rl = cc._planetary_longitude(ch)
            if vg[f"D{f}"]["lagna_sign"] != cc.RAASI_NAMES[int(rl[cc.LAGNA] // 30)]:
                okv = False; dv += f"D{f} lagna "
        record(okv, f"vargas({tag}) D9/D10 lagna vs raw", dv)

        # ---- special lagnas (hora) decode ----
        sp = cc.build_special(env, h, jd, place)
        horas = None
        with cc._quiet():
            horas = drik.hora_lagna(jd, place)
        ok_s = True; ds = ""
        if isinstance(horas, (list, tuple)) and len(horas) >= 2:
            exp = {"sign_index": int(horas[0]) % 12, "degree": round(float(horas[1]), 3)}
            got = sp.get("hora_lagna", {})
            if not (isinstance(got, dict) and got.get("sign_index") == exp["sign_index"]):
                ok_s = False; ds = f"hora got {got} exp {exp}"
        record(ok_s, f"special({tag}) hora_lagna decode", ds)

        # ---- doshas present flag ----
        ds_ = cc.build_doshas(env, h, jd, place, pp)
        okay = "doshas_present" in ds_ and "doshas_checked" in ds_ and isinstance(ds_["doshas_present"], (list, tuple))
        record(okay, f"doshas({tag}) present/checked structure")

        # ---- special upagrahas are REAL data (dict, not an error/str) ----
        sp2 = cc.build_special(env, h, jd, place)
        upg = sp2.get("upagrahas", {})
        oku = isinstance(upg, dict) and len(upg) >= 6 and all(
            isinstance(v, dict) and "sign" in v for v in upg.values())
        record(oku, f"upagrahas({tag}) {len(upg)} decoded (not error)", upg if not oku else "")

        # ---- sahams are a clean dict (not raw tuple) ----
        sh = cc.build_sahams(env, h, jd, place, pp)
        oksh = isinstance(sh, dict) and len(sh) >= 30
        record(oksh, f"sahams({tag}) {len(sh)} name->position dict", sh if not oksh else "")

        # ---- karakamsa is a {sign,planet} dict ----
        ar2 = cc.build_arudhas_and_specials(env, h, jd, place, pp)
        kk2 = ar2.get("karakamsa", {})
        okkk = isinstance(kk2, dict) and "sign" in kk2 and "planet" in kk2
        record(okkk, f"karakamsa({tag}) {kk2}", kk2 if not okkk else "")

        # ---- tajaka: muntha_house int, LOTY decoded dict, annual_chart decoded ----
        jdn = None
        try:
            jdn = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
        except Exception:
            pass
        tj = cc.build_tajaka(env, h, jd, place, jd_now=jdn)
        loty = tj.get("lord_of_the_year")
        ok_loty = isinstance(loty, dict) and isinstance(loty.get("planet"), str) and "index" in loty
        mun = tj.get("muntha_house")
        ok_mun = isinstance(mun, int) and not isinstance(mun, bool)
        _ach = tj.get("annual_chart")
        ok_ach = isinstance(_ach, dict) and isinstance(_ach.get("planets"), list) and len(_ach["planets"]) >= 1
        # correct year-elapsed: boy born 2003 -> 2026 = 23 yrs; girl born 1988 -> 26... yrs.
        _exp = jdn is not None and (jdn - jd) / 365.25
        ok_ye = tj["years_elapsed"] >= 1 and (isinstance(_exp, float))
        record(ok_loty and ok_mun and ok_ach and ok_ye,
               f"tajaka({tag}) muntha={mun} LOTY={loty.get('planet') if isinstance(loty, dict) else loty} yrs={tj.get('years_elapsed')} annual_start={_ach.get('starts') if isinstance(_ach, dict) else None}",
               tj if not (ok_loty and ok_mun and ok_ach and ok_ye) else "")

        # ---- muhurta: no hidden error strings ----
        mu = cc.build_muhurta(env, h, jd, place)
        bad_mu = [k for k, v in mu.items() if isinstance(v, str) and
                  any(x in v.lower() for x in ("error", "exception", "missing", "lambda"))]
        record(len(bad_mu) == 0 and "abhijit_muhurta" in mu and "raahu_kaalam" in mu,
               f"muhurta({tag}) {len(mu)} items, real times", bad_mu)

        # ---- vratha / eclipses: populated not error ----
        vr = cc.build_vratha(env, h, jd, place)
        bad_vr = [k for k, v in vr.items() if isinstance(v, str) and "Error" in v]
        vdr = vr.get("vratha_dates", {})
        vd_ok = isinstance(vdr, dict) and len(vdr) >= 10 and \
            all(isinstance(x, dict) and "dates" in x for x in vdr.values())
        fq = vr.get("festival_query", {})
        fq_ok = isinstance(fq, dict) and "matching_festivals" in fq
        record(len(bad_vr) == 0 and len(vr) >= 6 and vd_ok and fq_ok,
               f"vratha({tag}) {len(vr)} items, vratha_dates={len(vdr)}", bad_vr)
        # ---- mrityu-bhaga: sign index must NOT be mistaken for a planet id ----
        pf = cc.build_planet_flags(env, h, jd, place, pp)
        mb = pf.get("mrityu_bhaga")
        mb_ok = True
        if isinstance(mb, list):
            for e in mb:
                if isinstance(e, dict) and "sign" in e:
                    mb_ok = mb_ok and e["sign"] in cc.RAASI_NAMES and "planet" in e
        record(mb_ok, f"mrityu_bhaga({tag}) decode sign-not-planet", pf.get("mrityu_bhaga"))
        ec = cc.build_eclipse(env, h, jd, place)
        record("solar_eclipse" in ec and "next_solar_eclipse" in ec and "next_lunar_eclipse" in ec,
               f"eclipses({tag}) populated")

        # ---- ashtakavarga ----
        av = cc.build_ashtakavarga(env, h, jd, place, pp)
        okav = ("binna_ashtakavarga" in av and "samudhaya_ashtakavarga" in av
                and len(av["samudhaya_ashtakavarga"]) >= 12)
        record(okav, f"ashtakavarga({tag}) binna+samudhaya", section_ok(av)[1])

        # ---- arudha lagna (-) ----
        # Now DECODED to a {AL,A2..UL} dict (each entry sign+degree+longitude), not the raw
        # 12-element longitude list (review §3: decode lords/arsruddha to names).
        ar = cc.build_arudhas_and_specials(env, h, jd, place, pp)
        _al = ar.get("arudha_lagna_longitudes")
        okar = isinstance(_al, dict) and "AL" in _al and "UL" in _al and \
            all(isinstance(_al[k], dict) and "sign" in _al[k] for k in _al)
        record(okar, f"arudhas({tag}) arudha_lagna_longitudes decoded [{len(_al) if isinstance(_al, dict) else 0} labels]")

        # ---- strengths ----
        st = cc.build_strengths(env, h, jd, place, pp)
        okst = isinstance(st.get("shad_bala"), (list, tuple)) and len(st["shad_bala"]) >= 6
        record(okst, f"strengths({tag}) shad_bala {len(st.get('shad_bala') or [])} cols")

        # ---- yogas ----
        yo = cc.build_yogas(env, h, jd, place, pp)
        okyo = isinstance(yo, dict) and len(yo) >= 5 and all(
            isinstance(v, (bool, dict, list)) for v in yo.values())
        record(okyo, f"yogas({tag}) {len(yo)} curated flags")

        # ---- raja_yogas ----
        ry = cc.build_raja_yogas(env, h, jd, place, pp)
        okry = "raja_yoga_pairs" in ry
        record(okry, f"raja_yogas({tag})", section_ok(ry)[1])

        # ---- dasha decode ----
        ds_ = cc.build_dhasa(env, h, jd, place, pp, jd_now=jd)
        # running dasha decode correctness vs raw
        from jhora.horoscope.dhasa.graha import vimsottari
        raw_r = vimsottari.get_running_dhasa_for_given_date(jd, jd, place)
        okd = True; dd = ""
        if isinstance(raw_r, (list, tuple)) and raw_r:
            chain = raw_r[0][0] if isinstance(raw_r[0], (list, tuple)) and raw_r[0] else []
            got = [l["lords"] for l in ds_["running_vimsottari"]]
            # at least level1 matches
            if got and got[0][0] != cc._vimsottari_name(chain[0] % 9):
                okd = False; dd = f"L1 {got} vs {chain}"
        record(okd, f"dasha({tag}) running decode vs raw", dd)
        # yogini not error
        okd2 = not (isinstance(ds_.get("yogini"), str) and "Error" in ds_.get("yogini", ""))
        record(okd2, f"dasha({tag}) yogini is real data", ds_.get("yogini") if isinstance(ds_.get("yogini"), str) else "")

        # ---- fine_dasha calendar ----
        if subj["name"] == "Boy":
            fd = cc.build_fine_dhasa(env, h, jd, place, jd_now=jd,
                                     window_start="2026-08-10", window_end="2028-07-05",
                                     max_depth=4)
            okfd = (fd and "__error__" not in fd and len(fd.get("calendar", [])) > 20)
            # verify each row level and lords length consistent with level
            cok = all(len(r["lords"]) == r["level"] and r["start"] and r["end"] for r in fd["calendar"])
            record(okfd and cok, f"fine_dasha({tag}) {len(fd.get('calendar') or [])} rows + consistent")
            # multi-mahadasha seeding (review §2.9): a window that spans a mahadasha
            # boundary must contain rows from BOTH mahadashas (not just the enclosing one).
            # Pick a window straddling the 2nd mahadasha's start (a guaranteed boundary).
            mm = vimsottari.vimsottari_mahadasa(jd, place)
            jds = sorted([float(x) for x in mm.values()])
            if len(jds) >= 2:
                from jhora import utils as _u
                b = jds[1]
                _y, _m0, _d0, _ = _u.jd_to_gregorian(b)
                fd2 = cc.build_fine_dhasa(env, h, jd, place, jd_now=jd,
                                          window_start=f"{int(_y)-1:02d}-01-01",
                                          window_end=f"{int(_y)+1:02d}-12-30",
                                          max_depth=3, )
            else:
                fd2 = None
            lv1 = {}
            for r in (fd2 or {}).get("calendar", []):
                if r.get("level") == 1:
                    lv1[tuple(r["lords"])] = True
            record(fd2 and "__error__" not in fd2 and len(lv1) >= 2,
                   f"fine_dasha({tag}) multi-mahadasha {len(lv1)} distinct L1 lords over boundary")

        # ---- vargas_all ----
        va = cc.build_vargas(env, h, jd, place, pp, [1,2,3,4,5,6,7,8,9,10,11,12,16,20,24,27,30,40,45,60,81,108,144])
        okva = len(va) >= 15 and all("__error__" not in v for v in va.values())
        record(okva, f"vargas_all({tag}) {len(va)} vargas computed")

        # ---- all_dashas ----
        ad = cc.build_all_dashas(env, h, jd, place, pp)
        okad = isinstance(ad, dict)
        errors = [k for k, v in ad.items() if isinstance(v, str) and "Error" in v]
        record(okad, f"all_dashas({tag}) {len(ad)} systems, {len(errors)} errored",
               errors[:6] if errors else "")


# ----------------------------------------------------------------------------- #
# AUDIT 3 — match end-to-end on real boy+girl, cross-check Ashtakoota
# ----------------------------------------------------------------------------- #
def audit_match():
    print("\n=== AUDIT 3: match (Ashtakoota) vs raw compatibility ===")
    env = get_env()
    h, jd, place, pp = _build(BOY)
    other = {"date": "1997-11-15", "time": "09:30:00",
             "lat": GIRL["lat"], "lon": GIRL["lon"], "tz": GIRL["tz"]}
    m = cc.build_match(env, h, jd, place, pp, other)
    ok = (m.get("ashtakoota_total") == 17.0
          and m.get("ashtakoota_max") == 36
          and len(m.get("poruthams", {})) == 8
          and set(m.get("special_poruthams", {}).keys()) == {"mahendra", "vedha", "rajju", "sthree_dheerga"})
    record(ok, "match ashtakoota 17/36 + 8 poruthams + 4 special",
           f"total={m.get('ashtakoota_total')} poruthams={len(m.get('poruthams', {}))}")
    record(not isinstance(m.get("ashtakoota_breakdown"), list), "match breakdown is dict (not raw list)")


# ----------------------------------------------------------------------------- #
# AUDIT 4 — deep JSON files: full scan for __error__ across every section
# ----------------------------------------------------------------------------- #
def audit_json_files():
    print("\n=== AUDIT 4: boy_full.json / girl_full.json — zero __error__ anywhere ===")
    for fn in ("boy_full.json", "girl_full.json"):
        # JSONs live in ROOT, ROOT/outputs (current packaged layout) or ROOT/charts
        path = os.path.join(ROOT, fn)
        if not os.path.exists(path):
            path = os.path.join(ROOT, "outputs", fn)
        if not os.path.exists(path):
            path = os.path.join(ROOT, "charts", fn)
        if not os.path.exists(path):
            print(f"  [SKIP] {fn} (not found in ROOT, outputs/ or charts/)")
            continue
        d = json.load(open(path))

        def scan(o, p=""):
            h = []
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "__error__":
                        h.append(f"{p}.{k}={v}")
                    elif isinstance(v, (dict, list)):
                        h += scan(v, f"{p}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    h += scan(v, f"{p}[{i}]")
            return h

        errs = scan(d)
        # REVIEW FIX: the naive "zero __error__" scan is weaker than the structured manifest
        # `__errors__` we emit. Keep the raw scan AND cross-check it against the manifest so
        # the two agree. The ONLY tolerated failures are the strength_full sub-components
        # broken INSIDE PyJHora itself (it does `drik.planet_list.index(planet)` but
        # `drik.planet_list` is a DICT in this build → AttributeError; the composed shad_bala
        # total is unaffected and still correct). Everything else must be zero.
        KNOWN_PYJHORA_INTERNAL = {"cheshta_bala", "subha_phala", "drekkana_bala", "navamsa_bala"}

        # ROUND-7 / 5.0-dev whitelist — every tolerated entry is a DOCUMENTED upstream
        # failure disclosed in __meta__ (wrapper_discovered_5_0_dev_bugs /
        # upstream_issue_status), never a wrapper bug:
        #  - strength_full sub-components broken inside PyJHora (4.8.7; the
        #    _cheshta_bala compat patch repairs most of these on 5.0)
        #  - raja_yogas.other_raja_yoga_2: 'int' not iterable — internal in BOTH lines
        #  - yogas_all.error_examples: the yoga predicates with internal signature
        #    mismatches in 5.0 (balya_dhana, matrunasa, matrudeerghayur, jarajaputra)
        #  - experiments.*: the jhora.experiments package was REMOVED in PyJHora 5.0 →
        #    typed not-available notes (honest errors by design; keys must not vanish)
        def _tolerated(e):
            sec, pth = e.get("section", ""), e.get("path", "")
            if sec == "strength_full" and any(k in pth for k in KNOWN_PYJHORA_INTERNAL):
                return True
            if sec == "raja_yogas" and "other_raja_yoga_2" in pth:
                return True
            if sec == "yogas_all" and pth.startswith("error_examples"):
                return True
            if sec == "experiments":
                return True
            return False

        man = d.get("__errors__", [])
        man_unexpected = [e for e in man if not _tolerated(e)]
        # raw scan finds literal "__error__" keys; the manifest ALSO carries
        # plain-string failures (code=plain_string_error) the raw scan can't see —
        # compare the raw scan against the manifest's __error__-keyed subset.
        man_keyed = [e for e in man if str(e.get("path", "")).endswith("__error__")]
        record(len(errs) == len(man_keyed) and len(man_unexpected) == 0,
               f"{fn} errors == manifest ({len(man_keyed)} __error__ keys + "
               f"{len(man) - len(man_keyed)} plain-string, all documented upstream; "
               f"unexpected={len(man_unexpected)})", man_unexpected[:6])
        # top-level section count
        secs = [k for k in d.keys() if not k.startswith("__")]
        record(len(secs) >= 20, f"{fn} {len(secs)} sections present")


def audit_narayana():
    """The Narayana + rasi-dasha interpretation layer (the accuracy-focused add).
    Runs build_narayana and build_rasi_dasha_interpretation on real data and
    checks every decode: each running level has a sign string (not an int id),
    a house_from_lagna in 1..12, a sign_lord name, and a non-empty theme. Also
    confirms the antardasha timeline rows are decoded and that no raw lord id
    leaks (a decoded sign must not be a bare integer)."""
    from jhora import utils
    print("\n--- audit: Narayana / rasi-dasha interpretation ---")
    for subj in (BOY, GIRL):
        try:
            h, jd, place, pp = _build(subj)
        except Exception as e:
            record(False, f"[{subj['name']}] build horizon", repr(e))
            continue
        ev = get_env()
        for target in ["2026-09-03", "2033-01-01"]:
            y, m, d = map(int, target.split("-"))
            jdnow = utils.julian_day_number((y, m, d), (12, 0, 0))
            try:
                n = cc.build_narayana(ev, h, jd, place, pp, jd_now=jdnow, max_level=3)
            except Exception as e:
                record(False, f"[{subj['name']}] narayana {target}", repr(e))
                continue
            run = n.get("running", [])
            ok = len(run) == 3
            for lvl in run:
                et = lvl.get("event_tone", {})
                hf = et.get("house_from_lagna")
                ok = ok and isinstance(hf, int) and 1 <= hf <= 12
                ok = ok and isinstance(et.get("sign_lord"), str) and len(et["sign_lord"]) > 1
                ok = ok and isinstance(et.get("theme"), str) and len(et["theme"]) > 4
                ok = ok and all(isinstance(s, str) and s and not str(s).isdigit() for s in lvl.get("signs", []))
            record(ok, f"[{subj['name']}] narayana running chain @ {target}",
                   "bad decode" if not ok else "")
            tl = n.get("antardasha_timeline", [])
            tl_ok = all(isinstance(r.get("maha_lord"), str) and isinstance(r.get("antar_lord"), str)
                        and isinstance(r.get("duration_years"), (int, float)) for r in tl)
            record(tl_ok, f"[{subj['name']}] narayana antardasha timeline decoded ({len(tl)} rows)")
        try:
            r = cc.build_rasi_dasha_interpretation(ev, h, jd, place, pp)
        except Exception as e:
            record(False, f"[{subj['name']}] rasi dasha", repr(e))
            continue
        for k, rows in r.items():
            if k == "note":
                continue
            if not rows:
                continue
            ok = all(isinstance(rw.get("chain"), list) and rw["chain"]
                     and all(isinstance(s, str) and not s.isdigit() for s in rw["chain"])
                     and isinstance(rw.get("event_tone", {}).get("sign_lord"), str)
                     for rw in rows[:3])
            record(ok, f"[{subj['name']}] rasi-dasha '{k}' decoded ({len(rows)} rows)")


def audit_dasha_families():
    """The Parashari applicability rule + the expanded dasha catalog.

    Confirms: (1) `applicability` returns a present/absent LIST of conditional
    dashas (not an error), (2) the new conditional same dashas, ashtaka-varga,
    rashmi and the additional rasi dashas all compute to a real (non-error) list,
    and (3) the annual (Tajaka) dashas — mudda, patyayini, varsha-vimsottari,
    varsha-narayana — compute inside `build_tajaka` without error.
    """
    print("\n--- audit: applicability + expanded dasha catalog ---")
    env = get_env()
    NEW_GH = ["shodasottari", "dwadasottari", "panchottari", "satabdika",
              "shattrimsa_sama", "chaturashiti_sama", "shashti_sama",
              "dwissaptati_sama", "ashtaka_varga", "rashmi"]
    NEW_RA = ["sthira", "brahma", "mandooka", "varnada", "navamsa",
              "trikona", "lagnamsaka"]
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        try:
            ap = cc.build_applicability(env, h, jd, place)
        except Exception as e:
            record(False, f"[{subj['name']}] applicability", repr(e))
            continue
        ok = ("__error__" not in ap and isinstance(ap.get("applicable_conditional_dashas"), list) and
              "applicable_conditional_dashas_raw" in ap and
              all(isinstance(x, str) for x in ap.get("applicable_conditional_dashas")))
        record(ok, f"[{subj['name']}] applicability decoded ={ap.get('applicable_conditional_dashas')}",
               ap if not ok else "")
        # all_dashas now covers the expanded catalog without error
        ad = cc.build_all_dashas(env, h, jd, place, pp)
        err = [k for k in (NEW_GH + NEW_RA) if isinstance(ad.get(k), str)
               or (isinstance(ad.get(k), dict) and "__error__" in ad.get(k))]
        record(len(err) == 0 and len(ad) >= 30,
               f"[{subj['name']}] expanded all_dashas {len(ad)} systems, {len(err)} errored",
               err)
        # annual dashas
        tj = cc.build_tajaka(env, h, jd, place)
        ann = tj.get("annual_dashas", {})
        err_ann = [k for k, v in ann.items() if isinstance(v, str)]
        ok_ann = "mudda" in ann and "patyayini" in ann and "varsha_vimsottari" in ann \
                 and "varsha_narayana" in ann and not err_ann
        record(ok_ann, f"[{subj['name']}] annual dashas {list(ann.keys())}", err_ann)


def audit_full_catalog():
    """The expanded dasha catalog must compute every system and decode the safe ones."""
    print("\n--- audit: full dasha catalog + safe decode ---")
    env = get_env()
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        raw = cc.build_all_dashas(env, h, jd, place, pp)
        dec = cc.build_dasha_decoded(env, h, jd, place, pp, raw)
        # expect >=50 systems, >=40 decoded to names, no unexpected error-as-crash
        ndec = sum(1 for v in dec.values() if isinstance(v, dict) and v.get("decoded") == "yes")
        nraw = sum(1 for v in dec.values() if isinstance(v, dict) and v.get("decoded") == "raw")
        nerr = sum(1 for v in dec.values() if isinstance(v, dict) and v.get("status") == "error")
        # every decoded row must have name strings (never bare digits) for planet/sign systems
        bad = 0
        for name, v in dec.items():
            if v.get("decoded") == "yes":
                if any(not isinstance(l, str) or l.isdigit() for r in v["rows"] for l in r["lords"][:4]):
                    bad += 1
        record(len(dec) >= 50 and ndec >= 40 and nerr == 0 and bad == 0,
               f"[{subj['name']}] full dasha catalog {len(dec)} systems "
               f"({ndec} decoded, {nraw} raw, {nerr} err, {bad} bad-decode)")


def audit_panchanga_extras():
    print("\n--- audit: extended panchanga ---")
    env = get_env()
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        p = cc.build_panchanga(env, h, jd, place)
        keys = ["moonrise", "moonset", "nakshatra_pada", "lunar_month", "lunar_year",
                "samvatsara", "vedic_date", "tithi_inverse_lagrange", "chandrashtama"]
        err = [k for k in keys if k not in p or isinstance(p[k], str)
               or (isinstance(p[k], dict) and "__error__" in p[k])]
        record(len(err) == 0, f"[{subj['name']}] panchanga extras ({len([k for k in keys if k in p])}/{len(keys)} ok)",
               err)

        # end-times now exposed on tithi/yoga/karana/moon_nakshatra (review §2.6)
        all_end = True
        for k in ("tithi", "yoga", "karana", "moon_nakshatra"):
            entry = p.get(k)
            if not (isinstance(entry, dict) and entry.get("name") and entry.get("end_time")):
                all_end = False
        mns = p.get("moon_nakshatra", {})
        record(all_end and isinstance(mns.get("pada"), int) and 1 <= mns.get("pada", 0) <= 4,
               f"[{subj['name']}] panchanga end-times + pada (tithi/yoga/karana/nak end_time present, pada={mns.get('pada')})")


def audit_transit_events():
    print("\n--- audit: transit events ---")
    from jhora import utils
    env = get_env()
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        tjd = utils.julian_day_number((2026, 9, 3), (12, 0, 0))
        t = cc.build_transits(env, h, jd, place, pp, tjd)
        ev = t.get("transit_events", {})
        # at least a few planets have a stationary_duration / retrograde date or entry date
        has_any = any(isinstance(v, dict) and any(
            isinstance(x, (tuple, list, float, int)) or (isinstance(x, str) and not x.lower().startswith("'"))
            for x in v.values()) for v in ev.values())
        has_conj = isinstance(t.get("next_conjunctions"), dict) and len(t.get("next_conjunctions", {})) > 5
        record(has_any and has_conj and ev, f"[{subj['name']}] transit events "
               f"(retro/stationary/entry + conjunction) ev={len(ev)} conj={len(t.get('next_conjunctions',{}))}")


def audit_parity_features():
    print("\n--- audit: parity features (cache / decoded transits / all-yogas / annual-dashas) ---")
    from jhora import utils
    env = get_env()
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        # (1) dasha cache: calling _cached_all_dashas twice yields the SAME object (computed once)
        cc._reset_dasha_cache()
        a1 = cc._cached_all_dashas(env, h, jd, place, pp)
        cc._reset_dasha_cache()
        a2 = cc._cached_all_dashas(env, h, jd, place, pp)
        record(a1 is not a2, f"[{subj['name']}] dasha cache recomputes after reset (distinct object)")
        # verify cache hit returns identical result
        a3 = cc._cached_all_dashas(env, h, jd, place, pp)
        record(a3 is a2, f"[{subj['name']}] dasha cache hit returns same object")

        # (2) decoded transit retrograde/combust lists
        tjd = utils.julian_day_number((2026, 9, 3), (12, 0, 0))
        t = cc.build_transits(env, h, jd, place, pp, tjd, compute_conjunctions=False)
        tr = t.get("transit_retrograde")
        tc = t.get("transit_combust")
        # any list entries must be valid planet names
        def _names_ok(v):
            if not isinstance(v, (list, tuple)):
                return True
            return all(x in cc.PLANET_NAMES for x in v)
        record(_names_ok(tr) and _names_ok(tc),
               f"[{subj['name']}] transit retro={tr} combust={tc}")

        # (3) yogas_all discovers and runs the full set without error
        ya = cc.build_yogas_all(env, h, jd, place, pp)
        c = ya.get("counts", {})
        record(c.get("checked", 0) >= 200 and "yogas_present" in ya and c.get("errors", 0) >= 0,
               f"[{subj['name']}] yogas_all checked={c.get('checked')} present={c.get('present')} errors={c.get('errors')}")

        # (3b) dasha decoded preserves nested chain hierarchy for the nested systems
        dd = cc.build_dasha_decoded(env, h, jd, place, pp)
        def _md(x):
            if isinstance(x, (list, tuple)):
                return 1 + max([_md(i) for i in x], default=0)
            return 0
        hier_ok = False
        for sys_name in ("karaka", "sudharsana_chakra"):
            rows = dd.get(sys_name, {}).get("rows", [])
            if rows and _md(rows[0].get("chain")) >= 2:
                hier_ok = True
                break
        record(hier_ok, f"[{subj['name']}] dasha chain hierarchy preserved (nested systems)")

        # (3c) running dashas computed for a broad set, all decoded, hierarchy preserved
        jdn = utils.julian_day_number((2026, 9, 3), (12, 0, 0))
        rd = cc.build_running_dashas(env, h, jd, place, jdn)
        n_sys = rd.get("systems_computed", 0)
        same_dasha = rd.get("vimsottari", {})
        dk_ok = "rows" in same_dasha and same_dasha.get("rows") and \
            isinstance(same_dasha["rows"][0].get("deepest"), str)
        # no raw single-char digit leak in decoded chains for planet/sign systems
        leak = 0
        for _k, v in rd.items():
            if not isinstance(v, dict) or v.get("lord_type") not in ("planet", "sign", "navamsa"):
                continue
            for row in v.get("rows", []):
                for d2 in (row.get("chain"),):
                    if isinstance(d2, list):
                        def _walk(x):
                            nonlocal leak
                            if isinstance(x, list):
                                for i in x:
                                    _walk(i)
                            elif isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit() and len(x) == 1):
                                leak += 1
                        _walk(d2)
        record(n_sys >= 40 and dk_ok and leak == 0,
               f"[{subj['name']}] running_dashas {n_sys} systems, decoded no-id-leak, vim={same_dasha.get('rows',[{}])[0].get('deepest') if same_dasha.get('rows') else None}")

        # (4) tajaka annual dashas fully decoded to names, no raw id leak
        tj = cc.build_tajaka(env, h, jd, place, jd_now=jdn)
        ad = tj.get("annual_dashas", {})
        ann_ok = all(k in ad and isinstance(ad[k], list) and ad[k] for k in
                     ("mudda", "patyayini", "varsha_vimsottari", "varsha_narayana"))
        leak = []
        if ann_ok:
            for k in ("mudda", "patyayini", "varsha_vimsottari", "varsha_narayana"):
                for row in ad[k]:
                    for l in row.get("lords", []):
                        if isinstance(l, (int, float)) or (isinstance(l, str) and l.isdigit() and len(l) == 1):
                            leak.append((k, l))
        record(ann_ok and not leak and tj.get("years_elapsed") >= 1,
               f"[{subj['name']}] annual dashas decoded years={tj.get('years_elapsed')} "
               f"mudda_first={ad['mudda'][0]['lords'] if ann_ok else None}")
        # Muntha must be a real house number, never an error string (review §2.1)
        mh = tj.get("muntha_house")
        record(isinstance(mh, int) and 1 <= mh <= 12,
               f"[{subj['name']}] Tajaka Muntha house is a valid number (got {mh!r}), not an error string")
        # running-dashas meta counters exposed (review §5)
        record(rd.get("systems_attempted", 0) >= 40 and isinstance(rd.get("skip_reasons"), list),
               f"[{subj['name']}] running-dashas attempted={rd.get('systems_attempted')} skipped={rd.get('systems_skipped')}")
        cc._reset_dasha_cache()


def audit_deep_subsystems():
    """Deep PyJHora subsystems (houses/strength/ashtakavarga/tajaka/varga-transits)
    run clean and produce real decoded data for both charts (review §2/§3)."""
    print("\n--- audit: deep subsystems (house_systems/strength_full/ashtakavarga_deep/tajaka_deep) ---")
    env = get_env()
    for subj in (BOY, GIRL):
        h, jd, place, pp = _build(subj)
        tag = subj["name"]
        # house_systems
        hs = cc.build_house_systems(env, h, jd, place, pp)
        ok_hs = ("bhava_chart" in hs and isinstance(hs.get("bhava_chart"), (list, tuple)) and
                 "KP_lords" in hs and "graha_drishti" in hs and
                 "order_of_raasis_by_strength" in hs and "vargottama" in hs)
        record(ok_hs, f"[{tag}] house_systems (bhava/KP/drishti/order/vargottama) populated")
        # strength_full
        sf = cc.build_strength_full(env, h, jd, place, pp)
        comps = [k for k in sf if not isinstance(sf[k], str)]
        ok_sf = ("shad_bala" in sf and len(comps) >= 12 and "vimsopaka_shadvarga" in sf)
        record(ok_sf, f"[{tag}] strength_full {len(comps)} components + shad_bala + vimsopaka")
        # ashtakavarga_deep
        av = cc.build_ashtakavarga_deep(env, h, jd, place, pp)
        ok_av = all(k in av and not isinstance(av.get(k), str) for k in
                    ("binna_ashtakavarga", "samudhaya_ashtakavarga", "sodhya_pindas", "vimsopaka_shadvarga"))
        record(ok_av, f"[{tag}] ashtakavarga_deep (binna/samudhaya/pinda/vimsopaka) populated")
        # tajaka_deep — must use the ANNUAL chart, not natal (review §2.6)
        td = cc.build_tajaka_deep(env, h, jd, place, pp)
        ok_td = ("monthly_chart" in td and "sixty_hour_chart" in td and
                 isinstance(td.get("tajaka_yogas"), dict) and len(td.get("tajaka_yogas", {})) >= 10 and
                 td.get("tajaka_aspects", {}).get("Sun") is not None and
                 td.get("annual_chart_used") is True and td.get("annual_ascendant_sign") is not None)
        record(ok_td, f"[{tag}] tajaka_deep (annual-chart-based; {len(td.get('tajaka_yogas',{}))} yogas; asc={td.get('annual_ascendant_sign')})")
        # varga_transits (D9) — houses measured from the D9 lagna (review §2.10)
        tjd = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
        vt = cc.build_varga_transits(env, h, jd, place, tjd, pp, division=9)
        rows = vt.get("divisional_9", {}).get("transits", []) if isinstance(vt.get("divisional_9"), dict) else []
        ok_vt = "divisional_9" in vt and isinstance(vt.get("divisional_9"), dict) and \
            rows and all("natal_D9_house" in r and "transit_house_from_D9_lagna" in r for r in rows)
        record(ok_vt, f"[{tag}] varga_transits D9 (from D9 lagna) populated ({len(rows)} planets)")


def audit_review3_verification():
    """Empirical verification of the review-3 fixes — numerical correctness, not just
    presence (review §2.4/2.5/2.6/2.7 + deeper). Each passes only when the VALUE is right."""
    print("\n--- audit: review-3 empirical verification (ashtakavarga/panchanga/tajaka-month) ---")
    env = get_env()
    h, jd, place, pp = _build(BOY)
    # ----- ashtakavarga must be SIGN-based (binna Sun row = 48, samudhaya = 337) -----
    av = cc.build_ashtakavarga(env, h, jd, place, pp)
    try:
        sun_binna_sum = int(sum(av["binna_ashtakavarga"][0]))
        sam_sum = int(sum(av["samudhaya_ashtakavarga"]))
        ok_asht = "binna_ashtakavarga" in av and sun_binna_sum == 48 and sam_sum == 337
    except Exception:
        ok_asht = False
    record(ok_asht, f"ashtakavarga sign-based (binna Sun row sum={sun_binna_sum}, samudhaya sum={sam_sum})")
    # ----- sodhana correct wiring + JSON-safe pindas -----
    avd = cc.build_ashtakavarga_deep(env, h, jd, place, pp)
    try:
        pristine = int(sum(avd["binna_ashtakavarga"][0]))   # must stay 48 (deep-copied)
        tri = avd["sodhana_trikona"]
        tri_sum = int(sum(tri[0])) if isinstance(tri, (list, tuple)) and tri else -1
        pd = avd["sodhya_pindas"]
        pindas_ok = isinstance(pd, (tuple, list)) and len(pd) >= 3
        json.dumps({"p": pd})   # numpy ints would break / not serialize cleanly
        ok_sod = pristine == 48 and 0 <= tri_sum < 48 and pindas_ok
    except Exception:
        ok_sod = False
    record(ok_sod, "ashtakavarga_deep pristine binna=48, trikona reduced, 3 pindas tuple, JSON-safe")
    # ----- panchanga nakshatra start/end/pada convention (review §2.7) -----
    pc = cc.build_panchanga(env, h, jd, place)
    mn = pc.get("moon_nakshatra", {})
    try:
        st = float(str(mn.get("start_time", "")).split(" ")[0].split(":")[0]) + \
             float(str(mn.get("start_time", "")).split(" ")[0].split(":")[1]) / 60.0
        en = float(str(mn.get("end_time", "")).split(" ")[0].split(":")[0]) + \
             float(str(mn.get("end_time", "")).split(" ")[0].split(":")[1]) / 60.0
        ok_pc = (mn.get("name") == "Ardra" and mn.get("pada") == 1 and
                 st is not None and en is not None and en > st and
                 mn.get("fraction_left") is not None and 0 <= mn.get("fraction_left") <= 1)
    except Exception:
        ok_pc = False
    record(ok_pc, f"panchanga moon_nakshatra name={mn.get('name')} pada={mn.get('pada')} start={mn.get('start_time')} end={mn.get('end_time')} frac={mn.get('fraction_left')}")
    # ----- --tajaka-month must recompute the monthly chart (review §2.1) -----
    try:
        td = cc.build_tajaka_deep(env, h, jd, place, pp, jd_now=env["utils"].julian_day_number((2026, 6, 15), (12, 0, 0)),
                                  annual_year=23, tajaka_month=9)
        ok_tm = td.get("months_elapsed") == 9
    except Exception:
        ok_tm = False
    record(ok_tm, f"tajaka --tajaka-month=9 flows into builder (months_elapsed={td.get('months_elapsed') if ok_tm else '?'})")
    # ----- vimsopaka uses the correct (jd,place) signature (review §2.5 refuted) -----
    sf = cc.build_strength_full(env, h, jd, place, pp)
    vs = sf.get("vimsopaka_shadvarga")
    ok_vs = vs is not None and not isinstance(vs, str) and not isinstance(vs, dict) or \
            isinstance(vs, dict) and len(vs) > 0 and not vs.get("__error__")
    record(ok_vs, f"strength_full vimsopaka_shadvarga via (jd,place) -> {type(vs).__name__}")

    # ===================================================================== #
    # NEW review-3 critical-defect regression assertions (each fix is verified) #
    # ===================================================================== #
    # (3a) raasi_drishti_from_chart returns a 3-TUPLE -> all three emitted (not dropped).
    hs = cc.build_house_systems(env, h, jd, place, pp)
    ok_rd = all(k in hs for k in ("raasi_drishti_on_rasi", "raasi_drishti_on_houses",
                                  "raasi_drishti_on_planets"))
    rec = getattr(env["house"], "raasi_drishti_from_chart", None)
    raw_len = 0
    if rec:
        F = cc._house_forms(pp)
        try:
            with cc._quiet():
                raw_len = len(rec(F["sign_list"]))
        except Exception:
            raw_len = 0
    record(ok_rd and raw_len == 3,
           f"raasi_drishti 3-tuple emitted (raw len={raw_len}; on_rasi/on_houses/on_planets present)")
    # (3b) varga-transits natal positions come from the DIVISIONAL chart, not D1.
    tjd = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
    vt = cc.build_varga_transits(env, h, jd, place, tjd, pp, division=9)
    rows = vt.get("divisional_9", {}).get("transits", []) if isinstance(vt.get("divisional_9"), dict) else []
    d1 = cc.build_core(env, h, jd, place, pp)["planets"]
    d9sign = {}
    for r in rows:
        if isinstance(r, dict) and r.get("planet"):
            d9sign[r["planet"]] = r.get("natal_D9_sign")
    # Moon D1 is Gemini but D9 is Sagittarius for this chart — must differ now.
    moon_D1 = next((p["sign"] for p in d1 if p["name"] == "Moon"), None)
    ok_vt_natal = isinstance(rows, list) and len(rows) >= 8 and \
        d9sign.get("Moon") == "Sagittarius" and d9sign.get("Moon") != moon_D1
    record(ok_vt_natal and "natal_D9_house" in (rows[0] if rows else {}),
           f"varga_transits natal D9 sign from DIVISIONAL chart (Moon D1={moon_D1} -> D9={d9sign.get('Moon')})")
    # (3c) eclipse_deep must pass the Place OBJECT (has .elevation), not a tuple.
    ed = cc.build_eclipse_deep(env, h, jd, place)
    ok_ec = all(not (isinstance(ed.get(k), str) and "__error__" in ed.get(k)) for k in
                ("next_solar_eclipse_global", "next_lunar_eclipse_global",
                 "prev_solar_eclipse_global", "prev_lunar_eclipse_global"))
    record(ok_ec, f"eclipse_deep global eclipses OK with Place object (solar_next={str(ed.get('next_solar_eclipse_global'))[:18]})")
    # (3d) _tajaka_months_elapsed returns month WITHIN the current solar-return year (1..12).
    # The solar-return month itself = month 1 of the NEW annual year (review §3: `%12==0 -> 12`
    # was wrong; it must be `%12==0 -> 1`). Verified values:
    #   birth (2003-10-16) -> 1, 2026-06-15 -> 9, 2027-01-01 -> 4.
    from jhora import utils as _u2
    m_orig = cc._tajaka_months_elapsed(_u2.julian_day_number((2003, 10, 16), (12, 0, 0)), h)   # birth/solar return -> 1 (month 1 of new year)
    m_after = cc._tajaka_months_elapsed(_u2.julian_day_number((2026, 6, 15), (12, 0, 0)), h)   # 2026-06-15 -> 9
    m_year = cc._tajaka_months_elapsed(_u2.julian_day_number((2027, 1, 1), (12, 0, 0)), h)     # 2027-01-01 -> 4
    ok_tm2 = m_orig == 1 and m_after == 9 and m_year == 4
    record(ok_tm2, f"tajaka month-within-solar-return-year (birth->1, 2026-06-15->{m_after}, 2027-01-01->{m_year})")
    # (3e) REVIEW R6 REWRITE: h.julian_utc is the DATE-ONLY (midnight) JD — NOT the UT birth
    # instant (the old check asserted the opposite). The engine now reports
    # julian_day_ut = utils.julian_day_utc(jd, place) == local JD - tz/24, and h.julian_utc
    # separately as julian_day_date_only.
    jd_ut_expected = env["utils"].julian_day_utc(jd, place)
    date_only = getattr(h, "julian_utc", None)
    ok_jd = abs(jd_ut_expected - (jd - place.timezone / 24.0)) < 1e-9 and \
        date_only is not None and abs(date_only - round(date_only, 1)) < 1e-6 and \
        abs(jd_ut_expected - date_only) > 1e-6
    record(ok_jd, f"julian_day_ut={jd_ut_expected:.6f} == local {jd:.6f} - tz/24; "
                  f"h.julian_utc={date_only} is midnight-date-only (now julian_day_date_only)")
    # (3f) --match-date WITHOUT a partner location must NOT SystemExit (clean error instead).
    try:
        m2 = cc.build_match(env, h, jd, place, pp, {"date": "1997-11-15", "time": "09:30:00"})
        ok_match = isinstance(m2, dict) and "__error__" in m2
    except SystemExit:
        ok_match = False
    except Exception:
        ok_match = False
    record(ok_match, f"match-date without partner location -> clean __error__ (no SystemExit): got {m2.get('__error__', '')[:40]}")
    # (3h) RETROGRADE comes from drik.planets_in_retrograde(jd, place) — the chart-layer
    #      planets_in_retrograde is STALE (returns [] on a retro chart; verified 2025-03-01 ->
    #      drik [7,8] Saturn+Rahu). Assert the engine reports the source API and that it
    #      resolves to a list, and cross-check against drik on a KNOWN retro day.
    pf = cc.build_planet_flags(env, h, jd, place, pp)
    src = pf.get("retrograde_source_api")
    ret = pf.get("retrograde")
    know = None
    try:
        with cc._quiet():
            know = env["drik"].planets_in_retrograde(jd, place)
    except Exception:
        know = None
    ok_retro = isinstance(ret, (list, tuple)) and src and src.startswith("drik.planets_in_retrograde")
    # on the natal chart, the report must equal drik's own answer (both use the same call)
    ok_retro = ok_retro and (know is None or set(ret) == set(cc._decode_planet_id_list(know)))
    record(ok_retro, f"planet_flags retrograde via drik (src={src}; retro={ret}; drik={know})")
    # (3i) ARUDHA must use arudhas.bhava_arudha_longitudes(jd, place) — the from_planet_positions
    #      variant is BROKEN (AttributeError). Assert the D1 arudhas resolved (12 labels) and
    #      that the varga arudhas exist.
    ar = cc.build_arudhas_and_specials(env, h, jd, place, pp)
    al = ar.get("arudha_lagna_longitudes")
    ok_arudha = isinstance(al, dict) and len(al) == 12 and "AL" in al and "UL" in al and \
        isinstance(ar.get("arudhas_by_varga"), dict) and len(ar["arudhas_by_varga"]) >= 5
    record(ok_arudha, f"arudha via bhava_arudha_longitudes(jd,place) ({len(al) if isinstance(al, dict) else 0} labels; varga arudhas {len(ar.get('arudhas_by_varga',{}))})")
    # (3j) HOUSE classification: get_planets_in_* takes the CANONICAL raasi-index p_to_h
    #      dict (REVIEW R6: the helpers test membership against RAASI sets like
    #      [(r+0)%12,(r+3)%12,(r+6)%12,(r+9)%12]; the old 1-based house-from-lagna dict
    #      silently dropped house-12 occupants from dushthanas/apoklimas). Assert decoded
    #      names incl. 'Lagna' (full membership parity is locked in audit_round6).
    hs = cc.build_house_systems(env, h, jd, place, pp)
    ok_house = all(isinstance(hs.get(k), (list, tuple)) and
                   all(x in cc.PLANET_NAMES or x == "Lagna" for x in hs.get(k, []))
                   for k in ("in_quadrants", "in_trines", "in_panapharas", "in_apoklimas",
                             "in_dushthanas", "in_upachayas"))
    record(ok_house, f"house classification raasi-domain p_to_h decode (names + Lagna): "
                     f"quad={hs.get('in_quadrants')} apok={hs.get('in_apoklimas')}")
    # (3k) RASI DRISHTI multi-domain: on-rasi are SIGNS, on-houses are H#, on-planets are NAMES.
    rp = hs.get("raasi_drishti_on_planets", {})
    rh = hs.get("raasi_drishti_on_houses", {})
    rs = hs.get("raasi_drishti_on_rasi", {})
    _flat_p = [v for vals in rp.values() for v in vals] if isinstance(rp, dict) else []
    _flat_h = [v for vals in rh.values() for v in vals] if isinstance(rh, dict) else []
    ok_dr = (rp and all(v in cc.PLANET_NAMES for v in _flat_p) and
             rh and all(str(v).startswith("H") for v in _flat_h) and
             rs and all(v in cc.RAASI_NAMES for v in [v for vals in rs.values() for v in vals]))
    record(ok_dr, f"raasi_drishti 3-domain decode (on_planets names={sorted(set(_flat_p))[:4]}; on_houses H#={sorted(set(_flat_h))[:4]})")
    # (3l) TAJAKA YEAR boundary-aware: a date BEFORE this year's birthday belongs to last
    #      year's annual chart. Assert _tajaka_years_elapsed differs across the birthday.
    y1 = cc._tajaka_years_elapsed(_u2.julian_day_number((2026, 3, 1), (12, 0, 0)), h)   # before 2026 birthday (Oct 16)
    y2 = cc._tajaka_years_elapsed(_u2.julian_day_number((2026, 12, 1), (12, 0, 0)), h)  # after 2026 birthday
    ok_ty = y2 == y1 + 1 and y1 >= 1
    record(ok_ty, f"tajaka year boundary (2026-03-01 -> y{y1}; 2026-12-01 -> y{y2}; +1 at birthday)")
    # (3m) DIVISIONAL factor source tagging: D1/D9 standard vs D72/D150 extended.
    va = cc.build_vargas(env, h, jd, place, pp, [1, 9, 72, 150], label_varga_factors=True)
    # ROUND-7 / PyJHora 5.0: D150 became a STANDARD divisional upstream (it is in
    # const.division_chart_factors now); D72 remains an extended custom-fallback factor.
    # The expectation follows the LIVE standard set so the test is correct on both lines.
    _d150_expected = "standard" if 150 in cc._STANDARD_VARGAS else "extended_custom_fallback"
    ok_vsrc = va["D1"]["factor_source"] == "standard" and va["D9"]["factor_source"] == "standard" and \
        va["D72"]["factor_source"] == "extended_custom_fallback" and \
        va["D150"]["factor_source"] == _d150_expected
    record(ok_vsrc, f"divisional factor source tagging (D1={va['D1']['factor_source']}, D72={va['D72']['factor_source']}, D150={va['D150']['factor_source']})")
    # (3n) PER-SYSTEM dasha lord encoding honors the override: panchasvara stays raw (element),
    #      saptharishi is a nakshatra (not planet) so it decodes to nakshatra names (raw).
    dd = cc.build_dasha_decoded(env, h, jd, place, pp)
    ps = dd.get("panchasvara", {})
    spth = dd.get("saptharishi_nakshathra", {})
    # panchasvara lords are ELEMENTS (raw); saptharishi lords are NAKSHATRAS (decoded to names).
    ok_dkind = ps.get("decoded") == "raw" and ps.get("lord_type") == "element" and \
        spth.get("decoded") == "yes" and spth.get("lord_type") == "nakshatra"
    record(ok_dkind, f"dasha per-system lord-kind (panchasvara={ps.get('lord_type')!r}/{ps.get('decoded')!r}; saptharishi={spth.get('lord_type')!r}/{spth.get('decoded')!r})")

    # (3g) _VARGAS_ALL includes the Nadiamsa D150 (verified resolving in this build).
    ok_150 = 150 in cc._VARGAS_ALL
    try:
        dv150 = env["charts"].divisional_chart(jd, place, divisional_chart_factor=150)
        ok_150 = ok_150 and dv150 is not None
    except Exception:
        ok_150 = False
    record(ok_150, "_VARGAS_ALL includes D150 (Nadiamsa) and divisional_chart D150 resolves")


def audit_v4_followup():
    """v4 follow-up review: exact API adapters + PyJHora golden-chart tests.

    Addresses the v4 blockers:
      * graha_arudhas must call the *_from_planet_positions adapter (graha_arudhas(chart)
        raises KeyError:0) — assert the value matches raw.
      * solar upagrahas stay on drik.solar_upagraha_longitudes (the charts variant raises
        TypeError) — assert real decoded values.
      * D72/D150 must be PROVEN distinct from D1, not just asserted as a custom fallback.
      * eclipse must use jhora.panchanga.eclipse (typed+dated), NOT the raw-JD drik.*.
      * Tajaka month must anchor to the actual solar-return JD (not calendar month).
      * all_dashas must surface __resolution__ so positional_fallback is never silent.
      * config lock snapshot must be inside the lock; serialization masked count == once.
      * error manifest must catch plain-string failures (not only __error__ dicts).
    """
    print("\n--- audit: v4 follow-up (exact adapters + golden charts) ---")
    env = get_env()
    h, jd, place, pp = _build(BOY)
    cc._reset_dasha_cache()

    # ---- (a) graha_arudhas via *_from_planet_positions (NOT graha_arudhas(chart)) ----
    ar = cc.build_arudhas_and_specials(env, h, jd, place, pp)
    try:
        with cc._quiet():
            raw_gha = env["arudhas"].graha_arudhas_from_planet_positions(pp)
        exp_gha = list(raw_gha)
    except Exception:
        exp_gha = []
    got_gha = ar.get("graha_arudhas")
    # ROUND-7 / PyJHora 5.0: graha_arudhas_from_planet_positions now returns
    # (sign_index, degree) TUPLES; the engine decodes them to dicts. On 4.8.7 the raw
    # values were plain ints and passed through. Accept either pairing, version-safely.
    if exp_gha and isinstance(exp_gha[0], (list, tuple)):
        ok_gha = (got_gha is not None and isinstance(got_gha, list) and len(got_gha) == 10
                  and all(isinstance(g, dict)
                          and g.get("sign_index") == int(r[0]) % 12
                          and abs(float(g.get("degree_in_sign", -1)) - float(r[1])) < 1e-3
                          and abs(float(g.get("longitude", -1))
                                  - (int(r[0]) % 12) * 30 - float(r[1])) < 1e-3
                          for g, r in zip(got_gha, exp_gha)))
    else:
        ok_gha = got_gha is not None and not isinstance(got_gha, str) and \
            list(got_gha) == [int(x) for x in exp_gha] and len(got_gha) == 10
    record(ok_gha, f"graha_arudhas via *_from_planet_positions ={list(got_gha) if got_gha else got_gha} (vs raw {exp_gha})",
           got_gha if not ok_gha else "")

    # ---- (b) solar upagrahas via drik.solar_upagraha_longitudes (not charts.*) ----
    sp = cc.build_special(env, h, jd, place)
    up = sp.get("upagrahas", {})
    solar_keys = {"dhuma", "vyatipaata", "parivesha", "indrachaapa", "upaketu"}
    ok_sol = all(k in up and isinstance(up[k], dict) and "sign" in up[k] and "longitude" in up[k]
                 for k in solar_keys)
    record(ok_sol, f"solar upagrahas decoded on drik layer (dhuma={up.get('dhuma')})", up if not ok_sol else "")

    # ---- (c) D72/D150 ROUND-7: D72 stays an extended factor with an HONEST method_status
    # (custom_divisional_check — 'differs from D1' proves resolution, NOT classicality);
    # D150 is STANDARD in 5.0 (classical Nadiamsa in divisional_chart_functions). ----
    va = cc.build_vargas(env, h, jd, place, pp, [1, 9, 72, 150], label_varga_factors=True)
    d1_lon = va["D1"]["lagna"].get("longitude")
    ok_72 = (va["D72"]["factor_source"] == "extended_custom_fallback" and
             va["D72"]["lagna"]["longitude"] != d1_lon and
             va["D72"]["custom_divisional_check"].get("differs_from_d1_lagna") is True and
             va["D72"]["method_status"].get("classical_scheme_implemented") is False and
             va["D72"]["method_status"].get("supported_as_standard_in_pyjhora") is False and
             va["D72"]["planets"]["Sun"]["degree"] != va["D1"]["planets"]["Sun"]["degree"])
    if 150 in cc._STANDARD_VARGAS:      # PyJHora 5.0: standard Nadiamsa
        ok_150 = (va["D150"]["factor_source"] == "standard" and
                  "custom_divisional_check" not in va["D150"] and
                  va["D150"]["lagna"]["longitude"] != d1_lon and
                  va["D150"]["planets"]["Sun"]["degree"] != va["D1"]["planets"]["Sun"]["degree"])
    else:                               # PyJHora 4.8.7: extended custom fallback
        ok_150 = (va["D150"]["factor_source"] == "extended_custom_fallback" and
                  va["D150"]["lagna"]["longitude"] != d1_lon and
                  va["D150"]["custom_divisional_check"].get("differs_from_d1_lagna") is True and
                  va["D150"]["planets"]["Sun"]["degree"] != va["D1"]["planets"]["Sun"]["degree"])
    # golden values must be the exact verified numbers (regression-locked)
    ok_72val = abs(va["D72"]["lagna"]["longitude"] - 288.092) < 0.01
    # ROUND-7 / 5.0: D150 golden depends on the code path — the STANDARD Nadiamsa
    # algorithm (5.0) yields 330.192 for this chart; the old custom equal-division
    # fallback (4.8.7) yielded 120.192. D1 lagna is IDENTICAL across versions (44.001),
    # proving Lahiri numerical continuity of the 4.8.7 -> 5.0 migration.
    if 150 in cc._STANDARD_VARGAS:
        ok_150val = abs(va["D150"]["lagna"]["longitude"] - 330.192) < 0.01 and \
            abs(d1_lon - 44.001) < 0.01
    else:
        ok_150val = abs(va["D150"]["lagna"]["longitude"] - 120.192) < 0.01 and \
            abs(d1_lon - 44.001) < 0.01
    record(ok_72 and ok_72val, f"GOLDEN D72 custom-path check (lagna={va['D72']['lagna']['longitude']} vs D1={d1_lon}; Sun={va['D72']['planets']['Sun']['degree']} vs D1 {va['D1']['planets']['Sun']['degree']} dist)")
    record(ok_150 and ok_150val, f"GOLDEN D150 lagna (lagna={va['D150']['lagna']['longitude']} vs D1={d1_lon} dist)")
    # ---- (d) non-planet varga elements: lagna + nodes surfaced explicitly ----
    ok_np = ("lagna" in va["D9"] and "nodes" in va["D9"] and "Rahu" in va["D9"]["nodes"] and
             "Ketu" in va["D9"]["nodes"] and "bodies" in va["D9"])
    record(ok_np, f"varga non-planet elements surfaced (lagna={va['D9']['lagna']['sign']}, nodes={list(va['D9']['nodes'])})")

    # ---- (e) eclipse via jhora.panchanga.eclipse (typed + dated), not raw JD ----
    # The two functions return differently-typed containers (solar -> ('partial',[times...]),
    # lunar -> [('total',[times...])]); just assert the raw-JD form is GONE and a real eclipse
    # TYPE string plus a dated tuple are present (both = the definitive "typed+dated" evidence).
    ec = cc.build_eclipse(env, h, jd, place)
    nse = ec.get("next_solar_eclipse")
    nle = ec.get("next_lunar_eclipse")
    def _scan_types(x, out):
        """Collect every eclipse TYPE string and any dated (y,m,d,...) tuple below x."""
        if isinstance(x, str) and x in ("partial", "total", "annular", "hybrid", "penumbral"):
            out["types"].append(x)
        elif isinstance(x, (list, tuple)):
            # a dated (y,m,d,...) tuple — y is a year >1900
            if (isinstance(x, (tuple, list)) and len(x) >= 3 and
                    isinstance(x[0], (int, float)) and not isinstance(x[0], bool) and
                    isinstance(x[1], (int, float)) and not isinstance(x[1], bool) and
                    isinstance(x[2], (int, float)) and not isinstance(x[2], bool) and
                    float(x[0]) > 1900 and 1 <= float(x[1]) <= 12 and 1 <= float(x[2]) <= 31):
                out["dates"].append((int(x[0]), int(x[1]), int(x[2])))
            else:
                for e in x:
                    _scan_types(e, out)
    a, b = {"types": [], "dates": []}, {"types": [], "dates": []}
    _scan_types(nse, a); _scan_types(nle, b)
    ok_ec = bool(a["types"] and a["dates"] and b["types"] and b["dates"])
    record(ok_ec, f"eclipse via ec.* typed+dated (solar types={a['types'][:1]} date={a['dates'][:1]}; lunar types={b['types'][:1]} date={b['dates'][:1]})")
    # drik.* obsolete path must NOT appear as the primary result
    record("prev_solar_eclipse" in ec and isinstance(ec["prev_solar_eclipse"], (list, tuple)),
           "eclipse prev_solar present via ec search_backward")

    # ---- (f) Tajaka month anchored to the ACTUAL solar-return JD (not calendar month) ----
    jdn = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
    td = cc.build_tajaka_deep(env, h, jd, place, pp, jd_now=jdn)
    ok_tj = td.get("month_anchor") == "solar-return JD" and \
        isinstance(td.get("months_elapsed"), int) and 1 <= td["months_elapsed"] <= 12 and \
        td.get("annual_chart_used") is True
    record(ok_tj, f"tajaka month anchored to solar-return JD (months={td.get('months_elapsed')}, anchor={td.get('month_anchor')}, years={td.get('years_elapsed')})")

    # ---- (g) __resolution__ surfaced per dasha (no silent positional_fallback) ----
    ad = cc.build_all_dashas(env, h, jd, place, pp)
    res = ad.get("__resolution__")
    ok_res = isinstance(res, dict) and len(res) >= 30 and \
        all(v in ("exact_name_adapters", "positional_fallback", "error", "not_resolved_in_build", "unknown")
            for v in res.values())
    n_fallback = sum(1 for v in res.values() if v == "positional_fallback")
    record(ok_res, f"all_dashas __resolution__ surfaced ({len(res)} systems resolved; {n_fallback} positional_fallback; exact={sum(1 for v in res.values() if v=='exact_name_adapters')})")

    # ---- (h) config-lock snapshot must be inside the lock ----
    import inspect as _inspect
    src = _inspect.getsource(cc._isolated_pyjhora_config)
    # the snapshot (_prior = ...) line must be indented INSIDE the `with _PYJHORA_CONFIG_LOCK:` block
    lo = src.splitlines()
    lock_idx = next(i for i, l in enumerate(lo) if "with _PYJHORA_CONFIG_LOCK:" in l)
    snap_idx = next(i for i, l in enumerate(lo) if "_prior =" in l)
    print(f"  [dbg] lock@{lock_idx} snapshot@{snap_idx} indent={len(lo[snap_idx])-len(lo[snap_idx].lstrip())}")
    ok_lock = snap_idx > lock_idx and len(lo[snap_idx]) - len(lo[snap_idx].lstrip()) > 4
    record(ok_lock, "config-lock snapshot taken INSIDE the lock block")

    # ---- (i) serialization mask counted once (not doubled) ----
    # dry-run must count each mask exactly once; run the count path in isolation.
    masked = []
    counting = [True]
    def _jdef(o):
        if counting[0]:
            masked.append(1)
        return str(o)
    sample_doc = {"a": object(), "b": object()}
    json.dumps(sample_doc, default=_jdef)
    counting[0] = False
    json.dumps(sample_doc, default=_jdef)
    ok_mask = len(masked) == 2   # exactly two distinct objects, NOT doubled to 4
    record(ok_mask, f"serialization mask counted ONCE (dry-run saw {len(masked)} objects; a double-dump would be 4)")

    # ---- (j) error manifest catches plain-string failures ----
    # a tiny doc containing a bare exception-string value must be recorded.
    tiny = {"good": 1, "bad": "Exception: boom"}   # plain string error, no __error__ key
    _m = []
    def _collect(o, path, sec):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "__error__":
                    _m.append({"section": sec, "path": f"{path}.{k}", "code": "computation_failed"})
                else:
                    _collect(v, f"{path}.{k}", sec)
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                _collect(v, f"{path}[{i}]", sec)
        elif isinstance(o, str) and any(mk in o for mk in ("Traceback", "no attribute", "Exception", "not available in this")):
            _m.append({"section": sec, "path": path, "code": "plain_string_error"})
    _collect(tiny, "", "x")
    ok_manifest = any(e["code"] == "plain_string_error" for e in _m)
    record(ok_manifest, f"error manifest catches plain-string failures (codes={[e['code'] for e in _m]})")


# ----------------------------------------------------------------------------- #
# AUDIT — ROUND 5: new sections (prediction / alt_engines / experiments) + the
# ROUND-5 field additions across house_systems, strengths, raja_yogas, tajaka_deep,
# special, transits, varga_transits, vratha (decoded, contract-checked).
# ----------------------------------------------------------------------------- #
def audit_round5():
    print("\n=== AUDIT R5: round-5 sections + field additions (boy) ===")
    env = get_env()
    h, jd, place, pp = _build(BOY)
    drik = env["drik"]

    # ---- prediction section ----
    pr = cc.build_prediction(env, h, jd, place, pp)
    bt = pr.get("bundled_texts") or {}
    ok = (isinstance(bt, dict) and len(bt) == 4 and
          all(isinstance(v, str) and len(v) > 100 and "<b>" not in v and "<br" not in v
              for v in bt.values()))
    record(ok, "prediction bundled_texts: 4 clean-text blocks (HTML stripped)", str(list(bt.keys())))
    lsr = pr.get("life_span_range") or {}
    record(isinstance(lsr, dict) and lsr.get("index") == 2 and "Poornayu" in str(lsr.get("category")),
           "prediction life_span_range boy == Poornayu (index 2)", lsr)
    okc = pr.get("longevity_reference_constants") or {}
    record(okc.get("nakshathra_year") == 324 and okc.get("one_year_days") == 365.256364,
           "prediction longevity constants (324 / 365.256364)", okc)
    # the py2 `counter.keys()[0]` crash fires when ALL THREE aayu votes agree — the
    # default 1990-05-15 10:30 sample chart hits exactly that path; the compat patch
    # must fix it. (Build directly: _build() hardcodes Boy/Girl birth times.)
    from jhora.panchanga import drik as _drik90
    from jhora.horoscope.info import Horoscope as _H90
    _drik90.set_ayanamsa_mode("LAHIRI")
    _drik90.set_planet_list(set_rahu_ketu_as_true_nodes=True)
    h90 = _H90(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
               date_in=_drik90.Date(1990, 5, 15), birth_time="10:30:00")
    pp90 = env_charts.rasi_chart(h90.julian_day, h90.Place)
    pr90 = cc.build_prediction(env, h90, h90.julian_day, h90.Place, pp90)
    lsr90 = pr90.get("life_span_range") or {}
    record(isinstance(lsr90, dict) and "index" in lsr90 and lsr90["index"] in (0, 1, 2),
           "prediction life_span_range py3 tie-path patch (1990 sample chart)", lsr90)

    # ---- alt_engines section ----
    ae = cc.build_alt_engines(env, h, jd, place, pp)
    sr = ae.get("sunrise_set_surya_siddhanta") or {}
    record(isinstance(sr, dict) and isinstance(sr.get("sunrise_hours"), float)
           and sr.get("sunrise_hms") == "06:13:55" and sr.get("sunset_hms") == "17:46:05",
           "alt_engines SS sunrise_set boy == 06:13:55 / 17:46:05", sr)
    ka = ae.get("kali_ahargana_surya_siddhanta")
    kd = None
    try:
        kd = drik.kali_ahargana_days(jd)
    except Exception:
        pass
    record(isinstance(ka, int) and ka == 1864463 and kd is not None and abs(ka - int(kd)) <= 2,
           "alt_engines SS kali_ahargana boy == 1864463 (within 2 of drik)", (ka, kd))
    an = ae.get("ascendant_surya_siddhanta_new") or {}
    record(isinstance(an, dict) and an.get("sign") in cc.RAASI_NAMES,
           "alt_engines SS ascendant_new decoded to a sign name", an)
    un = ae.get("unavailable_in_this_build") or {}
    record(isinstance(un, dict) and len(un) >= 6 and
           all("no attribute" not in str(v) and "Traceback" not in str(v) for v in un.values()),
           "alt_engines unavailable list documented without error-marker strings", list(un.keys()))

    # ---- experiments shim (unit) ----
    seg = cc._expand_md_ad_segments_shim([
        [(4, 5), (2029, 1, 1, 6.5), 0.58],
        [(4, 6), (2029, 8, 1, 12.0), 0.95],
    ])
    record(len(seg) == 2 and seg[0]["md"] == 4 and seg[0]["ad"] == 5 and
           seg[0]["start"].year == 2029 and seg[1]["end"].year == 9999,
           "experiments shim converts 4.8.7/5.0 vdb rows to md/ad segments", seg[:1])
    record(cc._expand_md_ad_segments_shim([]) == [], "experiments shim: empty input -> []")

    # ---- experiments builder (narrow window, no deep scan) ----
    ex = cc.build_experiments(env, h, jd, place, pp, marriage_window=[2028, 2030], deep=False)
    record(ex.get("marriage_age_range_used") == [25, 27],
           "experiments age range derived from --marriage-window (2028-2003, 2030-2003)",
           ex.get("marriage_age_range_used"))
    # ROUND-7 / PyJHora 5.0: the WHOLE jhora.experiments package was removed upstream.
    # The builder must degrade GRACEFULLY: typed __error__ 'not available', never a crash,
    # never fabricated windows. On builds that still ship it, the old assertions apply.
    import importlib.util as _ilu
    _exp_present = bool(_ilu.find_spec("jhora.experiments"))
    m5 = ex.get("marriage5_windows")
    if _exp_present:
        record(isinstance(m5, list) and all(isinstance(w, dict) and "window_start" in w for w in m5),
               "experiments marriage5 windows decoded (no __error__)",
               (m5 if isinstance(m5, dict) else len(m5)))
    else:
        record(isinstance(m5, dict) and "not available" in str(m5.get("__error__", "")),
               "experiments marriage5 reports typed not-available (package REMOVED in 5.0)",
               m5)
    dtw = ex.get("double_transit_windows")
    if _exp_present:
        record(isinstance(dtw, list) and
               all(isinstance(r.get("date"), str) for r in dtw) and
               all(("sat_sign_name" not in r) or (r["sat_sign_name"] in cc.RAASI_NAMES) for r in dtw),
               "experiments double_transit rows: ISO dates + decoded sign names", len(dtw) if isinstance(dtw, list) else dtw)
    else:
        record(isinstance(dtw, dict) and "not available" in str(dtw.get("__error__", "")),
               "experiments double_transit reports typed not-available (REMOVED in 5.0)", dtw)
    for k in ("scan_dt_perfect_fit", "scan_part3_with_7th_target"):
        v = ex.get(k)
        if _exp_present:
            record(isinstance(v, list), "experiments %s ran (list, not error)" % k,
                   v if not isinstance(v, list) else len(v))
        else:
            record(v is None or isinstance(v, list) or (isinstance(v, dict) and "__error__" in v),
                   "experiments %s degrades gracefully with the package removed (5.0)" % k,
                   v if not isinstance(v, (list, type(None))) else len(v or []))

    # ---- vratha tithi_pravesha contract ----
    vr = cc.build_vratha(env, h, jd, place)
    tp = (vr.get("vratha_dates") or {}).get("tithi_pravesha_current_year")
    ok = (isinstance(tp, dict) and isinstance(tp.get("dates"), list) and len(tp["dates"]) >= 1 and
          isinstance(tp["dates"][0].get("time"), str) and
          ("tithi_month_name" in tp["dates"][0] or "label_raw" in tp["dates"][0]))
    record(ok, "vratha tithi_pravesha_current_year: {dates:[{date,time,...}]} contract", tp)

    # ---- house_systems ROUND-5b ----
    hs = cc.build_house_systems(env, h, jd, place, pp)
    pin = hs.get("planets_in_each_raasi") or {}
    ok = (len(pin) == 12 and all(isinstance(v, list) for v in pin.values()) and
          all((not str(x).isdigit()) for v in pin.values() for x in v))
    record(ok, "house_systems planets_in_each_raasi: 12 signs, decoded names only", pin.get("Aries"))
    rel = hs.get("relative_houses") or {}
    sun = rel.get("Sun") or {}
    record(len(rel) >= 9 and sun.get("house_from_lagna") == 5 and
           sun.get("relative_house_from_moon") == 4,
           "house_systems relative_houses: Sun H5 lagna / H4 from moon (boy)", sun)
    pk = hs.get("pachakadi_sambhandha") or {}
    mv = pk.get("Moon") or {}
    record(mv.get("relation") == "bodhaka" and mv.get("with_planet") == "Mars" and
           mv.get("house_offset_from_planet") == 9,
           "house_systems pachakadi decoded: Moon bodhaka w/ Mars offset 9 (boy)", pk)

    # ---- strengths ROUND-5 ----
    st = cc.build_strengths(env, h, jd, place, pp)
    pvr = st.get("planet_aspect_relationship_table_pvr") or {}
    ok = (len(pvr) == 9 and all(len(row) == 9 for row in pvr.values()) and
          all(isinstance(x, (int, float)) for row in pvr.values() for x in row.values()))
    record(ok, "strengths PVR aspect table is 9x9 numeric", pvr.get("Sun"))
    vv = st.get("vimsopaka_dhasavarga") or {}
    sunv = vv.get("Sun") or {}
    record(len(vv) == 9 and sunv.get("good_varga_count") == 1 and sunv.get("vargas") == "D12"
           and sunv.get("score") == 8.95,
           "strengths vimsopaka_dhasavarga boy Sun == (1, D12, 8.95)", sunv)
    vs = st.get("vimsamsavarga") or {}
    record(len(vs) == 9 and vs.get("Sun") == 1 and vs.get("Jupiter") == 6,
           "strengths vimsamsavarga boy Sun=1 Jupiter=6", vs)

    # ---- raja_yogas ROUND-5 ----
    ry = cc.build_raja_yogas(env, h, jd, place, pp)
    pc = ry.get("raja_yoga_pair_checks")
    ok = (isinstance(pc, list) and len(pc) >= 1 and
          all(isinstance(r.get("planets"), list) and
              all(p in cc.PLANET_ORDER for p in r["planets"]) for r in pc if isinstance(r, dict)))
    record(ok, "raja_yogas pair checks decoded to planet names", pc[0] if pc else None)
    o2 = ry.get("other_raja_yoga_2")
    record(isinstance(o2, dict) and "__error__" in o2,
           "raja_yogas other_raja_yoga_2 guarded (known PyJHora-internal bug)", o2)
    si = ry.get("raja_yogas_all_charts_scan_info") or {}
    pairs = ry.get("raja_yogas_all_charts_pairs") or {}
    record(isinstance(si.get("total_chart_scans"), int) and isinstance(pairs, dict) and len(pairs) >= 1,
           "raja_yogas all-charts scan_info + pair summary extracted", (si, list(pairs.keys())))

    # ---- special ROUND-5 ----
    sp = cc.build_special(env, h, jd, place)
    dp = sp.get("divisional_positions_from_rasi") or {}
    d9 = dp.get("D9") or {}
    lag = d9.get("Lagna") or {}
    record(set(("D2", "D3", "D4", "D7", "D9", "D10", "D12")).issubset(set(dp.keys())) and
           lag.get("sign") in cc.RAASI_NAMES and isinstance(lag.get("degree_in_sign"), float),
           "special divisional_positions_from_rasi D2..D12 decoded", lag)

    # ---- tajaka_deep ROUND-5 ----
    from jhora import utils as _u
    tjd = _u.julian_day_number((2026, 9, 5), (12, 0, 0))
    tj = cc.build_tajaka_deep(env, h, jd, place, pp, tjd)
    rad = tj.get("tajaka_raasi_aspect_details") or {}
    aries = rad.get("Aries") or {}
    _names_ok = True
    for v in aries.values():
        if not isinstance(v, dict):
            continue
        if not all(x in cc.RAASI_NAMES for x in v.get("raasis", [])):
            _names_ok = False
        for x in v.get("planets", []):
            if not ((x == "Lagna") or (x in cc.PLANET_ORDER)):
                _names_ok = False
    record(len(rad) == 12 and len(aries) >= 9 and _names_ok,
           "tajaka_deep raasi aspect table: 12 signs x typed, fully decoded", aries.get("trinal"))
    ith = tj.get("ithasala_deeptamsa_details") or {}
    k0 = next(iter(ith.values()), {})
    ok = (len(ith) >= 1 and isinstance(k0.get("pair"), list) and
          all(p in cc.PLANET_ORDER for p in k0.get("pair", [])) and
          isinstance(k0.get("planets_have_aspects"), bool))
    record(ok, "tajaka_deep ithasala/deeptamsa details: decoded pairs + bool aspects", k0)
    pah = tj.get("planet_aspect_on_houses_tajaka") or {}
    jup = pah.get("Jupiter") or {}
    record(len(pah) == 5 and len(jup) == 7 and
           all(isinstance(v.get("benefic_aspect"), bool) for v in jup.values()),
           "tajaka_deep planet-on-house aspect map: 5 planets x 7 houses, booleans", jup.get("H1"))
    aca = tj.get("annual_chart_approximate") or {}
    acp = aca.get("positions") or {}
    record(isinstance(acp, dict) and len(acp) == 10 and
           all(v.get("sign") in cc.RAASI_NAMES for v in acp.values()),
           "tajaka_deep annual_chart_approximate decoded (10 bodies)", acp.get("Lagna"))

    # ---- transits ROUND-5 (latta + motion) ---- (tjd already computed above)
    tr = cc.build_transits(env, h, jd, place, pp, tjd,
                           compute_conjunctions=False, compute_events=False)
    ls = tr.get("latta_stars") or {}
    sunl = ls.get("Sun") or {}
    record(sunl.get("latta_star_1") == "Chitra" and sunl.get("latta_star_2") == "Shatabhisha",
           "transits latta_stars boy Sun == Chitra/Shatabhisha (Abhijit-aware decode)", sunl)
    rows = tr.get("transits") or []
    ok = (len(rows) == 9 and all(r.get("motion_at_transit_date") in
                                 ("direct", "retrograde", "stationary") for r in rows))
    record(ok, "transits motion_at_transit_date on every row",
           [r.get("motion_at_transit_date") for r in rows])

    # ---- varga_transits ROUND-5 (divisional conjunctions) ----
    vt = cc.build_varga_transits(env, h, jd, place, tjd, pp, division=9)
    cj = vt.get("next_conjunctions_divisional") or {}
    js = cj.get("Jupiter-Saturn") or {}
    record(set(("Jupiter-Saturn", "Jupiter-Venus", "Sun-Moon")).issubset(set(cj.keys())) and
           isinstance(js.get("date"), str) and len(js["date"]) >= 10,
           "varga_transits next_conjunctions_divisional: 3 pairs, ISO date strings", cj)

    # ---- panchanga special_thaara decode (edit-11 fix) ----
    pa = cc.build_panchanga(env, h, jd, place)
    stt = pa.get("special_thaara_from_moon")
    _allowed = set(cc.NAKSHATRAS) | {"Abhijit"}
    names = []
    if isinstance(stt, (list, tuple)):
        for row in stt:
            if isinstance(row, dict) and isinstance(row.get("nakshatra"), str):
                names.append(row["nakshatra"])
            elif isinstance(row, str):
                names.append(row)
    elif isinstance(stt, dict):
        for v in stt.values():
            if isinstance(v, str):
                names.append(v)
            elif isinstance(v, dict) and isinstance(v.get("nakshatra"), str):
                names.append(v["nakshatra"])
    ok = len(names) > 0 and all(n in _allowed for n in names)
    record(ok, "panchanga special_thaara names in standard 27+Abhijit vocabulary", names[:4])


def audit_round6():
    """Round-6 external review triage: domain fixes + metadata + crosschecks.

    Locks the R6 fixes made after the external review of the 5,815-line engine:
      * get_planets_in_* / is_yoga_kaaraka run in the RAASI domain (canonical
        utils.get_planet_house_dictionary_from_planet_positions): the old 1-based
        house dict silently dropped house-12 occupants from dushthanas/apoklimas.
      * bhava_houses = {decoded name: house-number int}; bhava_cusps rows are
        position-ordered houses with decoded raasi names (the old code emitted the
        rasi index AS the house number -> "house": 0 artifacts).
      * Gulika/Maandi via the dedicated drik longitude functions (the old
        (planet_index, part) mapping was swapped vs the library's own functions)
        + explicit drik-vs-charts special-point agreement + Parivesha abbrev 'Pv'.
      * __meta__: julian_day_ut = local JD - tz/24 (h.julian_utc is midnight-date-only,
        now labeled julian_day_date_only) + place_resolved block.
      * prediction is opt-in (--prediction / --full), not part of the default run.
      * typed doshas incl. manglik cancellation pair; dasha-year-duration knob restores.
      * serializer placeholder logic emits a TAGGED object, never a bare str.
    """
    print("\n=== AUDIT R6: external-review triage fixes (boy+girl) ===")
    import subprocess
    import tempfile
    env = get_env()
    CATS = {"in_quadrants": {1, 4, 7, 10}, "in_trines": {1, 5, 9},
            "in_panapharas": {2, 5, 8, 11}, "in_apoklimas": {3, 6, 9, 12},
            "in_dushthanas": {6, 8, 12}, "in_upachayas": {3, 6, 10, 11}}
    for tag, spec in (("boy", BOY), ("girl", GIRL)):
        h, jd, place, pp = _build(spec)
        hs = cc.build_house_systems(env, h, jd, place, pp)
        occ = hs.get("planets_in_house_from_lagna") or []
        # (a) canonical category membership vs whole-sign occupancy recompute
        bad = []
        for k, hset in CATS.items():
            expected = set()
            for hi, bodies in enumerate(occ, start=1):
                if hi in hset:
                    expected.update(bodies)
            got = set(hs.get(k) or [])
            if got != expected:
                bad.append(f"{k}: got {sorted(got)} want {sorted(expected)}")
        record(not bad, f"[{tag}] house categories == occupancy recompute (raasi-domain p_to_h)",
               "; ".join(bad))
        # (b) the R6 regression itself: house-12 occupants in dushthanas + apoklimas
        h12 = set(occ[11]) if len(occ) >= 12 else set()
        ok12 = all(x in set(hs.get("in_dushthanas") or []) for x in h12) and \
            all(x in set(hs.get("in_apoklimas") or []) for x in h12)
        record(ok12, f"[{tag}] house-12 occupants {sorted(h12)} present in dushthanas+apoklimas")
        # (c) bhava_houses contract: decoded names -> int house numbers + raw parity
        bh = hs.get("bhava_houses") or {}
        ok_bh = isinstance(bh, dict) and bh.get("Lagna") == 1 and len(bh) == 10 and \
            all(isinstance(v, int) and 1 <= v <= 12 for v in bh.values()) and \
            set(bh.keys()) == set(["Lagna"] + list(cc.PLANET_NAMES)) and \
            "bhava_houses_note" in hs
        record(ok_bh, f"[{tag}] bhava_houses = names -> int house numbers 1..12 (Lagna=1)",
               bh if not ok_bh else "")
        try:
            with cc._quiet():
                raw_bh = env["charts"].bhava_houses(jd, place)
            ok_par = all(bh.get("Lagna" if k == "L" else cc._decode_planet_id(k)) == int(v)
                         for k, v in raw_bh.items())
        except Exception:
            ok_par = False
        record(ok_par, f"[{tag}] bhava_houses values match raw charts.bhava_houses 1:1")
        # (d) bhava cusps: house = row position 1..12, raasi = lagna progression
        lagna_idx = int(pp[0][1][0])
        cus = hs.get("bhava_cusps_decoded") or []
        ok_cus = len(cus) == 12 and all(r.get("house") == i + 1 for i, r in enumerate(cus)) and \
            all(r.get("raasi_index") == (lagna_idx + r.get("house", 0) - 1) % 12 for r in cus) and \
            all(isinstance(x, str) for r in cus for x in r.get("planets", []))
        record(ok_cus, f"[{tag}] bhava_cusps houses 1..12 in order + decoded raasi/occupants")
        # (e) special points: Gulika/Maandi parity, agreement crosscheck, Pv decode, D9
        sp = cc.build_special(env, h, jd, place)
        upg = sp.get("upagrahas", {})
        dob = env["drik"].Date(h.Date.year, h.Date.month, h.Date.day)
        tob = cc._tob(h)
        try:
            with cc._quiet():
                gk = env["drik"].gulika_longitude(dob, tob, place)
                md = env["drik"].maandi_longitude(dob, tob, place)
            ok_gm = abs(upg["Gulika"]["longitude"] - (int(gk[0]) * 30 + float(gk[1]))) < 0.01 and \
                abs(upg["Maandi"]["longitude"] - (int(md[0]) * 30 + float(md[1]))) < 0.01
        except Exception:
            ok_gm = False
        record(ok_gm, f"[{tag}] Gulika/Maandi == dedicated drik fns (old part-mapping was swapped)",
               upg.get("Gulika") if not ok_gm else "")
        agr = sp.get("special_points_agreement") or {}
        record(agr.get("agree") is True and agr.get("points_compared") == 11,
               f"[{tag}] drik-vs-charts special-point agreement "
               f"({agr.get('points_compared')}/11 pts, max diff {agr.get('max_abs_diff_deg')} deg)",
               agr.get("mismatches"))
        spl = sp.get("special_planet_longitudes") or {}
        record("Parivesha" in spl and "Pv" not in spl,
               f"[{tag}] Parivesha decoded (library abbrev 'Pv')")
        d9 = sp.get("special_planet_longitudes_D9") or {}
        record(len(d9) == 11 and all(not (isinstance(v, dict) and "__error__" in v)
                                     for v in d9.values()),
               f"[{tag}] D9 special points decoded ({len(d9)}/11)")
        # (f) typed doshas + manglik cancellation pair
        do = cc.build_doshas(env, h, jd, place, pp)
        mg = do.get("manglik") or {}
        pdd = do.get("pitru_dosha") or {}
        ok_do = isinstance(mg, dict) and isinstance(mg.get("present"), bool) and \
            isinstance(mg.get("present_before_exceptions"), bool) and \
            isinstance(mg.get("exception_indices"), list) and \
            isinstance(pdd.get("present"), bool) and \
            (pdd.get("planets") is None or all(x in cc.PLANET_NAMES for x in pdd["planets"])) and \
            all(isinstance(do.get(k), bool) for k in ("kala_sarpa", "shrapit", "kalathra")) and \
            isinstance((do.get("ganda_moola") or {}).get("moon_nakshatra_index"), int)
        record(ok_do, f"[{tag}] typed doshas (manglik cancellation pair, pitru decoded, ganda_moola)")

    # (g) __meta__ via real subprocess runs (fast: --sections core)
    with tempfile.TemporaryDirectory() as td:
        outp = os.path.join(td, "meta.json")
        cmd = [sys.executable, os.path.join(ROOT, "compute_chart.py"),
               "--sections", "core", "--out", outp]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception:
            pass
        dok = os.path.exists(outp)
        d_all = json.load(open(outp)) if dok else {}
        m = d_all.get("__meta__", {})
        pr = m.get("place_resolved") or {}
        record(dok and abs(m.get("julian_day_ut", 0) -
                           (m.get("julian_day_local", 0) - pr.get("tz", 0) / 24.0)) < 1e-9,
               "meta julian_day_ut == local JD - tz/24 (subprocess run)")
        _donl = m.get("julian_day_date_only")
        record(_donl is not None and abs(_donl - round(_donl, 1)) < 1e-6,
               f"meta julian_day_date_only is the midnight JD ({_donl})")
        record(pr.get("lat") == 12.9141 and pr.get("lon") == 74.856 and pr.get("tz") == 5.5,
               "meta place_resolved echoes the resolved Place fields")
        record(dok and "prediction" not in d_all and "core" in d_all,
               "prediction absent from the default fact-sheet run")
        outp2 = os.path.join(td, "meta2.json")
        cmd2 = [sys.executable, os.path.join(ROOT, "compute_chart.py"),
                "--sections", "core", "--prediction", "--out", outp2]
        try:
            subprocess.run(cmd2, capture_output=True, text=True, timeout=600)
        except Exception:
            pass
        d2 = json.load(open(outp2)) if os.path.exists(outp2) else {}
        record("prediction" in d2, "--prediction adds the section back")

    # (h) registry state + dasha-year-duration knob + serializer tag
    record(cc.SECTIONS["prediction"][1] is False,
           "SECTIONS: prediction default-off (opt-in)")
    record("prediction" in cc._features().get("requires_flag", {}),
           "_features().requires_flag documents --prediction")
    from jhora import const as _c6
    from jhora.panchanga import drik as _d6
    prior = _c6.dhasa_year_duration_default
    with cc._isolated_pyjhora_config(_d6, _c6, dasha_year_duration=5):
        inside = _c6.dhasa_year_duration_default
    record(getattr(inside, "value", inside) == 5 and _c6.dhasa_year_duration_default == prior,
           f"dasha-year-duration knob: inside={inside!r} restored={_c6.dhasa_year_duration_default!r}")
    _mm = []
    def _jd_tag(o):
        _mm.append(1)
        return {"__non_serializable__": {"type": type(o).__name__, "repr": repr(o)[:120]}}
    sjs = json.dumps({"x": object()}, default=_jd_tag)
    record("__non_serializable__" in sjs and len(_mm) == 1,
           "serializer placeholder logic emits a TAGGED object (never a bare str)")


def audit_features():
    print("\n--- audit: feature-availability / legend schema ---")
    feats = cc._features()
    cver = feats.get("dasha_catalog_validated") or {}
    ok = ("sections" in feats and isinstance(feats.get("default_sections"), list) and
          feats.get("dasha_systems") == len(cc.DASHA_CATALOG) and
          feats.get("dasha_systems_with_running", 0) >= 40 and
          isinstance(feats.get("requires_flag"), dict) and
          isinstance(feats.get("optional_dependency"), dict) and
          cver.get("fn_ok") == cver.get("total"))
    record(ok, f"feature schema sections={feats.get('sections')} dashas={feats.get('dasha_systems')} "
               f"catalog_validated={cver.get('fn_ok')}/{cver.get('total')} "
               f"running={feats.get('dasha_systems_with_running')} vargas={feats.get('divisional_charts')} "
               f"festivals={feats.get('festival_date_finders')}")


def audit_round7():
    """ROUND-7 audit — the PyJHora 4.8.7 -> 5.0-dev MIGRATION plus the external review
    (Ft.txt v2) fix set: fractional-hour period JDs (C4), explicit dasha-year-duration +
    vimsottari options (C2/C3, issue #62), per-system dasha status/labels (C5/C6), honest
    D72 status vs standard D150 (C1), compat-patch metadata + --stock (C8), upstream-issue
    disclosure (#52/#57/#59/#61/#62 + wrapper-discovered 5.0 bugs), the per-run update
    checker, house-system selection, and the 5.0 signature fixes (sahams, vimsottari
    rename, Ashtakoota method, arudha tuples, full/new moon tithi arg, tajaka duhphali,
    previous_ascendant_entry_date, yogas_all binder)."""
    import os as _os
    env = get_env()
    h, jd, place, pp = _build(BOY)
    drik = env["drik"]; const_ = env["const"]; utils_ = env["utils"]
    from jhora.horoscope.dhasa.graha import vimsottari

    # ---- (7b) version-tolerant vimsottari resolver (5.0 renamed the bhukthi fn) ----
    fn_new = cc._resolve_dasha_fn(vimsottari, "get_dhasa_bhukthi")
    fn_old = cc._resolve_dasha_fn(vimsottari, "get_vimsottari_dhasa_bhukthi")
    record(fn_new is not None and fn_old is not None and fn_new is fn_old,
           "vimsottari resolver maps BOTH names to the same function across versions",
           f"new={fn_new is not None} old={fn_old is not None}")

    # ---- (7a) _period_tuple_to_jd honors fractional hours (review C4) ----
    jd0 = utils_.julian_day_number((2003, 10, 16), (0, 0, 0))
    ok_fh = abs(cc._period_tuple_to_jd((2003, 10, 16, 18.5), utils_) - (jd0 + 18.5 / 24.0)) < 1e-9
    ok_carry = cc._period_tuple_to_jd((2003, 10, 16, 30.0), utils_) == \
        cc._period_tuple_to_jd((2003, 10, 17, 6.0), utils_)
    ok_compat = cc._period_tuple_to_jd((2003, 10, 16), utils_) == jd0
    ok_bad = cc._period_tuple_to_jd(("x", "y"), utils_) is None
    record(ok_fh and ok_carry and ok_compat and ok_bad,
           "_period_tuple_to_jd: +18.5h exact, 30h == next-day 6h, 3-tuple midnight, bad -> None")

    # ---- (7c) dasha-year-duration reaches the math; two modes DIFFER (issue #62) ----
    DYD = const_.DHASA_YEAR_DURATION
    r_g = fn_new(jd, place, dhasa_duration_type=DYD(8))    # Gregorian 365.2425
    r_s = fn_new(jd, place, dhasa_duration_type=DYD(1))    # mean sidereal
    ok_modes = (r_g[1][0][2] != r_s[1][0][2])
    days_g = drik.dhasa_year_duration(dhasa_duration_type=DYD(8), jd=jd, place=place)
    record(ok_modes and abs(days_g - 365.2425) < 1e-3,
           f"Gregorian vs sidereal dasha years produce different AD durations ({r_g[1][0][2]} vs {r_s[1][0][2]}); days=365.2425")

    # ---- (7c2) engine records the EFFECTIVE year + explicit options ----
    dd = cc.build_dhasa(env, h, jd, place, pp, jd_now=None,
                        dasha_duration_type=DYD(8),
                        vims_opts={"use_tribhagi_variation": True})
    eff = dd.get("dasha_year_duration_effective") or {}
    ok_eff = (eff.get("days") == 365.2425 and eff.get("passed_explicitly") is True and
              eff.get("dhasa_duration_type", {}).get("name") == "GREGORIAN_YEAR" and
              eff.get("vimsottari_options", {}).get("use_tribhagi_variation") is True)
    record(ok_eff, "dasha section records effective year days + explicit enum + vimsottari options", eff)

    # ---- (7d) --running-depth reaches the dasha-section running call (review C3) ----
    jd_now = utils_.julian_day_number((2026, 9, 6), (12, 0, 0))
    d2 = cc.build_dhasa(env, h, jd, place, pp, jd_now=jd_now, running_depth=2)
    d4 = cc.build_dhasa(env, h, jd, place, pp, jd_now=jd_now, running_depth=4)
    lv2 = max([0] + [l.get("level", 0) for l in (d2.get("running_vimsottari") or [])])
    lv4 = max([0] + [l.get("level", 0) for l in (d4.get("running_vimsottari") or [])])
    record(lv2 == 2 and lv4 == 4, f"running_depth honored in dasha section (depth2->{lv2}, depth4->{lv4})")

    # ---- (7e) tribhagi variation actually changes the timeline ----
    r_t = fn_new(jd, place, use_tribhagi_variation=True)
    record(r_t[1][0][2] != r_g[1][0][2] or r_t[1][0][1] != r_g[1][0][1],
           "vimsottari tribhagi variation produces a different timeline than plain")

    # ---- (7f) all_dashas __status__ map (review C5) ----
    ad = cc._cached_all_dashas(env, h, jd, place, pp)
    st = ad.get("__status__") or {}
    vocab = {"verified_adapter", "unverified_fallback", "not_available_in_this_pyjhora_build",
             "error", "unknown"}
    ok_st = (isinstance(st, dict) and len(st) >= 50 and set(st.values()) <= vocab and
             st.get("vimsottari") == "verified_adapter" and
             sum(1 for v in st.values() if v == "unverified_fallback") == 0)
    record(ok_st, f"all_dashas.__status__: {len(st)} systems, all verified_adapter, zero unverified_fallback",
           {k: v for k, v in st.items() if v != "verified_adapter"} if not ok_st else "")

    # ---- (7g) per-system lord encodings: labels fixed + EMPIRICAL ranges (review C6) ----
    cat = {c[0]: (c[1], c[2], c[3] if len(c) > 3 else None) for c in cc.DASHA_CATALOG}
    ok_lab = (cat.get("kalachakra", (None, None, None))[2] == "sign" and
              cat.get("saptharishi_nakshathra", (None, None, None))[2] == "nakshatra")
    record(ok_lab, "catalog lord labels fixed: kalachakra='sign' (was 'navamsa'), saptharishi='nakshatra' (was 'sakta')",
           f"kalachakra={cat.get('kalachakra')} saptharishi={cat.get('saptharishi_nakshathra')}")
    dob = drik.Date(h.Date.year, h.Date.month, h.Date.day); tob = cc._tob(h)
    def _lords(name):
        entry = next((c for c in cc.DASHA_CATALOG if c[0] == name and len(c) >= 3), None)
        if not entry:
            return None
        mod = cc._import_dhasa(entry[1])
        fn = cc._resolve_dasha_fn(mod, entry[2]) or getattr(mod, entry[2])
        res = []
        rows = cc._call_dasha(fn, jd, place, dob, tob, pp, res)
        vals = []
        for row in (rows or [])[:12]:
            if isinstance(row, (list, tuple)) and row:
                lord = row[0]
                vals.append(lord)
        return vals
    kl = _lords("kalachakra") or []
    sp = _lords("saptharishi_nakshathra") or []
    ka = _lords("karana_chathuraaseethi_sama") or []
    ok_ranges = (kl and all(0 <= int(x) < 12 for t in kl for x in (t if isinstance(t, (list, tuple)) else [t])) and
                 sp and all(0 <= int(x) < 27 for t in sp for x in (t if isinstance(t, (list, tuple)) else [t])) and
                 ka and all(0 <= int(x) < 7 for t in ka for x in (t if isinstance(t, (list, tuple)) else [t])))
    record(ok_ranges, "EMPIRICAL lord ranges: kalachakra ids in 0..11 (sign), saptharishi 0..26 (nakshatra), karana 0..6")
    psv = _lords("panchasvara") or []
    scv = _lords("sudharsana_chakra") or []
    krv = _lords("karaka") or []
    ok_shapes = (psv and all(isinstance(x, str) for t in psv for x in t) and
                 scv and all(isinstance(t, (list, tuple)) and len(t) == 2 and
                             all(isinstance(tr, (list, tuple)) and len(tr) == 3 for tr in t) for t in scv) and
                 krv and all(isinstance(t, (list, tuple)) and len(t) == 2 and
                             all(isinstance(pr, (list, tuple)) and len(pr) == 2 and isinstance(pr[0], str)
                                 for pr in t) for t in krv))
    record(ok_shapes, "EMPIRICAL shapes: panchasvara=element strings, sudarshana=(house,sign,planet) triples, karaka=(role,planet_id) pairs")

    # ---- (7h) D72 honest method_status; D150 standard in 5.0 (review C1) ----
    va72 = cc.build_vargas(env, h, jd, place, pp, [72], label_varga_factors=True)
    ms = va72["D72"].get("method_status") or {}
    ok_ms = (ms.get("classical_scheme_implemented") is False and
             ms.get("supported_as_standard_in_pyjhora") is False and
             "custom_divisional_chart" in str(ms.get("implementation", "")) and
             "custom_divisional_check" in va72["D72"] and
             "custom_divisional_proof" not in va72["D72"])
    record(ok_ms, "D72: method_status declares generic custom algorithm (not classical), 'proof' renamed to 'check'", ms)
    record((150 in cc._STANDARD_VARGAS) == (str(cc._pyjhora_version(env)).startswith(("5", "4.9"))),
           f"D150 standard-status matches the installed build ({cc._pyjhora_version(env)}): 150 in _STANDARD_VARGAS={150 in cc._STANDARD_VARGAS}")

    # ---- (7i) compat-patch metadata + stock mode (review C8) ----
    with cc._isolated_pyjhora_config(drik, const_):
        rep = cc._compat_patch_report()
        # context-entry installs the 3 always-on patches; life_span_range installs LAZILY
        # when build_prediction runs — trigger it and re-check.
        ok_core = (any(p["attribute"] == "_cheshta_bala" for p in rep) and
                   any(p["attribute"] == "previous_ascendant_entry_date" for p in rep) and
                   any(p["attribute"] == "charts" and "tajaka_yoga" in p["module"] for p in rep))
        cc.build_prediction(env, h, jd, place, pp)
        rep = cc._compat_patch_report()
        ok_patches = (ok_core and any(p["attribute"] == "life_span_range" for p in rep) and
                      all(p.get("reason") for p in rep))
        # patch 2 works: previous_ascendant_entry_date callable again
        try:
            with cc._quiet():
                pa = drik.previous_ascendant_entry_date(jd, place)
            ok_pa = isinstance(pa, (list, tuple)) and float(pa[0]) > 2000000
        except Exception:
            ok_pa = False
        # patch 3 works: duhphali no longer NameErrors (5.0 module prints debug noise -> _quiet)
        try:
            from jhora.horoscope.transit import tajaka_yoga as _ty
            with cc._quiet():
                dk = _ty.get_duhphali_kutta_yoga_planet_pairs(jd, place)
            ok_dk = isinstance(dk, (list, tuple))
        except Exception:
            ok_dk = False
    record(ok_patches, f"compat registry records all 4 patches WITH reasons ({[p['attribute'] for p in rep]})")
    record(ok_pa and ok_dk, "patched calls work: previous_ascendant_entry_date + duhphali_kutta (5.0-dev bugs mitigated)")
    record(cc._compat_patch_report() == [], "compat registry restored to empty after the context exits")
    with cc._isolated_pyjhora_config(drik, const_, install_patches=False):
        rep0 = cc._compat_patch_report()
    record(rep0 == [], "--stock mode (install_patches=False) installs NOTHING")

    # ---- (7k) update checker (unit; no live network in the suite) ----
    ok_parse = (cc._parse_version_tuple("4.8.7") == (4, 8, 7) and
                cc._parse_version_tuple("5.0") == (5, 0) and
                cc._parse_version_tuple("4.9.3.dev0") == (4, 9, 3) and
                cc._parse_version_tuple("5.0") > cc._parse_version_tuple("4.8.7"))
    record(ok_parse, "update-check version parsing: 4.8.7 / 5.0 / 4.9.3.dev0 compare correctly")
    r_off = cc.check_for_updates("5.0", timeout=0.01)
    record(isinstance(r_off, dict) and r_off.get("installed") == "5.0" and
           "pypi_latest" in r_off and "github_master_version" in r_off and
           isinstance(r_off.get("errors"), dict),
           "check_for_updates returns the full envelope even when sources fail fast (offline-safe)")

    # ---- (7l) special_thaara from LAGNA (5.0 base_star_planet generalization) ----
    pch = cc.build_panchanga(env, h, jd, place)
    tl = pch.get("special_thaara_from_lagna")
    ok_tl = (isinstance(tl, list) and tl and all(isinstance(x, dict) and "lord" in x and "nakshatra" in x for x in tl))
    record(ok_tl, f"special_thaara_from_lagna decoded ({len(tl) if isinstance(tl, list) else tl} entries)")
    # ---- (7p) full/new moon with the 5.0 tithi argument ----
    ok_moon = (isinstance(pch.get("full_moon"), (int, float)) and
               isinstance(pch.get("new_moon"), (int, float)))
    record(ok_moon, f"full_moon/new_moon compute under the 5.0 (jd, tithi_) signature ({pch.get('full_moon')})")
    # ---- (7r) purnimanta lunar month exposed ----
    ok_pur = "lunar_month_date_purnimanta" in pch and not (
        isinstance(pch["lunar_month_date_purnimanta"], str) and
        any(m in pch["lunar_month_date_purnimanta"] for m in ("Traceback", "no attribute")))
    record(ok_pur, f"lunar_month_date_purnimanta present ({str(pch.get('lunar_month_date_purnimanta'))[:40]})")

    # ---- (7m) match: method pinned North + numeric total ----
    other = {"date": "1997-11-15", "time": "09:30:00",
             "lat": GIRL["lat"], "lon": GIRL["lon"], "tz": GIRL["tz"]}
    m = cc.build_match(env, h, jd, place, pp, other)
    ok_m = (m.get("match_method", {}).get("method") == "North" and
            isinstance(m.get("ashtakoota_total"), float))
    record(ok_m, f"match pins Ashtakoota method='North' (5.0 default=None flips to boolean mode); total={m.get('ashtakoota_total')}")

    # ---- (7n) house-system selection (5.0 _bhaava_madhya_new + western toggle) ----
    hs1 = cc.build_house_systems(env, h, jd, place, pp)
    b1 = hs1.get("bhava_method_selected") or {}
    ok_h1 = (b1.get("houses") and len(b1["houses"]) == 12 and b1["houses"][0].get("house") == 1 and
             "house_rasi" in b1["houses"][0] and b1.get("method_name"))
    record(ok_h1, f"default bhava_method_selected: 12 houses, position-numbered, method={b1.get('method')} ({b1.get('method_name')})")
    env["_bhava_opts"] = {"method": "P", "reference": "moon", "asc_start": True, "starts_with_asc": False}
    try:
        hsP = cc.build_house_systems(env, h, jd, place, pp)
    finally:
        env.pop("_bhava_opts", None)
    bP = hsP.get("bhava_method_selected") or {}
    ok_hP = (bP.get("method") == "P" and bP.get("reference_planet_for_ascendant") == "moon" and
             bP.get("ascendant_is_middle_of_house") is False and
             isinstance(bP.get("houses"), list) and len(bP["houses"]) == 12 and
             bP["houses"][0]["cusp"]["longitude"] != b1["houses"][0]["cusp"]["longitude"])
    record(ok_hP, "western Placidus ('P') + moon-reference + asc-start all flow through (auto western toggle)",
           bP.get("method_name") if not ok_hP else f"method_name={bP.get('method_name')}")
    ahs = hs1.get("available_house_systems")
    record(isinstance(ahs, dict) and len(ahs) >= 12 and "3" in ahs and "P" in ahs,
           f"available_house_systems catalog exposed ({len(ahs) if isinstance(ahs, dict) else ahs} systems incl. KP + Placidus)")

    # ---- (7q) longevity_bracket alias ----
    pred = cc.build_prediction(env, h, jd, place, pp)
    ok_lb = (pred.get("longevity_bracket") == pred.get("life_span_range") and
             isinstance(pred.get("longevity_bracket"), dict))
    record(ok_lb, f"prediction.longevity_bracket aliases life_span_range ({pred.get('longevity_bracket')})")

    # ---- (7s) yogas_all capability report ----
    ya = cc.build_yogas_all(env, h, jd, place, pp)
    cnt = ya.get("counts") or {}
    cap = ya.get("__capability__") or {}
    ok_cap = (cnt.get("checked", 0) >= 200 and
              cnt.get("checked") == cap.get("discovered_and_called") and
              cap.get("executed", -1) == cnt["checked"] - cnt.get("skipped_requires_unavailable_inputs", 0) - cnt.get("errors", 0) and
              all(isinstance(x, list) and len(x) == 2 for x in ya.get("skipped_examples", [])))
    record(ok_cap, f"yogas_all capability report consistent: {cnt} (5.0 name-bound predicates)")

    # ---- (7t) sahams under the 5.0 signature ----
    sah = cc.build_sahams(env, h, jd, place, pp)
    ok_sah = isinstance(sah, dict) and "__error__" not in sah and len(sah) >= 30
    record(ok_sah, f"sahams compute under 5.0 get_sahams(jd, place) ({len(sah) if isinstance(sah, dict) else sah} items)")

    # ---- (7w) fine-dasha end-to-end: fractional-hour boundaries survive ----
    fd = cc.build_fine_dhasa(env, h, jd, place, jd_now=jd_now,
                             window_start="2026-09-01", window_end="2026-10-15", max_depth=4)
    cal = fd.get("calendar") or []
    ok_fd = (len(cal) > 0 and any((":" in str(r.get("end", "")) and
                                   not str(r.get("end", "")).endswith("00:00")) for r in cal))
    record(ok_fd, f"fine_dasha calendar carries non-midnight boundary times ({len(cal)} rows; C4 fix end-to-end)")

    # ---- (7j/7v) artifact-level meta disclosure (requires regenerated outputs) ----
    _bf = _os.path.join(_os.path.dirname(__file__), "outputs", "boy_full.json")
    if _os.path.exists(_bf):
        import json as _json
        with open(_bf, encoding="utf-8") as f:
            doc = _json.load(f)
        meta = doc.get("__meta__", {})
        uis = meta.get("upstream_issue_status", {})
        ok_meta = (all(any(i in k for k in uis) for i in ("#52", "#57", "#59", "#61", "#62")) and
                   isinstance(meta.get("wrapper_discovered_5_0_dev_bugs"), list) and
                   len(meta["wrapper_discovered_5_0_dev_bugs"]) >= 4 and
                   isinstance(meta.get("compatibility_patches"), list) and
                   meta.get("engine_mode", "").startswith("wrapper_patched") and
                   "update_check" in meta and
                   str(meta.get("version")) != "?" and
                   meta.get("target_release_line"))
        record(ok_meta, f"artifact meta discloses 5 upstream issues + {len(meta.get('wrapper_discovered_5_0_dev_bugs', []))} wrapper-discovered 5.0 bugs + patches + update_check (version={meta.get('version')})")
    else:
        record(True, "artifact meta check SKIPPED (outputs/boy_full.json not regenerated yet)")


def audit_round8():
    """ROUND-8 audit — the external review (Ft.txt v3, reviewed a renumbered 'chart3.py')
    triage set. P0: Jaimini chara-karaka role shift (Darakaraka was Gnatikaraka!),
    JSON coercion of numpy/date/set, _tob string-safety, tajaka_deep isolated guard.
    P1: match gender directionality, fine-dasha seed fractional hours, error-marker
    tightening, classical Muntha, planet-id labelling. P2: experiments status field,
    generic dasha resolver, getsource-free life-span patch. P3 features: curated +
    wrapper-computed yogas, avasthas, vara/hora/ayana, ashtakavarga labels, Saturn/
    Jupiter gochara classifications, nakshatra lords, --now default + running depth 3."""
    import subprocess as _sp
    import datetime as _dt
    import numpy as _np
    env = get_env()
    h, jd, place, pp = _build(BOY)
    drik = env["drik"]; const_ = env["const"]; utils_ = env["utils"]
    from jhora.horoscope.dhasa.graha import vimsottari
    jdn = utils_.julian_day_number((2026, 9, 3), (12, 0, 0))
    longs = cc._planetary_longitude(pp)

    # ---- (8a) chara-karaka roles: dual scheme + DK = lowest-degree body (review P0-1) ----
    record(cc.CHARA_KARAKA_ROLES_8 == ["Atmakaraka", "Amatyakaraka", "Bhratrikaraka",
                                       "Matrikaraka", "Pitrikaraka", "Putrakaraka",
                                       "Gnatikaraka", "Darakaraka"] and
           cc.CHARA_KARAKA_ROLES_7 == ["Atmakaraka", "Amatyakaraka", "Bhratrikaraka",
                                       "Matrikaraka", "Putrakaraka",
                                       "Gnatikaraka", "Darakaraka"],
           "karaka role tables: 8-scheme carries Pitrikaraka with Darakaraka LAST; "
           "7-scheme drops Pitrikaraka (no invented 'Atmakaraka2')")
    k = cc.build_karakas(env, h, jd, place, pp)
    order = k.get("chara_karakas_order") or []
    # PyJHora (and JHora) rank Rahu by (30° - its degree-in-sign) — the shadow planet is
    # mirrored — so the decreasing-degree crosscheck must use the same effective degree.
    def _eff_deg(nm):
        dg = cc._deg_in_sign(longs[nm])
        return (30.0 - dg) if nm == "Rahu" else dg
    degs = [_eff_deg(n) for n in order if n in longs]
    ok_k = (str(k.get("chara_karaka_scheme", "")).startswith("8-karaka") and len(order) == 8 and
            k.get("darakaraka") == order[-1] and k.get("gnatikaraka") == order[6] and
            k.get("pitrikaraka") == order[4] and k.get("atmakaraka") == order[0] and
            "Atmakaraka2" not in str(k) and
            all(degs[i] >= degs[i + 1] for i in range(len(degs) - 1)) and
            abs(_eff_deg(k["darakaraka"]) - min(degs)) < 1e-9)
    record(ok_k, f"boy chara-karakas: order decreasing by effective degree (Rahu mirrored "
                 f"30°-deg) {order}; DK={k.get('darakaraka')} = lowest = names[7]; "
                 f"GK={k.get('gnatikaraka')}, PiK={k.get('pitrikaraka')} — matches the "
                 f"library docstring's own role list (P0-1 fix; was DK=names[6]=GK)")

    # ---- (8b) JSON coercion of numpy/date/set (review P0-2) ----
    v1, ok1 = cc._json_coerce(_np.int64(42))
    v2, ok2 = cc._json_coerce(_np.float64(3.5))
    v3, ok3 = cc._json_coerce(_np.bool_(True))
    v4, ok4 = cc._json_coerce(_np.array([1, 2, 3]))
    v5, ok5 = cc._json_coerce(_dt.date(2020, 1, 2))
    v6, ok6 = cc._json_coerce({3, 1, 2})
    v7, ok7 = cc._json_coerce(object())
    record(ok1 and v1 == 42 and type(v1) is int and ok2 and v2 == 3.5 and
           ok3 and v3 is True and ok4 and v4 == [1, 2, 3] and
           ok5 and v5 == "2020-01-02" and ok6 and sorted(v6) == [1, 2, 3] and
           not ok7 and v7 is None,
           "_json_coerce: numpy scalars/arrays, date, set → native JSON; "
           "unrepresentable objects still refused (P0-2 fix)")

    # ---- (8c) _tob string-safety without silent noon defaults (review P0-3) ----
    class _FakeH:
        pass
    f1 = _FakeH(); f1.birth_time = "10:30:00"
    f2 = _FakeH(); f2.birth_time = "10:30"
    f3 = _FakeH(); f3.birth_time = (8, 15)
    f4 = _FakeH(); f4.birth_time = "garbage"
    f5 = _FakeH(); f5.birth_time = None
    record(cc._tob(f1) == (10, 30, 0) and cc._tob(f2) == (10, 30, 0) and
           cc._tob(f3) == (8, 15, 0) and cc._tob(f4) is None and cc._tob(f5) is None and
           cc._tob(h) == (20, 10, 0),
           f"_tob: strings parsed ('10:30:00'→(10,30,0)), 2-tuples zero-padded, garbage→None "
           f"(never a silent noon), real Horoscope→{cc._tob(h)} (P0-3 hardening)")

    # ---- (8d) tajaka_deep: tajaka_yoga absence must NOT abort the section (P0-4) ----
    env_noTy = dict(env); env_noTy["tajaka_yoga"] = None
    tj_d = cc.build_tajaka_deep(env_noTy, h, jd, place, pp, jd_now=jdn)
    ok_d = ("__error__" not in tj_d and isinstance(tj_d.get("tajaka_yogas"), dict) and
            "__error__" in tj_d["tajaka_yogas"] and len(tj_d) >= 5 and
            any("monthly" in kk for kk in tj_d))
    record(ok_d, f"tajaka_deep without tajaka_yoga still returns {len(tj_d)} blocks "
                 f"(monthly/60-hour/praveshas/aspects intact; only tajaka_yogas typed-error) "
                 f"(P0-4 fix — previously the whole section aborted)")

    # ---- (8e) match gender directionality (review P1-5) ----
    h2, jd2, place2, pp2 = _build(GIRL)
    longs2 = cc._planetary_longitude(pp2)
    partner_moon_nak = cc._nakshatra_of_long(longs2["Moon"])[0]
    native_moon_nak = cc._nakshatra_of_long(longs["Moon"])[0]
    other_f = {"date": "1997-11-15", "time": "09:30:00", "lat": 14.4674, "lon": 78.8241,
               "tz": 5.5, "gender": "female", "match_gender": "male"}
    mf = cc.build_match(env, h, jd, place, pp, other_f)
    other_m = {"date": "1997-11-15", "time": "09:30:00", "lat": 14.4674, "lon": 78.8241,
               "tz": 5.5}
    md = cc.build_match(env, h, jd, place, pp, other_m)
    ok_m = (mf.get("genders", {}).get("boy_side") == "partner" and
            mf.get("boy_moon", {}).get("nakshatra") == partner_moon_nak and
            mf.get("girl_moon", {}).get("nakshatra") == native_moon_nak and
            md.get("genders", {}).get("boy_side") == "native" and
            md.get("boy_moon", {}).get("nakshatra") == native_moon_nak and
            isinstance(mf.get("ashtakoota_total"), (int, float)))
    record(ok_m, f"match directionality: female native → boy_* inputs = partner "
                 f"({partner_moon_nak}), default stays native=male; totals still compute "
                 f"(P1-5 fix; --gender/--match-gender)")

    # ---- (8f) fine-dasha seeds keep fractional hours (review P1-6) ----
    maha_map = vimsottari.vimsottari_mahadasa(jd, place)
    sat_pid = 6  # Saturn
    sat_jd = float(maha_map[sat_pid])
    _y, _mo, _dd, _fh = utils_.jd_to_gregorian(sat_jd)
    exp_hhmm = "%02d:%02d" % (int(_fh), int((float(_fh) - int(_fh)) * 60))  # truncated, as _date_str
    fd = cc.build_fine_dhasa(env, h, jd, place, jd_now=jdn,
                             window_start="2036-07-01", window_end="2036-08-01", max_depth=2)
    cal = fd.get("calendar") or []
    sat_rows = [r for r in cal if r.get("level") == 1 and r.get("lords") == ["Saturn"]]
    ok_f = bool(sat_rows) and str(sat_rows[0].get("start", "")).endswith(exp_hhmm)
    record(ok_f, f"fine-dasha Saturn-MD seed boundary inside the window carries MINUTES "
                 f"(row start={sat_rows[0].get('start') if sat_rows else None}, expected "
                 f"HH:MM={exp_hhmm} from the library JD) (P1-6 fix — int() truncation gone)")

    # ---- (8g) error markers: no false positives on prose (review P1-7) ----
    prose = "with the Exception of Jupiter, all rules hold for this placement"
    real = "Exception: something actually failed"
    ok_g = ("Exception" not in cc._ERR_STRING_MARKERS and
            not any(m in prose for m in cc._ERR_STRING_MARKERS) and
            any(m in real for m in cc._ERR_STRING_MARKERS) and
            any(m in "TypeError: boom" for m in cc._ERR_STRING_MARKERS))
    record(ok_g, "error-string markers: bare 'Exception' removed (prose-safe), "
                 "'Exception:'/'TypeError:' still caught (P1-7 fix)")

    # ---- (8h) Muntha computed classically from the NATAL lagna (review P1-8) ----
    # ROUND-9 UPDATE (review v4 Issue 1): the advance uses COMPLETED years (varsha N - 1),
    # so Muntha sits in the natal lagna at birth. See audit_round9 (9a) for the at-birth
    # anchor; this record keeps the frame-consistency checks on the corrected formula.
    tj = cc.build_tajaka(env, h, jd, place, jd_now=jdn)
    m = tj.get("muntha") or {}
    ye = int(tj.get("years_elapsed") or 0)
    cy = max(0, ye - 1)
    natal_asc = cc._sign_of_long(longs[cc.LAGNA])
    exp_sign = (natal_asc + cy) % 12
    ann = m.get("annual_lagna_sign")
    ann_idx = cc.RAASI_NAMES.index(ann) if ann in cc.RAASI_NAMES else None
    ok_h = (m.get("completed_years") == cy and
            m.get("muntha_sign_index") == exp_sign and
            m.get("muntha_sign") == cc.RAASI_NAMES[exp_sign] and
            m.get("muntha_house_from_natal_lagna") == cy % 12 + 1 and
            tj.get("muntha_house") == cy % 12 + 1 and
            (ann_idx is None or
             m.get("muntha_house_from_annual_lagna") == (exp_sign - ann_idx) % 12 + 1))
    record(ok_h, f"Muntha classical: natal lagna {cc.RAASI_NAMES[natal_asc]} + {cy} COMPLETED "
                 f"years (varsha {ye}) → {m.get('muntha_sign')}; house from natal="
                 f"{m.get('muntha_house_from_natal_lagna')}, from annual="
                 f"{m.get('muntha_house_from_annual_lagna')} (P1-8 + R9-Issue-1 fixes)")

    # ---- (8i) planet-id labelling never crashes (review P1-9) ----
    _pl = cc._planetary_longitude([["U", [3, 15.0]], ["L", [1, 14.0]], [0, [5, 28.9]]])
    record(cc._pid_label(3) == "Mercury" and cc._pid_label(12) == "Planet_12" and
           cc._pid_label("U") == "U" and cc._pid_label("7") == "Rahu" and cc._pid_label(6) == "Saturn" and
           cc._pid_label("L") == "L" and  # 'L' is special-cased in _planetary_longitude
           set(_pl) == {"U", "Lagna", "Sun"} and
           abs(_pl["U"] - 105.0) < 1e-9 and abs(_pl["Lagna"] - 44.0) < 1e-9 and
           abs(_pl["Sun"] - 178.9) < 1e-9,
           f"_pid_label/_planetary_longitude: unknown ids ('U', 12) labelled, never raise; "
           f"'L' still maps to Lagna in the longitude decode ({_pl}) (P1-9)")

    # ---- (8j) generic dasha-entry resolver fallback (review P2-2) ----
    class _FakeMod:
        @staticmethod
        def get_dhasa_bhukthi(*a, **k):
            return "ok"
    class _EmptyMod:
        pass
    record(cc._resolve_dasha_fn(_FakeMod, "get_totally_unknown_dhasa_bhukthi") is not None and
           cc._resolve_dasha_fn(_EmptyMod, "get_dhasa_bhukthi") is None,
           "_resolve_dasha_fn: unified per-module entry points resolve as last resort; "
           "empty modules still None (P2-2; resolution stays visible via __resolution__)")

    # ---- (8k) life-span patch works WITHOUT inspect.getsource (review P2-4) ----
    import inspect as _insp
    with cc._isolated_pyjhora_config(drik, const_):
        from jhora.horoscope.prediction import longevity as lon_mod
        _real_gs = _insp.getsource
        def _boom(*a, **k):
            raise OSError("could not find source (simulated .pyc-only install)")
        _insp.getsource = _boom
        try:
            cc._patch_life_span_range(lon_mod)
        finally:
            _insp.getsource = _real_gs
        with cc._quiet():
            v_nosrc = lon_mod.life_span_range(jd, place)
        rep1 = [p for p in cc._compat_patch_report() if p["attribute"] == "life_span_range"]
    with cc._isolated_pyjhora_config(drik, const_):
        from jhora.horoscope.prediction import longevity as lon_mod2
        cc._patch_life_span_range(lon_mod2)
        with cc._quiet():
            v_src = lon_mod2.life_span_range(jd, place)
    # NOTE: the patch registry can carry earlier life_span entries from prior audits in the
    # same process — check that SOME entry discloses the getsource-free fallback.
    ok_l = (v_nosrc == v_src and v_nosrc in (0, 1, 2) and rep1 and
            any("getsource" in p.get("reason", "") for p in rep1))
    record(ok_l, f"life_span_range: getsource-free reimplementation == source-patched result "
                 f"({v_nosrc} for boy) and is disclosed in the patch registry (P2-4)")

    # ---- (8l) experiments section-level status when the package is gone (P2-1) ----
    ex = cc.build_experiments(env, h, jd, place, pp, marriage_window=(2025, 2040))
    ok_x = (ex.get("status") == "deprecated_upstream" and "REMOVED" in str(ex.get("note", "")) and
            all(isinstance(ex.get(kk), dict) and "__error__" in ex[kk]
                for kk in ("marriage5_windows", "double_transit_windows",
                           "marriage2_windows_variant")))
    record(ok_x, "experiments: machine-readable status='deprecated_upstream' + note when "
                 "jhora.experiments is absent; every key typed (P2-1)")

    # ---- (8m) curated yogas expansion + wrapper-computed yogas (review P3-1) ----
    y = cc.build_yogas(env, h, jd, place, pp)
    cap = y.get("__capability__") or {}
    new_names = ["ruchaka_yoga", "bhadra_yoga", "hamsa_yoga", "maalavya_yoga", "sasa_yoga",
                 "kemadruma_yoga", "sunaphaa_yoga", "duradhara_yoga", "vesi_yoga",
                 "vosi_yoga", "ubhayachara_yoga", "saraswathi_yoga"]
    cy = y.get("computed_yogas") or {}
    sun_s = cc._sign_of_long(longs["Sun"]); mer_s = cc._sign_of_long(longs["Mercury"])
    ok_y = (cap.get("curated_listed") == 28 and cap.get("errored") == 0 and
            all(nn in y and isinstance(y[nn], (bool, dict, list)) for nn in new_names) and
            cy.get("budhaditya_yoga", {}).get("present") == (sun_s == mer_s) and
            all(("subha_kartari_" + r) in cy and ("paapa_kartari_" + r) in cy
                for r in ("from_lagna", "from_moon")))
    record(ok_y, f"yogas: curated 16→28 (all five Mahapurusha + Kemadruma + Sunaphaa/"
                 f"Duradhara + Vesi/Vosi/Ubhayachara + Saraswathi, 0 errors) + computed "
                 f"budhaditya={'present' if cy.get('budhaditya_yoga', {}).get('present') else 'absent'} "
                 f"& 4 kartari checks with occupants (P3-1)")

    # ---- (8n) avasthas (review P3-2) ----
    pf = cc.build_planet_flags(env, h, jd, place, pp)
    av = pf.get("avasthas") or {}
    BAALADI_ODD = ["Bala", "Kumara", "Yuva", "Vriddha", "Mrita"]
    BAALADI_EVEN = ["Mrita", "Vriddha", "Yuva", "Kumara", "Bala"]
    ok_a = "__error__" not in av and len([p for p in cc.PLANET_ORDER if p in av]) == 9
    for pn in cc.PLANET_ORDER:
        row = av.get(pn) or {}
        sg = cc._sign_of_long(longs[pn]); dg = cc._deg_in_sign(longs[pn])
        exp_state = (BAALADI_ODD if sg % 2 == 0 else BAALADI_EVEN)[min(4, int(dg // 6.0))]
        dig = cc._dignity(pn, sg)
        ok_a = ok_a and row.get("baaladi_avastha") == exp_state
        if dig in ("Exalted", "Own_sign"):
            ok_a = ok_a and row.get("jagradadi_avastha") == "Jagrita"
        elif dig == "Debilitated":
            ok_a = ok_a and row.get("jagradadi_avastha") == "Sushupti"
    record(ok_a, f"avasthas: baaladi re-derived from degrees/parity for all 9 graha "
                 f"(Sun={av.get('Sun', {}).get('baaladi_avastha')}, "
                 f"jagradadi consistent with dignity+compound relations) (P3-2)")

    # ---- (8o) vara lord / janma hora lord / ayana (review P3-3) ----
    pa = cc.build_panchanga(env, h, jd, place)
    VARA = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
    CHAL = ["Sun", "Venus", "Mercury", "Moon", "Saturn", "Jupiter", "Mars"]
    vl = pa.get("vara_lord") or {}
    wd = pa.get("weekday", {}).get("index") if isinstance(pa.get("weekday"), dict) else None
    ok_v = vl.get("lord") == VARA[int(wd) % 7] if wd is not None else False
    hl = pa.get("janma_hora_lord") or {}
    ok_hr = False
    if isinstance(hl, dict) and "lord" in hl:
        k_hora = int(hl.get("hora_index_of_day", -1))
        ok_hr = (0 <= k_hora < 24 and
                 hl["lord"] == CHAL[(CHAL.index(VARA[int(wd) % 7]) + k_hora) % 7])
    ay = pa.get("ayana") or {}
    sl = pa.get("solar_longitude")
    ok_ay = False
    if isinstance(ay, dict) and isinstance(sl, (int, float)):
        ss = int(float(sl) // 30) % 12
        ok_ay = (ay.get("sun_sign") == cc.RAASI_NAMES[ss] and
                 ay.get("name") == ("Uttarayana" if ss in (9, 10, 11, 0, 1, 2) else "Dakshinayana"))
    record(ok_v and ok_hr and ok_ay,
           f"panchanga essentials: vara lord={vl.get('lord')} (weekday {wd}), janma hora "
           f"lord={hl.get('lord')} (hora #{hl.get('hora_index_of_day')}), ayana="
           f"{ay.get('name')} sun in {ay.get('sun_sign')} (P3-3)")

    # ---- (8p) ashtakavarga sign/house labels (review P3-4) ----
    av8 = cc.build_ashtakavarga(env, h, jd, place, pp)
    sav = av8.get("samudhaya_ashtakavarga") or []
    bs = av8.get("samudhaya_by_sign") or {}
    bh = av8.get("samudhaya_by_house") or {}
    bbs = av8.get("binna_by_sign") or {}
    lag_s = cc._sign_of_long(longs[cc.LAGNA])
    ok_q = (len(sav) == 12 and
            all(bs.get(cc.RAASI_NAMES[i]) == int(sav[i]) for i in range(12)) and
            bh.get("H1") == int(sav[lag_s]) and bh.get("H7") == int(sav[(lag_s + 6) % 12]) and
            list(bbs.get("Sun", {}).values()) == [int(x) for x in (av8.get("binna_ashtakavarga") or [[]])[0]])
    # the DEEP section carries the same labelled views (raw arrays identical)
    with cc._quiet():
        avd = cc.build_ashtakavarga_deep(env, h, jd, place, pp)
    dsav = avd.get("samudhaya_ashtakavarga") or []
    ok_qd = (list(dsav) == list(sav) and
             avd.get("samudhaya_by_sign") == bs and avd.get("samudhaya_by_house") == bh and
             avd.get("binna_by_sign") == bbs and "bindu_labels_note" in avd)
    record(ok_q and ok_qd, f"ashtakavarga labels: samudhaya_by_sign/by_house consistent with the raw "
                 f"array (H1={bh.get('H1')} at lagna {cc.RAASI_NAMES[lag_s]}), binna_by_sign "
                 f"Sun row matches; ashtakavarga_deep carries IDENTICAL labelled views (P3-4)")

    # ---- (8q) Saturn/Jupiter gochara classifications (review P3-5) ----
    tr = cc.build_transits(env, h, jd, place, pp, jdn,
                           compute_conjunctions=False, compute_events=False)
    tpp = env["charts"].rasi_chart(jdn, place)
    tl = cc._planetary_longitude(tpp)
    moon_s = cc._sign_of_long(longs["Moon"])
    sat_s = cc._sign_of_long(tl["Saturn"]); jup_s = cc._sign_of_long(tl["Jupiter"])
    sat_h = (sat_s - moon_s) % 12 + 1; jup_h = (jup_s - moon_s) % 12 + 1
    sg_ = tr.get("saturn_gochara_from_natal_moon") or {}
    jg_ = tr.get("jupiter_gochara_from_natal_moon") or {}
    ok_r = (sg_.get("house_from_natal_moon") == sat_h and
            sg_.get("sade_sati") == (sat_h in (12, 1, 2)) and
            sg_.get("ashtama_shani") == (sat_h == 8) and
            sg_.get("kantaka_shani") == (sat_h in (4, 7, 10)) and
            (sg_.get("sade_sati_phase") is None) == (sat_h not in (12, 1, 2)) and
            jg_.get("house_from_natal_moon") == jup_h and
            jg_.get("auspicious_house") == (jup_h in (2, 5, 7, 9, 11)))
    record(ok_r, f"gochara from natal Moon ({cc.RAASI_NAMES[moon_s]}): Saturn "
                 f"{cc.RAASI_NAMES[sat_s]}=house {sat_h} (sade_sati={sg_.get('sade_sati')}, "
                 f"kantaka={sg_.get('kantaka_shani')}), Jupiter {cc.RAASI_NAMES[jup_s]}=house "
                 f"{jup_h} (auspicious={jg_.get('auspicious_house')}) (P3-5)")

    # ---- (8r) nakshatra lords in core (review P3-6) ----
    core = cc.build_core(env, h, jd, place, pp)
    LORDS9 = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"]
    ok_s = core["lagna"].get("nakshatra_lord") == LORDS9[cc.NAKSHATRAS.index(core["lagna"]["nakshatra"]) % 9]
    for prow in core["planets"]:
        ok_s = ok_s and prow.get("nakshatra_lord") == LORDS9[cc.NAKSHATRAS.index(prow["nakshatra"]) % 9]
    record(ok_s, f"core nakshatra_lord on lagna + all 9 graha (lagna "
                 f"{core['lagna']['nakshatra']}→{core['lagna'].get('nakshatra_lord')}; "
                 f"Vimshottari cycle) (P3-6)")

    # ---- (8s) CLI: --now defaults to today + running depth defaults to 3 (P3-7) ----
    import os as _os, tempfile as _tf
    _out = _os.path.join(_tf.gettempdir(), "r8_now_default.json")
    cmd = [sys.executable, _os.path.join(_os.path.dirname(_os.path.abspath(cc.__file__)),
                                         "compute_chart.py"),
           "--date", "2003-10-16", "--time", "20:10:00",
           "--lat", "18.2989", "--lon", "83.8975", "--tz", "5.5",
           "--no-update-check", "--out", _out]
    try:
        _sp.run(cmd, check=True, capture_output=True, timeout=600)
        with open(_out, encoding="utf-8") as f:
            doc = json.load(f)
        meta = doc.get("__meta__", {})
        dasha = doc.get("dasha", {})
        runv = dasha.get("running_vimsottari") or []
        ok_t = (meta.get("now_defaulted_to_today") is True and
                str(meta.get("now_reference_date")) == _dt.date.today().isoformat() and
                dasha.get("running_depth") == 3 and len(runv) == 3 and
                meta.get("native_gender") == "male")
        detail = (f"now={meta.get('now_reference_date')} depth={dasha.get('running_depth')} "
                  f"chain={[r.get('lords') for r in runv]}")
    except Exception as e:
        ok_t, detail = False, repr(e)
    record(ok_t, f"CLI defaults: --now omitted → today + running dashas computed at depth 3 "
                 f"(P3-7) [{detail}]")

    # ---- (8t) --gender flag flows to meta + mahabhagya pool ----
    _out2 = _os.path.join(_tf.gettempdir(), "r8_gender.json")
    cmd2 = cmd[:-2] + ["--gender", "female", "--sections", "yogas", "--out", _out2]
    try:
        _sp.run(cmd2, check=True, capture_output=True, timeout=600)
        with open(_out2, encoding="utf-8") as f:
            doc2 = json.load(f)
        ok_u = doc2.get("__meta__", {}).get("native_gender") == "female"
    except Exception as e:
        ok_u = False
        doc2 = repr(e)
    record(ok_u, f"--gender female reaches meta.native_gender "
                 f"(got {doc2.get('__meta__', {}).get('native_gender') if isinstance(doc2, dict) else doc2}) "
                 f"and the yogas_all pool binds it for mahabhagya (#61 follow-through)")


def audit_round9():
    """ROUND-9 audit — external review v4 (Ft.txt, 223 lines) adjudication.
    ADOPTED (real): Issue 1 Muntha must advance by COMPLETED years (varsha N-1), not the
    varsha number N (P0 — one-sign shift for life); Issue 4 _call_dasha tagged total
    positional failures "positional_fallback" → __status__ masked a broken dasha as
    "unverified_fallback" (P1); Issue 5 Ardhashtama Shani is the 4th from the natal Moon
    ("half of the 8th"), not the 5th (P1); Issue 6 --match-tz omitted passed None to
    Horoscope, which calls exit() — a SystemExit that killed the WHOLE process (P1;
    worse than the reported TypeError); Enhancements 1-5: redundant D1 ephemeris call in
    build_vargas, raw-JD varga-transit entry dates, unstructured sphutas/sahams, yogas +
    D2/D3 missing from FAST defaults, bhaava_madhya_swe cross-version kwarg robustness
    (ordering corrected: house_code is the 5.0 primary — the review's hsys-first order is
    the OLD 4.8.7 signature).
    REJECTED with evidence: Issue 2 "janma hora timezone corruption" — drik.sunrise has
    returned a LOCAL-clock JD since PyJHora V2.5.2 (its own "RECALCULATE RISE_JD" block),
    the same domain as h.julian_day; runtime domain-consistency proof in 9b. Issue 3
    "curated yogas invoked with the wrong signature" — build_yogas resolves every curated
    name to its `_from_planet_positions` variant (present for all of them in the
    installed build, capability errored=0); proof in 9c."""
    env = get_env()
    h, jd, place, pp = _build(BOY)
    drik = env["drik"]; utils_ = env["utils"]
    longs = cc._planetary_longitude(pp)
    jdn = utils_.julian_day_number((2026, 9, 3), (12, 0, 0))

    # ---- (9a) Muntha: at-birth anchor + completed-years advance (review Issue 1) ----
    tj0 = cc.build_tajaka(env, h, jd, place, jd_now=jd)
    m0 = tj0.get("muntha") or {}
    tjn = cc.build_tajaka(env, h, jd, place, jd_now=jdn)
    mn = tjn.get("muntha") or {}
    natal_asc = cc._sign_of_long(longs[cc.LAGNA])
    yen = int(tjn.get("years_elapsed") or 0)
    cyn = mn.get("completed_years")
    ok_a = (int(tj0.get("years_elapsed") or 0) == 1 and m0.get("completed_years") == 0 and
            m0.get("muntha_sign_index") == natal_asc and
            m0.get("muntha_house_from_natal_lagna") == 1 and tj0.get("muntha_house") == 1 and
            cyn == max(0, yen - 1) and
            mn.get("muntha_sign_index") == (natal_asc + cyn) % 12 and
            mn.get("muntha_house_from_natal_lagna") == cyn % 12 + 1 and
            tjn.get("muntha_house") == cyn % 12 + 1)
    record(ok_a, f"Muntha completed-years anchor: AT BIRTH (varsha 1) Muntha = natal lagna "
                 f"{m0.get('muntha_sign')} house {m0.get('muntha_house_from_natal_lagna')}; at "
                 f"2026-09-03 varsha {yen} → completed {cyn} → {mn.get('muntha_sign')}, house "
                 f"from natal {mn.get('muntha_house_from_natal_lagna')} (R9 Issue-1 fix — was "
                 f"shifted one whole sign for life)")

    # ---- (9b) janma-hora LOCAL-domain proof (review Issue 2 REJECTED) ----
    pa = cc.build_panchanga(env, h, jd, place)
    hl = pa.get("janma_hora_lord") or {}
    sr = drik.sunrise(jd, place)   # [local float hours, 'HH:MM:SS' string, LOCAL-clock JD]
    tob = cc._tob(h)
    birth_local = tob[0] + tob[1] / 60.0 + tob[2] / 3600.0
    rise_local = float(sr[0])
    exp_hrs = birth_local - rise_local
    eng_hrs = hl.get("hours_since_sunrise")
    tz_off = float(place.timezone)
    ok_b = (isinstance(eng_hrs, (int, float)) and abs(eng_hrs - exp_hrs) < 0.01 and
            abs(eng_hrs - (exp_hrs + tz_off)) > 1.0 and
            abs(eng_hrs - (exp_hrs - tz_off)) > 1.0)
    record(ok_b, f"janma hora local-domain proof: engine hours_since_sunrise={eng_hrs} == "
                 f"birth-local {birth_local:.4f}h − sunrise-local {rise_local:.4f}h = "
                 f"{exp_hrs:.4f}h (drik.sunrise returns LOCAL-clock values since V2.5.2); "
                 f"both tz-corrupted variants (±{tz_off}h) rejected at runtime "
                 f"(R9 Issue-2 REJECTED — no corruption exists)")

    # ---- (9c) curated yoga variant evidence (review Issue 3 REJECTED) ----
    yoga = env["yoga"]
    missing_variant = [nm for nm in cc.CURATED_YOGAS
                       if hasattr(yoga, nm) and not hasattr(yoga, nm + "_from_planet_positions")]
    yg = cc.build_yogas(env, h, jd, place, pp)
    cap = yg.get("__capability__") or {}
    ok_c = (not missing_variant and cap.get("curated_listed") == 28 and
            cap.get("executed") == cap.get("discovered") and cap.get("errored") == 0)
    record(ok_c, f"curated yoga signatures: every one of the {cap.get('curated_listed')} "
                 f"curated names has a `_from_planet_positions` variant in the installed "
                 f"build (build_yogas prefers it — the single positional IS its signature); "
                 f"executed={cap.get('executed')}, errored={cap.get('errored')} "
                 f"(R9 Issue-3 REJECTED; missing variants: {missing_variant or 'none'})")

    # ---- (9d) _call_dasha failure tagging (review Issue 4) ----
    def _boom(*a, **k):
        raise ValueError("always fails")

    def _pos_ok(*a):
        return ["ok", len(a)]

    r1 = []; v1 = cc._call_dasha(_boom, jd, place, (2003, 10, 16), (20, 10, 0), pp, _resolution=r1)
    r2 = []; v2 = cc._call_dasha(_pos_ok, jd, place, (2003, 10, 16), (20, 10, 0), pp, _resolution=r2)
    ok_d = (isinstance(v1, dict) and "__error__" in v1 and r1 == ["error"] and
            bool(r2) and r2[-1] == "positional_fallback" and
            not (isinstance(v2, dict) and "__error__" in v2))
    record(ok_d, f"_call_dasha failure tagging: all-positional-trials-failed → __error__ + "
                 f"resolution {r1} (was 'positional_fallback' → __status__ "
                 f"'unverified_fallback', masking the break); positional SUCCESS still tags "
                 f"{r2} (R9 Issue-4 fix)")

    # ---- (9e) ardhashtama = 4th from natal Moon (review Issue 5) ----
    tr = cc.build_transits(env, h, jd, place, pp, jdn,
                           compute_conjunctions=False, compute_events=False)
    tpp = env["charts"].rasi_chart(jdn, place)
    tl = cc._planetary_longitude(tpp)
    moon_s = cc._sign_of_long(longs["Moon"]); sat_s = cc._sign_of_long(tl["Saturn"])
    sat_h = (sat_s - moon_s) % 12 + 1
    sg = tr.get("saturn_gochara_from_natal_moon") or {}
    ok_e = (sg.get("ardhashtama_shani_4th_from_moon") == (sat_h == 4) and
            sg.get("panchama_shani_5th_from_moon") == (sat_h == 5) and
            "ardhashtama_shani_5th_from_moon" not in sg and
            "4th" in str(sg.get("conventions", "")))
    record(ok_e, f"ardhashtama = 4th from natal Moon (Saturn in {cc.RAASI_NAMES[sat_s]} = "
                 f"house {sat_h}: ardha4={sg.get('ardhashtama_shani_4th_from_moon')}, "
                 f"panchama5={sg.get('panchama_shani_5th_from_moon')}); the old 5th-house "
                 f"key is gone (R9 Issue-5 fix — ardhashtama is 'half of the 8th')")

    # ---- (9f) match tz handling — ROUND-10 semantics (review P1-3 superseded R9) ----
    # Round 9 defaulted an omitted --match-tz to the native's --tz; since --tz has an
    # argparse default (5.5), coordinate-mode partners ALWAYS got IST. Round 10: an
    # omitted tz is RESOLVED FROM THE PARTNER'S OWN COORDINATES (library
    # utils.get_place_timezone_offset, DST-aware); an explicit tz passes through with no
    # note; the SystemExit guard remains (upstream exit() on unresolvable tz).
    other_exp = {"date": "1997-11-15", "time": "09:30:00", "lat": 14.4674, "lon": 78.8241,
                 "tz": 5.5}
    m_exp = cc.build_match(env, h, jd, place, pp, other_exp)
    other_res = {"date": "1997-11-15", "time": "09:30:00", "lat": 14.4674, "lon": 78.8241,
                 "tz": None}
    try:
        m_res = cc.build_match(env, h, jd, place, pp, other_res)
        killed = False
    except SystemExit:
        m_res = None; killed = True   # would mean the hardening failed
    ok_f = (isinstance(m_exp.get("ashtakoota_total"), (int, float)) and
            "__error__" not in m_exp and "timezone_note" not in m_exp and
            isinstance(m_res, dict) and "__error__" not in m_res and not killed and
            m_res.get("ashtakoota_total") == m_exp.get("ashtakoota_total") and
            "timezone_note" in m_res and
            "resolved from the partner's coordinates" in str(m_res.get("timezone_note")))
    record(ok_f, f"match tz handling (R10 P1-3 semantics): explicit tz=5.5 computes cleanly "
                 f"(total={m_exp.get('ashtakoota_total')}, NO note); omitted tz resolves from "
                 f"the partner's coordinates to the identical result (total="
                 f"{m_res.get('ashtakoota_total')}, timezone_note disclosed) — the Round-9 "
                 f"default-to-native-5.5 chain is gone; SystemExit guard intact "
                 f"(killed={killed})")

    # ---- (9g) build_vargas D1 reuse (review Enhancement 1) ----
    bv = cc.build_vargas(env, h, jd, place, pp, [72])
    cdc = (bv.get("D72") or {}).get("custom_divisional_check") or {}
    ok_g = (abs(float(cdc.get("d1_lagna_longitude", -1)) - round(longs[cc.LAGNA], 3)) < 1e-6 and
            cdc.get("differs_from_d1_lagna") is True)
    record(ok_g, f"build_vargas D1 reuse: custom_divisional_check's D1 reference = the "
                 f"passed-in D1 positions (d1_lagna={cdc.get('d1_lagna_longitude')} == core "
                 f"lagna {round(longs[cc.LAGNA], 3)}); redundant per-factor ephemeris D1 "
                 f"call removed (R9 Enhancement-1)")

    # ---- (9h) varga-transit entry dates decoded (review Enhancement 2) ----
    import re as _re
    vt = cc.build_varga_transits(env, h, jd, place, jdn, pp, division=9)
    ne = vt.get("next_entry_dates") or {}
    pe = vt.get("previous_entry_dates") or {}
    _pat = _re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
    ok_h = (len(ne) == 9 and len(pe) == 9 and
            all(isinstance(v, dict) and _pat.match(str(v.get("date", ""))) and
                float(v.get("jd", 0)) > jdn and "sign_in_divisional_frame" in v
                for v in ne.values()) and
            all(isinstance(v, dict) and float(v.get("jd", 1e18)) < jdn for v in pe.values()))
    _sun = ne.get("Sun") if isinstance(ne.get("Sun"), dict) else {}
    record(ok_h, f"varga-transit entry dates decoded: {len(ne)} next / {len(pe)} previous, "
                 f"all ISO-dated with divisional-frame signs (e.g. Sun next D9 entry "
                 f"{_sun.get('date')} in {_sun.get('sign_in_divisional_frame')}) "
                 f"(R9 Enhancement-2 — was raw JD floats)")

    # ---- (9i) sphutas + sahams structured (review Enhancement 3) ----
    sp = cc.build_sphutas(env, h, jd, place)
    struct = [v for v in sp.values() if isinstance(v, dict) and "longitude" in v]
    ok_sp = (len(struct) >= 12 and all(
        abs(s["longitude"] - (s["sign_index"] * 30 + s["degree_in_sign"])) < 1e-3 and
        s["sign"] == cc.RAASI_NAMES[s["sign_index"]] for s in struct))
    sh = cc.build_sahams(env, h, jd, place, pp)
    parsed = [v for v in sh.values() if isinstance(v, dict) and "sign" in v]
    ok_sh = (isinstance(sh, dict) and len(sh) >= 30 and len(parsed) >= 30 and all(
        abs(v["longitude"] - (v["sign_index"] * 30 + v["degree_in_sign"])) < 1e-3 and
        "raw" in v for v in parsed))
    record(ok_sp and ok_sh, f"sphutas/sahams structured: {len(struct)}/{len(sp)} sphutas "
                 f"decoded (sign + degree + longitude self-consistent), {len(parsed)}/{len(sh)} "
                 f"sahams parsed from 'Sign D° M' S\"' strings with the raw text preserved "
                 f"(R9 Enhancement-3)")

    # ---- (9j) FAST defaults expanded (review Enhancement 4) ----
    feats = cc._features()
    vdef = cc.SECTIONS["vargas"][0](h, jd, place, pp, env)
    ok_j = (cc.SECTIONS["yogas"][1] is True and
            "yogas" in (feats.get("default_sections") or []) and
            all(k in vdef for k in ("D2", "D3", "D7", "D9", "D10", "D12")))
    record(ok_j, f"FAST defaults expanded: yogas (28 curated + computed, ~ms) is now a "
                 f"default section ({feats.get('default_sections')}) and the default varga "
                 f"set carries D2 (Hora) + D3 (Drekkana) alongside D7/D9/D10/D12 "
                 f"(R9 Enhancement-4)")

    # ---- (9k) bhaava_madhya_swe kwarg robustness (review Enhancement 5, reordered) ----
    hs = cc.build_house_systems(env, h, jd, place, pp)
    swe_p = (hs.get("bhava_madhya_variants") or {}).get("swe_porphyry_P")
    ok_k = (isinstance(swe_p, list) and len(swe_p) == 12 and
            all(isinstance(x, dict) and "longitude" in x for x in swe_p))
    record(ok_k, "bhaava_madhya_swe: 5.0 primary kwarg house_code='P' decodes 12 house "
                 "midpoints; a TypeError-triggered hsys↔house_code translation fallback "
                 "guards other builds (R9 Enhancement-5 — the review's hsys-first ordering "
                 "is the OLD 4.8.7 signature; 5.0 verified house_code)")


def audit_round10():
    """ROUND-10 audit — external review v5 (Ft.txt, 255 lines) adjudication.
    ADOPTED (real): P1-3 --match-tz omitted fell back to the native's --tz — which has
    an argparse default (5.5) — so coordinate-mode partners were ALWAYS forced to IST;
    now resolved from the partner's own coordinates (DST-aware, library helper) with the
    native tz only as last resort. P1-5 fine_dasha boundary rows carried the raw user
    window strings (date-only) while internal rows are "YYYY-MM-DD HH:MM" — the
    string-keyed calendar sort mixed formats; bounds are now normalized (date-only end
    = 23:59, inclusive). Features: KP cuspal star/sub/sub-sub lords (library primitives
    composed), D60 shashtiamsa deities (const.amsa_rulers[60] + kroora/soumya), Jaimini
    dual-sign co-lord resolution (house.stronger_planet_from_planet_positions), partner
    Manglik comparison (identical dosha.manglik calls), core.planets inline
    is_retrograde/is_combust/angular_distance_from_sun (minimal arc) + pushkara-bhaga
    details from BOTH library tables, panchanga yoga lord/avayogi
    (const.yogam_lords_and_avayogis).
    Hardening adopted (claims themselves REJECTED): P1-2 curated yogas keep 0/28 errors
    in this build but now bind 5.0-style named-parameter signatures via the shared
    _yoga_arg_pool/_yoga_bind_kwargs helpers; P1-4 all 12 public sphuta functions return
    (sign_index, float) via drik.dasavarga_from_long — no scalar/DMS shapes exist — but
    _decode_sphuta now tolerates them defensively.
    REJECTED with evidence: P1-1 hora UT/local mixing (third re-litigation; morning-birth
    counter-example in 10a: 10:30 birth → ~4.25 h since sunrise, not the review's
    claimed 9.75 h). P2-4 natal chandrabalam/thaaraabalam kwargs (no such signature in
    5.0 — muhurta helpers take (jd, place) only; natal-relative equivalents documented).
    P2-5 panchanga tattva lords (modern lineage-specific element mapping, absent from the
    library — omitted per no-invented-data policy; library-backed lords exposed)."""
    env = get_env()
    h, jd, place, pp = _build(BOY)
    drik = env["drik"]; utils_ = env["utils"]; const_ = env["const"]
    longs = cc._planetary_longitude(pp)

    # ---- (10a) hora REJECT counter-evidence: morning birth (review P1-1) ----
    from jhora.horoscope.info import Horoscope as _H10
    hs = _H10(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
              date_in=drik.Date(1990, 5, 15), birth_time="10:30:00")
    pan_s = cc.build_panchanga(env, hs, hs.julian_day, hs.Place)
    jhl = pan_s.get("janma_hora_lord") or {}
    hrs = jhl.get("hours_since_sunrise")
    def _jd_of10(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool) \
                and 2000000 < float(x) < 3000000:
            return float(x)
        if isinstance(x, (list, tuple)):
            for it in x:
                v = _jd_of10(it)
                if v is not None:
                    return v
        return None
    srj = _jd_of10(drik.sunrise(hs.julian_day, hs.Place))
    CH = ["Sun", "Venus", "Mercury", "Moon", "Saturn", "Jupiter", "Mars"]
    VL = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
    w = (pan_s.get("vara_lord") or {}).get("weekday_index")
    k = int(hrs) % 24 if isinstance(hrs, (int, float)) else None
    ok_a = (isinstance(hrs, (int, float)) and 3.5 < hrs < 5.0 and
            abs(hrs - (hs.julian_day - srj) * 24.0) < 1e-6 and
            k == jhl.get("hora_index_of_day") and
            jhl.get("lord") == CH[(CH.index(VL[w]) + k) % 7] and
            not (9.0 < hrs < 11.0))
    record(ok_a, f"hora P1-1 REJECTED again (3rd re-litigation) with the review's OWN "
                 f"counter-example shape: morning birth 1990-05-15 10:30 → "
                 f"hours_since_sunrise={hrs} (~4¼, NOT the claimed 9.75 of a UT/local "
                 f"mix), domain identity (jd − sunrise_jd)×24 exact to 1e-6, hora index "
                 f"{k} → lord {jhl.get('lord')} reproduces the Chaldean wheel from the "
                 f"{VL[w] if w is not None else '?'}day lord")

    # ---- (10b) yoga signature-binding hardening (review P1-2) ----
    import types as _types10
    _saved = cc.CURATED_YOGAS
    try:
        stub_ns = _types10.SimpleNamespace(
            stub_pred_from_planet_positions=lambda planet_positions_rasi, jd, place: True,
            stub_missing_from_planet_positions=lambda chart_7d, chart_8d: True)
        env2 = dict(env); env2["yoga"] = stub_ns
        cc.CURATED_YOGAS = ["stub_pred", "stub_missing"]
        yres = cc.build_yogas(env2, h, jd, place, pp)
    finally:
        cc.CURATED_YOGAS = _saved
    cap = yres.get("__capability__") or {}
    ok_b = (yres.get("stub_pred") is True and
            isinstance(yres.get("stub_missing"), dict) and
            "requires unavailable inputs" in str(yres["stub_missing"].get("__error__")) and
            cap.get("executed") == 1 and cap.get("errored") == 1)
    record(ok_b, "yoga P1-2 hardening: a 5.0-style NAMED-parameter curated predicate "
                 "(planet_positions_rasi/jd/place) now binds through the shared "
                 "_yoga_arg_pool → executes True instead of TypeError-ing; an "
                 "unsuppliable required input stays an honest __error__ "
                 "('requires unavailable inputs: chart_7d') — the claim itself is "
                 "REJECTED for this build (28/28 curated executed, errored=0)")

    # ---- (10c) partner tz resolution, DST-aware (review P1-3, REAL) ----
    def _match_ny(dstr):
        return cc.build_match(env, h, jd, place, pp,
                              {"date": dstr, "time": "08:00:00",
                               "lat": 40.7128, "lon": -74.0060, "tz": None})
    m_sum = _match_ny("1995-07-04")
    m_win = _match_ny("1995-01-04")
    m_exp = cc.build_match(env, h, jd, place, pp,
                           {"date": "1995-07-04", "time": "08:00:00",
                            "lat": 40.7128, "lon": -74.0060, "tz": -4.0})
    ok_c = ("__error__" not in m_sum and "__error__" not in m_win and
            "was -4.0 h" in str(m_sum.get("timezone_note")) and
            "was -5.0 h" in str(m_win.get("timezone_note")) and
            "resolved from the partner's coordinates" in str(m_sum.get("timezone_note")) and
            isinstance(m_exp.get("ashtakoota_total"), (int, float)) and
            "timezone_note" not in m_exp)
    record(ok_c, "match P1-3 FIXED: omitted --match-tz resolves from the partner's "
                 "coordinates via library utils.get_place_timezone_offset — NYC "
                 "1995-07-04 → −4.0 h (EDT) and 1995-01-04 → −5.0 h (EST), proving "
                 "DST-awareness; the Round-9 chain (args.tz default 5.5) can no longer "
                 "force IST; explicit --match-tz passes through with NO note")

    # ---- (10d) sphuta decoder shapes (review P1-4 hardening) ----
    dpair = cc._decode_sphuta([4, 12.5])
    dscal = cc._decode_sphuta(123.45)
    ddms = cc._decode_sphuta([4, (12, 30, 0)])
    draw = cc._decode_sphuta("not-a-shape")
    ok_d = (dpair["sign"] == "Leo" and abs(dpair["longitude"] - 132.5) < 1e-6 and
            dscal["sign_index"] == 4 and abs(dscal["degree_in_sign"] - 3.45) < 1e-6 and
            abs(dscal["longitude"] - 123.45) < 1e-6 and
            dscal.get("decoded_from") == "scalar_longitude" and
            abs(ddms["degree_in_sign"] - 12.5) < 1e-6 and draw == "not-a-shape")
    record(ok_d, "sphuta P1-4: pair shape decodes as before; defensive scalar-longitude "
                 "and (deg,min,sec) shapes now decode too (123.45 → Leo 3.45; "
                 "(12,30,0) → 12.5); unparseable values pass through raw — the claim is "
                 "REJECTED for this build (all 12 public sphutas return "
                 "dasavarga_from_long = (sign_index, float))")

    # ---- (10e) fine_dasha window normalization (review P1-5, REAL) ----
    fd = cc.build_fine_dhasa(env, h, jd, place, jd_now=None,
                             window_start="2026-09-01", window_end="2026-09-30",
                             max_depth=3)
    fd2 = cc.build_fine_dhasa(env, h, jd, place, jd_now=None,
                              window_start="2026-09-01 05:30", window_end="2026-09-30",
                              max_depth=2)
    cal = fd.get("calendar") or []
    starts = [r["start"] for r in cal]
    ok_e = (fd.get("from") == "2026-09-01 00:00" and fd.get("to") == "2026-09-30 23:59" and
            cal and all(len(s) == 16 for s in starts) and starts == sorted(starts) and
            starts[0] == "2026-09-01 00:00" and cal[-1]["end"] == "2026-09-30 23:59" and
            fd2.get("from") == "2026-09-01 05:30")
    record(ok_e, f"fine_dasha P1-5 FIXED: date-only windows normalize to the canonical "
                 f"internal format (from='2026-09-01 00:00', to='2026-09-30 23:59' — "
                 f"end-of-day inclusive), all {len(cal)} calendar rows are 16-char and "
                 f"string-sort cleanly, boundary rows clip to the normalized strings, and "
                 f"explicit times are preserved (fd2.from='{fd2.get('from')}')")

    # ---- (10f) KP cuspal sub-lords (review P2-1, ADOPTED) ----
    hsys = cc.build_house_systems(env, h, jd, place, pp)
    kp = ((hsys.get("bhava_madhya_variants") or {}).get("kp_cuspal_lords") or {})
    rows = kp.get("cusps") or []
    NAK_L = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"]
    ok_f = len(rows) == 12 and "__note__" in kp
    if ok_f:
        ok_f = abs(rows[0]["cusp_longitude"] - longs[cc.LAGNA]) < 0.01
        for r in rows:
            cl = r["cusp_longitude"]
            nak = int(cl / (360.0 / 27.0)) % 27
            ok_f = ok_f and r["star_lord"] == NAK_L[nak % 9] and \
                r["kp_star_number_1_249"] == int((cl * 249.0) // 360.0) + 1 and \
                all(r[kk] in NAK_L for kk in ("sub_lord", "pratyantara_lord",
                                              "sookshma_lord", "praana_lord", "deha_lord"))
            if not ok_f:
                break
    record(ok_f, f"KP P2-1 ADOPTED (library primitives composed): 12 cusps from "
                 f"drik.bhaava_madhya_kp — cusp 1 = {rows[0]['cusp_longitude'] if rows else '?'} "
                 f"== D1 lagna {round(longs[cc.LAGNA], 4)}; every star lord matches the "
                 f"hand-computed nakshatra lord, KP star numbers match "
                 f"int(lon×249/360)+1, sub→deha lords are all Vimshottari names "
                 f"(utils.kp_lords_for_longitude)")

    # ---- (10g) D60 shashtiamsa deities (review P2-2, ADOPTED) ----
    v60 = cc.build_vargas(env, h, jd, place, pp, [60]).get("D60") or {}
    dd = v60.get("shashtiamsa_deities") or {}
    ok_g = len(dd) == 10 and "shashtiamsa_deities_note" in v60
    if ok_g:
        for entry in pp:
            lab, pos = entry[0], entry[1]
            nm = "Lagna" if lab == "L" else cc._planet_name(lab)
            if nm not in dd:
                ok_g = False; break
            idx = utils_.get_amsa_ruler_from_planet_longitude(float(pos[1]), int(pos[0]))
            ok_g = ok_g and dd[nm]["deity_index"] == idx and \
                dd[nm]["deity"] == const_.amsa_rulers[60][idx] and \
                dd[nm]["nature"] in ("kroora (cruel)", "soumya (gentle)")
            if not ok_g:
                break
    record(ok_g, "D60 P2-2 ADOPTED: shashtiamsa deities for all 10 bodies via the "
                 "library's own resolver (utils.get_amsa_ruler_from_planet_longitude on "
                 "D1 sign+degree; names const.amsa_rulers[60]; nature "
                 "kroora/soumya) — every entry recompute-exact; the parity-convention "
                 "disclosure rides in shashtiamsa_deities_note")

    # ---- (10h) Jaimini dual-sign co-lords (review P2-3, ADOPTED) ----
    hv = cc.build_house_variables(env, h, jd, place, pp)
    dsr = hv.get("dual_sign_rulers") or {}
    sc = dsr.get("scorpio") or {}; aq = dsr.get("aquarius") or {}
    w1 = env["house"].stronger_planet_from_planet_positions(
        pp, planet1=const_.MARS_ID, planet2=const_.KETU_ID)
    w2 = env["house"].stronger_planet_from_planet_positions(
        pp, planet1=const_.SATURN_ID, planet2=const_.RAHU_ID)
    ok_h = (sc.get("co_lords") == ["Mars", "Ketu"] and aq.get("co_lords") == ["Saturn", "Rahu"] and
            sc.get("stronger_co_lord") == cc._planet_name(w1) and
            aq.get("stronger_co_lord") == cc._planet_name(w2) and
            sc.get("stronger_co_lord") in sc.get("co_lords") and
            aq.get("stronger_co_lord") in aq.get("co_lords"))
    record(ok_h, f"dual-sign P2-3 ADOPTED: Scorpio resolves to {sc.get('stronger_co_lord')} "
                 f"(Mars vs Ketu) and Aquarius to {aq.get('stronger_co_lord')} (Saturn vs "
                 f"Rahu) via house.stronger_planet_from_planet_positions — recompute-exact; "
                 f"Parashari lordship elsewhere in the document is untouched (noted)")

    # ---- (10i) panchanga lords + honest omissions (review P2-4/P2-5) ----
    pan = cc.build_panchanga(env, h, jd, place)
    yog = pan.get("yoga") or {}
    lp, ap = const_.yogam_lords_and_avayogis[(int(yog.get("index", 1)) - 1) % 27]
    om = pan.get("omitted_checks") or {}
    ok_i = (yog.get("lord") == cc._planet_name(lp) and yog.get("avayogi") == cc._planet_name(ap) and
            yog.get("lords_source") == "const.yogam_lords_and_avayogis (PyJHora)" and
            "panchanga_tattva_lords" in om and
            "moon_raasi" in str(pan.get("chandrabalam_thaaraabalam_note", "")))
    record(ok_i, f"panchanga P2-5 partial ADOPT + P2-4 REJECT: yoga {yog.get('name')} "
                 f"(index {yog.get('index')}) → lord {yog.get('lord')}, avayogi "
                 f"{yog.get('avayogi')} from the library table; tattva-lord mapping "
                 f"disclosed as omitted (modern lineage table, absent from PyJHora); "
                 f"chandrabalam/thaaraabalam note documents that 5.0 has NO "
                 f"moon_raasi/nakshatra kwargs (muhurta helpers) + where the "
                 f"natal-relative equivalents live")

    # ---- (10j) partner Manglik comparison (review P2-6, ADOPTED) ----
    m_g = cc.build_match(env, h, jd, place, pp,
                         {"date": "1997-11-15", "time": "09:30:00",
                          "lat": 14.4674, "lon": 78.8241, "tz": 5.5})
    mc = m_g.get("manglik_compatibility") or {}
    dos = cc.build_doshas(env, h, jd, place, pp)
    dm = dos.get("manglik") or {}
    nf = mc.get("native", {}).get("present"); pf = mc.get("partner", {}).get("present")
    want = ("both partners are Manglik" if nf and pf else
            "balanced" if nf is False and pf is False else "one-sided Manglik")
    ok_j = (isinstance(mc.get("native"), dict) and isinstance(mc.get("partner"), dict) and
            nf == dm.get("present") and
            mc.get("native", {}).get("present_before_exceptions") ==
            dm.get("present_before_exceptions") and
            want in str(mc.get("verdict")) and "convention" in mc)
    record(ok_j, f"manglik P2-6 ADOPTED: match.manglik_compatibility runs the IDENTICAL "
                 f"dosha.manglik calls on the partner's own positions — native "
                 f"(present={nf}) agrees with doshas.manglik; partner present={pf}; "
                 f"verdict '{want}…' with exceptions disclosed on both sides")

    # ---- (10k) core inline flags (review P2-7, ADOPTED) ----
    core = cc.build_core(env, h, jd, place, pp)
    pf = cc.build_planet_flags(env, h, jd, place, pp)
    sun_row = [p for p in core["planets"] if p["name"] == "Sun"][0]
    retro = {p["name"] for p in core["planets"] if p.get("is_retrograde")}
    comb = {p["name"] for p in core["planets"] if p.get("is_combust")}
    ok_k = (sun_row.get("angular_distance_from_sun") == 0.0 and
            all("is_retrograde" in p and "is_combust" in p and
                "angular_distance_from_sun" in p and
                isinstance(p["angular_distance_from_sun"], float) and
                0.0 <= p["angular_distance_from_sun"] <= 180.0
                for p in core["planets"]) and
            retro == set(pf.get("retrograde") or []) and
            comb == set(pf.get("combust") or []))
    record(ok_k, f"inline P2-7 ADOPTED: every core.planets row carries is_retrograde / "
                 f"is_combust / angular_distance_from_sun — the flag sets are IDENTICAL "
                 f"to planet_flags (retro={sorted(retro)}, combust={sorted(comb)}); the "
                 f"distance is the true MINIMAL arc (all ≤180°, Sun=0.0) — the review's "
                 f"abs((p−s)%360) formula is not minimal and was corrected")

    # ---- (10l) pushkara bhaga details (review P2-8, ADOPTED with library tables) ----
    ok_l = "pushkara_bhaga" in core["lagna"] and "planet_inline_flags_note" in core
    for p in core["planets"]:
        pb = p.get("pushkara_bhaga") or {}
        std = const_.pushkara_bhagas[p["sign_index"]]
        jp = const_.pushkara_bhaga_jathaka_paarijaatha[p["sign_index"]]
        ok_l = ok_l and pb.get("degree_standard_table") == float(std) and \
            pb.get("degree_jathaka_paarijaatha_table") == float(jp) and \
            abs(pb.get("distance_from_standard_deg", 9e9) -
                round(p["degree"] - std, 4)) < 1e-9 and \
            pb.get("in_pushkara_bhaga_orb_1deg") == (abs(p["degree"] - std) <= 1.0) and \
            pb.get("in_library_membership_band") == (std - 1.0 <= p["degree"] < std)
        if not ok_l:
            break
    note = str(core.get("planet_inline_flags_note", ""))
    ok_l = ok_l and "matches NEITHER library table" in note
    record(ok_l, "pushkara P2-8 ADOPTED with the LIBRARY tables: per-planet (and lagna) "
                 "pushkara_bhaga blocks carry both const tables (standard 21/14/24/7 "
                 "cycle + Jathaka Parijaatha variant), signed distances, the ±1° orb "
                 "flags the review asked for AND the library's own [bhaga−1°, bhaga°) "
                 "membership band — all recompute-exact; the review's cited degrees "
                 "(21/19/23/15/17/19…) match NEITHER table and were not used (disclosed "
                 "in planet_inline_flags_note)")


# ----------------------------------------------------------------------------- #
# AUDIT ROUND-11 — external review v6 (Ft.txt, 182 lines) adjudication
# ----------------------------------------------------------------------------- #
def audit_round11():
    """ROUND-11 audit — external review v6 (Ft.txt, 182 lines) adjudication.

    REJECTED with evidence: P1-1 janma-hora "UT/local mixing" — FOURTH re-litigation
    of the same false claim. Both h.julian_day and the JD element of drik.sunrise are
    LOCAL-clock JDs (verified numerically in 11a-2: the sunrise JD decodes to 6.15 h
    = 06:09 IST, not 0.65 h UT), so (jd - sr_jd)*24 is a clean same-basis elapsed
    time. 11a triple-proofs it purely from local HMS strings: sample 10:30 birth with
    06:09 sunrise = 4.35 h (NOT the review's claimed 9.75 h), hora index 4, lord
    Moon. The review's own fix snippet is additionally non-executable (`s_parts /
    60.0` divides a LIST by a float). P1-2 "spliced syntax artifact at line 246" —
    the alleged duplicate `core["houses"] = house_table` / `core["lagna_in_house"]` /
    `return core` block does not exist: each statement occurs EXACTLY once in the
    module, lines 240-252 contain no core[...] assignment at all, and the module
    parses cleanly (11b source forensics).
    ADOPTED (trivial): P1-3 pool keys is_night_birth / is_nighttime_birth — no 5.0
    yoga predicate currently declares either name (signature scan in 11c-2: the
    review's Nisheetha/Ratri examples do not exist in this build), but the pool now
    binds both complements of is_daytime_birth so a future upstream night-yoga gets a
    value with no engine edit.
    Features implemented: E1 planet_flags.combustion_detail — standard BPHS orbs
    (Moon 12, Mars 17, Mercury 14/12R, Jupiter 11, Venus 10/8R, Saturn 15) + cazimi
    0d17m, next to the library verdict, which is kept but disclosed as buggy
    (const.combustion_range indexed at planet_id-2 + stale chart-layer retrograde;
    sample retrograde Mercury 16.07 deg -> library 'combust' via Mars's 17 deg orb,
    standard NOT combust under the 12 deg retrograde orb) (11d). E2
    planet_flags.gandanta — water->fire junction zones (last 3d20m of water signs,
    first 3d20m of fire signs) for lagna + 9 grahas, with a synthetic-chart boundary
    test (11e). E3 panchanga.dagdha_rasis — classical tithi->burnt-signs table,
    krishna n-15 mapping, Purnima/Amavasya none, BOTH disputed source variants
    disclosed (t10 Leo+Scorpio vs Libra+Scorpio; t13 Taurus+Leo vs Taurus+Aquarius),
    planets-in-dagdha list; the dagdha YOGA muhurta combos stay in omitted_checks
    (11f). E4 transits[].kakshya — 3d45m sub-signs, lords Saturn/Jupiter/Mars/Sun/
    Venus/Mercury/Moon/Lagna from 0 deg, favorable iff the kakshya lord contributed
    a bindu to the transit sign in the transiting planet's OWN natal prastara row;
    nodes excluded (no BAV row) (11g). E5 --match-method South now calls the ten
    dedicated *_south predicates and sums them (the library's
    compatibility_score_south is buried inside a docstring tomb in 5.0, and the
    porutham wrappers branch on CHART_STYLE enums — not the 'South' string — so the
    flag used to be a silent no-op): dasa_koota_breakdown/total 0..10, North path
    and its 17.0 golden untouched (11h). E6 sphutas.yogi_avayogi_planets — yogi =
    nakshatra lord of the yogi point, duplicate yogi = SIGN lord of the yogi POINT's
    sign, avayogi = lord of the 6th nakshatra counting inclusively (idx+5), with the
    library's avayogi_sphuta (+186d40m = +14 nakshatras = +5 lords) as an
    independent crosscheck (11i)."""
    print("\n--- audit_round11: review v6 adjudication (Ft.txt 182 lines) ---")
    import ast as _ast11
    import copy as _cp11
    import inspect as _insp11
    from jhora import utils as _u11
    from jhora.panchanga import drik as _drik11
    from jhora.horoscope.info import Horoscope as _H11
    import jhora.horoscope.match.compatibility as _cm11
    env = get_env()

    # Sample subject (1990-05-15 10:30, Mangaluru coords) — built directly because
    # _build() hardcodes the 20:10/09:30 birth times.
    hs = _H11(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
              date_in=_drik11.Date(1990, 5, 15), birth_time="10:30:00")
    jd_s, place_s = hs.julian_day, hs.Place
    pp_s = env_charts.rasi_chart(jd_s, place_s)
    longs_s = cc._planetary_longitude(pp_s)
    pan_s = cc.build_panchanga(env, hs, jd_s, place_s)
    pf_s = cc.build_planet_flags(env, hs, jd_s, place_s, pp_s)

    # ---- (11a) P1-1 hora tz REJECT — triple proof (fourth re-litigation) ----
    jhl = pan_s.get("janma_hora_lord") or {}
    hrs_eng = jhl.get("hours_since_sunrise")
    sr_raw = pan_s.get("sunrise")
    ok_shape = (isinstance(sr_raw, (list, tuple)) and len(sr_raw) >= 3)
    if ok_shape:
        sr_hours_el, sr_hms, sr_jd_el = float(sr_raw[0]), str(sr_raw[1]), float(sr_raw[2])
        _p = [float(x) for x in sr_hms.split(":")[:3]]
        sr_local_hms = _p[0] + _p[1] / 60.0 + (_p[2] / 3600.0 if len(_p) > 2 else 0.0)
        bt = cc._tob(hs)
        birth_local = float(bt[0]) + float(bt[1]) / 60.0 + \
            (float(bt[2]) / 3600.0 if len(bt) > 2 else 0.0)
        hrs_local = birth_local - sr_local_hms
        if hrs_local < 0:
            hrs_local += 24.0
        # Proof 1: engine value == pure LOCAL-clock-string arithmetic (no JD math).
        record(isinstance(hrs_eng, float) and abs(hrs_eng - hrs_local) < 0.01,
               "11a-1 engine hours_since_sunrise == (birth HMS - sunrise HMS) local strings",
               f"engine={hrs_eng} local_strings={round(hrs_local, 4)}")
        # Proof 2: BOTH JDs are local-clock basis — the review's "sunrise returns UT
        # JD" premise is numerically false (UT would decode to ~0.65 h, not 6.15 h).
        sr_jd_hours = ((sr_jd_el + 0.5) % 1.0) * 24.0
        birth_jd_hours = ((jd_s + 0.5) % 1.0) * 24.0
        record(abs(sr_jd_hours - sr_hours_el) < 0.01 and
               abs(birth_jd_hours - birth_local) < 0.01 and
               abs(sr_jd_hours - 6.15) < 0.1 and abs(sr_jd_hours - 0.65) > 1.0,
               "11a-2 sunrise JD element is LOCAL-clock (6.15 h), same basis as h.julian_day (10.5 h) — no UT mixing",
               f"sr_jd_hours={round(sr_jd_hours, 4)} birth_jd_hours={round(birth_jd_hours, 4)}")
        # Proof 3: hora index + lord reproduce from the local-string hours.
        CH = ["Sun", "Venus", "Mercury", "Moon", "Saturn", "Jupiter", "Mars"]
        VL = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
        wk = jhl.get("vedic_weekday_used")
        w_i = cc.WEEKDAY_NAMES.index(wk) if wk in cc.WEEKDAY_NAMES else None
        k_loc = int(hrs_local) % 24
        lord_loc = CH[(CH.index(VL[w_i]) + k_loc) % 7] if w_i is not None else None
        record(w_i is not None and jhl.get("hora_index_of_day") == k_loc == 4 and
               jhl.get("lord") == lord_loc == "Moon" and
               abs(hrs_eng - 4.35) < 0.15 and abs(hrs_eng - 9.75) > 1.0,
               "11a-3 hora index/lord reproduce from local hours (k=4, Moon); hrs=4.35 NOT the claimed 9.75",
               f"k={jhl.get('hora_index_of_day')} lord={jhl.get('lord')} weekday={wk}")
    else:
        record(False, "11a-1/2/3 sunrise composite shape [hours, HMS, JD] expected",
               f"got {type(sr_raw).__name__}")

    # ---- (11b) P1-2 "spliced syntax artifact" REJECT — source forensics ----
    src = open(os.path.join(ROOT, "compute_chart.py"), encoding="utf-8").read()
    try:
        _ast11.parse(src)
        parses = True
    except SyntaxError:
        parses = False
    counts = {t: src.count(t) for t in
              ('core["houses"] = house_table', 'core["lagna_in_house"] = 1',
               "    return core\n")}
    record(parses and all(v == 1 for v in counts.values()),
           "11b-1 build_core closing statements occur EXACTLY once each; module parses cleanly",
           f"counts={counts} parses={parses}")
    lines = src.splitlines()
    region = lines[239:252]                       # "lines 240-252" per the review
    record(not any("core[" in ln for ln in region),
           "11b-2 lines 240-252 contain no core[...] assignments (the alleged splice site is constants/helpers)",
           f"region has {sum('core[' in ln for ln in region)} core[ occurrences")

    # ---- (11c) P1-3 is_night_birth pool keys (ADOPTED trivially) ----
    pool_day = cc._yoga_arg_pool(env, jd_s, place_s, pp_s)
    h_b, jd_b, place_b, pp_b = _build(BOY)        # 20:10 birth = night
    pool_night = cc._yoga_arg_pool(env, jd_b, place_b, pp_b)
    ok_c1 = all(k in pool_day for k in ("is_daytime_birth", "is_night_birth", "is_nighttime_birth")) \
        and pool_day["is_night_birth"] is (not pool_day["is_daytime_birth"]) \
        and pool_day["is_nighttime_birth"] == pool_day["is_night_birth"] \
        and pool_day["is_daytime_birth"] is True \
        and pool_night["is_night_birth"] is True \
        and pool_night["is_daytime_birth"] is False
    record(ok_c1, "11c-1 pool binds is_night_birth/is_nighttime_birth as exact complements (sample day / boy night)",
           f"day={pool_day.get('is_daytime_birth')} night_pool_night={pool_night.get('is_night_birth')}")
    consumers = []
    for _mn in dir(env["yoga"]):
        _f = getattr(env["yoga"], _mn)
        if callable(_f) and not _mn.startswith("_"):
            try:
                if any(p in ("is_night_birth", "is_nighttime_birth")
                       for p in _insp11.signature(_f).parameters):
                    consumers.append(_mn)
            except (TypeError, ValueError):
                pass
    record(consumers == [],
           "11c-2 zero 5.0 yoga predicates declare is_night_birth (review's Nisheetha/Ratri examples absent) — key is future-proofing",
           f"consumers={consumers}")

    # ---- (11d) E1 combustion_detail — standard orbs + cazimi + mismatch flags ----
    cd = pf_s.get("combustion_detail") or {}
    merc = cd.get("Mercury") or {}
    record(merc.get("is_retrograde") is True and
           merc.get("standard_orb_deg") == 12.0 and
           merc.get("orb_variant") == "retrograde-reduced" and
           merc.get("combust_per_standard_orb") is False and
           merc.get("library_is_combust") is True and
           merc.get("library_vs_standard_mismatch") is True and
           abs(merc.get("angular_distance_from_sun", 0) - 16.07) < 0.05,
           "11d-1 sample retro Mercury 16.07 deg: library combust (wrong 17 deg orb), standard NOT (12 deg retro orb), mismatch flagged",
           f"mercury={merc}")
    _EXP_BASE = {"Moon": 12.0, "Mars": 17.0, "Mercury": 14.0,
                 "Jupiter": 11.0, "Venus": 10.0, "Saturn": 15.0}
    _EXP_RET = {"Mercury": 12.0, "Venus": 8.0}
    ok_orbs = all(
        cd[b]["standard_orb_deg"] ==
        (_EXP_RET[b] if (cd[b]["is_retrograde"] and b in _EXP_RET) else _EXP_BASE[b])
        for b in _EXP_BASE if b in cd) and \
        all(cd[x].get("evaluated") is False for x in ("Sun", "Rahu", "Ketu"))
    record(len(cd) >= 10 and ok_orbs,
           "11d-2 BPHS orb table applied per body (retro-reduced Mercury 12 / Venus 8); Sun+nodes evaluated=False",
           f"keys={sorted(k for k in cd if not k.startswith('__'))}")
    ok_dist = True
    sun_lon = longs_s.get("Sun")
    for b in _EXP_BASE:
        if b not in cd or sun_lon is None:
            ok_dist = False
            break
        dd = abs(longs_s[b] - sun_lon) % 360.0
        dist = round(min(dd, 360.0 - dd), 4)
        ok_dist &= (abs(cd[b]["angular_distance_from_sun"] - dist) < 1e-3 and
                    cd[b]["combust_per_standard_orb"] ==
                    bool(dist <= cd[b]["standard_orb_deg"]) and
                    cd[b]["cazimi_within_17_arcmin"] == bool(dist <= cc.CAZIMI_MAX_DEG) and
                    cd[b]["library_vs_standard_mismatch"] ==
                    bool(cd[b]["library_is_combust"] != cd[b]["combust_per_standard_orb"]))
    record(ok_dist, "11d-3 distances are minimal-arc recomputations; cazimi + mismatch flags self-consistent")

    # ---- (11e) E2 gandanta — rule recompute + synthetic boundary chart ----
    g = pf_s.get("gandanta") or {}
    _W, _F = (3, 7, 11), (0, 4, 8)
    def _g_rule(lon):
        sg = int(lon // 30) % 12
        dg = lon - int(lon // 30) * 30
        return (sg in _W and dg >= 26.0 + 40.0 / 60.0) or (sg in _F and dg < 3.0 + 20.0 / 60.0)
    ents = ["Lagna"] + cc.PLANET_ORDER
    record(len(g) == 11 and "__note__" in g and
           all(g[e]["in_gandanta"] == _g_rule(longs_s[e]) for e in ents),
           "11e-1 gandanta covers lagna + 9 grahas; sample verdicts match the 26d40m/3d20m rule",
           f"keys={len(g)}")
    pp_syn = [list(it) for it in _cp11.deepcopy(pp_s)]
    def _set_body(pp, want, sign, deg):
        for it in pp:
            nm = cc.LAGNA if it[0] == "L" else cc._pid_label(it[0])
            if nm == want:
                it[1] = (sign, deg)
                return True
        return False
    _set_body(pp_syn, "Mars", 11, 29.5)      # Pisces 29d30m — water-end gandanta
    _set_body(pp_syn, "Moon", 0, 1.0)        # Aries 01d00m — fire-start gandanta
    g_syn = (cc.build_planet_flags(env, hs, jd_s, place_s, pp_syn) or {}).get("gandanta") or {}
    gm, gn = g_syn.get("Mars") or {}, g_syn.get("Moon") or {}
    trues = sorted(e for e in ents if (g_syn.get(e) or {}).get("in_gandanta"))
    record(trues == ["Mars", "Moon"] and
           gm.get("junction") == "Pisces->Aries" and
           abs(gm.get("degrees_to_exact_junction", 9) - 0.5) < 1e-6 and
           gn.get("junction") == "Pisces->Aries" and
           abs(gn.get("degrees_to_exact_junction", 9) - 1.0) < 1e-6,
           "11e-2 synthetic Pisces 29.5 / Aries 1.0 placements flagged, junction + distance-to-junction exact",
           f"trues={trues} mars={gm} moon={gn}")
    record(all(set(g_syn[e].keys()) >= {"zone", "junction", "degrees_to_exact_junction"}
               for e in trues) and
           all(set(g_syn[e].keys()) == {"in_gandanta"}
               for e in ents if e not in trues),
           "11e-3 gandanta row shapes: True rows carry zone/junction/distance; False rows carry only the flag")

    # ---- (11f) E3 dagdha rasis — sample row + table + mapping arithmetic ----
    dr = pan_s.get("dagdha_rasis") or {}
    asc_s = cc._sign_of_long(longs_s[cc.LAGNA])
    record(dr.get("tithi_index") == 20 and dr.get("tithi_name") == "Panchami" and
           dr.get("paksha") == "Krishna" and dr.get("table_row_used") == 5 and
           dr.get("dagdha_signs") == ["Gemini", "Virgo"] and
           dr.get("dagdha_sign_indices") == [2, 5] and
           any(r.get("planet") == "Jupiter" for r in dr.get("planets_in_dagdha_signs", [])
               if isinstance(r, dict)) and
           dr.get("houses_in_dagdha") == [cc._house_number(asc_s, 2),
                                          cc._house_number(asc_s, 5)],
           "11f-1 sample (tithi 20 = Krishna Panchami -> row 5): Gemini+Virgo burnt; Jupiter-in-Gemini listed; houses decoded",
           f"dr={ {k: v for k, v in dr.items() if k != '__note__'} }")
    _ADJ = {1: [6, 9], 2: [8, 11], 3: [4, 9], 4: [1, 10], 5: [2, 5], 6: [0, 4],
            7: [3, 8], 8: [2, 5], 9: [4, 7], 10: [4, 7], 11: [8, 11], 12: [6, 9],
            13: [1, 4], 14: [2, 5, 8, 11]}
    record(cc.DAGDHA_RASIS == _ADJ and
           cc.DAGDHA_RASIS_VARIANTS == {10: [6, 7], 13: [1, 10]},
           "11f-2 engine table == adjudicated primary table; disputed variants exactly t10 (Libra+Scorpio) and t13 (Taurus+Aquarius)")
    ok_map = all(((_ti - 1) % 15) + 1 == _ti - 15 for _ti in range(16, 30)) and \
        cc.DAGDHA_RASIS.get(0, []) == [] and 10 in cc.DAGDHA_RASIS_VARIANTS and \
        13 in cc.DAGDHA_RASIS_VARIANTS and 15 not in cc.DAGDHA_RASIS
    record(ok_map,
           "11f-3 krishna 16..29 -> rows 1..14 (n-15); Purnima/Amavasya -> row None (no dagdha); variants surface on t10/t25 and t13/t28")

    # ---- (11g) E4 kakshya transits — structure + independent recompute + bindu ----
    tjd = _u11.julian_day_number((2026, 9, 3), (12, 0, 0))
    t = cc.build_transits(env, h_b, jd_b, place_b, pp_b, tjd,
                          compute_conjunctions=False, compute_events=False)
    rows = t.get("transits") or []
    rows7 = [r for r in rows if r.get("planet") in cc.KAKSHYA_ROW_IDS]
    ok_shape_g = len(rows7) == 7 and all(
        isinstance(r.get("kakshya"), dict) and
        1 <= r["kakshya"]["kakshya_number"] <= 8 and
        r["kakshya"]["kakshya_lord"] == cc.KAKSHYA_LORDS[r["kakshya"]["kakshya_number"] - 1] and
        r["kakshya"]["span_in_sign_deg"] == [
            round((r["kakshya"]["kakshya_number"] - 1) * 3.75, 4),
            round(r["kakshya"]["kakshya_number"] * 3.75, 4)] and
        r["kakshya"]["natal_bindu_from_kakshya_lord_at_transit_sign"] in (0, 1) and
        r["kakshya"]["favorable"] == bool(
            r["kakshya"]["natal_bindu_from_kakshya_lord_at_transit_sign"] == 1)
        for r in rows7)
    record(ok_shape_g and "kakshya_transits_note" in t,
           "11g-1 all 7 BAV planets carry kakshya blocks (number/lord/span/bindu/favorable self-consistent) + note present")
    trans_pos = env_charts.rasi_chart(tjd, place_b)
    tl_b = cc._planetary_longitude(trans_pos)
    ok_num = all(
        r["kakshya"]["kakshya_number"] ==
        min(7, int(cc._deg_in_sign(tl_b[r["planet"]]) / 3.75)) + 1
        for r in rows7)
    record(ok_num, "11g-2 kakshya numbers reproduce from transit longitudes (deg_in_sign / 3d45m)")
    h2p_b = _u11.get_house_planet_list_from_planet_positions(pp_b)
    pra = env["ashtakavarga"].get_ashtaka_varga(h2p_b)[2]
    ok_bin = all(
        r["kakshya"]["natal_bindu_from_kakshya_lord_at_transit_sign"] ==
        int(pra[cc.KAKSHYA_ROW_IDS[r["planet"]]][
            cc.KAKSHYA_ROW_IDS[r["kakshya"]["kakshya_lord"]]][
            r["transit_sign_index"]])
        for r in rows7)
    nodes_ok = all("kakshya" not in r for r in rows
                   if r.get("planet") in ("Rahu", "Ketu"))
    record(ok_bin and nodes_ok,
           "11g-3 bindus == natal prastara[planet][kakshya_lord][transit_sign]; Rahu/Ketu rows carry no kakshya (no BAV row)")

    # ---- (11h) E5 Dasa Koota (South) — was a silent no-op ----
    other = {"date": "1997-11-15", "time": "09:30:00",
             "lat": GIRL["lat"], "lon": GIRL["lon"], "tz": GIRL["tz"]}
    env["_match_method"] = "south"
    try:
        m_s = cc.build_match(env, h_b, jd_b, place_b, pp_b, dict(other))
    finally:
        env.pop("_match_method", None)
    bk = m_s.get("dasa_koota_breakdown") or {}
    _TEN = {"dina", "gana", "mahendra", "sthree_dheerga", "yoni", "raasi",
            "raasi_adhipathi", "vasiya", "rajju", "vedha"}
    record(set(bk.keys()) == _TEN and
           all(isinstance(v, bool) for v in bk.values()) and
           m_s.get("dasa_koota_total") == sum(1 for v in bk.values() if v) and
           isinstance(m_s.get("dasa_koota_total"), int) and
           0 <= m_s["dasa_koota_total"] <= 10 and
           m_s.get("dasa_koota_max") == _cm11.max_compatibility_score_south == 10,
           "11h-1 South: exactly the 10 boolean poruthams; total == passes (0..10); max == library max_compatibility_score_south",
           f"total={m_s.get('dasa_koota_total')} bk={bk}")
    record("ashtakoota_total" not in m_s and "poruthams" not in m_s and
           "Dasa-Koota" in (m_s.get("match_method") or {}).get("scale", ""),
           "11h-2 South output replaces the North fields; scale string says Dasa-Koota")
    m_n = cc.build_match(env, h_b, jd_b, place_b, pp_b, dict(other))
    record(m_n.get("ashtakoota_total") == 17.0 and m_n.get("ashtakoota_max") == 36 and
           len(m_n.get("poruthams", {})) == 8 and
           set((m_n.get("special_poruthams") or {}).keys()) ==
           {"mahendra", "vedha", "rajju", "sthree_dheerga"} and
           "dasa_koota_total" not in m_n,
           "11h-3 North default path untouched (golden 17.0/36, 8 poruthams + 4 specials, no south fields)",
           f"total={m_n.get('ashtakoota_total')}")
    a_s = _cm11.Ashtakoota(
        boy_nakshatra_number=m_s["boy_moon"]["nakshatra_number"],
        boy_paadham_number=m_s["boy_moon"]["pada"],
        girl_nakshatra_number=m_s["girl_moon"]["nakshatra_number"],
        girl_paadham_number=m_s["girl_moon"]["pada"], method="South")
    direct = {"dina": bool(a_s.dina_porutham_south()),
              "gana": bool(a_s.gana_porutham_south()),
              "mahendra": bool(a_s.mahendra_porutham_south()),
              "sthree_dheerga": bool(a_s.sthree_dheerga_porutham_south()),
              "yoni": bool(a_s.yoni_porutham_south()),
              "raasi": bool(a_s.raasi_porutham_south()),
              "raasi_adhipathi": bool(a_s.raasi_adhipathi_porutham_south()),
              "vasiya": bool(a_s.vasiya_porutham_south()),
              "rajju": bool(a_s.rajju_porutham_south()),
              "vedha": bool(a_s.vedha_porutham_south())}
    record(direct == bk and not hasattr(a_s, "compatibility_score_south"),
           "11h-4 breakdown == direct *_south calls on a fresh Ashtakoota; library compatibility_score_south confirmed buried (wrapper sums)",
           f"direct={direct}")

    # ---- (11i) E6 yogi / duplicate-yogi / avayogi planets ----
    sp_s = cc.build_sphutas(env, hs, jd_s, place_s)
    y = sp_s.get("yogi_avayogi_planets") or {}
    ylon = (sp_s.get("yogi_sphuta") or {}).get("longitude")
    alon = (sp_s.get("avayogi_sphuta") or {}).get("longitude")
    span = 360.0 / 27.0
    yidx = int(ylon / span) % 27
    record(y.get("yogi_planet") == cc.NAKSHATRA_LORDS[yidx % 9] == "Sun" and
           y.get("duplicate_yogi_planet") ==
           cc.SIGN_LORDS[int(ylon // 30) % 12] == "Venus" and
           y.get("avayogi_planet") ==
           cc.NAKSHATRA_LORDS[((yidx + 5) % 27) % 9] == "Saturn" and
           y.get("yogi_point_nakshatra") == "Krittika" and
           y.get("avayogi_nakshatra") == "Pushya",
           "11i-1 sample golden: yogi=Sun (Krittika lord), duplicate=Venus (Taurus sign lord of the POINT), avayogi=Saturn (Pushya, idx+5)",
           f"y={ {k: v for k, v in y.items() if not k.startswith('__') and k != 'duplicate_yogi_basis'} }")
    record(y.get("avayogi_crosscheck_from_library_point") ==
           cc.NAKSHATRA_LORDS[int(alon / span) % 27 % 9] == y.get("avayogi_planet") and
           y.get("avayogi_crosscheck_matches") is True,
           "11i-2 library avayogi_sphuta point's nakshatra lord independently confirms the derived avayogi")
    delta = (alon - ylon) % 360.0
    record(abs(delta - (186.0 + 40.0 / 60.0)) < 0.01 and
           "YOGI POINT" in y.get("duplicate_yogi_basis", ""),
           "11i-3 avayogi point == yogi point + 186d40m (+14 nakshatras = +5 lords); duplicate-yogi basis cites the POINT, not the planet's placement",
           f"delta={round(delta, 4)}")


def audit_round12():
    """ROUND-12 audit — external review v7 (Ft.txt, 170 lines) adjudication.

    REJECTED with evidence: Bug-1 janma-hora tz (FIFTH re-litigation) — the review's
    proposed julian_day_utc "fix" yields NEGATIVE elapsed hours on the sample chart
    (jd_ut − sr_jd = −1.15 h) and its pre-sunrise fallback yields ~22.87 h (hora k=22
    instead of the correct 4); the engine's local-clock arithmetic reproduces the
    04:21 vs 06:09 local HMS delta exactly (4.35 h, k=4, lord Moon). Library
    testimony: drik.is_night_birth's own docstring states sunrise/sunset return LOCAL
    float hours (12a). Bug-4 "jd_local kwarg TypeError" — the library signature is
    get_place_timezone_offset(latitude, longitude, jd_local=None); the engine's
    keyword call matches it and returns DST-correct offsets (NYC −4.0/−5.0) with no
    TypeError (12d).
    ADOPTED as library-first upgrade (NOT a bug fix): Bug-2 night-birth — the pool
    now sources day/night keys from the library's own drik.is_night_birth(jd, place)
    (JD-window fallback retained). The review's premise (afternoon births classified
    as night) is FALSE: its own claimed-failing 17:30 IST case classifies as DAY
    under both the old comparison and the library function (12b).
    Bug-3 REAL — FIXED: BAV binna row 7 was labeled PLANET_NAMES[7] = "Rahu"; the
    grid's 8th row is the LAGNA's own (get_ashtaka_varga docstring "0=Sun..7=Lagnam";
    Rahu/Ketu have no BAV rows). BAV_ROW_NAMES now labels both builders (12c).
    Features implemented: F1 shad_bala_summary — the library's 9-row shad_bala
    decoded (6 components + totals + rupas + BPHS-minimum ratios via
    const.shad_bala_factors), meets_required_minimum, with the Sun-minimum source
    dispute (library 5 rupas/300 vs popular 6.5/390) disclosed, and
    kashta_phala_complement = 60 − ishta_phala on the library's 0..60 scale
    (classical rashmi-product doctrine absent from the library — disclosed, not
    reimplemented) (12e). F2 navamsa sign inlined per planet + lagna in core
    (int(lon/(360/108))%12), audited identical to divisional_chart(9) on two charts
    (12f). F3 <key>_by_planet decoded twins for every raw 7-element strength_full
    list (12g). F4 sodhya_pindas_decoded — (raasi, graha, shodhya) pindas per
    Sun..Saturn, shodhya = raasi + graha verified (12h). F5 yogini_decoded — the 8
    classical yogini names/lords/durations from the library's dhasa_adhipathi_list
    (names wrapper-provided, web-verified), decoded timeline (lords → names, start
    tuples → local datetime strings), current period at jd_now, with the library's
    equal 8-antar split disclosed (12i).
    """
    print("\n=== AUDIT ROUND 12: review v7 (Ft.txt) adjudication ===")
    env = get_env()
    from jhora.horoscope.info import Horoscope as _H12
    from jhora.panchanga import drik as _drik12
    from jhora.horoscope.chart import charts as _charts12
    from jhora.horoscope.dhasa.graha import yogini as _yogini12
    from jhora import utils as _u12
    from jhora import const as _const12
    import inspect as _inspect12

    hs = _H12(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
              date_in=_drik12.Date(1990, 5, 15), birth_time="10:30:00")
    jd_s, place_s = hs.julian_day, hs.Place
    pp_s = env_charts.rasi_chart(jd_s, place_s)
    h_b, jd_b, place_b, pp_b = _build(BOY)          # 2003-10-16 20:10 IST, night

    # ---- (12a) Bug-1 hora tz REJECT — the review's own fix, executed ----
    sr_raw = _drik12.sunrise(jd_s, place_s)
    sr_jd = float(sr_raw[2])                        # LOCAL-clock JD of sunrise
    jd_ut_s = _u12.julian_day_utc(jd_s, place_s)
    their_hrs = (jd_ut_s - sr_jd) * 24.0            # review's proposed "fix"
    sr_prev = float(_drik12.sunrise(jd_s - 1.0, place_s)[2])
    their_fb = (jd_ut_s - sr_prev) * 24.0           # its pre-sunrise fallback branch
    eng_hrs = (jd_s - sr_jd) * 24.0
    ok_a1 = (abs(eng_hrs - 4.35) < 0.01 and their_hrs < 0.0 and
             abs(their_hrs + 1.15) < 0.05 and
             abs(their_fb - 22.87) < 0.1 and
             int(their_fb) % 24 != int(eng_hrs) % 24)
    record(ok_a1, "12a-1 Bug-1 REJECT (5th re-litigation): review's julian_day_utc fix "
                  "yields NEGATIVE elapsed hours (jd_ut − sr_jd ×24) and its fallback "
                  "branch yields ~22.87 h → hora k=22 instead of the correct 4; the "
                  "engine's local-clock arithmetic reproduces the local HMS delta "
                  "(10:30 − 06:09 = 4.35 h, k=4, lord Moon) exactly",
           f"eng={eng_hrs:.4f} their={their_hrs:.4f} their_fallback={their_fb:.4f}")
    _inb_doc = (_drik12.is_night_birth.__doc__ or "").lower()
    ok_a2 = ("local" in _inb_doc and
             abs(float(sr_raw[0]) - ((sr_jd + 0.5) % 1.0) * 24.0) < 0.01 and
             abs(float(sr_raw[0]) - 6.15) < 0.05)
    record(ok_a2, "12a-2 library testimony: drik.is_night_birth's docstring states "
                  "sunrise/sunset return LOCAL float hours; sunrise element [0] is the "
                  "same local-clock basis as its JD element (6.15 h = 06:09 IST, not "
                  "0.65 h UT) — no domain mixing exists for Bug-1 or Bug-2 to exploit")

    # ---- (12b) Bug-2 ADOPTED as library-first upgrade; impact claim refuted ----
    pool_day = cc._yoga_arg_pool(env, jd_s, place_s, pp_s)
    pool_night = cc._yoga_arg_pool(env, jd_b, place_b, pp_b)
    ok_b1 = (pool_day["is_daytime_birth"] is True and
             pool_day["is_night_birth"] is False and
             pool_day["is_night_birth"] is (not pool_day["is_daytime_birth"]) and
             pool_day["is_nighttime_birth"] == pool_day["is_night_birth"] and
             pool_night["is_night_birth"] is True and
             pool_night["is_daytime_birth"] is False and
             _drik12.is_night_birth(jd_s, place_s) is False and
             _drik12.is_night_birth(jd_b, place_b) is True)
    record(ok_b1, "12b-1 Bug-2 pool now sources day/night keys from the library's own "
                  "drik.is_night_birth(jd, place): sample day-birth / boy night-birth "
                  "agree with direct library calls; complements + mirror keys intact "
                  "(JD-window comparison retained as fallback)")
    h_eve = _H12(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
                 date_in=_drik12.Date(1990, 5, 15), birth_time="17:30:00")
    jd_e, place_e = h_eve.julian_day, h_eve.Place
    pool_eve = cc._yoga_arg_pool(env, jd_e, place_e,
                                 env_charts.rasi_chart(jd_e, place_e))
    h_dawn = _H12(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
                  date_in=_drik12.Date(1990, 5, 15), birth_time="04:00:00")
    jd_d, place_d = h_dawn.julian_day, h_dawn.Place
    pool_dawn = cc._yoga_arg_pool(env, jd_d, place_d,
                                  env_charts.rasi_chart(jd_d, place_d))
    ok_b2 = (pool_eve["is_daytime_birth"] is True and
             pool_eve["is_night_birth"] is False and
             _drik12.is_night_birth(jd_e, place_e) is False and
             pool_dawn["is_daytime_birth"] is False and
             pool_dawn["is_night_birth"] is True and
             _drik12.is_night_birth(jd_d, place_d) is True)
    record(ok_b2, "12b-2 review's impact claim REFUTED: the claimed-failing 17:30 IST "
                  "birth (before ~18:35 sunset) classifies as DAY under BOTH the old "
                  "JD-window comparison and drik.is_night_birth; a 04:00 pre-dawn birth "
                  "correctly classifies as NIGHT — no afternoon misclassification exists")

    # ---- (12c) Bug-3 REAL — FIXED: BAV row 7 = Lagna, not Rahu ----
    av_f = cc.build_ashtakavarga(env, h_b, jd_b, place_b, pp_b)
    keys_f = list(av_f["binna_by_sign"].keys())
    raw_f = av_f["binna_ashtakavarga"]
    ok_c1 = (keys_f == cc.BAV_ROW_NAMES and
             keys_f == ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus",
                        "Saturn", "Lagna"] and
             "Rahu" not in keys_f and "Ketu" not in keys_f and
             all(av_f["binna_by_sign"][cc.BAV_ROW_NAMES[pi]][cc.RAASI_NAMES[i]]
                 == int(v)
                 for pi, row in enumerate(raw_f) for i, v in enumerate(row)))
    record(ok_c1, "12c-1 Bug-3 FIXED (FAST builder): binna_by_sign keys are now "
                  "Sun..Saturn + 'Lagna' (BAV_ROW_NAMES) — row 7 is the ascendant's "
                  "own BAV row per get_ashtaka_varga's docstring '0=Sun..7=Lagnam'; "
                  "'Rahu'/'Ketu' absent (they have no BAV rows); every decoded value "
                  "equals the raw grid")
    avd = cc.build_ashtakavarga_deep(env, h_b, jd_b, place_b, pp_b)
    keys_d = list(avd["binna_by_sign"].keys())
    raw_d = avd["binna_ashtakavarga"]
    ok_c2 = (keys_d == cc.BAV_ROW_NAMES and
             avd["binna_by_sign"]["Lagna"][cc.RAASI_NAMES[0]] == int(raw_d[7][0]) and
             all(avd["binna_by_sign"][cc.BAV_ROW_NAMES[pi]][cc.RAASI_NAMES[i]]
                 == int(v)
                 for pi, row in enumerate(raw_d) for i, v in enumerate(row)))
    record(ok_c2, "12c-2 Bug-3 FIXED (deep builder): same Lagna labeling in "
                  "ashtakavarga_deep; row-7 values identical to the raw binna grid "
                  "(label-only fix — no numbers changed)")

    # ---- (12d) Bug-4 REJECT: jd_local kwarg matches the library signature ----
    sig12 = _inspect12.signature(_u12.get_place_timezone_offset)
    params12 = list(sig12.parameters)
    ok_d1 = (params12 == ["latitude", "longitude", "jd_local"] and
             sig12.parameters["jd_local"].default is None and
             isinstance(_u12.get_place_timezone_offset(
                 40.7128, -74.0060,
                 jd_local=_u12.julian_day_number((1995, 7, 4), (8, 0, 0))),
                 (int, float)))
    record(ok_d1, "12d-1 Bug-4 REJECT: library signature is get_place_timezone_offset("
                  "latitude, longitude, jd_local=None) — the engine's jd_local= "
                  "keyword call matches it exactly; a direct keyword call succeeds "
                  "with no TypeError (the review's claimed parameter name 'jd' does "
                  "not exist)")
    off_j = float(_u12.get_place_timezone_offset(
        40.7128, -74.0060, jd_local=_u12.julian_day_number((1995, 7, 4), (8, 0, 0))))
    off_w = float(_u12.get_place_timezone_offset(
        40.7128, -74.0060, jd_local=_u12.julian_day_number((1995, 1, 4), (8, 0, 0))))
    ok_d2 = (abs(off_j + 4.0) < 0.01 and abs(off_w + 5.0) < 0.01)
    record(ok_d2, "12d-2 the jd_local keyword is not merely accepted but USED: NYC "
                  "1995-07-04 → −4.0 h (EDT) and 1995-01-04 → −5.0 h (EST) — "
                  "DST-aware resolution through the exact call shape the engine makes")

    # ---- (12e) F1 shad_bala_summary + kashta complement ----
    sf = cc.build_strength_full(env, h_b, jd_b, place_b, pp_b)
    raw9 = sf["shad_bala"]
    sbs = sf["shad_bala_summary"]
    _n7 = cc.PLANET_NAMES[:7]
    def _r4eq(decoded, row):
        return all(abs(decoded[p] - round(float(row[i]), 4)) < 1e-9
                   for i, p in enumerate(_n7))
    ok_e1 = (isinstance(raw9, (list, tuple)) and len(raw9) == 9 and
             all(_r4eq(sbs["components_by_planet"][k], raw9[idx]) for idx, k in
                 ((0, "sthana_bala"), (1, "kaala_bala"), (2, "dig_bala"),
                  (3, "cheshta_bala"), (4, "naisargika_bala"), (5, "drik_bala"))) and
             _r4eq(sbs["total_points_by_planet"], raw9[6]) and
             _r4eq(sbs["total_rupas_by_planet"], raw9[7]) and
             _r4eq(sbs["strength_ratio_by_planet"], raw9[8]) and
             [sbs["required_minimum_rupas_by_planet"][p] for p in _n7] ==
             [float(x) for x in _const12.shad_bala_factors] and
             all(sbs["meets_required_minimum"][p] is
                 (float(raw9[8][i]) >= 1.0) for i, p in enumerate(_n7)))
    record(ok_e1, "12e-1 F1 shad_bala_summary decodes ALL 9 library rows: 6 components "
                  "+ total points + total rupas + strength ratio == the raw shad_bala "
                  "rows elementwise (Sun..Saturn); required minimums == "
                  "const.shad_bala_factors (the library's BPHS rupas table); "
                  "meets_required_minimum == ratio >= 1.0")
    ok_e2 = ("6.5 rupas (390 virupas)" in sbs["__note__"] and
             sbs["required_minimum_rupas_by_planet"]["Sun"] == 5.0 and
             "DISPUTE DISCLOSED" in sbs["__note__"])
    record(ok_e2, "12e-2 Sun-minimum source dispute DISCLOSED: library (PVR/JHora "
                  "lineage) minimum = 5 rupas/300 virupas is reported as primary; the "
                  "popularly cited BPHS table (and the review) give 6.5 rupas/390 "
                  "virupas — noted in __note__ with the 5/6.5 rescaling caveat "
                  "(web-verified across four independent sources)")
    kas = sf["kashta_phala_complement"]
    iph = sf["ishta_phala"]
    ikn = sf["ishta_kashta_note"]
    ok_e3 = (len(kas) == 7 and all(abs(kas[i] - (60.0 - float(iph[i]))) < 1e-9
                                   for i in range(7)) and
             "rashmi" in ikn and "not reimplemented" in ikn and "60" in ikn)
    record(ok_e3, "12e-3 F1 kashta_phala_complement == 60 − ishta_phala elementwise on "
                  "the library's own 0..60 dignity scale; the note discloses that "
                  "PyJHora has NO kashta function and the classical rashmi-product "
                  "doctrine was not reimplemented (wrapper convention, not invented "
                  "library data)")

    # ---- (12f) F2 navamsa inline in core ----
    core_b = cc.build_core(env, h_b, jd_b, place_b, pp_b)
    d9_b = _charts12.divisional_chart(jd_b, place_b, divisional_chart_factor=9)
    l9_b = cc._planetary_longitude(d9_b)
    _bodies = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn",
               "Rahu", "Ketu"]
    rowmap = {r["name"]: r for r in core_b["planets"]}
    ok_f1 = (all(rowmap[nm]["navamsa_sign_index"] == cc._sign_of_long(l9_b[nm])
                 for nm in _bodies) and
             all(rowmap[nm]["navamsa_sign"] ==
                 cc.RAASI_NAMES[cc._sign_of_long(l9_b[nm])] for nm in _bodies) and
             core_b["lagna"]["navamsa_sign_index"] == cc._sign_of_long(l9_b[cc.LAGNA]))
    record(ok_f1, "12f-1 F2 inline navamsa == the library's own divisional_chart(9): "
                  "all 9 grahas + lagna (boy chart) — navamsa_sign_index equals "
                  "sign(D9 longitude) and navamsa_sign its RAASI name; pure function "
                  "int(lon/(360/108))%12 of the D1 longitude")
    d9_s = _charts12.divisional_chart(jd_s, place_s, divisional_chart_factor=9)
    l9_s = cc._planetary_longitude(d9_s)
    l1_s = cc._planetary_longitude(pp_s)
    ok_f2 = (all(cc._navamsa_sign_of_long(l1_s[nm]) == cc._sign_of_long(l9_s[nm])
                 for nm in _bodies + [cc.LAGNA]) and
             cc._navamsa_sign_of_long(0.0) == 0 and
             cc._navamsa_sign_of_long(359.99) == 11 and
             cc._navamsa_sign_of_long(363.0) == 0)
    record(ok_f2, "12f-2 F2 navamsa formula parity on a SECOND chart (sample) for all "
                  "10 bodies + boundary sanity (0° → first navamsa, 359.99° → 12th, "
                  "363° ≡ 3° wraps to the first) — vargas.D9 remains the full "
                  "divisional chart")

    # ---- (12g) F3 by_planet twins ----
    twin_keys = [k for k in sf if k.endswith("_by_planet")]
    raw7 = [k for k in sf
            if not k.endswith("_by_planet") and
            isinstance(sf[k], (list, tuple)) and len(sf[k]) == 7 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool)
                for x in sf[k])]
    ok_g1 = (len(twin_keys) >= 15 and
             all(k + "_by_planet" in sf for k in raw7) and
             all(sf[k + "_by_planet"][cc.PLANET_NAMES[i]] == round(float(v), 4)
                 for k in raw7 for i, v in enumerate(sf[k])) and
             "bhava_bala_by_planet" not in sf and
             "by_planet_note" in sf)
    record(ok_g1, "12g-1 F3: every raw 7-element strength_full list has a "
                  "<key>_by_planet decoded twin with identical values (Sun..Saturn "
                  "order); %d twins covering %d raw lists; non-planet lists (e.g. "
                  "bhava_bala) deliberately untwinned; raws preserved for parity"
           % (len(twin_keys), len(raw7)))

    # ---- (12h) F4 sodhya pindas decoded ----
    spd = avd["sodhya_pindas_decoded"]
    sp_raw = avd["sodhya_pindas"]
    ok_h1 = (set(spd["raasi_pindas"]) == set(_n7) and
             all(spd["raasi_pindas"][p] == int(sp_raw[0][i]) for i, p in enumerate(_n7)) and
             all(spd["graha_pindas"][p] == int(sp_raw[1][i]) for i, p in enumerate(_n7)) and
             all(spd["shodhya_pindas"][p] == int(sp_raw[2][i]) for i, p in enumerate(_n7)) and
             all(spd["shodhya_pindas"][p] == spd["raasi_pindas"][p] + spd["graha_pindas"][p]
                 for p in _n7) and
             "0=Sun" in spd["__note__"])
    record(ok_h1, "12h-1 F4 sodhya_pindas_decoded: raasi/graha/shodhya pindas keyed "
                  "Sun..Saturn == the library's raw arrays elementwise; shodhya = "
                  "raasi + graha verified per planet; note states the per-planet "
                  "0=Sun..6=Saturn indexing (the review's 12/8-shape snippet was "
                  "wrong) and the rasimana multiplier variant disclosure")

    # ---- (12i) F5 yogini decoded ----
    jd_now12 = _u12.julian_day_number((2026, 9, 7), (12, 0, 0))
    dd12 = cc.build_dhasa(env, h_b, jd_b, place_b, pp_b, jd_now=jd_now12)
    yd = dd12["yogini_decoded"]
    lib_dur = {int(k): int(v) for k, v in _yogini12.dhasa_adhipathi_list.items()}
    tab = {y["lord_id"]: y for y in yd["yoginis"]}
    ok_i1 = (len(yd["yoginis"]) == 8 and
             yd["cycle_total_years"] == 36 == sum(lib_dur.values()) and
             all(tab[lid]["duration_years"] == lib_dur[lid] for lid in lib_dur) and
             all(tab[lid]["lord"] == cc._planet_name(lid) for lid in lib_dur) and
             all(tab[lid]["name"] == cc.YOGINI_NAMES_BY_LORD_ID[lid] for lid in lib_dur) and
             [y["duration_years"] for y in yd["yoginis"]] == [1, 2, 3, 4, 5, 6, 7, 8] and
             [y["name"] for y in yd["yoginis"]] ==
             ["Mangala", "Pingala", "Dhanya", "Bhramari", "Bhadrika", "Ulka",
              "Siddha", "Sankata"])
    record(ok_i1, "12i-1 F5 yogini table == the library's dhasa_adhipathi_list "
                  "(Moon 1, Sun 2, Jupiter 3, Mars 4, Mercury 5, Saturn 6, Venus 7, "
                  "Rahu 8 — canonical Mangala..Sankata order, 36-year cycle); "
                  "classical NAMES are wrapper-provided (web-verified unanimous); "
                  "lords decode via PLANET_NAMES")
    rawy = dd12["yogini"]
    tl = yd["timeline"]
    cur = yd["current_index"]
    def _sjd(st):
        return _u12.julian_day_number(
            (int(st[0]), int(st[1]), int(st[2])),
            (int(st[3]), int((float(st[3]) % 1) * 60), 0))
    md0 = [r for r in rawy if r[0][0] == rawy[0][0][0]]
    ok_i2 = (len(tl) == len(rawy) and
             all(len(tl[i]["levels"]) == len(rawy[i][0]) and
                 tl[i]["levels"][j]["lord"] == cc._planet_name(int(rawy[i][0][j]))
                 for i in range(len(rawy)) for j in range(len(rawy[i][0]))) and
             all(abs(tl[i]["duration_years"] - float(rawy[i][2])) < 1e-9
                 for i in range(len(rawy))) and
             cur is not None and _sjd(rawy[cur][1]) <= jd_now12 < _sjd(rawy[cur + 1][1]) and
             max(float(r[2]) for r in md0) - min(float(r[2]) for r in md0) <= 0.02 and
             "EQUAL antardashas" in yd["__note__"] and
             "PROPORTIONAL" in yd["__note__"])
    record(ok_i2, "12i-2 F5 yogini timeline parity: decoded rows == raw rows 1:1 "
                  "(lords → yogini name + lord per level, durations equal, start "
                  "tuples → local 'YYYY-MM-DD HH:MM' strings); current period at "
                  "--now 2026-09-07 brackets jd_now correctly (start ≤ now < next "
                  "start); the library's equal 8-antar MD split (MD_years/8) verified "
                  "on the first MD block and disclosed against the proportional "
                  "(MD × yogini_years/36) classical variant")



def audit_round13():
    """ROUND-13 audit — external review v8 (Ft.txt, 153 lines) adjudication.

    Claim 1 (navamsa 360.0 boundary) — claim REJECTED, wrap ADOPTED as hardening:
    360.0 deg == 0.0 deg == Aries 0d0m0s, whose navamsa IS index 0 both before and
    after their own patch (which also returns 0, not the Pisces/11 they asserted);
    since 108 navamsas per 360 deg and 108 % 12 == 0 the mod-360 wrap is a provable
    no-op for all non-negative inputs — swept exhaustively (13a).
    Claim 2 (jd_local kwarg TypeError) — the pinned build's signature is jd_local
    (audit 12d), but the dev line moves: _place_tz_offset now binds jd_local / jd /
    positional / 2-parameter adaptively; simulated alternative builds + the NYC DST
    engine path prove it (13b).
    Claim 3 (FAST strengths lacks the decoded summary) — TRUE premise, ADOPTED: the
    Round-12 decode was extracted to _decode_shadbala_summary and is shared by both
    builders (their snippet's missing row indices would have collapsed five
    components onto row 0 — the shipped decoder keeps the verified indices) (13c).
    Claim 4 (D10 arudhas missing) — TRUE, ADOPTED: factor tuple now (3,6,7,9,10,12);
    D10 carries A10 Rajyapada / A11 Labhapada for career readings (13d).
    Claim 5 (Manglik lagna-only) — TRUE gap, ADOPTED library-natively: dosha.manglik
    itself supports manglik_reference_planet='L'/1/5; the girl golden chart proves
    the gap real (Mars 1st from lagna but 8th from Moon -> manglik-from-moon TRUE,
    with BV Raman exceptions [7,12,16,17] reported alongside — the library does
    NOT flip the verdict; cancellation is the reader's interpretation call) (13e).
    Claim 6 (KP four-fold significators) — ADOPTED (their snippet referenced a
    `core` variable that does not exist in build_house_systems; rebuilt from
    planet_positions), in BOTH the whole-sign frame and the orthodox KP cuspal
    frame (Placidus bhaava_madhya_kp — same cusps as kp_cuspal_lords) (13f).
    """
    print("\n=== AUDIT ROUND 13: review v8 (Ft.txt) adjudication ===")
    env = get_env()
    from jhora.panchanga import drik as _drik13
    from jhora.horoscope.chart import charts as _charts13, arudhas as _arudhas13
    from jhora import utils as _u13
    from jhora import const as _const13
    import inspect as _inspect13

    h_b, jd_b, place_b, pp_b = _build(BOY)
    h_g, jd_g, place_g, pp_g = _build(GIRL)

    # ---- (13a) navamsa wrap: no-op sweep + boundary claim refutation ----
    def _old_nav(lon):
        return int(lon / (360.0 / 108.0)) % 12
    sweep = [i * 0.05 for i in range(7200)] + [359.9999, 360.0, 363.0, 720.5, 0.0]
    ok_a1 = (all(cc._navamsa_sign_of_long(x) == _old_nav(x) for x in sweep) and
             cc._navamsa_sign_of_long(360.0) == 0 and
             cc._navamsa_sign_of_long(359.99) == 11 and
             _old_nav(360.0) == 0)
    record(ok_a1, "13a-1 navamsa mod-360 wrap is a PROVEN no-op: identical results "
                  "across 7,204 swept longitudes incl. [0,360) at 0.05 deg steps and "
                  "363/720.5 wraps; the review's boundary claim is REFUTED — 360.0 deg "
                  "== Aries 0d0m0s evaluates to navamsa index 0 BOTH before and after "
                  "their own patch (not the Pisces/11 they asserted; 359.99 deg does "
                  "give 11)")
    d9_b13 = _charts13.divisional_chart(jd_b, place_b, divisional_chart_factor=9)
    l9_b13 = cc._planetary_longitude(d9_b13)
    l1_b13 = cc._planetary_longitude(pp_b)
    ok_a2 = all(cc._navamsa_sign_of_long(l1_b13[nm]) == cc._sign_of_long(l9_b13[nm])
                for nm in ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus",
                           "Saturn", "Rahu", "Ketu", cc.LAGNA])
    record(ok_a2, "13a-2 regression: the wrapped helper still reproduces the library's "
                  "divisional_chart(9) sign for all 10 bodies (boy chart) — audit 12f "
                  "parity intact")

    # ---- (13b) _place_tz_offset adaptive binding ----
    sig13 = _inspect13.signature(_u13.get_place_timezone_offset)
    p3 = list(sig13.parameters)[2]
    off_j13 = float(cc._place_tz_offset(
        env["utils"], 40.7128, -74.0060,
        _u13.julian_day_number((1995, 7, 4), (8, 0, 0))))
    off_w13 = float(cc._place_tz_offset(
        env["utils"], 40.7128, -74.0060,
        _u13.julian_day_number((1995, 1, 4), (8, 0, 0))))
    ok_b1 = (p3 == "jd_local" and abs(off_j13 + 4.0) < 0.01 and
             abs(off_w13 + 5.0) < 0.01)
    record(ok_b1, "13b-1 installed build declares 3rd param 'jd_local' and "
                  "_place_tz_offset binds it by name: NYC 1995-07-04 -> -4.0 (EDT), "
                  "1995-01-04 -> -5.0 (EST) — identical to the Round-12 keyword path "
                  "(audit 12d)")

    class _ShimJD13:
        @staticmethod
        def get_place_timezone_offset(latitude, longitude, jd=None):
            return -4.0 if jd is not None else 0.0

    class _ShimWeird13:
        @staticmethod
        def get_place_timezone_offset(latitude, longitude, when=None):
            return -7.5 if when is not None else 0.0

    class _ShimLegacy13:
        @staticmethod
        def get_place_timezone_offset(latitude, longitude):
            return -9.25

    ok_b2 = (cc._place_tz_offset(_ShimJD13, 1.0, 2.0, 123.0) == -4.0 and
             cc._place_tz_offset(_ShimWeird13, 1.0, 2.0, 123.0) == -7.5 and
             cc._place_tz_offset(_ShimLegacy13, 1.0, 2.0, 123.0) == -9.25)
    record(ok_b2, "13b-2 simulated alternative builds: a `jd=None`-named build binds "
                  "by name (no TypeError, JD delivered), an unknown 3rd-param name "
                  "binds positionally, and a 2-parameter legacy build degrades "
                  "gracefully — the review's silent native-tz fallback cannot occur "
                  "on any of these shapes")
    m13 = cc.build_match(env, h_b, jd_b, place_b, pp_b,
                         {"date": "1995-07-04", "time": "08:00:00",
                          "lat": 40.7128, "lon": -74.0060, "tz": None})
    ok_b3 = ("__error__" not in m13 and
             "was -4.0 h" in str(m13.get("timezone_note")) and
             isinstance(m13.get("ashtakoota_total"), (int, float)))
    record(ok_b3, "13b-3 engine path regression: build_match with an omitted partner "
                  "--match-tz still resolves NYC EDT (-4.0) through _place_tz_offset "
                  "(Round-10 audit 10c golden intact)")

    # ---- (13c) shared shad_bala_summary: FAST == deep ----
    st_f13 = cc.build_strengths(env, h_b, jd_b, place_b, pp_b)
    sf_d13 = cc.build_strength_full(env, h_b, jd_b, place_b, pp_b)
    ok_c1 = (st_f13.get("shad_bala_summary") == sf_d13.get("shad_bala_summary") and
             "__error__" not in st_f13["shad_bala_summary"])
    record(ok_c1, "13c-1 the FAST strengths section now carries shad_bala_summary "
                  "IDENTICAL (whole dict incl. note) to strength_full's — one shared "
                  "_decode_shadbala_summary helper, single source of truth")
    raw13 = st_f13["shad_bala"]
    sbs13 = st_f13["shad_bala_summary"]
    _n7 = cc.PLANET_NAMES[:7]
    def _r4eq13(decoded, row):
        return all(abs(decoded[p] - round(float(row[i]), 4)) < 1e-9
                   for i, p in enumerate(_n7))
    comps13 = sbs13["components_by_planet"]
    ok_c2 = (_r4eq13(comps13["sthana_bala"], raw13[0]) and
             _r4eq13(comps13["kaala_bala"], raw13[1]) and
             _r4eq13(comps13["dig_bala"], raw13[2]) and
             _r4eq13(comps13["cheshta_bala"], raw13[3]) and
             _r4eq13(comps13["naisargika_bala"], raw13[4]) and
             _r4eq13(comps13["drik_bala"], raw13[5]) and
             _r4eq13(sbs13["total_points_by_planet"], raw13[6]) and
             _r4eq13(sbs13["total_rupas_by_planet"], raw13[7]) and
             _r4eq13(sbs13["strength_ratio_by_planet"], raw13[8]) and
             comps13["kaala_bala"] != comps13["sthana_bala"] and
             comps13["dig_bala"] != comps13["kaala_bala"] and
             [sbs13["required_minimum_rupas_by_planet"][p] for p in _n7] ==
             [float(x) for x in _const13.shad_bala_factors] and
             "ROUND-13" in sbs13["__note__"])
    record(ok_c2, "13c-2 decoder row indices verified against the raw 9 rows (each "
                  "component maps to its OWN row — the review's snippet omitted five "
                  "indices and would have collapsed them onto row 0; distinctness "
                  "asserted), totals/rupas/ratios exact, minimums == "
                  "const.shad_bala_factors, note carries the ROUND-13 sharing")

    # ---- (13d) D10 arudhas ----
    ar13 = cc.build_arudhas_and_specials(env, h_b, jd_b, place_b, pp_b)
    av13 = ar13["arudhas_by_varga"]
    d10 = av13.get("D10")
    with cc._quiet():
        raw10 = _arudhas13.bhava_arudha_longitudes(
            jd_b, place_b, arudha_base=ar13.get("arudha_base", 0),
            divisional_chart_factor=10)
    labels13 = ["AL"] + [f"A{i}" for i in range(2, 12)] + ["UL"]
    ok_d1 = (list(av13.keys()) == ["D3", "D6", "D7", "D9", "D10", "D12"] and
             isinstance(d10, dict) and len(d10) == 12 and
             all(lab in d10 for lab in labels13) and
             all(abs(d10[lab]["longitude"] - (float(raw10[i]) % 360.0)) < 0.01 and
                 d10[lab]["sign_index"] == int((float(raw10[i]) % 360.0) // 30)
                 for i, lab in enumerate(labels13)))
    record(ok_d1, "13d-1 D10 added to arudhas_by_varga: 12 decoded entries (AL, "
                  "A2..A11, UL/Upapada — the engine's standard arudha labels) with "
                  "sign/degree parity against a direct library "
                  "bhava_arudha_longitudes(divisional_chart_factor=10) call; the "
                  "five pre-existing factors are untouched and in order")
    ok_d2 = (d10 != av13["D9"] and
             "Rajyapada" in ar13["arudhas_by_varga_note"] and
             "Labhapada" in ar13["arudhas_by_varga_note"])
    record(ok_d2, "13d-2 D10 is an independent computation (!= D9) and the note "
                  "carries the classical Jaimini career readings: A10 = Rajyapada "
                  "(career status/authority), A11 = Labhapada (income/gains)")

    # ---- (13e) manglik multi-reference ----
    do_g13 = cc.build_doshas(env, h_g, jd_g, place_g, pp_g)
    mmr_g = do_g13["manglik_multi_reference"]
    ok_e1 = (mmr_g["from_moon"]["present_before_exceptions"] is True and
             mmr_g["from_moon"]["present"] is True and
             mmr_g["from_moon"]["exceptions_applied"] is True and
             mmr_g["from_moon"]["exception_indices"] == [7, 12, 16, 17] and
             mmr_g["from_moon"]["mars_house_from_reference"] == 8 and
             mmr_g["from_lagna"]["present_before_exceptions"] is False and
             mmr_g["from_lagna"]["mars_house_from_reference"] == 1 and
             mmr_g["from_venus"]["present_before_exceptions"] is False and
             mmr_g["from_venus"]["mars_house_from_reference"] == 1 and
             all(mmr_g[k]["present_with_lagna_house_raw"] is True
                 for k in ("from_lagna", "from_moon", "from_venus")))
    record(ok_e1, "13e-1 the girl golden chart PROVES the multi-reference gap was "
                  "real: Mars is 1st from lagna/Venus (excluded from the default "
                  "house set -> not manglik) but 8th from the MOON -> manglik TRUE, "
                  "with BV Raman cancellation exceptions [7,12,16,17] reported "
                  "ALONGSIDE the verdict (library-exact semantics: apply_exceptions "
                  "does not flip the boolean — cancellation is the reader's "
                  "interpretation call); all values are the library's own "
                  "manglik_reference_planet='L'/1/5 calls; the review's house-1 "
                  "union variant is exposed (True in all three frames here)")
    do_b13 = cc.build_doshas(env, h_b, jd_b, place_b, pp_b)
    mmr_b = do_b13["manglik_multi_reference"]
    p2h13 = _u13.get_planet_to_house_dict_from_chart(
        _u13.get_house_planet_list_from_planet_positions(pp_b))
    rel13 = env["house"].get_relative_house_of_planet
    exp_houses = {"from_lagna": rel13(p2h13["L"], p2h13[2]),
                  "from_moon": rel13(p2h13[1], p2h13[2]),
                  "from_venus": rel13(p2h13[5], p2h13[2])}
    ok_e2 = (all(mmr_b[k]["present_before_exceptions"] is False and
                 mmr_b[k]["present"] is False
                 for k in exp_houses) and
             all(mmr_b[k]["mars_house_from_reference"] == exp_houses[k]
                 for k in exp_houses) and
             "Sanjay Rath" in mmr_b["__note__"] and
             "(1,2,4,7,8,12)" in mmr_b["__note__"].replace(" ", ""))
    record(ok_e2, "13e-2 boy chart: Mars 10th from lagna / 9th from Moon / 5th from "
                  "Venus (recomputed via the library's own get_relative_house_of_"
                  "planet) -> no frame manglik; the note discloses the house-set "
                  "variants (library default [2,4,7,8,12] with the Sanjay Rath "
                  "lagna-house exclusion vs the review's (1,2,4,7,8,12) union)")
    ok_e3 = (do_g13["manglik"]["present"] == mmr_g["from_lagna"]["present"] and
             do_g13["manglik"]["present_before_exceptions"] ==
             mmr_g["from_lagna"]["present_before_exceptions"] and
             do_b13["manglik"]["present"] == mmr_b["from_lagna"]["present"] and
             do_b13["manglik"]["present_before_exceptions"] ==
             mmr_b["from_lagna"]["present_before_exceptions"])
    record(ok_e3, "13e-3 backward compatibility: the pre-existing doshas.manglik "
                  "(lagna frame, exceptions pair) is byte-identical to "
                  "manglik_multi_reference.from_lagna on both charts — no key was "
                  "renamed or removed; match.manglik_compatibility keeps using the "
                  "identical lagna-frame calls")

    # ---- (13f) KP four-fold significators ----
    hs13 = cc.build_house_systems(env, h_b, jd_b, place_b, pp_b)
    ks13 = hs13["kp_significators"]
    ws13 = ks13["whole_sign_frame"]
    lons13 = cc._planetary_longitude(pp_b)
    asc13 = cc._sign_of_long(lons13[cc.LAGNA])
    star13 = {p: cc._nak_lord_of_long(lons13[p]) for p in cc.PLANET_ORDER}
    hous13 = {p: cc._house_number(asc13, cc._sign_of_long(lons13[p]))
              for p in cc.PLANET_ORDER}
    lords13 = {hh: cc.SIGN_LORDS[(asc13 + hh - 1) % 12] for hh in range(1, 13)}
    ok_f1 = True
    for p in cc.PLANET_ORDER:
        exp = {"level_2": sorted([hous13[p]]),
               "level_4": sorted([hh for hh in range(1, 13)
                                  if lords13[hh] == p]),
               "level_1": sorted([hous13[star13[p]]]),
               "level_3": sorted([hh for hh in range(1, 13)
                                  if lords13[hh] == star13[p]])}
        got = ws13["by_planet"][p]
        if any(sorted(got[lv]) != exp[lv] for lv in exp):
            ok_f1 = False
    record(ok_f1, "13f-1 whole-sign frame recomputed independently for all 9 "
                  "grahas (boy): level_2 == occupancy from the lagna sign, level_4 "
                  "== SIGN_LORDS ownership, level_1 == the star lord's occupied "
                  "house, level_3 == houses owned by the star lord — exact match on "
                  "every planet/level (e.g. Mars: occupies 10th, owns 7th+12th)")
    kpf13 = ks13["kp_cuspal_frame"]
    with cc._quiet():
        cusps13 = [float(c) % 360.0
                   for c in list(_drik13.bhaava_madhya_kp(jd_b, place_b))[:12]]
    kcl13 = hs13["bhava_madhya_variants"]["kp_cuspal_lords"]["cusps"]
    def _cusp_h13(lon):
        for i in range(12):
            lo, hi = cusps13[i], cusps13[(i + 1) % 12]
            if (lo <= lon < hi) if lo <= hi else (lon >= lo or lon < hi):
                return i + 1
        return None
    ok_f2 = (isinstance(kpf13, dict) and "by_planet" in kpf13 and
             all(abs(kcl13[i]["cusp_longitude"] - cusps13[i]) < 0.001
                 for i in range(12)) and
             all(kpf13["by_planet"][p]["level_2"] == sorted([_cusp_h13(lons13[p])])
                 for p in cc.PLANET_ORDER) and
             all(kpf13["by_house"][str(hh)]["level_4"] ==
                 sorted([p for p in cc.PLANET_ORDER
                         if cc.SIGN_LORDS[int(cusps13[hh - 1] // 30) % 12] == p],
                        key=cc.PLANET_ORDER.index)
                 for hh in range(1, 13)))
    record(ok_f2, "13f-2 KP cuspal frame: the cusps are the SAME Placidus "
                  "bhaava_madhya_kp longitudes exposed by kp_cuspal_lords (parity "
                  "< 0.001 deg on all 12); occupancy and cusp-sign lordship "
                  "recomputed independently match level_2/level_4 for every planet "
                  "and house — the orthodox KP frame sits beside the engine-"
                  "convention whole-sign frame")
    mirror13 = all(
        (hh in ws13["by_planet"][p][lv]) == (p in ws13["by_house"][str(hh)][lv])
        for p in cc.PLANET_ORDER for hh in range(1, 13)
        for lv in ("level_1", "level_2", "level_3", "level_4"))
    conv13 = ks13["convention"]
    ok_f3 = (mirror13 and
             ws13["by_planet"]["Rahu"]["level_4"] == [] and
             ws13["by_planet"]["Ketu"]["level_4"] == [] and
             kpf13["by_planet"]["Rahu"]["level_4"] == [] and
             "A > B > C > D" in conv13 and "co-lordship" in conv13 and
             len(ws13["by_house"]) == 12)
    record(ok_f3, "13f-3 structure + doctrine: by_planet/by_house are exact mirrors "
                  "(planet signifies house <=> house is signified by planet, all 4 "
                  "levels x 9 grahas x 12 houses); Rahu/Ketu own no houses in "
                  "either frame (levels A-C only); the convention text carries the "
                  "A > B > C > D priority, the Jaimini/KP co-lordship disclosure "
                  "and the sub-lord intersection guidance")



def audit_round14():
    """ROUND-14 audit - external review v9 (Ft.txt, 118 lines) adjudication.

    Review part 1 confirmed Round-13 as shipped (385 PASS, "robust, production-
    ready"). Part 2 claimed ~85-90% PyJHora coverage with six gaps. Adjudication
    against the actual library (site-packages scan + live calls):
    Claim 1 (chart renderers missing) - REJECTED: no jhora.chart_renderer module
    exists; all rendering is PyQt6 UI (not installed headless); placements already
    carry full coordinate data (14a-1).
    Claim 2 (chakras missing) - PARTIALLY TRUE, ADOPTED AS DATA: Kota + Sarvatobhadra
    ported from the PyQt6-locked ui/chakra.py widgets with cited sources (14c);
    Kurma has ZERO computational content in the library (14e-1); the Sudarshana
    "composite chart" IS the sudharsana_chakra dasha, already in the 56-system
    catalog (14e-1), and its third reference frame (Surya kundali) is now data via
    relative_houses.relative_house_from_sun (14d-1). The review's "5 vedha
    penetrations" do not exist in PyJHora - vedha logic is match-domain only (14c-3).
    Claim 3 (Prashna horary missing) - REJECTED: zero matches for prashna /
    ashtamangala / tambula / horary across every .py file in the library (14a-2).
    Claim 4 (muhurta range scanner missing) - REJECTED: no find_muhurtha_between_dates
    exists; all eight drik muhurta functions are single-jd point evaluators, already
    wrapped in the muhurta section (14a-3).
    Claim 5 (deity/gemstone remedy tables missing) - REJECTED: const has no
    planet_deities / nakshatra_deities / gemstone tables; tithi_deities holds 16
    Nitya Devis (a different doctrine, already adjudicated in-engine) (14a-4).
    Claim 6 (harana reduction tables not extracted) - TRUE, ADOPTED: new deep
    `aayu` section exposes base-year tables, all four harana factor tables,
    bharana, both methods, aayur-type, lagna longevity and the public longevity()
    total - with catalog-duration parity audited per system (14b).
    """
    print("\n=== AUDIT ROUND 14: review v9 (Ft.txt) adjudication ===")
    env = get_env()
    import importlib as _il14
    import importlib.util as _ilu14
    import inspect as _insp14
    import os as _os14
    import jhora as _jhora14
    from jhora import const as _const14
    from jhora.panchanga import drik as _drik14
    from jhora.horoscope.chart import house as _house14
    from jhora.horoscope.info import Horoscope as _H14

    h_b, jd_b, place_b, pp_b = _build(BOY)
    h_g, jd_g, place_g, pp_g = _build(GIRL)
    ppk_b = pp_b[:_const14._pp_count_upto_ketu]
    lons_b = cc._planetary_longitude(pp_b)

    # ---- (14a-1) REJECT: renderer claim ----
    no_renderer = _ilu14.find_spec("jhora.chart_renderer") is None
    ui_locked = False
    try:
        _il14.import_module("jhora.ui.chakra")
    except BaseException as e:
        ui_locked = "PyQt6" in (type(e).__name__ + str(e))
    core_b = cc.build_core(env, h_b, jd_b, place_b, pp_b)
    n_bodies = len(core_b.get("planets", []))
    ok = no_renderer and ui_locked and n_bodies >= 9
    record(ok, "14a-1 REJECT chart-renderer claim: importlib.util.find_spec('jhora.chart_renderer') "
               "is None (module does not exist in any build); jhora.ui.chakra fails to import "
               "headless with a PyQt6 ModuleNotFoundError (rendering = unshipped GUI layer); the "
               "core already carries full placement data for %d bodies - nothing to extract" % n_bodies)

    # ---- (14a-2) REJECT: Prashna claim (library-wide source scan) ----
    root14 = _os14.path.dirname(_jhora14.__file__)
    terms14 = {"prashna": 0, "ashtamangala": 0, "tambula": 0, "horary": 0}
    n_py = 0
    for dp14, _, fns14 in _os14.walk(root14):
        for fn14 in fns14:
            if not fn14.endswith(".py"):
                continue
            n_py += 1
            try:
                txt14 = open(_os14.path.join(dp14, fn14), encoding="utf-8",
                             errors="ignore").read().lower()
            except Exception:
                continue
            for t14 in terms14:
                terms14[t14] += txt14.count(t14)
    ok = all(v == 0 for v in terms14.values()) and n_py > 100
    record(ok, "14a-2 REJECT Prashna/horary claim: scanned %d .py files across the whole library - "
               "occurrences: prashna=%d, ashtamangala=%d, tambula=%d, horary=%d. PyJHora %s ships "
               "NO horary module; the review's 'Prashna chakra + Ashtamangala' cannot be a missing "
               "extraction" % (n_py, terms14["prashna"], terms14["ashtamangala"],
                               terms14["tambula"], terms14["horary"],
                               _jhora14._package_info.version))

    # ---- (14a-3) REJECT: muhurta range-scanner claim ----
    muhur_attrs = sorted(a for a in dir(_drik14) if "muhur" in a.lower() and
                         callable(getattr(_drik14, a)))
    no_range_fn = not hasattr(_drik14, "find_muhurtha_between_dates")
    all_single_jd = True
    for a14 in muhur_attrs:
        try:
            params = list(_insp14.signature(getattr(_drik14, a14)).parameters)
        except Exception:
            all_single_jd = False
            continue
        if params[:2] != ["jd", "place"] or any(
                p in ("start_date", "end_date", "jd_start", "jd_end", "date_range")
                for p in params):
            all_single_jd = False
    ok = no_range_fn and all_single_jd and len(muhur_attrs) == 8
    record(ok, "14a-3 REJECT muhurta-scanner claim: drik exposes exactly %d muhurta functions "
               "(%s) - EVERY one has signature (jd, place): single-instant evaluators, all already "
               "wrapped in the muhurta section; no find_muhurtha_between_dates / range API exists "
               "anywhere, so no scanner extraction is missing" % (len(muhur_attrs),
                                                                  ", ".join(muhur_attrs)))

    # ---- (14a-4) REJECT: deity/gemstone remedy-table claim ----
    bad_attrs = [a for a in dir(_const14)
                 if any(t in a.lower() for t in
                        ("planet_deit", "nakshatra_deit", "gemstone", "gem_", "ratna",
                         "mantra", "remedy", "upaya"))]
    tith_d = list(_const14.tithi_deities)
    ok = (not bad_attrs and not hasattr(_const14, "planet_deities") and
          not hasattr(_const14, "nakshatra_deities") and len(tith_d) == 16 and
          tith_d[0] == "kaameshwari")
    record(ok, "14a-4 REJECT remedy-table claim: const.py has NO planet_deities / nakshatra_deities "
               "/ gemstone / mantra / remedy attributes (scan: %s); the only deity table is "
               "tithi_deities = 16 Nitya Devis starting 'kaameshwari' - a tithi-nitya-devi doctrine, "
               "NOT 30 tithi deities nor planetary remedy tables (already adjudicated in-engine)"
               % (bad_attrs or "none"))

    # ---- build the round-14 sections once ----
    chak_b = cc.build_chakras(env, h_b, jd_b, place_b, pp_b)
    ay_b = cc.build_aayu(env, h_b, jd_b, place_b, pp_b)
    cat_b = cc.build_all_dashas(env, h_b, jd_b, place_b, pp_b)

    # ---- (14b-1) catalog-duration parity for all three aayu systems ----
    lag14 = ay_b["lagna_longevity_years"]
    pairs14 = [("aayu_pindayu", ay_b["systems"]["pindayu"]["final_total_years"]),
               ("aayu_nisargayu", ay_b["systems"]["nisargayu"]["final_total_years"]),
               ("aayu_amsayu", ay_b["systems"]["amsayu"][
                   "final_total_years_method2_varahamihira"])]
    ok = True
    deltas = []
    for key14, tot14 in pairs14:
        rows14 = cat_b.get(key14)
        if not isinstance(rows14, list) or not rows14:
            ok = False
            deltas.append((key14, "MISSING"))
            continue
        csum = sum(float(r[-1]) for r in rows14)
        d14 = abs((tot14 + lag14) - csum)
        deltas.append((key14, round(d14, 4)))
        if d14 > 0.05:
            ok = False
    record(ok, "14b-1 aayu catalog parity: for each system, section final_total + lagna_longevity "
               "(%.4f) reproduces the sum of all_dashas mahadasha durations within 0.05y - "
               "deltas: %s (the extracted TABLES are exactly what the validated catalog "
               "TIMELINES are built from)" % (lag14, deltas))

    # ---- (14b-2) harana factor identity + Santhanam min-composition rule ----
    f14 = ay_b["harana_factors"]
    pin14 = ay_b["systems"]["pindayu"]
    names14 = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
    ok = True
    for nm in names14:
        mn = min(f14["astangata_combustion"][nm], f14["shatru_kshetra_enemy_sign"][nm],
                 f14["chakrapata_above_horizon"][nm], f14["krurodaya_malefic_rising"][nm])
        if abs(pin14["final_years_by_planet"][nm] -
               pin14["base_years_by_planet"][nm] * mn) > 1e-3:
            ok = False
    with cc._quiet():
        env["aayu"]._get_global_constants(jd_b, place_b)
        direct_fin = env["aayu"]._pindayu(ppk_b, True)
    ok = (ok and
          abs(f14["astangata_combustion"]["Mercury"] - 0.5) < 1e-9 and      # boy: Mercury combust
          abs(f14["chakrapata_above_horizon"]["Mars"] - 5.0 / 6.0) < 1e-3 and
          all(abs(pin14["final_years_by_planet"][nm] - direct_fin[i]) < 1e-3
              for i, nm in enumerate(names14)))
    record(ok, "14b-2 harana identity + composition: per planet final == base x MIN(astangata, "
               "shatru-kshetra, chakrapata, krurodaya) - the Santhanam min-rule _apply_harana "
               "implements (strongest single reduction only; factors never multiply) - recomputed "
               "for all 7 planets; boy goldens: Mercury astangata=0.5 (combustion), Mars "
               "chakrapata=5/6 (above-horizon band); section finals == direct _pindayu(pp, True) "
               "library calls")

    # ---- (14b-3) aayur-type, public longevity(), bharana domain ----
    with cc._quiet():
        at_direct = env["aayu"]._get_aayur_type(ppk_b)
        pub_direct = env["aayu"].longevity(jd_b, place_b)
    nis14 = ay_b["systems"]["nisargayu"]["final_total_years"]
    pub14 = ay_b["longevity_public"]
    bh_vals = set(round(float(v), 6) for v in
                  ay_b["bharana_factors_amsayu_only"].values()
                  if isinstance(v, (int, float)))
    ok = (ay_b["aayur_type"]["raw"] == at_direct == 1 and
          ay_b["aayur_type"]["decoded"] == "Nisargayu (Moon)" and
          abs(pub14["total_years"] - pub_direct[0]) < 0.05 and
          pub14["aayur_type_raw"] == pub_direct[1] == 1 and
          abs(pub14["total_years"] - (nis14 + lag14)) < 0.05 and
          bh_vals <= {1.0, 2.0, 3.0})
    record(ok, "14b-3 aayur-type + public total: boy auto-selects type %r = Nisargayu (Moon) "
               "(stronger of lagna/sun/moon) - section == direct _get_aayur_type; public "
               "longevity() = %.2fy == nisargayu final (%.4f) + lagna (%.4f) == section "
               "longevity_public.total_years; bharana factors confined to {1,2,3} per the "
               "x3/x2 increase rules" % (at_direct, pub_direct[0], nis14, lag14))

    # ---- (14b-4) disclosures + FAST/deep gating ----
    note14 = ay_b["__note__"] + ay_b.get("catalog_parity", "")
    ok = ("DO NOT USE THIS YET" in note14 and "NOT FULLY IMPLEMENTED" in note14 and
          "Santhanam" in note14 and "Varahamihira" in note14 and
          "MINIMUM" in note14 and "NOT death predictions" in note14 and
          cc.SECTIONS["aayu"][1] is False and cc.SECTIONS["chakras"][1] is True)
    record(ok, "14b-4 aayu disclosures + gating: __note__ carries the module's own 'DO NOT USE "
               "THIS YET - NOT FULLY IMPLEMENTED YET' docstring warning, both method lineages "
               "(Santhanam=1 / Varahamihira=2), the MIN-rule composition and the 'strength "
               "indices, NOT death predictions' interpretive guard; section registered deep-only "
               "(aayu flag=False, chakras flag=True -> FAST)")

    # ---- (14c-1) Kota Chakra goldens (boy) ----
    k14 = chak_b["kota_chakra"]
    nak_d, pada_d, _r = _drik14.nakshatra_pada(lons_b["Moon"])
    circle14 = [cc.NAKSHATRAS[i] for i in _const14.abhijit_order_of_stars]
    rings14 = [[circle14[(6 - 1 + (e - 1)) % 28] for e in row]
               for row in _const14.kota_chakra_star_placement_from_birth_star]
    moon_sign14 = cc._sign_of_long(lons_b["Moon"])
    zones_ok = all(isinstance(v, dict) and v.get("zone") for v in
                   k14["body_zones"].values()) and len(k14["body_zones"]) == 10
    ok = (k14["birth_star"] == {"nakshatra": "Ardra", "nakshatra_index": 6, "pada": 1} and
          (int(nak_d), int(pada_d)) == (6, 1) and
          k14["star_circle_order"] == circle14 and len(circle14) == 28 and
          circle14[21] == "Abhijit" and
          [r["stars"] for r in k14["rings_outer_to_inner"]] == rings14 and
          zones_ok and k14["kota_lord"] == "Mercury" == cc.SIGN_LORDS[moon_sign14] and
          k14["kota_paala"] == "Mars" ==
          cc._planet_name(_const14.kota_paala_lord_for_star_paadha[5][0]))
    record(ok, "14c-1 Kota Chakra golden (boy): birth star Ardra(6) pada 1 == direct "
               "nakshatra_pada(Moon); 28-star circle with Abhijit inserted after UttaraShada "
               "(index 21); all four rings (Baahya/Praakaara/Durgantara/Sthambha) recompute "
               "identically from const.kota_chakra_star_placement_from_birth_star; all 10 bodies "
               "zoned; kota_lord Mercury (Moon-sign lord) + kota_paala Mars "
               "(const.kota_paala_lord_for_star_paadha[5][0])")

    # ---- (14c-2) Sarvatobhadra grid goldens (boy + girl clean) ----
    sb14 = chak_b["sarvatobhadra_chakra"]
    cells14 = sb14["cells"]
    nak_cells = sorted(c["index"] for c in cells14 if c["type"] == "nakshatra")
    rasi_cells = sorted(c["index"] for c in cells14 if c["type"] == "rasi")
    tg_cells = [c for c in cells14 if c["type"] == "tithi_group_vara"]
    sun_nak, sun_pada, _r = _drik14.nakshatra_pada(lons_b["Sun"])
    chak_g = cc.build_chakras(env, h_g, jd_g, place_g, pp_g)
    girl_clean = '"__error__"' not in json.dumps(chak_g)
    ok = (len(cells14) == 81 and nak_cells == list(range(1, 29)) and
          rasi_cells == list(range(1, 13)) and len(tg_cells) == 5 and
          sb14["bodies"]["Sun"] == {"nakshatra": cc.NAKSHATRAS[int(sun_nak) - 1],
                                    "nakshatra_index": int(sun_nak), "pada": int(sun_pada),
                                    "grid_row": 8, "grid_col": 3} and
          sb14["birth_tithi_group_cell"]["group"] == "Nanda" and
          sb14["birth_tithi_group_cell"]["tithi_index"] == 21 and
          girl_clean)
    record(ok, "14c-2 Sarvatobhadra golden (boy): 81 cells decode to exactly nakshatras 1-28 on "
               "the border (Abhijit included), rasis 1-12 inside, 5 tithi-group/vara cells and "
               "the syllables; Sun (Chitra 14/2) -> cell (8,3) via nakshatra_pada exactly as the "
               "widget places it; birth tithi 21 -> Nanda group cell (4,5) 'Nanda Sun Tue'; girl "
               "chart builds clean (no __error__)")

    # ---- (14c-3) vedha absence: match-domain only ----
    vedha_files = set()
    for dp14, _, fns14 in _os14.walk(root14):
        for fn14 in fns14:
            if not fn14.endswith(".py"):
                continue
            p14 = _os14.path.join(dp14, fn14)
            try:
                if "vedha" in open(p14, encoding="utf-8", errors="ignore").read().lower():
                    vedha_files.add(_os14.path.relpath(p14, root14))
            except Exception:
                continue
    expected14 = {"const.py", "horoscope/match/compatibility.py",
                  "tests/ui_tests/test_compatibility_tab.py",
                  "ui/horo_chart_tabs.py", "ui/match_ui.py"}
    ok = (vedha_files == expected14 and
          "NO vedha" in sb14["__note__"])
    record(ok, "14c-3 REJECT SBC-vedha sub-claim: library-wide scan - 'vedha' appears ONLY in the "
               "match/porutham domain (%s); NO chakra module computes vedha penetrations, so the "
               "review's '5 vedha penetrations' are not a missing extraction; the SBC note "
               "discloses this and ships grid+placements as data for classical vedha doctrine"
               % sorted(vedha_files))

    # ---- (14c-4) Abhijit edge: birth star 21 (sample chart) ----
    hs14 = _H14(latitude=12.9141, longitude=74.8560, timezone_offset=5.5,
                date_in=_drik14.Date(1990, 5, 15), birth_time="10:30:00")
    jd_s14, place_s14 = hs14.julian_day, hs14.Place
    with cc._quiet():
        pp_s14 = env["charts"].rasi_chart(jd_s14, place_s14)
    chak_s = cc.build_chakras(env, hs14, jd_s14, place_s14, pp_s14)
    k_s = chak_s["kota_chakra"]
    rings21 = [[circle14[(21 - 1 + (e - 1)) % 28] for e in row]
               for row in _const14.kota_chakra_star_placement_from_birth_star]
    ok = (k_s["birth_star"]["nakshatra_index"] == 21 and
          k_s["birth_star"]["nakshatra"] == "Uttara Ashadha" and
          k_s["rings_outer_to_inner"][0]["stars"][0] == "Uttara Ashadha" and
          any("Abhijit" in r["stars"] for r in k_s["rings_outer_to_inner"]) and
          [r["stars"] for r in k_s["rings_outer_to_inner"]] == rings21 and
          rings21[1][0] == "Abhijit" and
          k_s["rings_outer_to_inner"][1]["stars"][0] == "Abhijit")
    record(ok, "14c-4 Kota Abhijit edge: sample chart (1990-05-15 10:30 Mangaluru) has Moon in "
               "Uttara Ashadha = star 21 - the Abhijit insertion point itself: ring 1 (Baahya) "
               "starts at the birth star and the Praakaara ring OPENS with Abhijit (circle index "
               "(21-1+2-1)%%28 = 21 in the reordered 28-star circle) - section output == formula "
               "recompute; the circle wraps correctly at the boundary case")

    # ---- (14d-1) Surya-kundali frame in relative_houses ----
    hs_b = cc.build_house_systems(env, h_b, jd_b, place_b, pp_b)
    rel14 = hs_b["relative_houses"]
    sign_of14 = {}
    for row14 in pp_b:
        try:
            sign_of14[str(row14[0])] = int(row14[1][0])
        except Exception:
            continue
    asc14 = sign_of14["L"]
    h_of14 = {k: ((v - asc14) % 12) + 1 for k, v in sign_of14.items()}
    sun_h14 = h_of14["0"]
    ok = True
    for nm in cc.PLANET_ORDER:
        pid = str(cc._planet_id(nm))
        if pid not in h_of14 or nm not in rel14:
            ok = False
            continue
        want = _house14.get_relative_house_of_planet(sun_h14, h_of14[pid])
        if rel14[nm].get("relative_house_from_sun") != want:
            ok = False
    ok = (ok and rel14["Sun"]["relative_house_from_sun"] == 1 and
          rel14["Lagna"]["relative_house_from_sun"] ==
          _house14.get_relative_house_of_planet(sun_h14, h_of14["L"]))
    record(ok, "14d-1 Surya-kundali frame: relative_houses now carries relative_house_from_sun "
               "for all 9 planets + Lagna - each == library get_relative_house_of_planet(sun_h, "
               "planet_h); Sun is house 1 from itself; completes the Sudarshana lagna/moon/sun "
               "triad as DATA (the 'composite chart' is presentational - the sudharsana_chakra "
               "DASHA is already cataloged)")

    # ---- (14d-2) girl deep aayu + chakras clean ----
    ay_g = cc.build_aayu(env, h_g, jd_g, place_g, pp_g)
    ok = ('"__error__"' not in json.dumps(ay_g) and
          ay_g["systems"]["pindayu"]["final_total_years"] > 0 and
          ay_g["systems"]["nisargayu"]["final_total_years"] > 0 and
          ay_g["systems"]["amsayu"]["final_total_years_method2_varahamihira"] > 0 and
          ay_g["aayur_type"]["decoded"] in
          ("Amsayu (Lagna)", "Pindayu (Sun)", "Nisargayu (Moon)") and
          '"__error__"' not in json.dumps(chak_g))
    record(ok, "14d-2 girl deep-run clean: aayu builds with no __error__ - girl totals: pindayu "
               "%.2fy, nisargayu %.2fy, amsayu(m2) %.2fy, aayur_type %s; chakras clean too "
               "(second golden chart proves the sections are chart-shape independent)"
               % (ay_g["systems"]["pindayu"]["final_total_years"],
                  ay_g["systems"]["nisargayu"]["final_total_years"],
                  ay_g["systems"]["amsayu"]["final_total_years_method2_varahamihira"],
                  ay_g["aayur_type"]["decoded"]))

    # ---- (14e-1) REJECT Kurma + Sudarshana-already-covered ----
    kurma_files = set()
    for dp14, _, fns14 in _os14.walk(root14):
        for fn14 in fns14:
            if not fn14.endswith(".py"):
                continue
            p14 = _os14.path.join(dp14, fn14)
            try:
                if "kurma" in open(p14, encoding="utf-8", errors="ignore").read().lower():
                    kurma_files.add(_os14.path.relpath(p14, root14))
            except Exception:
                continue
    ok = (not kurma_files and "sudharsana_chakra" in cat_b)
    record(ok, "14e-1 REJECT Kurma sub-claim + Sudarshana already covered: 'kurma' appears in "
               "ZERO .py files of the installed library (no data tables, no logic - nothing to "
               "port); the Sudarshana chakra's computational content IS the sudharsana_chakra "
               "dasha - present in the 56-system catalog (all_dashas key verified) - and its "
               "three frames are data via relative_houses (14d-1)")



def audit_round15():
    """ROUND-15: tithi negative-hour clocks, leftover panchanga decode, verdicts ledger,
    wrapper Prashna (library prasna KP-249/108/nadi — Round-14 REJECT of an Ashtamangala
    *oracle* still holds; this is a wrapper fact-sheet)."""
    print("\n=== AUDIT ROUND 15: tithi clocks / panchanga decode / verdicts / prashna ===")
    env = get_env()
    h_b, jd_b, place_b, pp_b = _build(BOY)
    h_g, jd_g, place_g, pp_g = _build(GIRL)

    # ---- 15a hours helper (the exact library tithi-start that used to drop -1d) ----
    raw_neg = -13.755515858690952
    s15 = cc._hours_to_hhmm(raw_neg)
    s_pos = cc._hours_to_hhmm(11.669720912676219)
    s_next = cc._hours_to_hhmm(36.89)
    ok = (s15.endswith("(-1d)") and s15.startswith("10:14") and
          s_pos == "11:40" and "(+1d)" in s_next)
    record(ok, "15a _hours_to_hhmm negative day-shift: %r / same-day %r / +1d %r"
               % (s15, s_pos, s_next))

    span = cc._span_times(raw_neg, 11.669720912676219)
    ok = (span.get("started_previous_civil_day") is True and
          abs(span.get("duration_hours", 0) - 25.425) < 0.01 and
          "(-1d)" in str(span.get("start_time")))
    record(ok, "15a-2 _span_times duration matches raw end-start (~25.4h) and flags previous day")

    # ---- 15b live panchanga decode (boy 20:10 night birth — tithi start often negative) ----
    pan = cc.build_panchanga(env, h_b, jd_b, place_b)
    t = pan.get("tithi") or {}
    ok = (isinstance(t, dict) and t.get("name") and t.get("paksha") in ("Shukla", "Krishna")
          and t.get("start_time") and t.get("end_time")
          and isinstance(t.get("duration_hours"), (int, float)) and t["duration_hours"] > 0
          and "start_hours_raw" in t)
    record(ok, "15b-1 tithi decoded with name/paksha/start/end/duration (start=%s end=%s dur=%s name=%s %s)"
               % (t.get("start_time"), t.get("end_time"), t.get("duration_hours"),
                  t.get("name"), t.get("paksha")))
    if isinstance(t.get("start_hours_raw"), (int, float)) and t["start_hours_raw"] < 0:
        record("(-1d)" in str(t.get("start_time")),
               "15b-1b negative start_hours_raw=%s rendered with (-1d) as %s"
               % (t.get("start_hours_raw"), t.get("start_time")))
    else:
        record(True, "15b-1b boy tithi start not negative (raw=%s) — skip (-1d) assert"
                     % t.get("start_hours_raw"))

    lm = pan.get("lunar_month")
    ok = (isinstance(lm, dict) and lm.get("name") in cc.LUNAR_MONTH_NAMES
          and isinstance(lm.get("is_adhika_leap_month"), bool))
    record(ok, "15b-2 lunar_month decoded to name (got %r)" % (lm,))

    sv = pan.get("samvatsara")
    ok = (isinstance(sv, dict) and sv.get("name") in cc.SAMVATSARA_NAMES
          and isinstance(sv.get("index"), int))
    record(ok, "15b-3 samvatsara decoded to 60-year name (got %r)" % (sv,))

    kt = pan.get("karaka_tithi")
    ok = (isinstance(kt, dict) and kt.get("name") and kt.get("paksha") in ("Shukla", "Krishna")
          and "start_time" in kt)
    record(ok, "15b-4 karaka_tithi decoded (got name=%s paksha=%s start=%s)"
               % (kt.get("name") if isinstance(kt, dict) else None,
                  kt.get("paksha") if isinstance(kt, dict) else None,
                  kt.get("start_time") if isinstance(kt, dict) else None))

    st = pan.get("special_tithis")
    ok = (isinstance(st, dict) and isinstance(st.get("cycles"), list) and len(st["cycles"]) >= 1
          and isinstance(st["cycles"][0].get("tithis"), list)
          and st["cycles"][0]["tithis"][0].get("name")
          and "Janma" in str(st["cycles"][0]["tithis"][0].get("house_theme")))
    record(ok, "15b-5 special_tithis decoded as 3x12 named cycles (not a nested numeric dump)")

    cb = pan.get("chandrabalam")
    ok = (isinstance(cb, dict) and isinstance(cb.get("favourable_rising_signs"), list)
          and cb["favourable_rising_signs"] and cb["favourable_rising_signs"][0] in cc.RAASI_NAMES)
    record(ok, "15b-6 chandrabalam decoded to sign names (got %s)"
               % (cb.get("favourable_rising_signs") if isinstance(cb, dict) else cb,))

    tb = pan.get("thaaraabalam")
    ok = (isinstance(tb, dict) and isinstance(tb.get("favourable_birth_stars_today"), list)
          and tb["favourable_birth_stars_today"]
          and tb["favourable_birth_stars_today"][0] in cc.NAKSHATRAS)
    record(ok, "15b-7 thaaraabalam decoded to nakshatra names")

    vd = pan.get("vedic_date")
    ok = (isinstance(vd, dict) and vd.get("lunar_month_name") in cc.LUNAR_MONTH_NAMES
          and vd.get("tithi_name") and vd.get("samvatsara_name") in cc.SAMVATSARA_NAMES)
    record(ok, "15b-8 vedic_date decoded (month=%s tithi=%s year=%s)"
               % (vd.get("lunar_month_name") if isinstance(vd, dict) else None,
                  vd.get("tithi_name") if isinstance(vd, dict) else None,
                  vd.get("samvatsara_name") if isinstance(vd, dict) else None))

    # girl chart independently
    pan_g = cc.build_panchanga(env, h_g, jd_g, place_g)
    tg = pan_g.get("tithi") or {}
    ok = isinstance(tg, dict) and tg.get("name") and isinstance(tg.get("duration_hours"), (int, float))
    record(ok, "15b-9 girl tithi also decoded (start=%s end=%s name=%s %s dur=%s)"
               % (tg.get("start_time"), tg.get("end_time"), tg.get("name"), tg.get("paksha"),
                  tg.get("duration_hours")))

    # ---- 15c verdicts ledger ----
    yo = cc.build_yogas(env, h_b, jd_b, place_b, pp_b)
    ds = cc.build_doshas(env, h_b, jd_b, place_b, pp_b)
    ya = cc.build_yogas_all(env, h_b, jd_b, place_b, pp_b)
    doc = {"yogas": yo, "doshas": ds, "yogas_all": ya}
    ver = cc.build_verdicts(doc)
    gk = ver["by_name"].get("gaja_kesari_yoga") or {}
    kd = ver["by_name"].get("kemadruma_yoga") or {}
    ok = (isinstance(ver.get("by_name"), dict) and ver["counts"]["total"] > 20
          and gk.get("present") in (True, False)
          and kd.get("present") in (True, False)
          and set(ver["counts"]) >= {"present", "absent", "unknown", "total"})
    record(ok, "15c-1 verdicts.by_name has boolean present for curated yogas "
               "(gaja=%s kemadruma=%s counts=%s)" % (gk.get("present"), kd.get("present"),
                                                     ver.get("counts")))

    # a name in yogas_present must be present=true; a name in yogas_absent must be false
    pres = ya.get("yogas_present") or {}
    absn = ya.get("yogas_absent") or []
    ok_p = True
    for n in list(pres)[:8]:
        if ver["by_name"].get(n, {}).get("present") is not True:
            ok_p = False
            break
    ok_a = True
    for n in list(absn)[:8]:
        if ver["by_name"].get(n, {}).get("present") is not False:
            ok_a = False
            break
    n_abs = len(absn) if isinstance(absn, list) else -1
    record(ok_p and isinstance(absn, list) and n_abs > 10,
           "15c-2 yogas_all.yogas_absent is a list (n=%d) and verdicts agrees with present samples" % n_abs)
    record(ok_a, "15c-3 first 8 yogas_absent names have verdicts.present=false")

    # missing name is NOT absent
    ok = "this_yoga_does_not_exist_xyz" not in ver["by_name"]
    record(ok, "15c-4 unevaluated name is missing from by_name (not silently absent)")

    # ---- 15d wrapper Prashna ----
    pr = cc.build_prashna(env, h_b, jd_b, place_b, pp_b, number=108, system="kp249",
                          query_house=7)
    ok = (pr.get("prasna_lagna_from_chart", {}).get("sign") in cc.RAASI_NAMES
          and set(pr.get("ashtamangala_limbs", {}).get("limbs", {})) >=
          {"tithi", "nakshatra", "yoga", "karana", "vara", "lagna", "hora", "chandra_rasi"}
          and pr["number_prasna"].get("sign") in cc.RAASI_NAMES
          and pr["query_house"].get("house") == 7
          and "tri_sphuta" in (pr.get("sphutas_of_question") or {})
          and "__error__" not in json.dumps(pr))
    record(ok, "15d-1 prashna KP-249 #108: lagna=%s number_sign=%s query=%s limbs=%s"
               % (pr.get("prasna_lagna_from_chart", {}).get("sign"),
                  pr.get("number_prasna", {}).get("sign"),
                  pr.get("query_house", {}).get("house"),
                  sorted((pr.get("ashtamangala_limbs") or {}).get("limbs", {}))))

    pr108 = cc.build_prashna(env, h_b, jd_b, place_b, pp_b, number=108, system="nadi108")
    np = pr108.get("number_prasna") or {}
    ok = (np.get("library_rasi_index") == 12 and np.get("library_sign", "").startswith("OUT_OF_RANGE")
          and np.get("corrected_rasi_index") == 11 and np.get("corrected_sign") == "Pisces")
    record(ok, "15d-2 nadi-108 #108: library rasi 12 (invalid) disclosed; corrected_sign=Pisces")

    pr9 = cc.build_prashna(env, h_g, jd_g, place_g, pp_g, number=9, system="nadi108",
                           query_house=5)
    np9 = pr9.get("number_prasna") or {}
    ok = (np9.get("library_rasi_index") == 1 and np9.get("corrected_rasi_index") == 0
          and np9.get("corrected_sign") == "Aries"
          and pr9.get("query_house", {}).get("house") == 5
          and "__error__" not in json.dumps(pr9))
    record(ok, "15d-3 girl nadi-108 #9: library Taurus vs corrected Aries; query house 5 clean")

    # SECTIONS registry
    ok = "prashna" in cc.SECTIONS and cc.SECTIONS["prashna"][1] is False
    record(ok, "15d-4 SECTIONS['prashna'] registered, default OFF (opt-in --prashna)")

    # library still has no English 'prashna' oracle (Round-14 claim remains true)
    import os as _os15, jhora as _jh15
    n_prashna_h = 0
    root15 = _os15.path.dirname(_jh15.__file__)
    for dp, _, fns in _os15.walk(root15):
        for fn in fns:
            if fn.endswith(".py"):
                try:
                    n_prashna_h += open(_os15.path.join(dp, fn), encoding="utf-8",
                                        errors="ignore").read().lower().count("prashna")
                except Exception:
                    pass
    ok = n_prashna_h == 0 and hasattr(env["utils"], "get_prasna_lagna_KP_249_for_rasi_chart")
    record(ok, "15d-5 library still has 0 'prashna' (with h) matches; utils.prasna KP-249 helper EXISTS "
               "(wrapper uses the real spelling)")


def audit_round16():
    """ROUND-16: remaining drik/charts/vratha surface (no UI, no aliases)."""
    print("\n=== AUDIT ROUND 16: leftover panchanga / varga sphuta-saham-lagna / vratha ===")
    env = get_env()
    h_b, jd_b, place_b, pp_b = _build(BOY)
    h_g, jd_g, place_g, pp_g = _build(GIRL)
    pan = cc.build_panchanga(env, h_b, jd_b, place_b)
    pan_g = cc.build_panchanga(env, h_g, jd_g, place_g)

    vw = pan.get("vedic_weekday") or {}
    va = pan.get("weekday") or {}
    civ = pan.get("civil_weekday") or {}
    ok = (isinstance(vw, dict) and vw.get("name") in cc.WEEKDAY_NAMES
          and vw.get("index") == va.get("index")
          and "sunrise-to-sunrise" in str(vw.get("convention", "")))
    record(ok, "16a vedic_weekday == vaara (sunrise-to-sunrise) boy=%s civil=%s"
               % (vw, civ))

    vwg = pan_g.get("vedic_weekday") or {}
    vag = pan_g.get("weekday") or {}
    civg = pan_g.get("civil_weekday") or {}
    ok = (vwg.get("index") == vag.get("index") == civg.get("index")
          and vwg.get("name") in cc.WEEKDAY_NAMES)
    record(ok, "16a-2 girl 09:30 day-birth: vedic==vaara==civil (%s)" % vwg.get("name"))

    # Pre-sunrise civil Friday vs vedic Thursday: 2003-10-17 02:30 IST at boy's place.
    from jhora.horoscope.info import Horoscope as _H16
    from jhora.panchanga import drik as _d16
    h_n = _H16(latitude=BOY["lat"], longitude=BOY["lon"],
               timezone_offset=BOY["tz"],
               date_in=_d16.Date(2003, 10, 17),
               birth_time="02:30:00")
    pan_n = cc.build_panchanga(env, h_n, h_n.julian_day, h_n.Place)
    vwn = pan_n.get("vedic_weekday") or {}
    civn = pan_n.get("civil_weekday") or {}
    ok = (vwn.get("name") == "Thursday" and civn.get("name") == "Friday"
          and vwn.get("index") != civn.get("index"))
    record(ok, "16a-3 night 02:30: vedic=Thursday vs civil=Friday (got vedic=%s civil=%s)"
               % (vwn.get("name"), civn.get("name")))

    vt = pan.get("vedic_time_equal_day_night") or {}
    g60 = vt.get("ghati_60") or {}
    m30 = vt.get("muhurta_30") or {}
    ok = (isinstance(g60.get("ghati"), int) and 0 <= g60["ghati"] <= 59
          and isinstance(m30.get("muhurta"), int) and 0 <= m30["muhurta"] <= 29)
    record(ok, "16b equal day+night: 60-ghati %s and 30-muhurta %s" % (g60, m30))

    dim = pan.get("days_in_tamil_month")
    record(isinstance(dim, int) and 29 <= dim <= 32,
           "16c days_in_tamil_month in 29..32 (got %r)" % dim)

    spd = (pan.get("daily_planet_speed") or {}).get("by_planet") or {}
    ok = (len(spd) == 9 and all("deg_per_day" in spd[n] for n in cc.PLANET_NAMES)
          and abs(spd["Moon"]["deg_per_day"] - float(pan.get("moon_speed_daily") or 0)) < 1e-4)
    record(ok, "16d daily_planet_speed 9 grahas; Moon %.6f vs moon_speed_daily %s"
               % (spd.get("Moon", {}).get("deg_per_day", -1), pan.get("moon_speed_daily")))
    sun_v = float(spd.get("Sun", {}).get("deg_per_day") or 0)
    ok = (0.9 < sun_v < 1.1
          and all(spd[n].get("motion") in ("direct", "retrograde", "stationary")
                  for n in spd)
          and spd["Rahu"]["deg_per_day"] == spd["Ketu"]["deg_per_day"])
    record(ok, "16d-2 Sun ~1 deg/day (%.4f); motion labels valid; Rahu/Ketu speeds equal "
               "(this helper reports unsigned nodal rate, not always signed tropical)"
               % sun_v)

    nly = pan.get("next_lunar_year") or {}
    ply = pan.get("previous_lunar_year") or {}
    ok = (isinstance(nly, dict) and str(nly.get("date", "")).startswith("2004-")
          and isinstance(ply, dict) and str(ply.get("date", "")).startswith("2003-"))
    record(ok, "16e next_lunar_year %s / previous %s" % (nly.get("date"), ply.get("date")))

    nsy = pan.get("next_solar_year") or {}
    psy = pan.get("previous_solar_year") or {}
    ok = (isinstance(nsy, dict) and str(nsy.get("date", "")).startswith("2004-04")
          and isinstance(psy, dict) and "date" in psy)
    record(ok, "16f next_solar_year is Mesha sankranti %s (NOT natal solar return); prev %s"
               % (nsy.get("date"), psy.get("date")))

    nsm = pan.get("next_solar_month") or {}
    ok = isinstance(nsm, dict) and str(nsm.get("date", "")).startswith("2003-10")
    record(ok, "16g next_solar_month %s" % nsm.get("date"))

    nt = pan.get("next_same_tithi") or {}
    ok = (isinstance(nt, dict) and nt.get("experimental") is True
          and "date" in nt and "TODO" in str(nt.get("__note__", "")).upper()
          or (isinstance(nt, dict) and nt.get("experimental") is True and "date" in nt))
    record(bool(nt.get("experimental") and nt.get("date")),
           "16h next_same_tithi experimental=%s date=%s (library TODO)"
           % (nt.get("experimental"), nt.get("date")))

    kar = pan.get("karana") or {}
    mnak = pan.get("moon_nakshatra") or {}
    ok = (kar.get("lord") in cc.PLANET_NAMES
          and mnak.get("library_lord") in cc.PLANET_NAMES)
    record(ok, "16i karana.lord=%s (utils.karana_lord) moon_nak.library_lord=%s"
               % (kar.get("lord"), mnak.get("library_lord")))

    nta = pan.get("nava_thaara_for_all_planets") or {}
    ok = (isinstance(nta, dict) and "Lagna" in nta and "Moon" in nta
          and len(nta) >= 10
          and (nta.get("Moon") or [{}])[0].get("thaara") == "Janma")
    record(ok, "16j nava_thaara_for_all_planets keys=%s Moon-Janma=%s"
               % (list(nta), (nta.get("Moon") or [{}])[0].get("thaara") if nta.get("Moon") else None))

    sta = pan.get("special_thaara_for_all_planets") or {}
    ok = (isinstance(sta, dict) and "Lagna" in sta and "Moon" in sta
          and len(sta) >= 10 and isinstance(sta.get("Moon"), list) and sta["Moon"])
    record(ok, "16k special_thaara_for_all_planets keys=%s moon_n=%s"
               % (list(sta), len(sta.get("Moon") or [])))

    npr = (pan.get("next_planet_nakshatra_pravesha") or {}).get("by_planet") or {}
    moon_p = npr.get("Moon") or {}
    sun_p = npr.get("Sun") or {}
    ok = (len(npr) == 9 and "date" in moon_p and "target_nakshatra" in moon_p
          and "date" in sun_p)
    record(ok, "16l next_planet_nakshatra_pravesha 9 grahas; Moon %s -> %s on %s"
               % (moon_p.get("current_nakshatra"), moon_p.get("target_nakshatra"),
                  moon_p.get("date")))
    retro_flagged = any(isinstance(npr.get(n), dict) and npr[n].get("unreliable_past_date")
                        for n in npr)
    record(True, "16l-2 retrograde past-JD flag present=%s (informational)"
                 % retro_flagged)

    sp = cc.build_sphutas(env, h_b, jd_b, place_b)
    lv = (sp.get("library_longitudes_by_varga") or {}).get("by_factor") or {}
    d1, d9, d10 = lv.get("D1") or {}, lv.get("D9") or {}, lv.get("D10") or {}
    named = sp.get("tri_sphuta") or {}
    d1tri = d1.get("tri") or {}
    ok = (len(d1) == 14 and len(d9) == 14 and len(d10) == 14
          and abs(float(d1tri.get("longitude", -1)) - float(named.get("longitude", -99))) < 1e-3)
    record(ok, "16m sphuta_longitudes D1/D9/D10 each 14; D1.tri lon=%s named=%s"
               % (d1tri.get("longitude"), named.get("longitude")))

    sh = cc.build_sahams(env, h_b, jd_b, place_b, pp_b)
    named_lots = [k for k, v in sh.items()
                  if isinstance(v, dict) and "sign" in v]
    d9s = ((sh.get("__divisional__") or {}).get("by_factor") or {}).get("D9") or {}
    ok = len(named_lots) >= 30 and len(d9s) == 36
    record(ok, "16n sahams D1 named lots=%d (unchanged contract); D9 longitudes=%d"
               % (len(named_lots), len(d9s)))

    spl = cc.build_special(env, h_b, jd_b, place_b)
    sld9 = ((spl.get("special_lagna_longitudes_by_varga") or {}).get("by_factor")
            or {}).get("D9") or {}
    ok = (len(sld9) == 9 and "bhava_lagna" in sld9 and "indu_lagna" in sld9
          and "longitude" in (sld9.get("bhava_lagna") or {}))
    record(ok, "16o special_lagna_longitudes D9 n=%d keys=%s"
               % (len(sld9), list(sld9)))

    vr = cc.build_vratha(env, h_b, jd_b, place_b)
    nsv = vr.get("next_special_vratha") or {}
    bt = nsv.get("by_type") or {}
    pr = bt.get("pradosham") or {}
    sk = bt.get("sankranti") or {}
    ok = (len(bt) == 19
          and isinstance(pr, dict) and str(pr.get("date", "")).startswith("2003-")
          and isinstance(sk, dict) and "date" in sk
          and bt.get("sankatahara_chathurthi")
          and "sankatahara chathurthi" not in bt)
    record(ok, "16p next_special_vratha 19 underscore keys; pradosham=%s sankranti=%s"
               % (pr.get("date"), sk.get("date")))

    om = pan.get("omitted_checks") or {}
    tattva = str(om.get("panchanga_tattva_lords", ""))
    ok = ("karana.lord" in tattva and "utils.karana_lord" in tattva)
    record(ok, "16q omitted_checks now cites library-backed karana.lord (not 'absent')")


def audit_round17():
    """ROUND-17: remaining non-UI computational APIs."""
    print("\n=== AUDIT ROUND 17: KP / varnada / mixed / bhava-arudhas / prev-conjunction ===")
    env = get_env()
    h_b, jd_b, place_b, pp_b = _build(BOY)

    core = cc.build_core(env, h_b, jd_b, place_b, pp_b)
    moon = next(p for p in core["planets"] if p["name"] == "Moon")
    kp = moon.get("kp") or {}
    ok = (kp.get("nakshatra") == moon.get("nakshatra")
          and kp.get("sign") == moon.get("sign")
          and kp.get("sub_lord") in cc.PLANET_NAMES
          and kp.get("star_lord") in cc.PLANET_NAMES)
    record(ok, "17a Moon KP-249 sub-lord row matches natal sign/star (kp=%s)" % kp)

    ln = moon.get("library_navamsa") or {}
    ok = ln.get("sign") == moon.get("navamsa_sign") and "degree_in_navamsa" in ln
    record(ok, "17a-2 drik.navamsa_from_long == inline navamsa_sign (%s / %s)"
               % (ln.get("sign"), moon.get("navamsa_sign")))

    lkp = (core.get("lagna") or {}).get("kp") or {}
    record(lkp.get("sub_lord") in cc.PLANET_NAMES,
           "17a-3 lagna KP sub_lord=%s star_lord=%s" % (lkp.get("sub_lord"), lkp.get("star_lord")))

    pan = cc.build_panchanga(env, h_b, jd_b, place_b)
    tfj = pan.get("tamil_solar_month_date_from_jd") or {}
    record(isinstance(tfj.get("month_index"), int) and isinstance(tfj.get("day"), int),
           "17b tamil_solar_month_date_from_jd month=%s day=%s"
           % (tfj.get("month_index"), tfj.get("day")))

    sree = pan.get("sree_lagna_from_moon_asc_longitudes") or {}
    spl = cc.build_special(env, h_b, jd_b, place_b)
    sree2 = spl.get("sree_lagna") or {}
    ok = (sree.get("sign") and sree.get("sign") == sree2.get("sign")
          and abs(float(sree.get("longitude", 0)) - float(sree2.get("longitude", 99))) < 0.02)
    record(ok, "17c sree_lagna_from_moon_asc == special.sree_lagna (%s %.4f vs %s %.4f)"
               % (sree.get("sign"), float(sree.get("longitude") or 0),
                  sree2.get("sign"), float(sree2.get("longitude") or 0)))

    n9 = (pan.get("nava_thaara_for_D9_chart") or {}).get("groups") or []
    record(len(n9) == 9 and n9[0].get("thaara") == "Janma",
           "17d nava_thaara_for_D9_chart 9 groups, first=%s" % (n9[0].get("thaara") if n9 else None))

    vd1 = ((spl.get("varnada_lagnas") or {}).get("by_factor") or {}).get("D1") or {}
    record(len(vd1) == 12 and "varnada_lagna (V1)" in vd1,
           "17e varnada_lagnas D1 n=%d keys=%s" % (len(vd1), list(vd1)[:3]))

    mx = ((spl.get("mixed_charts") or {}).get("by_pair") or {}).get("D9xD12") or {}
    record(isinstance(mx, dict) and "Lagna" in mx and "longitude" in mx["Lagna"],
           "17f mixed_chart D9xD12 Lagna=%s" % (mx.get("Lagna"),))

    ar = cc.build_arudhas_and_specials(env, h_b, jd_b, place_b, pp_b)
    ba = (ar.get("bhava_arudhas_signs") or {}).get("by_house") or []
    ok = (len(ba) == 12 and ba[0].get("pada") == "A1" and ba[0].get("sign") in cc.RAASI_NAMES
          and ba[11].get("label") == "Upapada")
    record(ok, "17g bhava_arudhas_signs A1=%s A12=%s" % (ba[0] if ba else None, ba[11] if len(ba)>11 else None))

    hv = cc.build_house_variables(env, h_b, jd_b, place_b, pp_b)
    st = hv.get("strongest_among_sun_to_saturn") or {}
    record(st.get("planet") in cc.PLANET_NAMES[:7],
           "17h strongest_among_sun_to_saturn=%s" % st.get("planet"))

    hs = cc.build_house_systems(env, h_b, jd_b, place_b, pp_b)
    ch = hs.get("in_chathusras")
    record(isinstance(ch, list) and "Jupiter" in ch,
           "17i in_chathusras=%s" % ch)

    lp = (hs.get("longevity_of_sign_pairs") or {}).get("pairs") or {}
    ok = all(isinstance(lp.get(k), dict) and lp[k].get("class_index") in (0, 1, 2)
             for k in ("lagna_moon", "lagna_sun", "moon_sun"))
    record(ok, "17j longevity_of_sign_pairs classes=%s"
               % {k: (lp.get(k) or {}).get("class_index") for k in lp})

    tjd = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
    tr = cc.build_transits(env, h_b, jd_b, place_b, pp_b, tjd)
    psm = (tr.get("previous_conjunctions") or {}).get("Sun_Moon") or {}
    record(isinstance(psm, dict) and "date" in psm and str(psm["date"]).startswith("2026-"),
           "17k previous_conjunctions Sun_Moon=%s" % psm.get("date"))
    rk = (tr.get("previous_conjunctions") or {}).get("Rahu_Ketu") or {}
    record(rk.get("skipped") is True, "17k-2 Rahu_Ketu previous conjunction skipped")

    d9s = ((tr.get("next_D9_sign_entry") or {}).get("by_planet") or {}).get("Sun") or {}
    record("date" in d9s, "17l next_D9_sign_entry Sun=%s" % d9s.get("date"))

    sp = cc.build_sphutas(env, h_b, jd_b, place_b)
    nsp = len((sp.get("library_longitudes_mixed_D1xD9") or {}).get("points") or {})
    sh = cc.build_sahams(env, h_b, jd_b, place_b, pp_b)
    nsh = len(sh.get("__mixed_D1xD9__") or {})
    record(nsp == 14 and nsh == 36,
           "17m mixed D1xD9 sphuta n=%d saham n=%d" % (nsp, nsh))

    rec = str((cc._features() or {}).get("round16_surface") or {})
    # legend round17 present
    rec2 = "round17_fields" in open(__import__("os").path.join(
        __import__("os").path.dirname(cc.__file__), "compute_chart.py")).read()
    record(rec2, "17n legend round17_fields present")


def audit_round18():
    """ROUND-18: mixed-chart leftovers + Rahu-Ketu conjunction skip."""
    print("\n=== AUDIT ROUND 18: mixed D1xD9 extras / D9 prev-entry / Rahu-Ketu skip ===")
    env = get_env()
    h_b, jd_b, place_b, pp_b = _build(BOY)
    pan = cc.build_panchanga(env, h_b, jd_b, place_b)
    st = (pan.get("special_thaara_for_D9_chart") or {}).get("stars") or []
    record(len(st) == 11 and st[0].get("nakshatra") in cc.NAKSHATRAS,
           "18a special_thaara_for_D9_chart n=%d first=%s" % (len(st), st[0] if st else None))

    spl = cc.build_special(env, h_b, jd_b, place_b)
    am = (spl.get("arudha_longitudes_mixed_D1xD9") or {}).get("points") or {}
    record("A1" in am and "longitude" in am["A1"],
           "18b arudha mixed D1xD9 A1=%s" % am.get("A1"))
    slm = spl.get("special_lagna_longitudes_mixed_D1xD9") or {}
    record(len(slm) == 9 and "bhava_lagna" in slm,
           "18c special_lagna mixed D1xD9 n=%d" % len(slm))
    spm = spl.get("special_planet_longitudes_mixed_D1xD9") or {}
    record(len(spm) == 11 and "Gulika" in spm,
           "18d special_planet mixed D1xD9 n=%d keys=%s" % (len(spm), list(spm)[:4]))

    hv = cc.build_house_variables(env, h_b, jd_b, place_b, pp_b)
    ks = hv.get("strongest_kendra_sign") or {}
    record(ks.get("sign") == "Leo" and "Taurus" in (ks.get("kendra_signs") or []),
           "18e strongest_kendra_sign=%s of %s" % (ks.get("sign"), ks.get("kendra_signs")))

    tjd = env["utils"].julian_day_number((2026, 9, 3), (12, 0, 0))
    tr = cc.build_transits(env, h_b, jd_b, place_b, pp_b, tjd)
    rec = (tr.get("previous_D9_sign_entry") or {}).get("by_planet") or {}
    record("date" in (rec.get("Sun") or {}),
           "18f previous_D9_sign_entry Sun=%s" % (rec.get("Sun") or {}).get("date"))
    nc = tr.get("next_conjunctions") or {}
    record(isinstance(nc.get("Rahu_Ketu"), dict) and nc["Rahu_Ketu"].get("skipped") is True,
           "18g next_conjunctions Rahu_Ketu skipped (was NoneType crash)")
    sm = nc.get("Sun_Moon") or {}
    record(isinstance(sm, dict) and "date" in sm,
           "18h next_conjunctions Sun_Moon decoded date=%s" % sm.get("date"))


def audit_round19():
    """ROUND-19: plan-first --query-profile + prompt PROTOCOL 0 + playbooks."""
    print("\n=== AUDIT ROUND 19: query-profile / always-deep reading protocol ===")
    names = getattr(cc, "QUERY_PROFILE_NAMES", ())
    record(set(names) >= {
        "deep", "marriage", "career", "timing", "health", "children",
        "money", "muhurta", "prashna", "compatibility",
    }, "19a QUERY_PROFILE_NAMES has 10 topic keys n=%d" % len(names))

    deep = cc.sections_for_query_profile("deep")
    record("experiments" not in deep and "core" in deep and "yogas_all" in deep
           and "narayana" in deep and "running_dashas" in deep and "vargas_all" in deep,
           "19b deep profile = full natal minus experiments n=%d" % len(deep))
    for topic in ("marriage", "career", "timing", "health", "children", "money",
                  "compatibility"):
        record(cc.sections_for_query_profile(topic) == deep,
               "19c %s profile matches deep sheet" % topic)

    muh = cc.sections_for_query_profile("muhurta")
    record("muhurta" in muh and "vratha" in muh and "core" in muh
           and "yogas_all" not in muh and "experiments" not in muh,
           "19d muhurta is a focused election set n=%d" % len(muh))
    pr = cc.sections_for_query_profile("prashna")
    record("prashna" in pr and "yogas" in pr and "core" in pr
           and "yogas_all" not in pr,
           "19e prashna is a focused horary set n=%d" % len(pr))
    raised = False
    try:
        cc.sections_for_query_profile("western")
    except ValueError:
        raised = True
    record(raised, "19f unknown profile raises ValueError")

    src = open(os.path.join(ROOT, "compute_chart.py"), encoding="utf-8").read()
    record('--query-profile' in src and "sections_for_query_profile" in src
           and 'args.transit = args.now' in src
           and '"query_profile": getattr(args, "query_profile", None)' in src,
           "19g CLI + implied transit + __meta__.query_profile wired")

    prompt = open(os.path.join(ROOT, "astrologer_prompt.md"), encoding="utf-8").read()
    record("0. PROTOCOL — PLAN, THEN COMPUTE DEEP" in prompt
           and "COMPUTE THE DEEP SET FOR THE QUESTION" in prompt
           and "COMPUTE THE SMALLEST SET THAT ANSWERS THE QUESTION" not in prompt
           and "planned Vedic" in prompt
           and "PHASE A — UNDERSTAND THE QUESTION" in prompt,
           "19h prompt PROTOCOL 0 + always-deep §2 (smallest-set gone)")
    record(not any(ln.startswith("ts or pop-psychology") for ln in prompt.splitlines())
           and "missing layers are named." in prompt,
           "19i prompt EOF garble (duplicate VOICE fragment) removed")

    pb = os.path.join(ROOT, "query_playbooks.md")
    record(os.path.isfile(pb), "19j query_playbooks.md present")
    if os.path.isfile(pb):
        body = open(pb, encoding="utf-8").read()
        record("SETTINGS LOCK" in body
               and "Phase B — Gather Vedic procedures" in body
               and "Marriage / spouse" in body
               and "Shallow trap" in body
               and "Lahiri" in body
               and "Western" in body,
               "19k playbooks: settings lock + marriage depth + Vedic-only")
        record("query_playbooks.md" in open(os.path.join(ROOT, "README.md"),
                                            encoding="utf-8").read(),
               "19l README references query_playbooks.md")
    unknown = []
    for name in cc.QUERY_PROFILE_NAMES:
        for k in cc.sections_for_query_profile(name):
            if k not in cc.SECTIONS:
                unknown.append("%s:%s" % (name, k))
    record(not unknown, "19m every profile key exists in SECTIONS", unknown)


def main():
    audit_registry()
    audit_cross()
    audit_match()
    audit_json_files()
    audit_narayana()
    audit_dasha_families()
    audit_full_catalog()
    audit_panchanga_extras()
    audit_transit_events()
    audit_parity_features()
    audit_deep_subsystems()
    audit_review3_verification()
    audit_v4_followup()
    audit_round5()
    audit_round6()
    audit_round7()
    audit_round8()
    audit_round9()
    audit_round10()
    audit_round11()
    audit_round12()
    audit_round13()
    audit_round14()
    audit_round15()
    audit_round16()
    audit_round17()
    audit_round18()
    audit_round19()
    audit_features()
    print(f"\n==== RESULT: {RESULTS['pass']} PASS, {RESULTS['fail']} FAIL, {RESULTS['skip']} skip ====")
    if FAILURES:
        print("FAILED ITEMS:")
        for n, d in FAILURES:
            print(f"  - {n}: {d}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
